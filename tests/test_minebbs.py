from __future__ import annotations

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
    def __init__(self, pages: dict[str, str], posts: list[str] | None = None) -> None:
        self.pages = pages
        self.posts = list(posts or [])
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append(("GET", url, kwargs))
        return StubResponse(self.pages[url])

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
                '<form method="post" action="/tool-shop/18/purchase/confirm">'
                '<input type="hidden" name="_xfToken" value="token"><button>购买</button></form>'
            ),
        },
        posts=["购买成功"],
    )
    adapter = MineBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.purchase_bump_item()

    assert result.status is ActionStatus.SUCCESS
    assert "紫晶顶贴卡" in result.message
    post = next(call for call in transport.calls if call[0] == "POST")
    assert post[1].endswith("/tool-shop/18/purchase/confirm")
    assert post[2]["data"]["_xfToken"] == "token"


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
