from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ..config import SiteConfig
from ..models import ActionResult, ActionStatus, Inventory, Resources
from ..promotion_proxy import PromotionVisitor
from ..transport import HttpTransport
from .base import SiteParseError


@dataclass(frozen=True, slots=True)
class _PromotionTask:
    apply_url: str | None = None
    draw_url: str | None = None
    visit_url: str | None = None
    complete: bool = False


class KLPBBSAdapter:
    name = "klpbbs"
    supports_promotion = True
    uses_rank_eligibility = True

    def __init__(
        self,
        config: SiteConfig,
        transport: HttpTransport,
        *,
        base_url: str = "https://klpbbs.com",
        promotion_visitor: PromotionVisitor | None = None,
    ) -> None:
        self.config = config
        self.thread_id = config.thread_id
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.promotion_visitor = promotion_visitor

    def _result(self, action: str, status: ActionStatus, message: str) -> ActionResult:
        return ActionResult(self.name, action, status, message)

    @staticmethod
    def _task_action(href: str, action: str) -> bool:
        query = parse_qs(urlsplit(href).query)
        return query.get("mod") == ["task"] and query.get("do") == [action]

    def _promotion_task(self, html: str) -> _PromotionTask | None:
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        candidates: list[Tag] = []
        for candidate in soup.select("tr, li, div"):
            text = candidate.get_text(" ", strip=True)
            if not any(marker in text for marker in ("推广", "宣传访问", "推广访问")):
                continue
            if candidate.select("a[href]") or any(
                marker in text for marker in ("进行中", "已完成", "任务完成", "尚需")
            ):
                candidates.append(candidate)
        if not candidates:
            if any(marker in page_text for marker in ("暂无可用任务", "没有可用任务", "暂无任务")):
                return None
            raise SiteParseError("KLPBBS 推广任务页面结构无法识别")

        scope = min(candidates, key=lambda item: len(item.get_text(" ", strip=True)))
        text = scope.get_text(" ", strip=True)
        apply_url: str | None = None
        draw_url: str | None = None
        visit_url: str | None = None
        for anchor in scope.select("a[href]"):
            href = str(anchor["href"])
            if self._task_action(href, "apply"):
                apply_url = self._url(href)
            elif self._task_action(href, "draw"):
                draw_url = self._url(href)
            elif "fromuid=" in href or "ac=promotion" in href or "promotion" in href.casefold():
                visit_url = self._url(href)
        complete = draw_url is not None or any(
            marker in text for marker in ("已完成", "任务完成", "可以领取")
        )
        if not any(
            (
                apply_url,
                draw_url,
                visit_url,
                complete,
                "进行中" in text,
                "尚需" in text,
            )
        ):
            raise SiteParseError("KLPBBS 推广任务状态无法识别")
        return _PromotionTask(apply_url, draw_url, visit_url, complete)

    def _load_promotion_task(self, path: str = "home.php?mod=task") -> _PromotionTask | None:
        page = self.transport.get(self._url(path))
        return self._promotion_task(page.text)

    def _draw_promotion_reward(
        self, draw_url: str, visits: int, *, attempts: int | None = None
    ) -> ActionResult:
        response = self.transport.get(draw_url)
        if any(
            marker in response.text
            for marker in ("任务奖励已领取", "领取奖励成功", "任务完成", "succeed")
        ):
            metadata: dict[str, str | int | bool | None] = {"visits": visits}
            if attempts is not None:
                metadata["attempts"] = attempts
            return ActionResult(
                self.name,
                "promotion_task",
                ActionStatus.SUCCESS,
                "推广任务已完成并领取奖励",
                metadata=metadata,
            )
        return self._result(
            "promotion_task", ActionStatus.TECHNICAL_FAILURE, "推广任务领奖结果无法确认"
        )

    def run_promotion_task(self) -> ActionResult:
        if not self.config.promotion_enabled:
            return self._result("promotion_task", ActionStatus.SKIPPED, "推广任务未启用")

        task = self._load_promotion_task()
        if task is None:
            return self._result("promotion_task", ActionStatus.SKIPPED, "当前没有可用推广任务")
        if task.apply_url is not None:
            response = self.transport.get(task.apply_url)
            if not any(
                marker in response.text for marker in ("任务申请成功", "成功申请", "succeed")
            ):
                raise SiteParseError("KLPBBS 推广任务领取结果无法确认")
            task = self._load_promotion_task("home.php?mod=task&do=doing")
            if task is None:
                raise SiteParseError("KLPBBS 推广任务领取后未出现在进行中列表")

        if task.draw_url is not None:
            return self._draw_promotion_reward(task.draw_url, 0)
        if task.complete:
            return self._result(
                "promotion_task", ActionStatus.SUCCESS, "推广任务已完成，页面未提供额外领奖动作"
            )
        if task.visit_url is None:
            raise SiteParseError("KLPBBS 推广任务进行中但缺少推广链接")
        if self.promotion_visitor is None:
            raise SiteParseError("KLPBBS 推广任务已启用但代理访问器未配置")
        if not self._is_same_origin(task.visit_url):
            raise SiteParseError("KLPBBS 推广链接不属于当前站点源")

        successful_visits = 0
        for attempts in range(1, self.config.promotion_max_visits + 1):
            if not self.promotion_visitor.visit(task.visit_url):
                if attempts < self.config.promotion_max_visits:
                    time.sleep(self.config.promotion_visit_delay_seconds)
                continue
            successful_visits += 1
            task = self._load_promotion_task("home.php?mod=task&do=doing")
            if task is None:
                raise SiteParseError("KLPBBS 推广访问后任务状态消失")
            if task.draw_url is not None:
                return self._draw_promotion_reward(
                    task.draw_url, successful_visits, attempts=attempts
                )
            if task.complete:
                return ActionResult(
                    self.name,
                    "promotion_task",
                    ActionStatus.SUCCESS,
                    "推广任务已完成，页面未提供额外领奖动作",
                    metadata={"visits": successful_visits, "attempts": attempts},
                )
            if attempts < self.config.promotion_max_visits:
                time.sleep(self.config.promotion_visit_delay_seconds)
        return ActionResult(
            self.name,
            "promotion_task",
            ActionStatus.SKIPPED,
            "推广访问已达到本轮上限，任务尚未完成",
            metadata={
                "visits": successful_visits,
                "attempts": self.config.promotion_max_visits,
            },
        )

    def _url(self, path: str) -> str:
        return urljoin(f"{self.base_url}/", path)

    def _is_same_origin(self, url: str) -> bool:
        candidate = urlsplit(url)
        base = urlsplit(self.base_url)
        candidate_port = candidate.port or (443 if candidate.scheme == "https" else 80)
        base_port = base.port or (443 if base.scheme == "https" else 80)
        return (
            candidate.scheme.casefold() == base.scheme.casefold()
            and candidate.hostname == base.hostname
            and candidate_port == base_port
        )

    @staticmethod
    def _formhash(html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        field = soup.select_one('input[name="formhash"]')
        if field and field.get("value"):
            return str(field["value"])
        match = re.search(r"formhash=([a-zA-Z0-9]+)", html)
        return match.group(1) if match else None

    @staticmethod
    def _is_authenticated(html: str) -> bool:
        return bool(
            re.search(r"discuz_uid\s*=\s*['\"]?[1-9]\d*", html)
            or "action=logout" in html
            or "退出登录" in html
        )

    def authenticate(self) -> ActionResult:
        login_page = self.transport.get(self._url("member.php?mod=logging&action=login"))
        formhash = self._formhash(login_page.text)
        data = {
            "loginfield": "username",
            "username": self.config.username,
            "password": self.config.password,
            "questionid": "0",
            "answer": "",
            "cookietime": "2592000",
            "loginsubmit": "yes",
        }
        if formhash:
            data["formhash"] = formhash
        self.transport.post(
            self._url("member.php?mod=logging&action=login&loginsubmit=yes"), data=data
        )
        home = self.transport.get(self.base_url)
        if self._is_authenticated(home.text):
            return self._result("authenticate", ActionStatus.SUCCESS, "登录成功")
        return self._result(
            "authenticate", ActionStatus.MANUAL_INTERVENTION, "登录失败或需要人工验证"
        )

    def daily_sign_in(self) -> ActionResult:
        page = self.transport.get(self.base_url)
        soup = BeautifulSoup(page.text, "html.parser")
        candidate = soup.select_one("a.midaben_signpanel.JD_sign")
        if candidate is None:
            candidate = soup.find(
                "a", href=lambda value: isinstance(value, str) and "k_misign:sign" in value
            )
        if candidate is None or not candidate.get("href"):
            return self._result("daily_sign_in", ActionStatus.SKIPPED, "今日可能已签到")
        response = self.transport.get(self._url(str(candidate["href"])))
        if any(marker in response.text for marker in ("签到成功", "已签到", "操作成功")):
            return self._result("daily_sign_in", ActionStatus.SUCCESS, "签到完成")
        return self._result(
            "daily_sign_in", ActionStatus.SKIPPED, "签到入口已访问，站点未返回明确成功标记"
        )

    def get_thread_rank(self) -> int:
        ids: list[str] = []
        for _attempt in range(2):
            page = self.transport.get(self._url("forum-56-1.html"))
            soup = BeautifulSoup(page.text, "html.parser")
            ids = [
                match.group(1)
                for node in soup.select('[id^="normalthread_"]')
                if (match := re.fullmatch(r"normalthread_(\d+)", str(node.get("id", ""))))
            ]
            if ids:
                break
        if not ids:
            raise SiteParseError("KLPBBS 服务器大厅结构无法识别")
        unique_ids = list(dict.fromkeys(ids))
        if self.thread_id in unique_ids:
            return unique_ids.index(self.thread_id) + 1
        return len(unique_ids) + 1

    def get_resources(self) -> Resources:
        page = self.transport.get(self._url("home.php?mod=spacecp&ac=credit"))
        text = BeautifulSoup(page.text, "html.parser").get_text(" ", strip=True)
        match = re.search(r"铁粒\s*(?:[:：]?\s*)?(\d+)", text)
        return Resources({"iron": int(match.group(1)) if match else None})

    def _target_posts(self) -> list[Tag]:
        page = self.transport.get(self._url(f"thread-{self.thread_id}-1-1.html"))
        soup = BeautifulSoup(page.text, "html.parser")
        posts = list(soup.select('table[id^="pid"]'))
        if not posts:
            raise SiteParseError("KLPBBS 目标帖结构无法识别")
        return posts

    def verify_target_ownership(self) -> None:
        first_post = self._target_posts()[0]
        author = first_post.select_one(".authi a[href*='space-']")
        if author is None:
            raise SiteParseError("KLPBBS 无法识别目标帖作者")
        if author.get_text(strip=True) != self.config.username:
            raise SiteParseError("KLPBBS 目标帖不属于配置账号，拒绝操作")

    def get_inventory(self) -> Inventory:
        page = self.transport.get(self._url("home.php?mod=magic&action=mybox"))
        soup = BeautifulSoup(page.text, "html.parser")
        candidates = soup.select('a[href*="mid=bump"], input[value="bump"]')
        count = len(candidates)
        text = soup.get_text(" ", strip=True)
        if count == 0 and "提升卡" in text:
            count = 1
        if count == 0 and not any(
            marker in text for marker in ("暂无道具", "没有道具", "道具箱为空", "您还没有道具")
        ):
            raise SiteParseError("KLPBBS 道具库存结构无法识别")
        return Inventory({"bump": count})

    def purchase_bump_item(self, *, excluded_items: frozenset[str] = frozenset()) -> ActionResult:
        if "bump" in excluded_items:
            return self._result(
                "purchase_bump_item", ActionStatus.INSUFFICIENT_RESOURCES, "本轮不允许购买提升卡"
            )
        page = self.transport.get(self.base_url)
        formhash = self._formhash(page.text)
        if not formhash:
            raise SiteParseError("KLPBBS formhash 缺失，拒绝购买")
        response = self.transport.post(
            self._url("home.php?mod=magic&action=shop&infloat=yes&inajax=1"),
            data={
                "formhash": formhash,
                "handlekey": "magics",
                "operation": "buy",
                "mid": "bump",
                "magicnum": "1",
                "operatesubmit": "yes",
            },
        )
        if any(marker in response.text for marker in ("购买成功", "道具购买成功", "succeed")):
            return self._result("purchase_bump_item", ActionStatus.SUCCESS, "已购买 1 张提升卡")
        if any(marker in response.text for marker in ("不足", "不够", "无法购买")):
            return self._result(
                "purchase_bump_item",
                ActionStatus.INSUFFICIENT_RESOURCES,
                "铁粒不足，无法购买提升卡",
            )
        return self._result(
            "purchase_bump_item", ActionStatus.TECHNICAL_FAILURE, "购买后未识别到明确结果"
        )

    def apply_bump_item(self) -> ActionResult:
        page = self.transport.get(self.base_url)
        formhash = self._formhash(page.text)
        if not formhash:
            raise SiteParseError("KLPBBS formhash 缺失，拒绝使用道具")
        response = self.transport.post(
            self._url("home.php?mod=magic&action=mybox&infloat=yes&inajax=1"),
            data={
                "formhash": formhash,
                "handlekey": "a_bump",
                "operation": "use",
                "magicid": "10",
                "tid": self.thread_id,
                "usesubmit": "yes",
                "idtype": "tid",
                "id": self.thread_id,
            },
        )
        if any(marker in response.text for marker in ("道具使用成功", "提升成功", "succeed")):
            return self._result("apply_bump_item", ActionStatus.SUCCESS, "提升卡使用成功")
        if any(marker in response.text for marker in ("没有此道具", "道具不存在", "冷却")):
            return self._result(
                "apply_bump_item",
                ActionStatus.INSUFFICIENT_RESOURCES,
                "没有可用提升卡或站点处于冷却",
            )
        return self._result(
            "apply_bump_item", ActionStatus.TECHNICAL_FAILURE, "使用提升卡后未识别到明确结果"
        )
