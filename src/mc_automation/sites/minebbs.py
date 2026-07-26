from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..config import SiteConfig
from ..models import ActionResult, ActionStatus, Inventory, Resources
from ..transport import HttpTransport
from .base import SiteParseError

THREAD_ID_RE = re.compile(r"/threads/(?:[^/?#]*\.)?(\d+)(?:/|$)")


@dataclass(frozen=True, slots=True)
class _FormSubmission:
    action: str
    method: str
    data: dict[str, str]


class MineBBSAdapter:
    name = "minebbs"
    supports_promotion = False
    uses_rank_eligibility = False
    PURPLE_CARD = "紫晶顶贴卡"
    GOLD_CARD = "金粒顶贴卡"

    def __init__(
        self,
        config: SiteConfig,
        transport: HttpTransport,
        *,
        base_url: str = "https://www.minebbs.com",
    ) -> None:
        self.config = config
        self.thread_id = config.thread_id
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self._preferred_item: str | None = None

    def _result(self, action: str, status: ActionStatus, message: str) -> ActionResult:
        return ActionResult(self.name, action, status, message)

    def run_promotion_task(self) -> ActionResult:
        return self._result("promotion_task", ActionStatus.SKIPPED, "MineBBS 不支持推广任务")

    def _url(self, path: str) -> str:
        return urljoin(f"{self.base_url}/", path)

    @staticmethod
    def _hidden_fields(form: Tag) -> dict[str, str]:
        data: dict[str, str] = {}
        for field in form.select("input[name]"):
            name = str(field.get("name", ""))
            field_type = str(field.get("type", "text")).lower()
            if field_type in {"checkbox", "radio"} and not field.has_attr("checked"):
                continue
            data[name] = str(field.get("value", ""))
        for button in form.select("button[name][value]"):
            data[str(button["name"])] = str(button["value"])
        return data

    def _unique_form(self, html: str, required_text: str) -> _FormSubmission:
        soup = BeautifulSoup(html, "html.parser")
        forms = [
            form for form in soup.select("form") if required_text in form.get_text(" ", strip=True)
        ]
        if len(forms) != 1:
            raise SiteParseError(f"MineBBS 未找到唯一的“{required_text}”表单")
        form = forms[0]
        action = str(form.get("action", ""))
        if not action:
            raise SiteParseError("MineBBS 表单缺少 action")
        return _FormSubmission(
            action=self._url(action),
            method=str(form.get("method", "post")).upper(),
            data=self._hidden_fields(form),
        )

    def _submit(self, submission: _FormSubmission) -> str:
        if submission.method == "GET":
            response = self.transport.get(submission.action, params=submission.data)
        elif submission.method == "POST":
            response = self.transport.post(
                submission.action,
                data=submission.data,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        else:
            raise SiteParseError("MineBBS 表单使用了不支持的方法")
        return response.text

    def authenticate(self) -> ActionResult:
        page = self.transport.get(self._url("login/"))
        soup = BeautifulSoup(page.text, "html.parser")
        candidates = [
            form
            for form in soup.select("form")
            if "login" in str(form.get("action", "")).lower()
            and form.select_one('input[name="password"]') is not None
        ]
        if len(candidates) != 1:
            raise SiteParseError("MineBBS 登录表单无法唯一识别")
        form = candidates[0]
        data = self._hidden_fields(form)
        data.update(
            {"login": self.config.username, "password": self.config.password, "remember": "1"}
        )
        action = self._url(str(form.get("action", "/login/login")))
        self.transport.post(action, data=data)
        home = self.transport.get(self.base_url)
        home_soup = BeautifulSoup(home.text, "html.parser")
        login_links = home_soup.select('a[href*="/login/"]')
        account_markers = home_soup.select(
            'a[href*="/account/"], a[href*="/logout"], [data-xf-click="account-menu"]'
        )
        if account_markers and not login_links:
            return self._result("authenticate", ActionStatus.SUCCESS, "登录成功")
        return self._result(
            "authenticate", ActionStatus.MANUAL_INTERVENTION, "登录失败或需要人工验证"
        )

    def daily_sign_in(self) -> ActionResult:
        page = self.transport.get(self.base_url)
        soup = BeautifulSoup(page.text, "html.parser")
        links = [
            link
            for link in soup.select("a[href]")
            if "签到" in link.get_text(" ", strip=True)
            and any(key in str(link["href"]).lower() for key in ("sign", "check-in", "attendance"))
        ]
        forms = [form for form in soup.select("form") if "签到" in form.get_text(" ", strip=True)]
        if len(links) + len(forms) == 0:
            return self._result(
                "daily_sign_in", ActionStatus.SKIPPED, "未发现签到入口，今日可能已签到"
            )
        if len(links) + len(forms) != 1:
            raise SiteParseError("MineBBS 签到入口不唯一")
        if links:
            result_text = self.transport.get(self._url(str(links[0]["href"]))).text
        else:
            form = forms[0]
            submission = _FormSubmission(
                self._url(str(form.get("action", ""))),
                str(form.get("method", "post")).upper(),
                self._hidden_fields(form),
            )
            result_text = self._submit(submission)
        if any(marker in result_text for marker in ("签到成功", "已签到", "success")):
            return self._result("daily_sign_in", ActionStatus.SUCCESS, "签到完成")
        return self._result(
            "daily_sign_in", ActionStatus.SKIPPED, "签到请求完成，站点未返回明确成功标记"
        )

    def get_thread_rank(self) -> int:
        page = self.transport.get(self._url("servers/"))
        soup = BeautifulSoup(page.text, "html.parser")
        primary = soup.select('.structItem-title a[href*="/threads/"]')
        candidates = primary or soup.select('a[href*="/threads/"]')
        ids: list[str] = []
        for link in candidates:
            match = THREAD_ID_RE.search(str(link.get("href", "")))
            if match and match.group(1) not in ids:
                ids.append(match.group(1))
        if not ids:
            raise SiteParseError("MineBBS 服务器宣传列表结构无法识别")
        if self.thread_id in ids:
            return ids.index(self.thread_id) + 1
        return len(ids) + 1

    def get_resources(self) -> Resources:
        pages = [
            self.transport.get(self.base_url).text,
            self.transport.get(self._url("account/")).text,
        ]
        text = BeautifulSoup(" ".join(pages), "html.parser").get_text(" ", strip=True)

        def balance(label: str) -> int | None:
            match = re.search(rf"{label}\s*[:：]?\s*(\d+)", text)
            return int(match.group(1)) if match else None

        return Resources({"amethyst": balance("紫水晶"), "gold": balance("金粒")})

    def verify_target_ownership(self) -> None:
        page = self.transport.get(self._url(f"threads/{self.thread_id}/"))
        soup = BeautifulSoup(page.text, "html.parser")
        posts = soup.select("article.message--post, .message--post")
        if not posts:
            raise SiteParseError("MineBBS 目标帖结构无法识别")
        first_post = posts[0]
        author_name = str(first_post.get("data-author", "")).strip()
        if not author_name:
            author = first_post.select_one(
                ".message-name .username, .message-userDetails .username"
            )
            author_name = author.get_text(strip=True) if author else ""
        if not author_name:
            raise SiteParseError("MineBBS 无法识别目标帖作者")
        if author_name != self.config.username:
            raise SiteParseError("MineBBS 目标帖不属于配置账号，拒绝操作")

    def _inventory_page(self) -> str | None:
        pages = [self.transport.get(self.base_url), self.transport.get(self._url("tool-shop/"))]
        candidates: list[str] = []
        for page in pages:
            soup = BeautifulSoup(page.text, "html.parser")
            for link in soup.select("a[href]"):
                text = link.get_text(" ", strip=True)
                href = str(link["href"])
                if any(label in text for label in ("我的道具", "道具背包", "道具库存")) or any(
                    key in href.lower() for key in ("tool-inventory", "my-tools", "inventory")
                ):
                    candidates.append(self._url(href))
        unique = list(dict.fromkeys(candidates))
        if len(unique) != 1:
            return None
        return self.transport.get(unique[0]).text

    def get_inventory(self) -> Inventory:
        html = self._inventory_page()
        if html is None:
            raise SiteParseError("MineBBS 无法唯一识别道具库存页面")
        soup = BeautifulSoup(html, "html.parser")

        def count_item(label: str) -> int:
            total = 0
            for container in soup.select("form, .structItem, .block-row, li"):
                text = container.get_text(" ", strip=True)
                if label not in text or "使用" not in text:
                    continue
                match = re.search(r"(?:数量|拥有)\s*[:：x×]?\s*(\d+)", text)
                total += int(match.group(1)) if match else 1
            return total

        return Inventory(
            {"purple": count_item(self.PURPLE_CARD), "gold": count_item(self.GOLD_CARD)}
        )

    def _purchase(self, item_id: int, label: str, item_key: str) -> ActionResult:
        page_url = self._url(f"tool-shop/{item_id}/purchase")
        page = self.transport.get(page_url)
        submission = self._unique_form(page.text, "购买")
        result = self._submit(submission)
        if any(marker in result for marker in ("购买成功", "success", "已购买")):
            self._preferred_item = label
            return ActionResult(
                self.name,
                "purchase_bump_item",
                ActionStatus.SUCCESS,
                f"已购买 1 张{label}",
                metadata={"item": item_key},
            )
        if any(marker in result for marker in ("不足", "余额", "限购", "无法购买")):
            return self._result(
                "purchase_bump_item", ActionStatus.INSUFFICIENT_RESOURCES, f"无法购买{label}"
            )
        return self._result(
            "purchase_bump_item", ActionStatus.TECHNICAL_FAILURE, f"购买{label}后未识别到明确结果"
        )

    def purchase_bump_item(self, *, excluded_items: frozenset[str] = frozenset()) -> ActionResult:
        resources = self.get_resources().balances
        amethyst = resources.get("amethyst")
        gold = resources.get("gold")
        if amethyst is None or gold is None:
            raise SiteParseError("MineBBS 余额结构无法可靠识别，拒绝购买")
        if amethyst >= 1 and "purple" not in excluded_items:
            purple = self._purchase(18, self.PURPLE_CARD, "purple")
            if purple.status is not ActionStatus.INSUFFICIENT_RESOURCES:
                return purple
        if gold >= 100 and "gold" not in excluded_items:
            return self._purchase(17, self.GOLD_CARD, "gold")
        return self._result(
            "purchase_bump_item",
            ActionStatus.INSUFFICIENT_RESOURCES,
            "紫水晶和金粒不足，或对应道具已达到本日购买限制",
        )

    def apply_bump_item(self) -> ActionResult:
        html = self._inventory_page()
        if html is None:
            raise SiteParseError("MineBBS 无法唯一识别道具库存页面")
        label_order = [self._preferred_item] if self._preferred_item else []
        label_order.extend([self.PURPLE_CARD, self.GOLD_CARD])
        for label in dict.fromkeys(item for item in label_order if item):
            try:
                submission = self._unique_form(html, f"{label}")
            except SiteParseError:
                continue
            soup = BeautifulSoup(html, "html.parser")
            forms = [
                form for form in soup.select("form") if label in form.get_text(" ", strip=True)
            ]
            form = forms[0]
            target_bound = any(
                self.thread_id == value
                for name, value in submission.data.items()
                if name.casefold() in {"thread", "thread_id", "threadid", "tid"}
            )
            for select in form.select("select[name]"):
                for option in select.select("option[value]"):
                    if self.thread_id in option.get_text(" ", strip=True) or self.thread_id in str(
                        option.get("value", "")
                    ):
                        submission.data[str(select["name"])] = str(option["value"])
                        target_bound = True
                        break
            if not target_bound:
                raise SiteParseError("MineBBS 顶贴表单未提供配置的目标帖，拒绝提交")
            result = self._submit(submission)
            if any(marker in result for marker in ("使用成功", "顶贴成功", "success")):
                return self._result("apply_bump_item", ActionStatus.SUCCESS, f"{label}使用成功")
            if any(marker in result for marker in ("过期", "不存在", "冷却", "无法使用")):
                return self._result(
                    "apply_bump_item", ActionStatus.INSUFFICIENT_RESOURCES, f"{label}不可用"
                )
            return self._result(
                "apply_bump_item", ActionStatus.TECHNICAL_FAILURE, f"使用{label}后未识别到明确结果"
            )
        return self._result(
            "apply_bump_item", ActionStatus.INSUFFICIENT_RESOURCES, "没有可用顶贴卡"
        )
