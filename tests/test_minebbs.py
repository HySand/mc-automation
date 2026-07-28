from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest

from mc_automation.config import SiteConfig
from mc_automation.models import ActionStatus
from mc_automation.sites.base import SiteParseError
from mc_automation.sites.minebbs import MineBBSAdapter


@dataclass
class StubResponse:
    text: str


class FakeTransport:
    def __init__(self, pages: dict[str, str | list[str]], posts: list[str] | None = None) -> None:
        self.pages = pages
        self.posts = list(posts or [])
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append(("GET", url, kwargs))
        response = self.pages[url]
        if isinstance(response, list):
            return StubResponse(response.pop(0))
        return StubResponse(response)

    def post(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append(("POST", url, kwargs))
        return StubResponse(self.posts.pop(0) if self.posts else "")


def config() -> SiteConfig:
    return SiteConfig(
        name="minebbs",
        enabled=True,
        username="owner@example.test",
        password="secret",
        thread_id="42",
    )


def test_adapter_has_no_forum_reply_api() -> None:
    assert not hasattr(MineBBSAdapter, "reply_bump")


def test_authenticate_discovers_login_form_and_verifies_account_marker() -> None:
    transport = FakeTransport(
        {
            "https://example.test/login/": (
                '<form method="post" action="/login/login">'
                '<input type="hidden" name="_xfToken" value="token">'
                '<input name="password"><button>登录</button></form>'
            ),
            "https://example.test": '<a href="/account/">owner</a>',
        }
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.authenticate()

    assert result.status is ActionStatus.SUCCESS
    post = next(call for call in transport.calls if call[0] == "POST")
    assert post[1] == "https://example.test/login/login"
    assert post[2]["data"]["_xfToken"] == "token"
    assert post[2]["data"]["login"] == "owner@example.test"


def test_rank_parses_thread_ids_in_visible_order() -> None:
    adapter = MineBBSAdapter(
        config(),
        FakeTransport(
            {
                "https://example.test/servers/": (
                    '<div class="structItem-title"><a href="/threads/alpha.7/">A</a></div>'
                    '<div class="structItem-title"><a href="/threads/server.42/">B</a></div>'
                )
            }
        ),
        base_url="https://example.test",
    )
    assert adapter.get_thread_rank() == 2


def test_inventory_requires_a_unique_inventory_page() -> None:
    transport = FakeTransport(
        {
            "https://example.test": "<html>home</html>",
            "https://example.test/tool-shop/": "<html>shop</html>",
        }
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    with pytest.raises(SiteParseError, match="库存页面"):
        adapter.get_inventory()


def test_target_owner_must_match_configured_public_username() -> None:
    thread_url = "https://example.test/threads/42/"
    owned = MineBBSAdapter(
        config(),
        FakeTransport(
            {thread_url: '<article class="message--post" data-author="owner@example.test">'}
        ),
        base_url="https://example.test",
    )
    owned.verify_target_ownership()

    foreign = MineBBSAdapter(
        config(),
        FakeTransport({thread_url: '<article class="message--post" data-author="other">'}),
        base_url="https://example.test",
    )
    with pytest.raises(SiteParseError, match="不属于配置账号"):
        foreign.verify_target_ownership()


def test_unknown_balance_fails_closed_before_purchase_page() -> None:
    transport = FakeTransport(
        {
            "https://example.test": "<span>紫水晶：未知</span>",
            "https://example.test/account/": "<span>金粒：100</span>",
        }
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    with pytest.raises(SiteParseError, match="余额结构无法可靠识别"):
        adapter.purchase_bump_item()

    assert not any("/purchase" in call[1] for call in transport.calls)


def test_purchase_prefers_purple_and_submits_discovered_form() -> None:
    transport = FakeTransport(
        {
            "https://example.test": "<span>紫水晶：1</span>",
            "https://example.test/account/": "<span>金粒：100</span>",
            "https://example.test/tool-shop/18/purchase": (
                '<form method="post" action="/tool-shop/18/purchase">'
                '<input type="hidden" name="_xfToken" value="token">'
                '<input type="number" name="quantity" value="9">'
                '<input type="hidden" name="_xfRedirect" value="/tool-shop/">'
                '<button type="submit" class="button--primary"></button></form>'
            ),
        },
        posts=["购买成功"],
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.purchase_bump_item()

    assert result.status is ActionStatus.SUCCESS
    assert "紫晶顶贴卡" in result.message
    post = next(call for call in transport.calls if call[0] == "POST")
    assert post[1].endswith("/tool-shop/18/purchase")
    assert post[2]["data"]["_xfToken"] == "token"
    assert post[2]["data"]["quantity"] == "1"
    assert post[2]["data"]["_xfResponseType"] == "json"
    assert post[2]["headers"] == {"X-Requested-With": "XMLHttpRequest"}


def test_purchase_form_logs_structural_selection(caplog: object) -> None:
    transport = FakeTransport(
        {
            "https://example.test": "<span>紫水晶：1</span>",
            "https://example.test/account/": "<span>金粒：100</span>",
            "https://example.test/tool-shop/18/purchase": (
                '<form method="post" action="/tool-shop/18/purchase">'
                '<input type="hidden" name="_xfToken" value="secret-token">'
                '<input type="number" name="quantity" value="1">'
                '<button type="submit"></button></form>'
            ),
        },
        posts=["购买成功"],
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")
    caplog.set_level(logging.INFO, logger="mc_automation.steps")  # type: ignore[attr-defined]

    adapter.purchase_bump_item()

    message = next(
        record.message
        for record in caplog.records  # type: ignore[attr-defined]
        if "检查表单" in record.message
    )
    assert "表单数 1" in message
    assert "字段 ['_xfToken', 'quantity']" in message
    assert "secret-token" not in message


def test_purchase_accepts_xenforo_ajax_success_response() -> None:
    transport = FakeTransport(
        {
            "https://example.test": "<span>紫水晶：1</span>",
            "https://example.test/account/": "<span>金粒：0</span>",
            "https://example.test/tool-shop/18/purchase": (
                '<form method="post" action="/tool-shop/18/purchase">'
                '<input name="quantity" value="1"><button type="submit"></button></form>'
            ),
        },
        posts=['{"status":"ok","redirect":"/tool-shop/"}'],
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.purchase_bump_item()

    assert result.status is ActionStatus.SUCCESS


def test_purchase_does_not_treat_json_error_as_success() -> None:
    transport = FakeTransport(
        {
            "https://example.test": "<span>紫水晶：1</span>",
            "https://example.test/account/": "<span>金粒：0</span>",
            "https://example.test/tool-shop/18/purchase": (
                '<form method="post" action="/tool-shop/18/purchase">'
                '<input name="quantity" value="1"><button type="submit"></button></form>'
            ),
        },
        posts=['{"status":"ok","success":false,"errors":["余额不足"]}'],
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.purchase_bump_item()

    assert result.status is ActionStatus.INSUFFICIENT_RESOURCES


def test_purchase_does_not_treat_explicit_false_json_as_success() -> None:
    transport = FakeTransport(
        {
            "https://example.test": "<span>紫水晶：1</span>",
            "https://example.test/account/": "<span>金粒：0</span>",
            "https://example.test/tool-shop/18/purchase": (
                '<form method="post" action="/tool-shop/18/purchase">'
                '<input name="quantity" value="1"><button type="submit"></button></form>'
            ),
        },
        posts=['{"status":"ok","success":false}'],
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.purchase_bump_item()

    assert result.status is ActionStatus.TECHNICAL_FAILURE


def test_purchase_confirms_opaque_redirect_response_by_inventory_increase() -> None:
    home = "https://example.test"
    shop = "https://example.test/tool-shop/"
    inventory = "https://example.test/tool-shop/inventory/"
    transport = FakeTransport(
        {
            home: [
                '<a href="/tool-shop/inventory/">我的道具</a>',
                "<span>紫水晶：1</span>",
                '<a href="/tool-shop/inventory/">我的道具</a>',
            ],
            shop: [
                '<a href="/tool-shop/inventory/">我的道具</a>',
                '<a href="/tool-shop/inventory/">我的道具</a>',
            ],
            inventory: [
                "<html><body>暂无道具</body></html>",
                "<form>紫晶顶贴卡 数量：1 <button>使用</button></form>",
            ],
            "https://example.test/account/": "<span>金粒：0</span>",
            "https://example.test/tool-shop/18/purchase": (
                '<form method="post" action="/tool-shop/18/purchase">'
                '<input name="quantity" value="1"><button type="submit"></button></form>'
            ),
        },
        posts=["<html><body>商店页面</body></html>"],
    )
    adapter = MineBBSAdapter(config(), transport, base_url=home)
    assert adapter.get_inventory().items["purple"] == 0

    result = adapter.purchase_bump_item()

    assert result.status is ActionStatus.SUCCESS
    assert result.metadata["item"] == "purple"
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1


def test_apply_accepts_xenforo_ajax_success_response() -> None:
    inventory = "https://example.test/tool-shop/inventory/"
    transport = FakeTransport(
        {
            "https://example.test": '<a href="/tool-shop/inventory/">我的道具</a>',
            "https://example.test/tool-shop/": ('<a href="/tool-shop/inventory/">我的道具</a>'),
            inventory: (
                '<form method="post" action="/tool-shop/inventory/use">紫晶顶贴卡'
                '<select name="thread_id"><option value="42">目标 42</option></select>'
                '<button type="submit">使用</button></form>'
            ),
        },
        posts=['{"status":"ok","redirect":"/threads/42/"}'],
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.apply_bump_item()

    assert result.status is ActionStatus.SUCCESS
    post = next(call for call in transport.calls if call[0] == "POST")
    assert post[2]["data"]["_xfResponseType"] == "json"


def test_purchase_form_rejects_ambiguous_structural_candidates() -> None:
    transport = FakeTransport(
        {
            "https://example.test": "<span>紫水晶：1</span>",
            "https://example.test/account/": "<span>金粒：100</span>",
            "https://example.test/tool-shop/18/purchase": (
                '<form method="post" action="/tool-shop/18/purchase">'
                '<input name="quantity" value="1"><button type="submit"></button></form>'
                '<form method="post" action="/tool-shop/18/purchase">'
                '<input name="quantity" value="1"><button type="submit"></button></form>'
            ),
        }
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    with pytest.raises(SiteParseError, match="购买表单"):
        adapter.purchase_bump_item()

    assert not any(call[0] == "POST" for call in transport.calls)


def test_same_day_gold_exclusion_prevents_purchase_submission() -> None:
    transport = FakeTransport(
        {
            "https://example.test": "<span>紫水晶：0</span>",
            "https://example.test/account/": "<span>金粒：100</span>",
        }
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.purchase_bump_item(excluded_items=frozenset({"gold"}))

    assert result.status is ActionStatus.INSUFFICIENT_RESOURCES
    assert not any(call[0] == "POST" for call in transport.calls)


def test_apply_card_targets_configured_thread_from_discovered_form() -> None:
    inventory_url = "https://example.test/account/tool-inventory/"
    inventory = (
        '<form method="post" action="/tools/use">'
        '<input type="hidden" name="_xfToken" value="token">'
        "<strong>紫晶顶贴卡</strong>"
        '<select name="thread"><option value="42">My server #42</option></select>'
        '<button name="confirm" value="1">使用</button></form>'
    )
    transport = FakeTransport(
        {
            "https://example.test": '<a href="/account/tool-inventory/">我的道具</a>',
            "https://example.test/tool-shop/": "<html>shop</html>",
            inventory_url: inventory,
        },
        posts=["顶贴成功"],
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.apply_bump_item()

    assert result.status is ActionStatus.SUCCESS
    post = next(call for call in transport.calls if call[0] == "POST")
    assert post[2]["data"]["thread"] == "42"
    assert post[2]["headers"] == {"X-Requested-With": "XMLHttpRequest"}


def test_apply_card_refuses_form_without_configured_thread() -> None:
    inventory_url = "https://example.test/account/tool-inventory/"
    inventory = (
        '<form method="post" action="/tools/use">'
        '<input type="hidden" name="_xfToken" value="token">'
        "<strong>紫晶顶贴卡</strong>"
        '<select name="thread"><option value="99">Another server</option></select>'
        "<button>使用</button></form>"
    )
    transport = FakeTransport(
        {
            "https://example.test": '<a href="/account/tool-inventory/">我的道具</a>',
            "https://example.test/tool-shop/": "<html>shop</html>",
            inventory_url: inventory,
        }
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    with pytest.raises(SiteParseError, match="未提供配置的目标帖"):
        adapter.apply_bump_item()

    assert not any(call[0] == "POST" for call in transport.calls)
