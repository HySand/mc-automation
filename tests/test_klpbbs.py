from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from mc_automation.config import SiteConfig
from mc_automation.models import ActionStatus
from mc_automation.sites.base import SiteParseError
from mc_automation.sites.klpbbs import KLPBBSAdapter


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
        value = self.pages[url]
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"No response left for {url}")
            return StubResponse(value.pop(0))
        return StubResponse(value)

    def post(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append(("POST", url, kwargs))
        return StubResponse(self.posts.pop(0) if self.posts else "")


class FakePromotionVisitor:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = outcomes
        self.urls: list[str] = []

    def visit(self, promotion_url: str) -> bool:
        self.urls.append(promotion_url)
        return self.outcomes.pop(0)


def config() -> SiteConfig:
    return SiteConfig(
        name="klpbbs",
        enabled=True,
        username="owner",
        password="secret",
        thread_id="42",
    )


def promotion_config(*, max_visits: int = 3) -> SiteConfig:
    return replace(
        config(),
        promotion_enabled=True,
        promotion_max_visits=max_visits,
        promotion_visit_delay_seconds=0,
    )


def test_adapter_has_no_forum_reply_api() -> None:
    assert not hasattr(KLPBBSAdapter, "reply_bump")


def test_authenticate_submits_current_formhash_and_verifies_session() -> None:
    transport = FakeTransport(
        {
            "https://example.test/member.php?mod=logging&action=login": (
                '<input name="formhash" value="abc123">'
            ),
            "https://example.test": '<a href="member.php?mod=logging&action=logout">退出登录</a>',
        }
    )
    adapter = KLPBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.authenticate()

    assert result.status is ActionStatus.SUCCESS
    post = next(call for call in transport.calls if call[0] == "POST")
    assert post[2]["data"]["formhash"] == "abc123"
    assert post[2]["data"]["username"] == "owner"


def test_rank_uses_normal_thread_order_and_rejects_unknown_markup() -> None:
    forum_url = "https://example.test/forum-56-1.html"
    adapter = KLPBBSAdapter(
        config(),
        FakeTransport(
            {forum_url: '<table id="normalthread_7"></table><table id="normalthread_42"></table>'}
        ),
        base_url="https://example.test",
    )
    assert adapter.get_thread_rank() == 2

    broken = KLPBBSAdapter(
        config(),
        FakeTransport({forum_url: "<html>changed</html>"}),
        base_url="https://example.test",
    )
    with pytest.raises(SiteParseError, match="结构无法识别"):
        broken.get_thread_rank()


def test_rank_reloads_once_when_the_forum_returns_an_incomplete_200_page() -> None:
    forum_url = "https://example.test/forum-56-1.html"
    transport = FakeTransport(
        {
            forum_url: [
                '<html><a href="thread-7-1-1.html">navigation only</a></html>',
                '<tbody id="normalthread_7"></tbody><tbody id="normalthread_42"></tbody>',
            ]
        }
    )
    site = KLPBBSAdapter(config(), transport, base_url="https://example.test")

    assert site.get_thread_rank() == 2
    assert [call[1] for call in transport.calls] == [forum_url, forum_url]


def test_inventory_distinguishes_explicitly_empty_from_unparseable() -> None:
    inventory_url = "https://example.test/home.php?mod=magic&action=mybox"
    empty = KLPBBSAdapter(
        config(),
        FakeTransport({inventory_url: "<p>道具箱为空</p>"}),
        base_url="https://example.test",
    )
    assert empty.get_inventory().items == {"bump": 0}

    changed = KLPBBSAdapter(
        config(),
        FakeTransport({inventory_url: "<html>new layout</html>"}),
        base_url="https://example.test",
    )
    with pytest.raises(SiteParseError, match="库存结构无法识别"):
        changed.get_inventory()


def test_target_owner_must_match_authenticated_username() -> None:
    thread_url = "https://example.test/thread-42-1-1.html"
    owned = KLPBBSAdapter(
        config(),
        FakeTransport(
            {
                thread_url: (
                    '<table id="pid1"><div class="authi">'
                    '<a href="space-uid-1.html">owner</a></div></table>'
                )
            }
        ),
        base_url="https://example.test",
    )
    owned.verify_target_ownership()

    foreign = KLPBBSAdapter(
        config(),
        FakeTransport(
            {
                thread_url: (
                    '<table id="pid1"><div class="authi">'
                    '<a href="space-uid-2.html">someone-else</a></div></table>'
                )
            }
        ),
        base_url="https://example.test",
    )
    with pytest.raises(SiteParseError, match="不属于配置账号"):
        foreign.verify_target_ownership()


def test_purchase_reports_insufficient_resources_without_retrying() -> None:
    transport = FakeTransport(
        {"https://example.test": '<input name="formhash" value="token">'},
        posts=["铁粒不足，无法购买"],
    )
    adapter = KLPBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.purchase_bump_item()

    assert result.status is ActionStatus.INSUFFICIENT_RESOURCES
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1


def test_promotion_task_applies_visits_and_draws_reward() -> None:
    task_url = "https://example.test/home.php?mod=task"
    apply_url = "https://example.test/home.php?mod=task&do=apply&id=7"
    doing_url = "https://example.test/home.php?mod=task&do=doing"
    visit_url = "https://example.test/promotion?fromuid=5"
    draw_url = "https://example.test/home.php?mod=task&do=draw&id=7"
    transport = FakeTransport(
        {
            task_url: (
                '<div class="task">推广任务 '
                '<a href="home.php?mod=task&do=apply&id=7">申请</a></div>'
            ),
            apply_url: "任务申请成功",
            doing_url: [
                (
                    '<div class="task">推广任务 进行中 '
                    '<a href="/promotion?fromuid=5">推广链接</a></div>'
                ),
                (
                    '<div class="task">推广任务 已完成 '
                    '<a href="home.php?mod=task&do=draw&id=7">领取奖励</a></div>'
                ),
            ],
            visit_url: "landing",
            draw_url: "领取奖励成功",
        }
    )
    visitor = FakePromotionVisitor([True])
    adapter = KLPBBSAdapter(
        promotion_config(),
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SUCCESS
    assert result.metadata["visits"] == 1
    assert result.metadata["attempts"] == 1
    assert visitor.urls == [visit_url]
    assert [call[1] for call in transport.calls] == [
        task_url,
        apply_url,
        doing_url,
        doing_url,
        draw_url,
    ]


def test_promotion_task_stops_at_configured_visit_cap() -> None:
    task_url = "https://example.test/home.php?mod=task"
    doing_url = "https://example.test/home.php?mod=task&do=doing"
    visit_url = "https://example.test/promotion?fromuid=5"
    incomplete = (
        '<div class="task">推广任务 进行中 <a href="/promotion?fromuid=5">推广链接</a></div>'
    )
    transport = FakeTransport(
        {
            task_url: incomplete,
            doing_url: [incomplete, incomplete],
            visit_url: ["landing", "landing"],
        }
    )
    visitor = FakePromotionVisitor([False, False])
    adapter = KLPBBSAdapter(
        promotion_config(max_visits=2),
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SKIPPED
    assert result.metadata == {"visits": 0, "attempts": 2}
    assert visitor.urls == [visit_url, visit_url]
    assert [call[1] for call in transport.calls] == [task_url]


def test_promotion_task_draws_already_completed_reward_without_visits() -> None:
    task_url = "https://example.test/home.php?mod=task"
    draw_url = "https://example.test/home.php?mod=task&do=draw&id=7"
    transport = FakeTransport(
        {
            task_url: (
                '<div class="task">推广任务 已完成 '
                '<a href="home.php?mod=task&do=draw&id=7">领取奖励</a></div>'
            ),
            draw_url: "任务奖励已领取",
        }
    )
    adapter = KLPBBSAdapter(promotion_config(), transport, base_url="https://example.test")

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SUCCESS
    assert result.metadata["visits"] == 0
    assert len(transport.calls) == 2


def test_promotion_task_rejects_unknown_markup_without_visiting() -> None:
    task_url = "https://example.test/home.php?mod=task"
    transport = FakeTransport({task_url: "<html>new task layout</html>"})
    adapter = KLPBBSAdapter(promotion_config(), transport, base_url="https://example.test")

    with pytest.raises(SiteParseError, match="推广任务页面结构无法识别"):
        adapter.run_promotion_task()

    assert len(transport.calls) == 1


def test_promotion_task_requires_an_isolated_proxy_visitor() -> None:
    task_url = "https://example.test/home.php?mod=task"
    transport = FakeTransport(
        {
            task_url: (
                '<div class="task">推广任务 进行中 '
                '<a href="/promotion?fromuid=5">推广链接</a></div>'
            )
        }
    )
    adapter = KLPBBSAdapter(promotion_config(), transport, base_url="https://example.test")

    with pytest.raises(SiteParseError, match="代理访问器未配置"):
        adapter.run_promotion_task()


def test_promotion_task_rejects_a_link_that_leaves_the_klpbbs_origin() -> None:
    task_url = "https://example.test/home.php?mod=task"
    visitor = FakePromotionVisitor([True])
    adapter = KLPBBSAdapter(
        promotion_config(),
        FakeTransport(
            {
                task_url: (
                    '<div class="task">推广任务 进行中 '
                    '<a href="https://outside.example/promotion?fromuid=5">推广链接</a></div>'
                )
            }
        ),
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    with pytest.raises(SiteParseError, match="不属于当前靶场源"):
        adapter.run_promotion_task()
    assert not visitor.urls
