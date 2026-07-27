from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from mc_automation import challenge
from mc_automation.challenge import EsaSliderChallengeResolver
from mc_automation.step_log import LOGGER_NAME


class FakeClock:
    def __init__(self) -> None:
        self.elapsed_ms = 0
        self.sleeps: list[int] = []

    def now(self) -> float:
        return self.elapsed_ms / 1000

    def advance(self, milliseconds: int) -> None:
        self.elapsed_ms += milliseconds

    async def sleep_ms(self, milliseconds: int) -> None:
        self.sleeps.append(milliseconds)
        self.advance(milliseconds)


class FakePosition:
    def __init__(self, *, x: float, y: float, width: float, height: float) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class FakeElement:
    def __init__(self, position: FakePosition | None) -> None:
        self.position = position
        self.position_reads = 0

    async def get_position(self) -> FakePosition | None:
        self.position_reads += 1
        return self.position


class FakeInput:
    class MouseButton:
        NONE = "none"
        LEFT = "left"

    @staticmethod
    def dispatch_mouse_event(
        event_type: str,
        x: float,
        y: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"type": event_type, "x": x, "y": y, **kwargs}


class FakeNetwork:
    @staticmethod
    def CookieParam(**kwargs: Any) -> dict[str, Any]:
        return kwargs


FAKE_CDP = SimpleNamespace(input_=FakeInput, network=FakeNetwork)


class FakeTab:
    def __init__(
        self,
        handle_position: FakePosition | None,
        *,
        track_position: FakePosition | None = None,
        clears: bool,
        clock: FakeClock | None = None,
        move_cost_ms: int = 0,
        fail_drag_move: int | None = None,
    ) -> None:
        self.fallback_content = "ready" if clears else "<title>滑动验证页面</title>"
        self.contents = ["<title>滑动验证页面</title>", self.fallback_content]
        self.current_content = self.contents[0]
        self.handle = FakeElement(handle_position)
        self.track = FakeElement(track_position or FakePosition(x=10, y=20, width=200, height=40))
        self.clock = clock
        self.move_cost_ms = move_cost_ms
        self.fail_drag_move = fail_drag_move
        self.drag_moves = 0
        self.events: list[dict[str, Any]] = []
        self.event_times_ms: list[int] = []
        self.selects: list[tuple[str, float]] = []
        self.evaluations: list[tuple[str, bool]] = []

    async def get_content(self) -> str:
        if self.contents:
            self.current_content = self.contents.pop(0)
        else:
            self.current_content = self.fallback_content
        return self.current_content

    async def select(self, selector: str, timeout: float) -> FakeElement | None:
        self.selects.append((selector, timeout))
        if selector == EsaSliderChallengeResolver.HANDLE_SELECTOR:
            return self.handle
        if selector == EsaSliderChallengeResolver.TRACK_SELECTOR:
            return self.track
        return None

    async def send(self, command: dict[str, Any]) -> None:
        self.events.append(command)
        self.event_times_ms.append(0 if self.clock is None else self.clock.elapsed_ms)
        if command["type"] == "mouseMoved":
            if len(self.events) > 1:
                self.drag_moves += 1
                if self.drag_moves == self.fail_drag_move:
                    raise RuntimeError("simulated CDP movement failure")
            if self.clock is not None:
                self.clock.advance(self.move_cost_ms)

    async def evaluate(self, script: str, *, return_by_value: bool) -> str | None:
        self.evaluations.append((script, return_by_value))
        if script == "navigator.userAgent":
            return "esa-nodriver"
        if script.startswith("JSON.stringify("):
            return json.dumps(
                {
                    "slider": self.current_content != "ready",
                    "title": "MineBBS",
                    "href": "https://example.test/",
                    "readyState": "complete",
                    "hasBody": True,
                }
            )
        return None


class FakeCookieJar:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.browser_cookies = [
            requests.cookies.create_cookie(
                name="esa_clearance",
                value="ok",
                domain="example.test",
                path="/",
            )
        ]

    async def set_all(self, cookies: list[dict[str, Any]]) -> None:
        self.received = cookies

    async def get_all(self, *, requests_cookie_format: bool) -> list[Any]:
        assert requests_cookie_format
        return self.browser_cookies


class FakeBrowser:
    def __init__(
        self,
        tab: FakeTab,
        *,
        navigation_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.tab = tab
        self.cookies = FakeCookieJar()
        self.navigation_error = navigation_error
        self.stop_error = stop_error
        self.urls: list[str] = []
        self.stopped = False

    async def get(self, url: str) -> FakeTab:
        self.urls.append(url)
        if self.navigation_error is not None:
            raise self.navigation_error
        return self.tab

    def stop(self) -> None:
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped = True


class FakeUtil:
    def __init__(self, *, cleanup_error: Exception | None = None) -> None:
        self.cleanup_error = cleanup_error
        self.calls: list[FakeBrowser] = []

    async def deconstruct_browser(self, browser: FakeBrowser) -> None:
        self.calls.append(browser)
        if self.cleanup_error is not None:
            raise self.cleanup_error
        browser.stopped = True


class HangingFakeUtil(FakeUtil):
    async def deconstruct_browser(self, browser: FakeBrowser) -> None:
        self.calls.append(browser)
        await asyncio.Future()


class FakeNodriver:
    def __init__(
        self,
        browser: FakeBrowser,
        *,
        start_error: Exception | None = None,
        cleanup_error: Exception | None = None,
    ) -> None:
        self.browser = browser
        self.start_errors = [start_error] if start_error is not None else []
        self.start_kwargs: dict[str, Any] = {}
        self.start_calls: list[dict[str, Any]] = []
        self.cdp = FAKE_CDP
        self.util = FakeUtil(cleanup_error=cleanup_error)

    async def start(self, **kwargs: Any) -> FakeBrowser:
        self.start_kwargs = kwargs
        self.start_calls.append(dict(kwargs))
        if self.start_errors:
            raise self.start_errors.pop(0)
        return self.browser


def install_fake_nodriver(
    monkeypatch: pytest.MonkeyPatch,
    handle_position: FakePosition | None,
    *,
    track_position: FakePosition | None = None,
    clears: bool = True,
    clock: FakeClock | None = None,
    move_cost_ms: int = 0,
    fail_drag_move: int | None = None,
    start_error: Exception | None = None,
    navigation_error: Exception | None = None,
    cleanup_error: Exception | None = None,
    stop_error: Exception | None = None,
) -> FakeNodriver:
    tab = FakeTab(
        handle_position,
        track_position=track_position,
        clears=clears,
        clock=clock,
        move_cost_ms=move_cost_ms,
        fail_drag_move=fail_drag_move,
    )
    browser = FakeBrowser(tab, navigation_error=navigation_error, stop_error=stop_error)
    nodriver = FakeNodriver(
        browser,
        start_error=start_error,
        cleanup_error=cleanup_error,
    )
    monkeypatch.setattr(challenge.importlib, "import_module", lambda _name: nodriver)
    return nodriver


def configured_resolver(
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    **kwargs: Any,
) -> EsaSliderChallengeResolver:
    resolver = EsaSliderChallengeResolver(
        wait_seconds=1,
        drag_steps=4,
        drag_duration_ms=40,
        random_source=random.Random(7),
        max_attempts=kwargs.pop("max_attempts", 1),
        **kwargs,
    )
    monkeypatch.setattr(resolver, "_monotonic", clock.now)
    monkeypatch.setattr(resolver, "_sleep_ms", clock.sleep_ms)

    async def wait_for_drag_deadline(drag_started: float, target_elapsed_ms: float) -> None:
        remaining_ms = target_elapsed_ms - (clock.now() - drag_started) * 1000.0
        if remaining_ms > 0:
            await clock.sleep_ms(max(1, int(remaining_ms + 0.5)))

    monkeypatch.setattr(resolver, "_wait_for_drag_deadline", wait_for_drag_deadline)
    return resolver


def test_missing_nodriver_leaves_challenge_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> None:
        raise ImportError

    monkeypatch.setattr(challenge.importlib, "import_module", missing)

    assert not EsaSliderChallengeResolver().resolve(
        "https://example.test/", requests.Session(), (1, 1)
    )


def test_resolver_refuses_to_nest_an_existing_event_loop() -> None:
    async def invoke() -> bool:
        return EsaSliderChallengeResolver().resolve(
            "https://example.test/", requests.Session(), (1, 1)
        )

    assert not asyncio.run(invoke())


def test_resolver_retries_three_independent_attempts_before_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = EsaSliderChallengeResolver(max_attempts=3)
    attempts = 0

    async def unresolved(*_args: Any) -> bool:
        nonlocal attempts
        attempts += 1
        return False

    monkeypatch.setattr(challenge.importlib, "import_module", lambda _name: object())
    monkeypatch.setattr(resolver, "_resolve_async", unresolved)

    assert not resolver.resolve("https://example.test/", requests.Session(), (1, 1))
    assert attempts == 3


def test_resolver_stops_retrying_after_second_attempt_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = EsaSliderChallengeResolver(max_attempts=3)
    outcomes = iter((False, True, False))
    attempts = 0

    async def resolve_on_second(*_args: Any) -> bool:
        nonlocal attempts
        attempts += 1
        return next(outcomes)

    monkeypatch.setattr(challenge.importlib, "import_module", lambda _name: object())
    monkeypatch.setattr(resolver, "_resolve_async", resolve_on_second)

    assert resolver.resolve("https://example.test/", requests.Session(), (1, 1))
    assert attempts == 2


def test_esa_slider_defaults_generate_a_dense_cubic_bezier_path() -> None:
    resolver = EsaSliderChallengeResolver(random_source=random.Random(7))

    assert resolver.drag_steps == 61
    assert resolver.drag_duration_ms == 465
    assert resolver.max_attempts == 3

    samples = resolver._bezier_drag_path(30.0, 40.0, 350.0)
    x_steps = [samples[0][0] - 30.0] + [
        second[0] - first[0] for first, second in zip(samples, samples[1:], strict=False)
    ]
    assert len(samples) == resolver.drag_steps
    assert all(step > 0 for step in x_steps)
    assert samples[-1][0] == 350.0
    assert samples[-1][1] == 34.0
    assert all(first[0] <= second[0] for first, second in zip(samples, samples[1:], strict=False))


def test_cloakbrowser_drag_uses_unwrapped_mouse_when_humanize_is_enabled() -> None:
    patched_mouse = object()
    raw_mouse = object()
    page = SimpleNamespace(mouse=patched_mouse, _human_raw_mouse=raw_mouse)

    assert EsaSliderChallengeResolver._raw_playwright_mouse(page) is raw_mouse
    assert (
        EsaSliderChallengeResolver._raw_playwright_mouse(SimpleNamespace(mouse=patched_mouse))
        is patched_mouse
    )


def test_browser_response_allows_generic_security_copy_but_rejects_esa_shell() -> None:
    resolver = EsaSliderChallengeResolver()

    assert resolver._browser_text_is_clear(200, "<html>安全验证设置</html>")
    assert not resolver._browser_text_is_clear(
        200,
        '<div id="captcha-element"><div id="aliyunCaptcha-sliding-slider"></div></div>',
    )
    assert not resolver._browser_text_is_clear(403, "access denied")


def test_page_classification_distinguishes_cloudflare_and_esa() -> None:
    resolver = EsaSliderChallengeResolver()

    assert resolver._classify_page(title="Attention Required! | Cloudflare") == "cloudflare_block"
    assert (
        resolver._classify_page(
            url="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g"
        )
        == "cloudflare_waiting"
    )
    assert resolver._classify_page(marker_names=["aliyunCaptcha-sliding-slider"]) == "esa"
    assert resolver._classify_page(status_code=403, text="denied") == "http_block"


def test_playwright_page_state_logs_sanitized_frame_paths(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChallengeFrame:
        url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g?ray=do-not-log"

        async def title(self) -> str:
            return "Just a moment... private title"

    class BrowserPage:
        frames = [ChallengeFrame()]

        async def evaluate(self, _script: str) -> dict[str, object]:
            return {
                "href": "https://www.minebbs.com/login/?token=do-not-log",
                "title": "登录 | MineBBS 我的世界中文论坛",
                "readyState": "complete",
                "hasBody": True,
                "containerCount": 0,
                "descendantCount": 0,
                "markerNames": [],
            }

    monkeypatch.setenv("MC_AUTOMATION_LOG_FORMAT", "json")
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    asyncio.run(
        EsaSliderChallengeResolver._log_playwright_page_state(
            BrowserPage(), expected_url="https://www.minebbs.com/login/"
        )
    )

    output = caplog.records[-1].message
    metadata = json.loads(output)["metadata"]
    assert metadata["page_class"] == "normal"
    assert metadata["frame_classes"] == ["cloudflare_waiting"]
    assert metadata["frame_urls"] == [
        "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g"
    ]
    assert "do-not-log" not in output
    assert "private title" not in output


def test_page_is_not_clear_while_cloudflare_turnstile_frame_is_active() -> None:
    class ChallengeFrame:
        url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/f"

        async def title(self) -> str:
            return "Just a moment..."

        async def evaluate(self, _script: str) -> dict[str, object]:
            return {"slider": False}

    class BrowserPage:
        frames = [ChallengeFrame()]

        async def content(self) -> str:
            return "<html><body>normal outer shell</body></html>"

        async def evaluate(self, _script: str) -> dict[str, object]:
            return {
                "slider": False,
                "title": "登录 | MineBBS 我的世界中文论坛",
                "href": "https://www.minebbs.com/login/",
                "readyState": "complete",
                "hasBody": True,
            }

    assert not asyncio.run(
        EsaSliderChallengeResolver._page_is_clear(
            BrowserPage(), expected_url="https://www.minebbs.com/login/"
        )
    )


def test_turnstile_click_uses_one_visible_control_and_a_humanized_approach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Mouse:
        def __init__(self) -> None:
            self.events: list[tuple[str, float | None, float | None]] = []

        async def move(self, x: float, y: float) -> None:
            self.events.append(("move", x, y))

        async def down(self) -> None:
            self.events.append(("down", None, None))

        async def up(self) -> None:
            self.events.append(("up", None, None))

    class Locator:
        first: Locator

        def __init__(self) -> None:
            self.first = self

        async def count(self) -> int:
            return 1

        async def bounding_box(self, *, timeout: int) -> dict[str, float]:
            assert timeout == 500
            return {"x": 120.0, "y": 240.0, "width": 280.0, "height": 65.0}

    class ChallengeFrame:
        url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/f"

        async def title(self) -> str:
            return "Just a moment..."

        def locator(self, selector: str) -> Locator:
            assert selector == "label.ctp-checkbox-label"
            return Locator()

    mouse = Mouse()
    page = SimpleNamespace(
        frames=[ChallengeFrame()],
        mouse=mouse,
        _human_raw_mouse=mouse,
        viewport_size={"width": 1280, "height": 720},
    )
    clock = FakeClock()
    resolver = EsaSliderChallengeResolver(
        wait_seconds=1,
        random_source=random.Random(37),
    )
    monkeypatch.setattr(resolver, "_monotonic", clock.now)
    monkeypatch.setattr(resolver, "_sleep_ms", clock.sleep_ms)

    async def wait_for_deadline(_started: float, target_elapsed_ms: float) -> None:
        clock.elapsed_ms = max(clock.elapsed_ms, round(target_elapsed_ms))

    monkeypatch.setattr(resolver, "_wait_for_drag_deadline", wait_for_deadline)

    assert asyncio.run(resolver._click_turnstile_playwright(page))

    assert [event[0] for event in mouse.events].count("move") == resolver.TURNSTILE_APPROACH_STEPS
    assert [event[0] for event in mouse.events][-2:] == ["down", "up"]
    assert clock.elapsed_ms >= resolver.TURNSTILE_APPROACH_DURATION_MS


def test_turnstile_control_discovery_rejects_ambiguous_matches() -> None:
    class Locator:
        first: Locator

        async def count(self) -> int:
            return 2

        async def bounding_box(self, *, timeout: int) -> dict[str, float]:
            raise AssertionError(f"ambiguous control must not be measured: {timeout}")

    class ChallengeFrame:
        url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/f"

        async def title(self) -> str:
            return "Just a moment..."

        def locator(self, _selector: str) -> Locator:
            return Locator()

    page = SimpleNamespace(frames=[ChallengeFrame()])

    assert asyncio.run(EsaSliderChallengeResolver()._find_turnstile_control(page)) is None


def test_browser_transport_refuses_cross_origin_after_origin_is_bound() -> None:
    resolver = EsaSliderChallengeResolver()
    resolver._browser_origin = "https://www.minebbs.com/login/"

    assert (
        resolver.browser_request(
            "GET",
            "https://example.test/steal",
            requests.Session(),
            (1, 1),
        )
        is None
    )


def test_browser_transport_retries_safe_methods_but_never_replays_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = EsaSliderChallengeResolver(max_attempts=3)
    calls: list[str] = []

    async def unresolved(
        _cloakbrowser: object,
        method: str,
        _url: str,
        _session: requests.Session,
        _timeout: tuple[float, float],
        _request_kwargs: dict[str, Any],
    ) -> None:
        calls.append(method)

    fake_cloakbrowser = SimpleNamespace(launch_persistent_context_async=object())
    monkeypatch.setattr(challenge.importlib, "import_module", lambda _name: fake_cloakbrowser)
    monkeypatch.setattr(resolver, "_browser_request_cloak_async", unresolved)

    assert (
        resolver.browser_request("HEAD", "https://www.minebbs.com/", requests.Session(), (1, 1))
        is None
    )
    assert calls == ["HEAD", "HEAD", "HEAD"]

    calls.clear()
    assert (
        resolver.browser_request(
            "POST",
            "https://www.minebbs.com/login/login",
            requests.Session(),
            (1, 1),
            data={"login": "owner"},
        )
        is None
    )
    assert calls == ["POST"]


def test_browser_get_refetch_rejects_a_403_before_business_parsing() -> None:
    class BrowserPage:
        url = "https://www.minebbs.com/login/"
        frames: list[object] = []

        async def goto(self, *_args: object, **_kwargs: object) -> object:
            return object()

        async def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        async def content(self) -> str:
            return "<html><body>normal-looking shell</body></html>"

        async def evaluate(
            self, script: str, payload: dict[str, object] | None = None
        ) -> dict[str, object]:
            if payload is not None:
                return {
                    "status": 403,
                    "url": self.url,
                    "headers": {"content-type": "text/html"},
                    "text": "access denied",
                }
            if "containerCount" in script:
                return {
                    "href": self.url,
                    "readyState": "complete",
                    "hasBody": True,
                    "containerCount": 0,
                    "descendantCount": 0,
                    "markerNames": [],
                }
            return {
                "slider": False,
                "title": "登录 | MineBBS 我的世界中文论坛",
                "href": self.url,
                "readyState": "complete",
                "hasBody": True,
            }

    response = asyncio.run(
        EsaSliderChallengeResolver()._browser_get_response(
            BrowserPage(),
            "GET",
            "https://www.minebbs.com/login/",
            (1, 1),
            {},
        )
    )

    assert response is None


def test_browser_get_refetch_returns_the_chromium_response() -> None:
    class BrowserPage:
        url = "https://www.minebbs.com/login/"
        frames: list[object] = []

        async def goto(self, *_args: object, **_kwargs: object) -> object:
            return object()

        async def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        async def content(self) -> str:
            return "<html><body>ready</body></html>"

        async def evaluate(
            self, script: str, payload: dict[str, object] | None = None
        ) -> dict[str, object]:
            if payload is not None:
                assert payload["method"] == "GET"
                return {
                    "status": 200,
                    "url": self.url,
                    "headers": {"content-type": "text/html; charset=utf-8"},
                    "text": '<form action="/login/login"><input name="password"></form>',
                }
            if "containerCount" in script:
                return {
                    "href": self.url,
                    "readyState": "complete",
                    "hasBody": True,
                    "containerCount": 0,
                    "descendantCount": 0,
                    "markerNames": [],
                }
            return {
                "slider": False,
                "title": "登录 | MineBBS 我的世界中文论坛",
                "href": self.url,
                "readyState": "complete",
                "hasBody": True,
            }

    response = asyncio.run(
        EsaSliderChallengeResolver()._browser_get_response(
            BrowserPage(),
            "GET",
            "https://www.minebbs.com/login/",
            (1, 1),
            {},
        )
    )

    assert response is not None
    assert response.status_code == 200
    assert 'action="/login/login"' in response.text


def test_playwright_wait_detects_cloudflare_auto_clear_without_esa_slider() -> None:
    class AutoClearingPage:
        frames: list[object] = []

        def __init__(self) -> None:
            self.content_reads = 0
            self.locator_reads = 0

        async def content(self) -> str:
            self.content_reads += 1
            if self.content_reads == 1:
                return "<title>Attention Required! | Cloudflare</title>"
            return "<html><body>ready</body></html>"

        async def evaluate(self, _script: str) -> dict[str, object]:
            return {
                "slider": False,
                "title": "登录 | MineBBS 我的世界中文论坛",
                "href": "https://www.minebbs.com/login/",
                "readyState": "complete",
                "hasBody": True,
            }

        def locator(self, _selector: str) -> object:
            self.locator_reads += 1
            raise AssertionError("ESA geometry must not be queried after Cloudflare auto-clears")

    page = AutoClearingPage()

    async def invoke() -> bool:
        resolver = EsaSliderChallengeResolver(wait_seconds=1)
        assert not await resolver._page_is_clear(
            page, expected_url="https://www.minebbs.com/login/"
        )
        return await resolver._drag_slider_playwright(
            page, expected_url="https://www.minebbs.com/login/"
        )

    cleared = asyncio.run(invoke())

    assert cleared
    assert page.content_reads == 2
    assert page.locator_reads == 0


def test_bezier_path_has_a_smooth_bounded_vertical_curve() -> None:
    resolver = EsaSliderChallengeResolver(random_source=random.Random(19))

    samples = resolver._bezier_drag_path(30.0, 40.0, 350.0)
    offsets = [y - 40.0 for _x, y in samples]

    assert all(-7.0 <= offset <= 0.0 for offset in offsets)
    assert (
        max(abs(second - first) for first, second in zip(offsets, offsets[1:], strict=False)) < 1.0
    )


def test_bezier_path_is_reproducible_with_an_injected_random_source() -> None:
    first = EsaSliderChallengeResolver(random_source=random.Random(23))
    second = EsaSliderChallengeResolver(random_source=random.Random(23))

    assert first._bezier_drag_path(30.0, 40.0, 350.0) == second._bezier_drag_path(30.0, 40.0, 350.0)


def test_humanized_deadlines_are_irregular_monotonic_and_bounded() -> None:
    resolver = EsaSliderChallengeResolver(random_source=random.Random(29))

    deadlines = resolver._humanized_drag_deadlines(61)
    intervals = [deadlines[0]] + [
        second - first for first, second in zip(deadlines, deadlines[1:], strict=False)
    ]

    assert deadlines[-1] == resolver.drag_duration_ms
    assert all(first < second for first, second in zip(deadlines, deadlines[1:], strict=False))
    assert max(intervals) - min(intervals) > 1.0


def test_page_is_clear_when_passive_marker_remains_after_slider_navigation() -> None:
    class NavigatedTab:
        async def get_content(self) -> str:
            return "<html><script>const passive = 'aliyunCaptcha';</script></html>"

        async def evaluate(self, _script: str, *, return_by_value: bool) -> str:
            assert return_by_value
            return json.dumps(
                {
                    "slider": False,
                    "title": "MineBBS 我的世界中文论坛",
                    "href": "https://example.test/threads/example.1/",
                    "readyState": "complete",
                    "hasBody": True,
                }
            )

    assert asyncio.run(
        EsaSliderChallengeResolver._page_is_clear(
            NavigatedTab(), expected_url="https://example.test/login/login"
        )
    )


def test_page_is_clear_when_login_page_contains_generic_security_copy() -> None:
    class LoginTab:
        frames: list[object] = []
        evaluated_script = ""

        async def content(self) -> str:
            return "<html><body>安全验证设置</body></html>"

        async def evaluate(self, script: str) -> dict[str, object]:
            self.evaluated_script = script
            return {
                "slider": False,
                "title": "登录 | MineBBS 我的世界中文论坛",
                "href": "https://example.test/login/login?u_atoken=redacted",
                "readyState": "complete",
                "hasBody": True,
            }

    tab = LoginTab()
    assert asyncio.run(
        EsaSliderChallengeResolver._page_is_clear(
            tab, expected_url="https://example.test/login/login"
        )
    )
    assert tab.evaluated_script.endswith("hasBody:!!document.body})")


def test_page_is_not_clear_when_real_esa_title_remains_without_slider_dom() -> None:
    class ChallengeTab:
        frames: list[object] = []

        async def content(self) -> str:
            return "<html><body>安全验证</body></html>"

        async def evaluate(self, _script: str) -> dict[str, object]:
            return {
                "slider": False,
                "title": "滑动验证页面",
                "href": "https://example.test/login/login",
                "readyState": "complete",
                "hasBody": True,
            }

    assert not asyncio.run(
        EsaSliderChallengeResolver._page_is_clear(
            ChallengeTab(), expected_url="https://example.test/login/login"
        )
    )


def test_page_is_not_clear_when_browser_is_still_on_blank_page() -> None:
    class BlankTab:
        async def get_content(self) -> str:
            return "<html></html>"

        async def evaluate(self, _script: str, *, return_by_value: bool) -> str:
            assert return_by_value
            return json.dumps(
                {
                    "slider": False,
                    "title": "",
                    "href": "about:blank",
                    "readyState": "complete",
                    "hasBody": True,
                }
            )

    assert not asyncio.run(
        EsaSliderChallengeResolver._page_is_clear(
            BlankTab(), expected_url="https://example.test/login/login"
        )
    )


def test_page_is_not_clear_when_a_different_challenge_replaces_esa() -> None:
    class DeniedTab:
        async def get_content(self) -> str:
            return "<title>Attention Required! | Cloudflare</title>"

        async def evaluate(self, _script: str, *, return_by_value: bool) -> str:
            raise AssertionError("non-ESA challenges must fail before DOM fallback")

    assert not asyncio.run(
        EsaSliderChallengeResolver._page_is_clear(
            DeniedTab(), expected_url="https://example.test/login/login"
        )
    )


def test_manual_sample_profile_preserves_probe_frames_and_endpoint_jumps() -> None:
    resolver = EsaSliderChallengeResolver()

    progress = resolver._manual_sample_progress(61)
    deadlines = resolver._humanized_drag_deadlines(61)

    assert progress[:3] == pytest.approx([0.0, 1 / 441, 2 / 441])
    assert progress[-3:] == pytest.approx([326 / 441, 440 / 441, 1.0])
    assert deadlines[:3] == pytest.approx([7.0, 104.0, 108.0])
    assert deadlines[-3:] == pytest.approx([340.0, 463.0, 465.0])


def test_nodriver_resolver_uses_dom_geometry_and_copies_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    nodriver = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        clock=clock,
    )
    session = requests.Session()
    session.cookies.set("existing", "one", domain="example.test", path="/")
    resolver = configured_resolver(
        monkeypatch,
        clock,
        browser_executable_path="C:/browser/chrome.exe",
    )

    assert resolver.resolve("https://example.test/", session, (1, 1))

    events = nodriver.browser.tab.events
    assert [event["type"] for event in events[: resolver.APPROACH_STEPS]] == [
        "mouseMoved"
    ] * resolver.APPROACH_STEPS
    assert [event["type"] for event in events[resolver.APPROACH_STEPS :]] == [
        "mousePressed",
        "mouseMoved",
        "mouseMoved",
        "mouseMoved",
        "mouseMoved",
    ]
    press = events[resolver.APPROACH_STEPS]
    approach = events[: resolver.APPROACH_STEPS]
    held_moves = events[resolver.APPROACH_STEPS + 1 :]
    press_x, press_y = press["x"], press["y"]
    assert 18.8 <= press_x <= 41.2
    assert 31.2 <= press_y <= 48.8
    grab_offset_x = press_x - 10.0
    clamp_end_x = 10.0 + 200.0 - 40.0 + grab_offset_x
    assert clamp_end_x + 160.0 * 0.36 <= held_moves[-1]["x"] <= clamp_end_x + 160.0 * 0.40
    assert press["button"] == FakeInput.MouseButton.LEFT
    assert press["buttons"] == 1
    assert approach[0]["x"] > press_x
    assert approach[0]["y"] < press_y
    assert (approach[-1]["x"], approach[-1]["y"]) == (press_x, press_y)
    assert all(event["button"] == FakeInput.MouseButton.NONE for event in held_moves)
    assert all(event["buttons"] == 1 for event in held_moves)
    assert all(event["buttons"] == 0 for event in events[: resolver.APPROACH_STEPS])
    drag_points = [(event["x"], event["y"]) for event in held_moves]
    assert all(press_x <= x <= clamp_end_x + 160.0 * 0.40 for x, _y in drag_points)
    assert all(press_y - 10.0 <= y <= press_y for _x, y in drag_points)
    movement_sleeps = clock.sleeps[-resolver.drag_steps :]
    assert len(movement_sleeps) == resolver.drag_steps
    assert sum(movement_sleeps) == resolver.drag_duration_ms
    assert len(set(movement_sleeps)) > 1
    assert nodriver.browser.tab.selects == [
        (resolver.HANDLE_SELECTOR, 1),
        (resolver.TRACK_SELECTOR, 1),
    ]
    assert nodriver.start_kwargs["headless"] is False
    assert nodriver.start_kwargs["expert"] is False
    assert nodriver.start_kwargs["browser_executable_path"] == "C:/browser/chrome.exe"
    profile = Path(str(nodriver.start_kwargs["user_data_dir"]))
    assert profile.is_absolute()
    assert profile.name.startswith("mc-automation-esa-browser-")
    assert nodriver.browser.cookies.received[0]["name"] == "existing"
    assert nodriver.browser.tab.evaluations[-1] == ("navigator.userAgent", True)
    assert session.cookies.get("esa_clearance", domain="example.test") == "ok"
    assert session.headers["User-Agent"] == "esa-nodriver"
    assert nodriver.browser.stopped


def test_nodriver_drag_schedule_subtracts_cdp_call_overhead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        clock=clock,
        move_cost_ms=3,
    )
    resolver = configured_resolver(monkeypatch, clock)

    assert resolver.resolve("https://example.test/", requests.Session(), (1, 1))

    movement_sleeps = clock.sleeps[-resolver.drag_steps :]
    assert len(movement_sleeps) == resolver.drag_steps
    assert sum(movement_sleeps) + (resolver.drag_steps - 1) * 3 == resolver.drag_duration_ms
    assert clock.elapsed_ms >= resolver.APPROACH_DURATION_MS + resolver.drag_duration_ms + 3


def test_nodriver_sends_each_move_only_after_its_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    nodriver = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        clock=clock,
        move_cost_ms=3,
    )
    resolver = configured_resolver(monkeypatch, clock)
    deadlines = [5.0, 18.0, 29.0, 40.0]
    monkeypatch.setattr(
        resolver,
        "_humanized_drag_deadlines",
        lambda _point_count, *, duration_ms: deadlines,
    )

    assert resolver.resolve("https://example.test/", requests.Session(), (1, 1))

    drag_started_ms = nodriver.browser.tab.event_times_ms[resolver.APPROACH_STEPS]
    move_times = [
        sent_at - drag_started_ms
        for event, sent_at in zip(
            nodriver.browser.tab.events,
            nodriver.browser.tab.event_times_ms,
            strict=True,
        )
        if event["type"] == "mouseMoved" and event["buttons"] == 1
    ]
    assert move_times == [5, 18, 29, 40]
    assert all(sent_at >= deadline for sent_at, deadline in zip(move_times, deadlines, strict=True))
    assert clock.elapsed_ms == drag_started_ms + deadlines[-1] + 3


def test_nodriver_resolver_fails_closed_without_slider_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodriver = install_fake_nodriver(monkeypatch, None)

    assert not EsaSliderChallengeResolver(wait_seconds=1, max_attempts=1).resolve(
        "https://example.test/", requests.Session(), (1, 1)
    )
    assert nodriver.browser.tab.events == []
    assert nodriver.browser.stopped


def test_nodriver_resolver_rejects_invalid_track_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodriver = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        track_position=FakePosition(x=10, y=20, width=40, height=40),
    )

    assert not EsaSliderChallengeResolver(wait_seconds=1, max_attempts=1).resolve(
        "https://example.test/", requests.Session(), (1, 1)
    )
    assert nodriver.browser.tab.events == []


def test_nodriver_resolver_does_not_sync_a_failed_drag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    nodriver = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        clears=False,
        clock=clock,
    )
    session = requests.Session()
    resolver = configured_resolver(monkeypatch, clock)

    assert not resolver.resolve("https://example.test/", session, (1, 1))

    assert session.cookies.get("esa_clearance", domain="example.test") is None
    assert session.headers.get("User-Agent") != "esa-nodriver"
    assert nodriver.browser.stopped


def test_nodriver_closes_browser_without_injecting_other_events_when_move_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    nodriver = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        clock=clock,
        fail_drag_move=2,
    )
    resolver = configured_resolver(monkeypatch, clock)

    assert not resolver.resolve("https://example.test/", requests.Session(), (1, 1))

    assert {event["type"] for event in nodriver.browser.tab.events} <= {
        "mousePressed",
        "mouseMoved",
    }
    assert nodriver.browser.stopped


def test_nodriver_start_and_navigation_failures_are_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_failure = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        start_error=FileNotFoundError("missing browser"),
    )
    resolver = EsaSliderChallengeResolver()
    monkeypatch.setattr(resolver, "_fallback_browser_executable", lambda: None)
    start_failure.start_errors = [FileNotFoundError("missing browser")] * 3
    assert not resolver.resolve("https://example.test/", requests.Session(), (1, 1))
    assert len(start_failure.start_calls) == 3
    assert start_failure.util.calls == []

    navigation_failure = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        navigation_error=RuntimeError("navigation failed"),
    )
    assert not EsaSliderChallengeResolver().resolve(
        "https://example.test/", requests.Session(), (1, 1)
    )
    assert navigation_failure.browser.stopped


def test_nodriver_falls_back_to_edge_when_chrome_discovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodriver = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        start_error=FileNotFoundError("chrome missing"),
    )
    nodriver.browser.tab.contents = ["ready"]
    resolver = EsaSliderChallengeResolver(wait_seconds=0)
    monkeypatch.setattr(
        resolver,
        "_fallback_browser_executable",
        lambda: "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    )

    assert resolver.resolve("https://example.test/", requests.Session(), (1, 1))
    normalized_calls = [
        {key: value for key, value in call.items() if key != "user_data_dir"}
        for call in nodriver.start_calls
    ]
    assert normalized_calls == [
        {"headless": False, "expert": False},
        {
            "headless": False,
            "expert": False,
            "browser_executable_path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        },
    ]


def test_nodriver_cleanup_failure_prevents_session_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
        cleanup_error=RuntimeError("cleanup failed"),
        stop_error=RuntimeError("stop failed"),
    )
    session = requests.Session()

    assert not EsaSliderChallengeResolver(wait_seconds=1).resolve(
        "https://example.test/", session, (1, 1)
    )
    assert session.cookies.get("esa_clearance", domain="example.test") is None
    assert session.headers.get("User-Agent") != "esa-nodriver"


def test_nodriver_cleanup_timeout_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodriver = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
    )
    nodriver.browser.tab.contents = ["ready"]
    nodriver.util = HangingFakeUtil()
    resolver = EsaSliderChallengeResolver(wait_seconds=0)
    monkeypatch.setattr(resolver, "BROWSER_CLEANUP_TIMEOUT_SECONDS", 0.01)

    assert not resolver.resolve("https://example.test/", requests.Session(), (1, 1))
    assert nodriver.browser.stopped


def test_nodriver_removes_a_managed_temporary_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodriver = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
    )
    nodriver.browser.tab.contents = ["ready"]

    assert EsaSliderChallengeResolver(wait_seconds=0).resolve(
        "https://example.test/", requests.Session(), (1, 1)
    )
    profile = Path(str(nodriver.start_kwargs["user_data_dir"]))
    assert not profile.exists()


def test_already_clear_page_syncs_without_mouse_input(monkeypatch: pytest.MonkeyPatch) -> None:
    nodriver = install_fake_nodriver(
        monkeypatch,
        FakePosition(x=10, y=20, width=40, height=40),
    )
    nodriver.browser.tab.contents = ["ready"]
    session = requests.Session()

    assert EsaSliderChallengeResolver(wait_seconds=1).resolve(
        "https://example.test/", session, (1, 1)
    )

    assert nodriver.browser.tab.events == []
    assert session.cookies.get("esa_clearance", domain="example.test") == "ok"
