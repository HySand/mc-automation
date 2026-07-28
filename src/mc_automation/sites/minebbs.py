from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ..config import SiteConfig
from ..models import ActionResult, ActionStatus, Inventory, Resources
from ..step_log import log_step
from ..transport import HttpTransport
from .base import SiteParseError

THREAD_ID_RE = re.compile(r"/threads/(?:[^/?#]*\.)?(\d+)(?:/|$)")


@dataclass(frozen=True, slots=True)
class _FormSubmission:
    action: str
    method: str
    data: dict[str, str]
    request_uri: str


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
        self._last_inventory: dict[str, int] | None = None

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

    @staticmethod
    def _request_uri(url: str) -> str:
        parsed = urlsplit(url)
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")

    @staticmethod
    def _same_origin(left_url: str, right_url: str) -> bool:
        left = urlsplit(left_url)
        right = urlsplit(right_url)
        try:
            left_port = left.port or (443 if left.scheme.casefold() == "https" else 80)
            right_port = right.port or (443 if right.scheme.casefold() == "https" else 80)
        except ValueError:
            return False
        return (
            left.scheme.casefold() == right.scheme.casefold()
            and (left.hostname or "").casefold() == (right.hostname or "").casefold()
            and left_port == right_port
        )

    @classmethod
    def _xenforo_form_data(cls, data: dict[str, str], page_url: str) -> dict[str, str]:
        result = dict(data)
        result["_xfResponseType"] = "json"
        result["_xfWithData"] = "1"
        result["_xfRequestUri"] = cls._request_uri(page_url)
        return result

    def _purchase_form(self, html: str, page_url: str) -> _FormSubmission:
        soup = BeautifulSoup(html, "html.parser")
        forms = soup.select("form")
        target = urlsplit(page_url)
        candidates: list[tuple[Tag, str, str]] = []
        for form in forms:
            raw_action = str(form.get("action", ""))
            method = str(form.get("method", "post")).upper()
            if not raw_action or method != "POST":
                continue
            action = self._url(raw_action)
            parsed = urlsplit(action)
            same_origin = self._same_origin(action, page_url)
            field_names = {
                str(field.get("name", ""))
                for field in form.select("input[name], select[name], textarea[name]")
            }
            submit_controls = form.select(
                'button[type="submit"], button:not([type]), input[type="submit"]'
            )
            if (
                same_origin
                and parsed.path.rstrip("/") == target.path.rstrip("/")
                and "quantity" in field_names
                and len(submit_controls) == 1
            ):
                candidates.append((form, action, method))

        metadata: dict[str, object] = {
            "action": "purchase_bump_item",
            "url": page_url,
            "form_count": len(forms),
            "control_count": len(candidates),
        }
        if len(candidates) == 1:
            selected, _action, method = candidates[0]
            metadata.update(
                {
                    "field_names": sorted(
                        str(field.get("name", ""))
                        for field in selected.select("input[name], select[name], textarea[name]")
                    ),
                    "submit_method": method,
                }
            )
        log_step(
            "form_inspection",
            site=self.name,
            status="completed" if len(candidates) == 1 else "failed",
            **metadata,
        )
        if len(candidates) != 1:
            raise SiteParseError("MineBBS 无法唯一识别购买表单")

        form, action, method = candidates[0]
        data = self._hidden_fields(form)
        data["quantity"] = "1"
        data = self._xenforo_form_data(data, page_url)
        return _FormSubmission(
            action=action,
            method=method,
            data=data,
            request_uri=self._request_uri(page_url),
        )

    def _checkout_form(self, html: str, page_url: str, label: str) -> _FormSubmission | None:
        soup = BeautifulSoup(html, "html.parser")
        quantity_count = 0
        target_label_seen = False
        candidates: list[tuple[Tag, Tag, str, Tag, str]] = []
        for form in soup.select("form"):
            raw_action = str(form.get("action", ""))
            method = str(form.get("method", "post")).upper()
            if not raw_action or method != "POST":
                continue
            action = self._url(raw_action)
            parsed = urlsplit(action)
            if not self._same_origin(action, page_url) or (
                parsed.path.rstrip("/") != "/tool-shop/checkout/update"
            ):
                continue
            target_label_seen = target_label_seen or label in form.get_text(" ", strip=True)
            quantities = form.select('input[name^="quantity["]')
            quantity_count += len(quantities)
            purchase_controls = form.select(
                'button[name="purchase"], input[type="submit"][name="purchase"]'
            )
            if len(purchase_controls) != 1:
                continue
            for quantity in quantities:
                quantity_name = str(quantity.get("name", ""))
                key_match = re.fullmatch(r"quantity\[([^\]]+)\]", quantity_name)
                if key_match is None:
                    continue
                container = quantity.parent
                label_matched = False
                while isinstance(container, Tag) and container is not form:
                    if label in container.get_text(" ", strip=True):
                        label_matched = True
                        break
                    container = container.parent
                if not label_matched:
                    continue
                cart_key = key_match.group(1)
                checkboxes = [
                    checkbox
                    for checkbox in form.select('input[name="cart_keys[]"][value]')
                    if str(checkbox.get("value", "")) == cart_key
                ]
                if len(checkboxes) == 1:
                    candidates.append(
                        (
                            form,
                            quantity,
                            cart_key,
                            purchase_controls[0],
                            action,
                        )
                    )

        log_step(
            "cart_inspection",
            site=self.name,
            status="completed" if len(candidates) <= 1 else "failed",
            url=page_url,
            control_count=len(candidates),
            item_types=quantity_count,
        )
        if len(candidates) > 1 or (target_label_seen and not candidates):
            raise SiteParseError("MineBBS 购物车目标道具结构无法唯一识别")
        if not candidates:
            return None

        form, quantity, cart_key, purchase_control, action = candidates[0]
        data = self._hidden_fields(form)
        for submit_control in form.select(
            'button[name], input[type="submit"][name], input[type="image"][name]'
        ):
            data.pop(str(submit_control.get("name", "")), None)
        data[str(quantity["name"])] = "1"
        data["cart_keys[]"] = cart_key
        data["purchase"] = str(purchase_control.get("value", ""))
        data = self._xenforo_form_data(data, page_url)
        return _FormSubmission(
            action=action,
            method="POST",
            data=data,
            request_uri=self._request_uri(page_url),
        )

    def _deployment_submission(
        self, inventory_html: str, inventory_url: str, label: str
    ) -> _FormSubmission | None:
        soup = BeautifulSoup(inventory_html, "html.parser")
        configure_urls: list[str] = []
        for container in soup.select(".itemList-item, .structItem, .block-row, li"):
            if label not in container.get_text(" ", strip=True):
                continue
            for link in container.select('a[href][data-xf-click="overlay"]'):
                configure_url = self._url(str(link.get("href", "")))
                parsed = urlsplit(configure_url)
                if self._same_origin(configure_url, inventory_url) and re.fullmatch(
                    r"/tool-shop/inventory/\d+/configure", parsed.path
                ):
                    configure_urls.append(configure_url)
        configure_urls = list(dict.fromkeys(configure_urls))
        if not configure_urls:
            return None
        if len(configure_urls) != 1:
            raise SiteParseError("MineBBS 无法唯一识别顶贴卡部署入口")
        configure_url = configure_urls[0]
        page = self.transport.get(configure_url)
        configure_soup = BeautifulSoup(page.text, "html.parser")
        candidates: list[tuple[Tag, str]] = []
        for form in configure_soup.select("form"):
            raw_action = str(form.get("action", ""))
            method = str(form.get("method", "post")).upper()
            if not raw_action or method != "POST":
                continue
            action = self._url(raw_action)
            parsed_action = urlsplit(action)
            parsed_configure = urlsplit(configure_url)
            if not self._same_origin(action, configure_url) or (
                parsed_action.path.rstrip("/") != parsed_configure.path.rstrip("/")
            ):
                continue
            fields = {
                str(field.get("name", ""))
                for field in form.select("input[name], select[name], textarea[name]")
            }
            submit_controls = form.select(
                'button[type="submit"], button:not([type]), input[type="submit"]'
            )
            if "code[contentid]" in fields and len(submit_controls) == 1:
                candidates.append((form, action))
        log_step(
            "deployment_inspection",
            site=self.name,
            status="completed" if len(candidates) == 1 else "failed",
            url=configure_url,
            form_count=len(configure_soup.select("form")),
            control_count=len(candidates),
        )
        if len(candidates) != 1:
            raise SiteParseError("MineBBS 无法唯一识别顶贴卡部署表单")
        form, action = candidates[0]
        data = self._hidden_fields(form)
        data["code[contentid]"] = self.thread_id
        data = self._xenforo_form_data(data, configure_url)
        return _FormSubmission(
            action=action,
            method="POST",
            data=data,
            request_uri=self._request_uri(configure_url),
        )

    def _submit(self, submission: _FormSubmission) -> str:
        if submission.method == "GET":
            response = self.transport.get(submission.action, params=submission.data)
        elif submission.method == "POST":
            data = dict(submission.data)
            data["_xfResponseType"] = "json"
            data["_xfWithData"] = "1"
            data["_xfRequestUri"] = submission.request_uri
            response = self.transport.post(
                submission.action,
                data=data,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        else:
            raise SiteParseError("MineBBS 表单使用了不支持的方法")
        return response.text

    @staticmethod
    def _json_submission_outcome(result: str) -> tuple[bool, bool | None]:
        try:
            payload = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            return False, None
        if not isinstance(payload, dict):
            return False, None
        if payload.get("success") is False or payload.get("errors") or payload.get("error"):
            return True, False
        if payload.get("success") is True:
            return True, True
        return True, None

    @staticmethod
    def _decoded_result_text(result: str) -> str:
        try:
            payload = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            return result
        return json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else result

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
                self._xenforo_form_data(self._hidden_fields(form), self.base_url),
                self._request_uri(self.base_url),
            )
            result_text = self._submit(submission)
        _json_response, json_outcome = self._json_submission_outcome(result_text)
        decoded_result = self._decoded_result_text(result_text)
        if (
            json_outcome is True
            or any(marker in decoded_result for marker in ("签到成功", "已签到"))
            or (not _json_response and "success" in result_text.casefold())
        ):
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

    def _inventory_page(self) -> tuple[str, str] | None:
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
        return unique[0], self.transport.get(unique[0]).text

    def _inventory_items(self, html: str) -> dict[str, int]:
        soup = BeautifulSoup(html, "html.parser")

        def count_item(label: str) -> int:
            total = 0
            containers = soup.select(".itemList-item") or soup.select(
                "form, .structItem, .block-row, li"
            )
            for container in containers:
                text = container.get_text(" ", strip=True)
                if label not in text or not any(action in text for action in ("使用", "部署")):
                    continue
                match = re.search(r"(?:数量|拥有)\s*[:：x×]?\s*(\d+)", text)
                total += int(match.group(1)) if match else 1
            return total

        return {"purple": count_item(self.PURPLE_CARD), "gold": count_item(self.GOLD_CARD)}

    def get_inventory(self) -> Inventory:
        inventory_page = self._inventory_page()
        if inventory_page is None:
            raise SiteParseError("MineBBS 无法唯一识别道具库存页面")
        _page_url, html = inventory_page
        items = self._inventory_items(html)
        self._last_inventory = dict(items)
        return Inventory(items)

    def _confirm_purchase_in_inventory(self, item_key: str) -> bool:
        before = None if self._last_inventory is None else self._last_inventory.get(item_key)
        if before is None:
            return False
        try:
            after = self.get_inventory().items.get(item_key, 0)
        except SiteParseError:
            return False
        return after > before

    def _purchase(self, item_id: int, label: str, item_key: str) -> ActionResult:
        checkout_url = self._url("tool-shop/checkout")
        checkout = self._checkout_form(
            self.transport.get(checkout_url).text,
            checkout_url,
            label,
        )
        if checkout is None:
            page_url = self._url(f"tool-shop/{item_id}/purchase")
            page = self.transport.get(page_url)
            add_submission = self._purchase_form(page.text, page_url)
            log_step(
                "cart_add",
                site=self.name,
                status="started",
                url=add_submission.action,
                submit_method=add_submission.method,
                field_names=sorted(add_submission.data),
                field_count=len(add_submission.data),
            )
            add_result = self._submit(add_submission)
            decoded_add_result = self._decoded_result_text(add_result)
            _add_json, add_outcome = self._json_submission_outcome(add_result)
            add_insufficient = any(
                marker in decoded_add_result for marker in ("不足", "余额", "限购", "无法购买")
            )
            if add_insufficient:
                log_step("cart_add", site=self.name, status="failed", url=add_submission.action)
                return self._result(
                    "purchase_bump_item", ActionStatus.INSUFFICIENT_RESOURCES, f"无法购买{label}"
                )
            if add_outcome is False:
                log_step("cart_add", site=self.name, status="failed", url=add_submission.action)
                return self._result(
                    "purchase_bump_item",
                    ActionStatus.TECHNICAL_FAILURE,
                    f"{label}加入购物车失败",
                )
            checkout = self._checkout_form(
                self.transport.get(checkout_url).text,
                checkout_url,
                label,
            )
            log_step(
                "cart_add",
                site=self.name,
                status="completed" if checkout is not None else "failed",
                url=add_submission.action,
            )
            if checkout is None:
                return self._result(
                    "purchase_bump_item",
                    ActionStatus.TECHNICAL_FAILURE,
                    f"{label}未出现在购物车",
                )

        log_step(
            "cart_checkout",
            site=self.name,
            status="started",
            url=checkout.action,
            submit_method=checkout.method,
            field_names=sorted(checkout.data),
            field_count=len(checkout.data),
        )
        result = self._submit(checkout)
        decoded_result = self._decoded_result_text(result)
        json_response, json_outcome = self._json_submission_outcome(result)
        insufficient_marker = any(
            marker in decoded_result for marker in ("不足", "余额", "限购", "无法购买")
        )
        if insufficient_marker:
            log_step("cart_checkout", site=self.name, status="failed", url=checkout.action)
            return self._result(
                "purchase_bump_item", ActionStatus.INSUFFICIENT_RESOURCES, f"无法购买{label}"
            )
        if json_outcome is False:
            log_step("cart_checkout", site=self.name, status="failed", url=checkout.action)
            return self._result(
                "purchase_bump_item", ActionStatus.TECHNICAL_FAILURE, f"{label}结算失败"
            )

        success_marker = json_outcome is True or any(
            marker in decoded_result for marker in ("购买成功", "已购买")
        )
        if not json_response and "success" in decoded_result.casefold():
            success_marker = True
        inventory_confirmed = False
        if not success_marker:
            inventory_confirmed = self._confirm_purchase_in_inventory(item_key)
            log_step(
                "purchase_confirmation",
                site=self.name,
                status="completed" if inventory_confirmed else "failed",
                inventory_confirmed=inventory_confirmed,
            )
        if success_marker or inventory_confirmed:
            log_step(
                "cart_checkout",
                site=self.name,
                status="completed",
                url=checkout.action,
                json_response=json_response,
                success_marker=success_marker,
                inventory_confirmed=inventory_confirmed,
                result_status=ActionStatus.SUCCESS.value,
            )
            self._preferred_item = label
            return ActionResult(
                self.name,
                "purchase_bump_item",
                ActionStatus.SUCCESS,
                f"已购买 1 张{label}",
                metadata={"item": item_key},
            )
        log_step(
            "cart_checkout",
            site=self.name,
            status="failed",
            url=checkout.action,
            json_response=json_response,
            success_marker=False,
            result_status=ActionStatus.TECHNICAL_FAILURE.value,
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
        inventory_page = self._inventory_page()
        if inventory_page is None:
            raise SiteParseError("MineBBS 无法唯一识别道具库存页面")
        page_url, html = inventory_page
        inventory_before = self._inventory_items(html)
        self._last_inventory = dict(inventory_before)
        label_order = [self._preferred_item] if self._preferred_item else []
        label_order.extend([self.PURPLE_CARD, self.GOLD_CARD])
        for label in dict.fromkeys(item for item in label_order if item):
            item_key = "purple" if label == self.PURPLE_CARD else "gold"
            if inventory_before.get(item_key, 0) <= 0:
                continue
            submission = self._deployment_submission(html, page_url, label)
            if submission is None:
                raise SiteParseError("MineBBS 顶贴卡缺少唯一部署入口")
            log_step(
                "deployment_submission",
                site=self.name,
                status="started",
                url=submission.action,
                submit_method=submission.method,
                field_names=sorted(submission.data),
                field_count=len(submission.data),
            )
            result = self._submit(submission)
            _json_response, json_outcome = self._json_submission_outcome(result)
            decoded_result = self._decoded_result_text(result)
            if (
                json_outcome is True
                or any(
                    marker in decoded_result for marker in ("道具已部署", "部署成功", "顶贴成功")
                )
                or (not _json_response and "success" in result.casefold())
            ):
                log_step(
                    "deployment_submission",
                    site=self.name,
                    status="completed",
                    url=submission.action,
                )
                return self._result("apply_bump_item", ActionStatus.SUCCESS, f"{label}已部署")
            if any(marker in decoded_result for marker in ("过期", "不存在", "冷却", "无法使用")):
                return self._result(
                    "apply_bump_item", ActionStatus.INSUFFICIENT_RESOURCES, f"{label}不可用"
                )
            try:
                after = self.get_inventory().items.get(item_key, 0)
            except SiteParseError:
                after = inventory_before.get(item_key, 0)
            inventory_confirmed = after < inventory_before.get(item_key, 0)
            log_step(
                "apply_confirmation",
                site=self.name,
                status="completed" if inventory_confirmed else "failed",
                inventory_confirmed=inventory_confirmed,
            )
            if inventory_confirmed:
                return self._result(
                    "apply_bump_item", ActionStatus.SUCCESS, f"{label}已部署（库存已确认）"
                )
            return self._result(
                "apply_bump_item", ActionStatus.TECHNICAL_FAILURE, f"部署{label}后未识别到明确结果"
            )
        return self._result(
            "apply_bump_item", ActionStatus.INSUFFICIENT_RESOURCES, "没有可用顶贴卡"
        )
