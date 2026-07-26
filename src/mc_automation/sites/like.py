from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urldefrag, urljoin

import requests
from bs4 import BeautifulSoup, Tag

from ..ai_solver import AISolverError, CaptchaSolution
from ..config import LikeSiteConfig
from ..models import ActionResult, ActionStatus
from ..transport import HttpTransport
from .base import SiteParseError

LIKE_LABELS = ("点我喜欢", "点赞", "喜欢", "like")
ALREADY_LIKED = ("已喜欢", "已经喜欢", "已点赞", "already liked")
SUCCESS_MARKERS = ("喜欢成功", "点赞成功", "感谢", "success", "liked")
CAPTCHA_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{3,8}$")


class CaptchaSolver(Protocol):
    def solve_wdsjfwq_captcha(
        self,
        image: bytes,
        *,
        content_type: str | None = None,
    ) -> CaptchaSolution: ...


class LikeAdapter:
    is_one_shot_action = True

    def __init__(
        self,
        config: LikeSiteConfig,
        transport: HttpTransport,
        *,
        captcha_solver: CaptchaSolver | None = None,
        username_factory: Callable[[], str] | None = None,
    ) -> None:
        self.name = config.name
        self.url = config.url
        self.transport = transport
        self.captcha_solver = captcha_solver
        self.username_factory = username_factory or self._random_username

    def _result(self, status: ActionStatus, message: str) -> ActionResult:
        return ActionResult(self.name, "like", status, message)

    @staticmethod
    def _matches_like(text: str) -> bool:
        normalized = text.casefold()
        return any(label.casefold() in normalized for label in LIKE_LABELS) and not any(
            marker.casefold() in normalized for marker in ALREADY_LIKED + ("取消喜欢", "unlike")
        )

    def _url(self, value: str) -> str:
        return urljoin(self.url, value)

    def _is_self_link(self, value: str) -> bool:
        return urldefrag(self._url(value))[0] == urldefrag(self.url)[0]

    def _mclists_button(self, soup: BeautifulSoup) -> tuple[str, str] | None:
        if self.name != "mclists":
            return None
        buttons = soup.select("button#server-like-button[data-server-id]")
        if not buttons:
            return None
        if len(buttons) != 1:
            raise SiteParseError("MCLISTS 喜欢按钮不唯一，未提交请求")
        server_id = str(buttons[0].get("data-server-id", "")).strip()
        if not server_id.isdigit() or int(server_id) <= 0:
            raise SiteParseError("MCLISTS 喜欢按钮缺少有效服务器 ID，未提交请求")
        return self._url("/server-like.php"), server_id

    @staticmethod
    def _requires_interaction(form: Tag) -> bool:
        return bool(
            form.select(
                "input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select"
            )
        )

    def _json_result(self, text: str) -> ActionResult | None:
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        message = payload.get("message")
        public_message = str(message) if isinstance(message, str) else ""
        if payload.get("success") is True:
            return self._result(ActionStatus.SUCCESS, public_message or "喜欢操作完成")
        if any(marker.casefold() in public_message.casefold() for marker in ALREADY_LIKED):
            return self._result(ActionStatus.SKIPPED, public_message)
        return self._result(ActionStatus.TECHNICAL_FAILURE, public_message or "喜欢操作失败")

    @staticmethod
    def _wdsjfwq_like_count(html: str) -> int | None:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        values = {
            int(value)
            for pattern in (
                r"当前点赞数\s*[:：]\s*(\d+)",
                r"点赞\s*\(\s*(\d+)\s*\)",
            )
            for value in re.findall(pattern, text)
        }
        if len(values) > 1:
            raise SiteParseError("WDSJFWQ 点赞计数相互矛盾，无法确认结果")
        return next(iter(values), None)

    @staticmethod
    def _random_username() -> str:
        return f"Player{100000 + secrets.randbelow(900000)}"

    @staticmethod
    def _input_signature(field: Tag) -> str:
        parts = [
            str(field.get("name", "")),
            str(field.get("id", "")),
            str(field.get("placeholder", "")),
            str(field.get("aria-label", "")),
        ]
        return " ".join(parts).casefold()

    @classmethod
    def _find_input(cls, form: Tag, markers: tuple[str, ...]) -> Tag | None:
        for field in form.select("input[name]"):
            signature = cls._input_signature(field)
            if any(marker.casefold() in signature for marker in markers):
                return field
        return None

    @classmethod
    def _input_data(cls, form: Tag) -> dict[str, str]:
        data: dict[str, str] = {}
        for field in form.select("input[name]"):
            field_type = str(field.get("type", "text")).casefold()
            if field_type in {"submit", "button", "image", "file"}:
                continue
            if field_type in {"checkbox", "radio"} and not field.has_attr("checked"):
                continue
            data[str(field["name"])] = str(field.get("value", ""))
        submitters = [
            control
            for control in form.select('button[name], input[type="submit"][name]')
            if cls._matches_like(control.get_text(" ", strip=True) or str(control.get("value", "")))
        ]
        if len(submitters) > 1:
            raise SiteParseError("喜欢表单提交按钮不唯一，未提交请求")
        if submitters:
            data[str(submitters[0]["name"])] = str(submitters[0].get("value", ""))
        return data

    def _captcha_image_url(self, soup: BeautifulSoup, form: Tag) -> str | None:
        def is_captcha_image(image: Tag) -> bool:
            signature = " ".join(
                [
                    str(image.get("src", "")),
                    str(image.get("id", "")),
                    str(image.get("class", "")),
                    str(image.get("alt", "")),
                    str(image.get("title", "")),
                ]
            ).casefold()
            return "captcha" in signature or "验证码" in signature

        images = [image for image in form.select("img[src]") if is_captcha_image(image)]
        if not images:
            images = [image for image in soup.select("img[src]") if is_captcha_image(image)]
        if len(images) != 1:
            return None
        return self._url(str(images[0]["src"]))

    def _submit_form(self, form: Tag, data: dict[str, str]) -> requests.Response:
        action = self._url(str(form.get("action", self.url)))
        if str(form.get("method", "get")).casefold() == "post":
            return self.transport.post(action, data=data)
        return self.transport.get(action, params=data)

    def _solve_wdsjfwq_form(self, soup: BeautifulSoup, form: Tag) -> requests.Response | None:
        if self.name != "wdsjfwq" or self.captcha_solver is None:
            return None
        username_field = self._find_input(form, ("username", "user", "玩家", "昵称"))
        captcha_field = self._find_input(form, ("captcha", "verification", "verify", "验证码"))
        captcha_url = self._captcha_image_url(soup, form)
        if username_field is None or captcha_field is None or captcha_url is None:
            return None
        captcha_image = self.transport.get(captcha_url)
        try:
            solution = self.captcha_solver.solve_wdsjfwq_captcha(
                captcha_image.content,
                content_type=captcha_image.headers.get("Content-Type", "image/png"),
            )
        except (AISolverError, AttributeError):
            return None
        code = solution.code.strip()
        if not CAPTCHA_CODE_PATTERN.fullmatch(code):
            return None
        data = self._input_data(form)
        data[str(username_field["name"])] = self.username_factory()
        data[str(captcha_field["name"])] = code
        return self._submit_form(form, data)

    def run_one_shot_action(self) -> ActionResult:
        page = self.transport.get(self.url)
        text = BeautifulSoup(page.text, "html.parser").get_text(" ", strip=True)
        initial_wdsjfwq_count = (
            self._wdsjfwq_like_count(page.text) if self.name == "wdsjfwq" else None
        )
        if any(marker.casefold() in text.casefold() for marker in ALREADY_LIKED):
            return self._result(ActionStatus.SKIPPED, "页面显示本轮已喜欢")

        soup = BeautifulSoup(page.text, "html.parser")
        links = [
            anchor
            for anchor in soup.select("a[href]")
            if self._matches_like(anchor.get_text(" ", strip=True))
            and not self._is_self_link(str(anchor["href"]))
        ]
        forms = [
            form
            for form in soup.select("form")
            if any(
                self._matches_like(
                    control.get_text(" ", strip=True) or str(control.get("value", ""))
                )
                for control in form.select("button, input[type=submit], input[type=button]")
            )
        ]
        mclists_button = self._mclists_button(soup)
        if len(links) + len(forms) + int(mclists_button is not None) != 1:
            raise SiteParseError("喜欢控件缺失或不唯一，未提交请求")

        if mclists_button is not None:
            action, server_id = mclists_button
            response = self.transport.post(
                action,
                data={"sid": server_id},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            )
        elif links:
            response = self.transport.get(self._url(str(links[0]["href"])))
        else:
            form = forms[0]
            if self._requires_interaction(form):
                solved_response = self._solve_wdsjfwq_form(soup, form)
                if solved_response is None:
                    return self._result(
                        ActionStatus.MANUAL_INTERVENTION,
                        "喜欢操作需要填写用户信息或验证码",
                    )
                response = solved_response
            else:
                response = self._submit_form(form, self._input_data(form))

        response_text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
        if json_result := self._json_result(response.text):
            return json_result
        if any(marker.casefold() in response_text.casefold() for marker in SUCCESS_MARKERS):
            return self._result(ActionStatus.SUCCESS, "喜欢操作完成")
        if initial_wdsjfwq_count is not None:
            response_count = self._wdsjfwq_like_count(response.text)
            if response_count is not None and response_count > initial_wdsjfwq_count:
                return self._result(ActionStatus.SUCCESS, "点赞计数已增加，喜欢操作完成")
            refreshed = self.transport.get(self.url)
            refreshed_count = self._wdsjfwq_like_count(refreshed.text)
            if refreshed_count is not None and refreshed_count > initial_wdsjfwq_count:
                return self._result(ActionStatus.SUCCESS, "点赞计数已增加，喜欢操作完成")
        return self._result(ActionStatus.TECHNICAL_FAILURE, "喜欢操作结果无法确认")
