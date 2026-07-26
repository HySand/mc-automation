from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mc_automation.ai_solver import CaptchaSolution
from mc_automation.config import LikeSiteConfig
from mc_automation.models import ActionStatus
from mc_automation.sites.base import SiteParseError
from mc_automation.sites.like import LikeAdapter


@dataclass
class StubResponse:
    text: str
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


PageValue = str | StubResponse


class FakeTransport:
    def __init__(self, pages: dict[str, PageValue], posts: list[PageValue] | None = None) -> None:
        self.pages = pages
        self.posts = list(posts or [])
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    @staticmethod
    def _response(value: PageValue) -> StubResponse:
        if isinstance(value, StubResponse):
            return value
        return StubResponse(value, content=value.encode())

    def get(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append(("GET", url, kwargs))
        return self._response(self.pages[url])

    def post(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append(("POST", url, kwargs))
        return self._response(self.posts.pop(0))


class FakeCaptchaSolver:
    def __init__(self, code: str = "AB12") -> None:
        self.code = code
        self.images: list[tuple[bytes, str | None]] = []

    def solve_wdsjfwq_captcha(
        self,
        image: bytes,
        *,
        content_type: str | None = None,
    ) -> CaptchaSolution:
        self.images.append((image, content_type))
        return CaptchaSolution(self.code, 0.93)


def adapter(
    pages: dict[str, PageValue],
    posts: list[PageValue] | None = None,
    *,
    name: str = "mclists",
    url: str = "https://example.test/server/9969.html",
    captcha_solver: FakeCaptchaSolver | None = None,
) -> tuple[LikeAdapter, FakeTransport]:
    transport = FakeTransport(pages, posts)
    return (
        LikeAdapter(
            LikeSiteConfig(name, True, url),
            transport,  # type: ignore[arg-type]
            captcha_solver=captcha_solver,
            username_factory=lambda: "Player123456",
        ),
        transport,
    )


def test_like_adapter_submits_the_unique_like_link() -> None:
    page = "https://example.test/server/9969.html"
    action = "https://example.test/server/like?id=9969"
    site, transport = adapter({page: '<a href="like?id=9969">点我喜欢</a>', action: "点赞成功"})

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.SUCCESS
    assert [call[1] for call in transport.calls] == [page, action]


def test_like_adapter_submits_hidden_form_data() -> None:
    page = "https://example.test/server/9969.html"
    site, transport = adapter(
        {
            page: (
                '<form action="/vote" method="post"><input type="hidden" name="token" value="abc">'
                "<button>喜欢</button></form>"
            )
        },
        ["喜欢成功"],
    )

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.SUCCESS
    assert transport.calls[-1] == ("POST", "https://example.test/vote", {"data": {"token": "abc"}})


def test_like_adapter_submits_the_named_button_used_to_activate_the_form() -> None:
    page = "https://example.test/server/9969.html"
    site, transport = adapter(
        {
            page: (
                '<form action="/vote" method="post">'
                '<input type="hidden" name="id" value="9969">'
                '<button type="submit" name="submit">点赞</button></form>'
            )
        },
        ["点赞成功"],
    )

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.SUCCESS
    assert transport.calls[-1] == (
        "POST",
        "https://example.test/vote",
        {"data": {"id": "9969", "submit": ""}},
    )


def test_like_adapter_submits_mclists_json_action() -> None:
    page = "https://example.test/server/9969.html"
    action = "https://example.test/server-like.php"
    site, transport = adapter(
        {
            page: '<button id="server-like-button" data-server-id="9969">我喜欢</button>',
        },
        ['{"success": true, "message": "点赞成功"}'],
    )

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.SUCCESS
    assert transport.calls[-1] == (
        "POST",
        action,
        {
            "data": {"sid": "9969"},
            "headers": {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        },
    )


def test_like_adapter_requires_manual_input_for_interactive_form() -> None:
    page = "https://example.test/server/9969.html"
    site, transport = adapter(
        {
            page: (
                '<a href="https://example.test/server/9969.html">点赞</a>'
                '<form action="#" method="post">'
                '<input type="hidden" name="id" value="9969">'
                '<input type="text" name="username">'
                '<input type="text" name="captcha">'
                '<button type="submit" name="submit">点赞</button></form>'
            )
        }
    )

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.MANUAL_INTERVENTION
    assert len(transport.calls) == 1


def test_wdsjfwq_adapter_solves_captcha_and_submits_random_username() -> None:
    page = "https://example.test/server-1991/vote.html"
    captcha = "https://example.test/captcha.png"
    solver = FakeCaptchaSolver("Z9K2")
    site, transport = adapter(
        {
            page: (
                '<form action="/server-1991/vote.html" method="post">'
                '<input type="hidden" name="server_id" value="1991">'
                '<input type="text" name="username">'
                '<input type="text" name="captcha" id="imageVerification">'
                '<img src="/captcha.png" alt="验证码">'
                '<button type="submit" name="submit">点赞</button></form>'
            ),
            captcha: StubResponse(
                "",
                content=b"captcha-bytes",
                headers={"Content-Type": "image/png"},
            ),
        },
        ["点赞成功"],
        name="wdsjfwq",
        url=page,
        captcha_solver=solver,
    )

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.SUCCESS
    assert solver.images == [(b"captcha-bytes", "image/png")]
    assert transport.calls[-1] == (
        "POST",
        page,
        {
            "data": {
                "server_id": "1991",
                "username": "Player123456",
                "captcha": "Z9K2",
                "submit": "",
            }
        },
    )


def test_wdsjfwq_confirms_an_opaque_response_by_like_count_increment() -> None:
    page = "https://example.test/server-1991/vote.html"
    captcha = "https://example.test/captcha.png"
    site, transport = adapter(
        {
            page: (
                "<span>当前点赞数：9</span><span>点赞 (9)</span>"
                '<form action="/server-1991/vote.html" method="post">'
                '<input type="text" name="username">'
                '<input type="text" name="captcha">'
                '<img src="/captcha.png" alt="验证码">'
                '<button type="submit" name="submit">点赞</button></form>'
            ),
            captcha: StubResponse("", content=b"captcha-bytes"),
        },
        ["<main><span>当前点赞数：10</span><span>点赞 (10)</span></main>"],
        name="wdsjfwq",
        url=page,
        captcha_solver=FakeCaptchaSolver("A1234"),
    )

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.SUCCESS
    assert "计数已增加" in result.message
    assert [call[0] for call in transport.calls] == ["GET", "GET", "POST"]


def test_wdsjfwq_rechecks_after_an_opaque_unchanged_response() -> None:
    page = "https://example.test/server-1991/vote.html"
    captcha = "https://example.test/captcha.png"
    initial = (
        "<span>当前点赞数：9</span>"
        '<form action="/server-1991/vote.html" method="post">'
        '<input type="text" name="username"><input type="text" name="captcha">'
        '<img src="/captcha.png" alt="验证码">'
        '<button type="submit" name="submit">点赞</button></form>'
    )
    transport = FakeTransport(
        {
            page: initial,
            captcha: StubResponse("", content=b"captcha-bytes"),
        },
        ["<main><span>当前点赞数：9</span></main>"],
    )
    original_get = transport.get
    page_reads = 0

    def get_with_refresh(url: str, **kwargs: Any) -> StubResponse:
        nonlocal page_reads
        if url == page:
            page_reads += 1
            if page_reads == 2:
                transport.calls.append(("GET", url, kwargs))
                return StubResponse("<main><span>当前点赞数：10</span></main>")
        return original_get(url, **kwargs)

    transport.get = get_with_refresh  # type: ignore[method-assign]
    site = LikeAdapter(
        LikeSiteConfig("wdsjfwq", True, page),
        transport,  # type: ignore[arg-type]
        captcha_solver=FakeCaptchaSolver("A1234"),
        username_factory=lambda: "Player123456",
    )

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.SUCCESS
    assert page_reads == 2


def test_wdsjfwq_rejects_an_opaque_response_when_the_count_stays_unchanged() -> None:
    page = "https://example.test/server-1991/vote.html"
    captcha = "https://example.test/captcha.png"
    initial = (
        "<span>当前点赞数：9</span>"
        '<form action="/server-1991/vote.html" method="post">'
        '<input type="text" name="username"><input type="text" name="captcha">'
        '<img src="/captcha.png" alt="验证码">'
        '<button type="submit" name="submit">点赞</button></form>'
    )
    transport = FakeTransport(
        {
            page: initial,
            captcha: StubResponse("", content=b"captcha-bytes"),
        },
        ["<main><span>当前点赞数：9</span></main>"],
    )
    site = LikeAdapter(
        LikeSiteConfig("wdsjfwq", True, page),
        transport,  # type: ignore[arg-type]
        captcha_solver=FakeCaptchaSolver("A1234"),
        username_factory=lambda: "Player123456",
    )

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.TECHNICAL_FAILURE
    assert [call[0] for call in transport.calls] == ["GET", "GET", "POST", "GET"]


def test_wdsjfwq_rejects_conflicting_like_counts() -> None:
    with pytest.raises(SiteParseError, match="计数相互矛盾"):
        LikeAdapter._wdsjfwq_like_count("<span>当前点赞数：9</span><span>点赞 (10)</span>")


def test_wdsjfwq_adapter_fails_closed_when_solver_returns_invalid_code() -> None:
    page = "https://example.test/server-1991/vote.html"
    captcha = "https://example.test/captcha.png"
    site, transport = adapter(
        {
            page: (
                '<form action="/server-1991/vote.html" method="post">'
                '<input type="text" name="username">'
                '<input type="text" name="captcha">'
                '<img src="/captcha.png" alt="验证码">'
                '<button type="submit">点赞</button></form>'
            ),
            captcha: StubResponse("", content=b"captcha-bytes"),
        },
        name="wdsjfwq",
        url=page,
        captcha_solver=FakeCaptchaSolver("bad code"),
    )

    result = site.run_one_shot_action()

    assert result.status is ActionStatus.MANUAL_INTERVENTION
    assert [call[0] for call in transport.calls] == ["GET", "GET"]


def test_like_adapter_skips_explicit_already_liked_page() -> None:
    page = "https://example.test/server/9969.html"
    site, transport = adapter({page: "您今天已经喜欢过了"})

    assert site.run_one_shot_action().status is ActionStatus.SKIPPED
    assert len(transport.calls) == 1


def test_like_adapter_refuses_ambiguous_controls_without_submission() -> None:
    page = "https://example.test/server/9969.html"
    site, transport = adapter({page: '<a href="/one">喜欢</a><a href="/two">点赞</a>'})

    with pytest.raises(SiteParseError, match="不唯一"):
        site.run_one_shot_action()
    assert len(transport.calls) == 1
