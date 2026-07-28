from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest
import requests

from mc_automation.config import SiteConfig
from mc_automation.models import ActionStatus
from mc_automation.promotion_proxy import PromotionVisitBatch, ProxyPoolExhausted
from mc_automation.sites.base import SiteParseError
from mc_automation.sites.klpbbs import KLPBBSAdapter


@dataclass
class StubResponse:
    text: str
    status_code: int = 200
    history: tuple[object, ...] = ()

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


class FakeTransport:
    def __init__(self, pages: dict[str, str | list[str]], posts: list[str] | None = None) -> None:
        self.pages = pages
        self.posts = list(posts or [])
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.session = requests.Session()

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

    def visit_batch(self, promotion_url: str) -> PromotionVisitBatch:
        self.urls.append(promotion_url)
        if not self.outcomes:
            raise ProxyPoolExhausted("test pool exhausted")
        outcome = self.outcomes.pop(0)
        return PromotionVisitBatch(1, int(outcome), not self.outcomes)


def config() -> SiteConfig:
    return SiteConfig(
        name="klpbbs",
        enabled=True,
        username="owner",
        password="secret",
        thread_id="42",
    )


def promotion_config() -> SiteConfig:
    return replace(
        config(),
        promotion_enabled=True,
        promotion_visit_delay_seconds=0,
    )


def test_adapter_has_no_forum_reply_api() -> None:
    assert not hasattr(KLPBBSAdapter, "reply_bump")


def test_authenticate_submits_reference_payload_and_verifies_session() -> None:
    transport = FakeTransport(
        {
            "https://example.test": (
                '<script>var discuz_uid = "1";</script>'
                '<a href="member.php?mod=logging&action=logout">退出登录</a>'
            ),
        }
    )
    adapter = KLPBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.authenticate()

    assert result.status is ActionStatus.SUCCESS
    post = next(call for call in transport.calls if call[0] == "POST")
    assert post[2]["data"] == {"username": "owner", "password": "secret"}
    assert post[2]["headers"] == {
        "Origin": "https://example.test",
        "Referer": "https://example.test/",
    }
    assert post[2]["data"]["username"] == "owner"
    assert adapter.authenticated_uid == "1"
    assert transport.session.headers["Origin"] == "https://example.test"
    assert transport.session.headers["Referer"] == "https://example.test/"


def test_authenticate_persists_cookie_header_without_logging_values(
    caplog: object, monkeypatch: object
) -> None:
    monkeypatch.setenv("MC_AUTOMATION_LOG_FORMAT", "json")  # type: ignore[attr-defined]
    caplog.set_level("INFO", logger="mc_automation.steps")  # type: ignore[attr-defined]
    transport = FakeTransport({"https://example.test": '<script>var discuz_uid = "1";</script>'})
    transport.session.cookies.set("auth", "do-not-log")
    adapter = KLPBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.authenticate()

    assert result.status is ActionStatus.SUCCESS
    assert transport.session.headers["Cookie"] == "auth=do-not-log"
    output = "\n".join(record.message for record in caplog.records)  # type: ignore[attr-defined]
    assert '"status_code":200' in output
    assert '"cookie_count":1' in output
    assert "do-not-log" not in output


def test_authenticate_rechecks_empty_home_without_reposting_credentials(
    monkeypatch: object,
) -> None:
    home_url = "https://example.test"
    transport = FakeTransport(
        {
            home_url: [
                "<html>empty shell one</html>",
                "<html>empty shell two</html>",
                '<script>var discuz_uid = "1";</script>',
            ],
        }
    )
    monkeypatch.setattr("mc_automation.sites.klpbbs.time.sleep", lambda _seconds: None)  # type: ignore[attr-defined]
    adapter = KLPBBSAdapter(config(), transport, base_url=home_url)

    result = adapter.authenticate()

    assert result.status is ActionStatus.SUCCESS
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1
    assert [call[1] for call in transport.calls if call[0] == "GET"] == [
        home_url,
        home_url,
        home_url,
    ]


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
        FakeTransport(
            {
                forum_url: "<html>changed</html>",
                "https://example.test/forum.php?mod=forumdisplay&fid=56&page=1": (
                    "<html>still changed</html>"
                ),
            }
        ),
        base_url="https://example.test",
    )
    with pytest.raises(SiteParseError, match="结构无法识别"):
        broken.get_thread_rank()


def test_rank_reloads_once_when_the_forum_returns_an_incomplete_200_page() -> None:
    forum_url = "https://example.test/forum-56-1.html"
    fallback_url = "https://example.test/forum.php?mod=forumdisplay&fid=56&page=1"
    transport = FakeTransport(
        {
            forum_url: '<html><a href="thread-7-1-1.html">navigation only</a></html>',
            fallback_url: (
                '<tbody id="normalthread_7"></tbody><tbody id="normalthread_42"></tbody>'
            ),
        }
    )
    site = KLPBBSAdapter(config(), transport, base_url="https://example.test")

    assert site.get_thread_rank() == 2
    assert [call[1] for call in transport.calls] == [forum_url, fallback_url]


def test_rank_retries_primary_after_two_incomplete_200_pages(monkeypatch: object) -> None:
    forum_url = "https://example.test/forum-56-1.html"
    fallback_url = "https://example.test/forum.php?mod=forumdisplay&fid=56&page=1"
    transport = FakeTransport(
        {
            forum_url: [
                "<html>incomplete primary shell</html>",
                '<tbody id="normalthread_7"></tbody><tbody id="normalthread_42"></tbody>',
            ],
            fallback_url: "<html>incomplete canonical shell</html>",
        }
    )
    monkeypatch.setattr("mc_automation.sites.klpbbs.time.sleep", lambda _seconds: None)  # type: ignore[attr-defined]
    site = KLPBBSAdapter(config(), transport, base_url="https://example.test")

    assert site.get_thread_rank() == 2
    assert [call[1] for call in transport.calls] == [forum_url, fallback_url, forum_url]


def test_rank_falls_back_to_discuz_subject_links_when_row_ids_are_absent() -> None:
    forum_url = "https://example.test/forum-56-1.html"
    html = """
        <table id="threadlisttableid">
          <tbody><tr><th class="new">
            <a class="s xst" href="thread-7-1-1.html">A</a>
          </th></tr></tbody>
          <tbody><tr><th class="new">
            <a class="s xst" href="forum.php?mod=viewthread&amp;tid=42">B</a>
          </th></tr></tbody>
        </table>
    """
    site = KLPBBSAdapter(
        config(),
        FakeTransport({forum_url: html}),
        base_url="https://example.test",
    )

    assert site.get_thread_rank() == 2


def test_rank_subject_link_fallback_excludes_sticky_threads() -> None:
    forum_url = "https://example.test/forum-56-1.html"
    html = """
        <table id="threadlisttableid">
          <tbody id="stickthread_9"><tr><th>
            <a class="xst" href="thread-9-1-1.html">S</a>
          </th></tr></tbody>
          <tbody><tr><th><a class="xst" href="thread-7-1-1.html">A</a></th></tr></tbody>
          <tbody><tr><th><a class="xst" href="thread-42-1-1.html">B</a></th></tr></tbody>
        </table>
    """
    site = KLPBBSAdapter(
        config(),
        FakeTransport({forum_url: html}),
        base_url="https://example.test",
    )

    assert site.get_thread_rank() == 2


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


def test_target_owner_must_match_authenticated_uid_when_login_uses_email() -> None:
    thread_url = "https://example.test/thread-42-1-1.html"
    account_url = "https://example.test"
    email_config = replace(config(), username="owner@example.test")
    owned = KLPBBSAdapter(
        email_config,
        FakeTransport(
            {
                account_url: '<script>var discuz_uid = "1589417";</script>',
                thread_url: (
                    '<table id="pid1"><div class="authi">'
                    '<a href="space-uid-1589417.html">Atmosss</a></div></table>'
                ),
            }
        ),
        base_url="https://example.test",
    )
    owned.verify_target_ownership()

    foreign = KLPBBSAdapter(
        email_config,
        FakeTransport(
            {
                account_url: '<script>var discuz_uid = "1589417";</script>',
                thread_url: (
                    '<table id="pid1"><div class="authi">'
                    '<a href="space-uid-2.html">Atmosss</a></div></table>'
                ),
            }
        ),
        base_url="https://example.test",
    )
    with pytest.raises(SiteParseError, match="不属于当前登录账号"):
        foreign.verify_target_ownership()


def test_target_owner_accepts_query_style_discuz_uid_links() -> None:
    thread_url = "https://example.test/thread-42-1-1.html"
    adapter = KLPBBSAdapter(
        replace(config(), username="owner@example.test"),
        FakeTransport(
            {
                "https://example.test": (
                    '<div class="user"><a href="home.php?mod=space&amp;uid=1589417">'
                    "Atmosss</a>"
                    '<a href="member.php?mod=logging&amp;action=logout">退出登录</a></div>'
                ),
                thread_url: (
                    '<table id="pid10355041"><div class="authi">'
                    '<a href="home.php?mod=space&amp;uid=1589417">Atmosss</a></div></table>'
                ),
            }
        ),
        base_url="https://example.test",
    )

    adapter.verify_target_ownership()


def test_purchase_reports_insufficient_resources_without_retrying() -> None:
    transport = FakeTransport(
        {"https://example.test": '<input name="formhash" value="token">'},
        posts=["铁粒不足，无法购买"],
    )
    adapter = KLPBBSAdapter(config(), transport, base_url="https://example.test")

    result = adapter.purchase_bump_item()

    assert result.status is ActionStatus.INSUFFICIENT_RESOURCES
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1


def test_promotion_task_applies_candidate_visits_and_draws_reward() -> None:
    task_url = "https://example.test/home.php?mod=task"
    apply_url = "https://example.test/home.php?mod=task&do=apply&id=7"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
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
    assert result.metadata["proxy_successes"] == 1
    assert result.metadata["attempts"] == 1
    assert visitor.urls == [visit_url]
    assert [call[1] for call in transport.calls] == [
        task_url,
        apply_url,
        doing_url,
        doing_url,
        draw_url,
    ]


def test_promotion_task_uses_server_progress_instead_of_http_success() -> None:
    task_url = "https://example.test/home.php?mod=task"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    visit_url = "https://example.test/promotion?fromuid=5"
    draw_url = "https://example.test/home.php?mod=task&do=draw&id=1"

    def progress(value: int) -> str:
        return (
            '<div class="task">推广任务 进行中 '
            f'<span id="csc_1">{value}.00</span>'
            '<a href="home.php?mod=task&do=draw&id=1">领取奖励</a>'
            f'<a href="{visit_url}">推广链接</a></div>'
        )

    transport = FakeTransport(
        {
            task_url: progress(20),
            doing_url: [progress(20), progress(100)],
            draw_url: "领取奖励成功",
        }
    )
    visitor = FakePromotionVisitor([True, False, True])
    adapter = KLPBBSAdapter(
        promotion_config(),
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SUCCESS
    assert result.metadata == {"proxy_successes": 2, "attempts": 3}
    assert visitor.urls == [visit_url, visit_url, visit_url]


def test_promotion_task_does_not_draw_when_incomplete_draw_link_exists() -> None:
    task_url = "https://example.test/home.php?mod=task"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    visit_url = "https://example.test/promotion?fromuid=5"
    incomplete = (
        '<div class="task">推广任务 进行中 任务完成 <span id="csc_1">25.00</span>%'
        '<a href="home.php?mod=task&do=draw&id=1">领取奖励</a>'
        f'<a href="{visit_url}">推广链接</a></div>'
    )
    visitor = FakePromotionVisitor([True])
    transport = FakeTransport({task_url: incomplete, doing_url: incomplete})
    adapter = KLPBBSAdapter(
        promotion_config(),
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SKIPPED
    assert result.metadata == {
        "attempts": 1,
        "proxy_successes": 1,
        "progress_percent": 25,
    }
    assert draw_url_not_called(transport.calls)


def draw_url_not_called(calls: list[tuple[str, str, dict[str, Any]]]) -> bool:
    return not any("do=draw" in url for _, url, _ in calls)


def test_promotion_task_matches_reference_draw_cycle_when_progress_is_opaque(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_url = "https://example.test/home.php?mod=task"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    visit_url = "https://example.test/promotion?fromuid=5"
    draw_url = "https://example.test/home.php?mod=task&do=draw&id=1"
    opaque = '<a href="home.php?mod=task&do=draw&id=1">task 1</a>'
    transport = FakeTransport({task_url: opaque, doing_url: opaque, draw_url: "succeed"})
    visitor = FakePromotionVisitor([True] * 12)
    sleeps: list[float] = []
    monkeypatch.setattr("mc_automation.sites.klpbbs.time.sleep", sleeps.append)
    adapter = KLPBBSAdapter(
        replace(promotion_config(), promotion_url=visit_url),
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SUCCESS
    assert result.metadata == {"proxy_successes": 12, "attempts": 12}
    assert sleeps.count(15.0) == 1
    assert [call[1] for call in transport.calls].count(draw_url) == 1


def test_promotion_task_detects_completion_on_final_pool_check() -> None:
    task_url = "https://example.test/home.php?mod=task"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    visit_url = "https://example.test/promotion?fromuid=5"
    draw_url = "https://example.test/home.php?mod=task&do=draw&id=1"
    initial = (
        '<div class="task">推广任务 进行中 <span id="csc_1">0</span>'
        f'<a href="{visit_url}">推广链接</a></div>'
    )
    complete = (
        '<div class="task">推广任务 进行中 <span id="csc_1">100.00</span>'
        '<a href="home.php?mod=task&do=draw&id=1">领取奖励</a></div>'
    )
    transport = FakeTransport(
        {
            task_url: initial,
            doing_url: complete,
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
    assert result.metadata == {"proxy_successes": 1, "attempts": 1}
    assert [call[1] for call in transport.calls] == [
        task_url,
        doing_url,
        draw_url,
    ]


def test_promotion_task_continues_failed_proxies_without_delay_until_pool_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_url = "https://example.test/home.php?mod=task"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    visit_url = "https://example.test/promotion?fromuid=5"
    incomplete = (
        '<div class="task">推广任务 进行中 <a href="/promotion?fromuid=5">推广链接</a></div>'
    )
    transport = FakeTransport(
        {
            task_url: incomplete,
            doing_url: incomplete,
        }
    )
    visitor = FakePromotionVisitor([False, False])
    sleeps: list[float] = []
    monkeypatch.setattr("mc_automation.sites.klpbbs.time.sleep", sleeps.append)
    adapter = KLPBBSAdapter(
        promotion_config(),
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SKIPPED
    assert result.metadata == {"attempts": 2, "proxy_successes": 0}
    assert visitor.urls == [visit_url, visit_url]
    assert [call[1] for call in transport.calls] == [task_url, doing_url]
    assert sleeps == []


def test_promotion_task_draws_already_completed_reward_without_proxy_requests() -> None:
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
    assert result.metadata["proxy_successes"] == 0
    assert len(transport.calls) == 2


def test_promotion_task_uses_stable_task_one_when_the_list_is_incomplete() -> None:
    task_url = "https://example.test/home.php?mod=task"
    apply_url = "https://example.test/home.php?mod=task&do=apply&id=1"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    transport = FakeTransport(
        {
            task_url: "<html>new task layout</html>",
            apply_url: "当前无法申请",
            doing_url: "暂无任务",
        }
    )
    adapter = KLPBBSAdapter(promotion_config(), transport, base_url="https://example.test")

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SKIPPED
    assert [call[1] for call in transport.calls] == [
        task_url,
        task_url,
        task_url,
        doing_url,
        apply_url,
        doing_url,
    ]


def test_promotion_checks_doing_list_after_known_empty_task_center() -> None:
    task_url = "https://example.test/home.php?mod=task"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    draw_url = "https://example.test/home.php?mod=task&do=draw&id=1"
    task_center = (
        '<body class="pg_task">'
        '<a href="home.php?mod=task&item=doing">doing</a>'
        '<a href="home.php?mod=task&item=new">new</a>'
        "</body>"
    )
    doing = '<span id="csc_1">100.00</span><a href="home.php?mod=task&do=draw&id=1">draw</a>'
    transport = FakeTransport({task_url: task_center, doing_url: doing, draw_url: "succeed"})
    adapter = KLPBBSAdapter(promotion_config(), transport, base_url="https://example.test")

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SUCCESS
    assert [call[1] for call in transport.calls] == [task_url, doing_url, draw_url]


def test_promotion_task_requires_a_proxy_visitor() -> None:
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

    with pytest.raises(SiteParseError, match="不属于当前站点源"):
        adapter.run_promotion_task()
    assert not visitor.urls


def test_promotion_falls_back_to_stable_task_one_and_configured_url() -> None:
    task_url = "https://example.test/home.php?mod=task"
    apply_url = "https://example.test/home.php?mod=task&do=apply&id=1"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    draw_url = "https://example.test/home.php?mod=task&do=draw&id=1"
    promotion_url = "https://example.test/?fromuid=5"
    transport = FakeTransport(
        {
            task_url: "<html>incomplete task center</html>",
            apply_url: "任务申请成功",
            doing_url: [
                '<span id="csc_1">100.00</span>'
                '<a href="home.php?mod=task&do=draw&id=1">领取奖励</a>',
            ],
            draw_url: "请注意查收",
        }
    )
    visitor = FakePromotionVisitor([])
    site_config = replace(promotion_config(), promotion_url=promotion_url)
    adapter = KLPBBSAdapter(
        site_config,
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SUCCESS
    assert not visitor.urls
    assert [call[1] for call in transport.calls] == [
        task_url,
        task_url,
        task_url,
        doing_url,
        draw_url,
    ]


def test_promotion_incomplete_doing_page_skips_without_visiting_proxies() -> None:
    task_url = "https://example.test/home.php?mod=task"
    apply_url = "https://example.test/home.php?mod=task&do=apply&id=1"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    incomplete = "<html>incomplete task shell</html>"
    transport = FakeTransport(
        {
            task_url: incomplete,
            apply_url: "task status unavailable",
            doing_url: incomplete,
        }
    )
    visitor = FakePromotionVisitor([True])
    adapter = KLPBBSAdapter(
        replace(promotion_config(), promotion_url="https://example.test/?fromuid=5"),
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SKIPPED
    assert result.message == "推广任务状态暂时无法确认，已跳过推广并继续主流程"
    assert not visitor.urls
    assert [call[1] for call in transport.calls] == [
        task_url,
        task_url,
        task_url,
        doing_url,
        doing_url,
        doing_url,
    ]


def test_promotion_ignores_management_link_and_uses_configured_fromuid_url() -> None:
    task_url = "https://example.test/home.php?mod=task"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    configured_url = "https://example.test/?fromuid=5"
    management_url = "home.php?mod=spacecp&ac=promotion"
    task_html = (
        '<a href="home.php?mod=task&do=draw&id=1">task 1</a>'
        f'<a href="{management_url}">promotion management</a>'
    )
    transport = FakeTransport({task_url: task_html, doing_url: task_html})
    visitor = FakePromotionVisitor([True])
    adapter = KLPBBSAdapter(
        replace(promotion_config(), promotion_url=configured_url),
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SKIPPED
    assert visitor.urls == [configured_url]


def test_promotion_uses_bare_task_one_progress_node_from_live_doing_page() -> None:
    task_url = "https://example.test/home.php?mod=task"
    apply_url = "https://example.test/home.php?mod=task&do=apply&id=1"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    configured_url = "https://example.test/?fromuid=5"
    transport = FakeTransport(
        {
            task_url: "<html>incomplete task center</html>",
            apply_url: "already doing",
            doing_url: [
                '<span id="csc_1">10</span>',
                '<span id="csc_1">10</span>',
            ],
        }
    )
    visitor = FakePromotionVisitor([True])
    adapter = KLPBBSAdapter(
        replace(promotion_config(), promotion_url=configured_url),
        transport,
        base_url="https://example.test",
        promotion_visitor=visitor,
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SKIPPED
    assert result.metadata == {
        "attempts": 1,
        "proxy_successes": 1,
        "progress_percent": 10,
    }
    assert visitor.urls == [configured_url]


def test_promotion_confirms_opaque_draw_by_rechecking_doing_tasks() -> None:
    task_url = "https://example.test/home.php?mod=task"
    draw_url = "https://example.test/home.php?mod=task&do=draw&id=1"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    transport = FakeTransport(
        {
            task_url: (
                '<div>推广任务 已完成 <a href="home.php?mod=task&do=draw&id=1">领取奖励</a></div>'
            ),
            draw_url: "opaque response",
            doing_url: (
                "<h1>进行中的任务</h1><p>暂无任务</p>"
                '<a href="home.php?mod=task&do=apply&id=1">再次申请</a>'
            ),
        }
    )
    adapter = KLPBBSAdapter(
        replace(promotion_config(), promotion_url="https://example.test/?fromuid=5"),
        transport,
        base_url="https://example.test",
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.SUCCESS
    assert result.metadata["proxy_successes"] == 0
    assert [call[1] for call in transport.calls] == [task_url, draw_url, doing_url]


def test_promotion_rejects_opaque_draw_when_draw_link_remains() -> None:
    task_url = "https://example.test/home.php?mod=task"
    draw_url = "https://example.test/home.php?mod=task&do=draw&id=1"
    doing_url = "https://example.test/home.php?mod=task&item=doing"
    task = '<a href="home.php?mod=task&do=draw&id=1">领取奖励</a>'
    transport = FakeTransport(
        {
            task_url: f"<div>推广任务 已完成 {task}</div>",
            draw_url: "opaque response",
            doing_url: f"<div>推广任务 已完成 {task}</div>",
        }
    )
    adapter = KLPBBSAdapter(
        replace(promotion_config(), promotion_url="https://example.test/?fromuid=5"),
        transport,
        base_url="https://example.test",
    )

    result = adapter.run_promotion_task()

    assert result.status is ActionStatus.TECHNICAL_FAILURE
