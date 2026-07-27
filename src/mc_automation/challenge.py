from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import json
import os
import random
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from .security import detect_security_challenge
from .step_log import log_step


class EsaSliderChallengeResolver:
    """Clear an Alibaba ESA challenge with the free CloakBrowser Chromium build."""

    HANDLE_SELECTOR = "#aliyunCaptcha-sliding-slider"
    TRACK_SELECTOR = "#aliyunCaptcha-sliding-wrapper"
    CLEAR_PAGE_MARKERS = ("aliyunCaptcha", "captcha-element", "安全验证")
    CHALLENGE_TITLES = ("滑动验证页面",)
    HORIZONTAL_GRAB_RANGE = (0.22, 0.78)
    VERTICAL_GRAB_RANGE = (0.28, 0.72)
    END_OVERSHOOT_RANGE = (0.36, 0.40)
    APPROACH_STEPS = 232
    APPROACH_DURATION_MS = 1196
    MANUAL_X_PROGRESS = (
        0,
        1,
        2,
        3,
        4,
        5,
        7,
        9,
        11,
        13,
        17,
        22,
        27,
        31,
        34,
        39,
        45,
        50,
        58,
        64,
        71,
        77,
        84,
        93,
        100,
        107,
        113,
        120,
        127,
        134,
        143,
        149,
        155,
        162,
        168,
        174,
        181,
        185,
        192,
        197,
        203,
        210,
        217,
        223,
        229,
        236,
        243,
        251,
        258,
        265,
        273,
        280,
        287,
        295,
        302,
        308,
        315,
        321,
        326,
        440,
        441,
    )
    MANUAL_DEADLINES_MS = (
        7,
        104,
        108,
        112,
        116,
        119,
        124,
        128,
        132,
        136,
        140,
        144,
        149,
        153,
        157,
        161,
        165,
        169,
        173,
        178,
        182,
        186,
        190,
        194,
        199,
        203,
        207,
        211,
        215,
        219,
        224,
        228,
        232,
        236,
        240,
        244,
        248,
        253,
        257,
        261,
        265,
        269,
        274,
        278,
        282,
        286,
        290,
        294,
        299,
        303,
        307,
        311,
        315,
        319,
        323,
        328,
        332,
        336,
        340,
        463,
        465,
    )
    BROWSER_CLEANUP_TIMEOUT_SECONDS = 5.0
    DEFAULT_MAX_ATTEMPTS = 3

    def __init__(
        self,
        *,
        wait_seconds: float = 15.0,
        drag_steps: int = 61,
        drag_duration_ms: int = 465,
        headless: bool = False,
        browser_executable_path: str | Path | None = None,
        random_source: random.Random | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.wait_seconds = max(0.0, wait_seconds)
        self.drag_steps = max(1, drag_steps)
        self.drag_duration_ms = max(self.drag_steps, drag_duration_ms)
        self.headless = headless
        self.browser_executable_path = (
            str(browser_executable_path) if browser_executable_path is not None else None
        )
        self._random = random_source or random.Random()
        self.max_attempts = max(1, max_attempts)
        self._browser_origin: str | None = None

    def resolve(
        self,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool:
        """Run bounded independent browser attempts from the synchronous transport boundary."""

        log_step(
            "esa_resolution",
            site="minebbs",
            status="started",
            url=url,
            headless=self.headless,
            max_attempts=self.max_attempts,
        )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # The HTTP transport is synchronous. Refuse to nest an event loop instead of
            # trying to schedule a second browser attempt in a caller-owned loop.
            log_step(
                "esa_event_loop_check",
                site="minebbs",
                status="failed",
                resolved=False,
            )
            return False
        return self._resolve_without_running_loop(url, session, timeout)

    def browser_request(
        self,
        method: str,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
        **kwargs: Any,
    ) -> requests.Response | None:
        """Execute a MineBBS request with Chromium's network stack after a challenged safe GET."""

        current_method = method.upper()
        if current_method not in {"GET", "HEAD", "POST"}:
            return None
        if self._browser_origin is None:
            self._browser_origin = url
        elif not self._is_same_origin(url, self._browser_origin):
            return None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return None

        os.environ.setdefault("CLOAKBROWSER_AUTO_UPDATE", "false")
        try:
            cloakbrowser = importlib.import_module("cloakbrowser")
        except ImportError:
            return None
        if not hasattr(cloakbrowser, "launch_persistent_context_async"):
            return None

        attempt_limit = 1 if current_method == "POST" else self.max_attempts
        log_step(
            "esa_browser_transport",
            site="minebbs",
            status="started",
            method=current_method,
            url=url,
            max_attempts=attempt_limit,
        )
        for attempt in range(1, attempt_limit + 1):
            try:
                response = asyncio.run(
                    self._browser_request_cloak_async(
                        cloakbrowser,
                        current_method,
                        url,
                        session,
                        timeout,
                        kwargs,
                    )
                )
            except Exception as exc:
                log_step(
                    "esa_browser_transport",
                    site="minebbs",
                    status="failed",
                    method=current_method,
                    url=url,
                    attempt=attempt,
                    max_attempts=attempt_limit,
                    exception_type=type(exc).__name__,
                )
                continue
            if response is not None:
                log_step(
                    "esa_browser_transport",
                    site="minebbs",
                    status="completed",
                    method=current_method,
                    url=url,
                    attempt=attempt,
                    max_attempts=attempt_limit,
                    status_code=response.status_code,
                )
                return response
        return None

    async def _browser_request_cloak_async(
        self,
        cloakbrowser: Any,
        method: str,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
        request_kwargs: dict[str, Any],
    ) -> requests.Response | None:
        context: Any = None
        profile_path = Path(tempfile.mkdtemp(prefix="mc-automation-esa-browser-request-"))
        browser_response: requests.Response | None = None
        browser_session: tuple[list[Any], str] | None = None
        cleanup_ok = False
        try:
            context = await cloakbrowser.launch_persistent_context_async(
                str(profile_path),
                headless=self.headless,
                humanize=True,
                human_preset="careful",
            )
            cookies = self._request_cookies_for_playwright(session, url)
            if cookies:
                await context.add_cookies(cookies)
            page = await context.new_page()
            if method in {"GET", "HEAD"}:
                browser_response = await self._browser_get_response(
                    page,
                    method,
                    url,
                    timeout,
                    request_kwargs,
                )
            else:
                browser_response = await self._browser_post_response(
                    page,
                    method,
                    url,
                    timeout,
                    request_kwargs,
                )
            if browser_response is not None:
                browser_session = await self._read_playwright_session(context, page)
        finally:
            if context is not None:
                try:
                    await asyncio.wait_for(
                        context.close(), timeout=self.BROWSER_CLEANUP_TIMEOUT_SECONDS
                    )
                except Exception:
                    cleanup_ok = False
                else:
                    cleanup_ok = True
            else:
                cleanup_ok = True
            cleanup_ok = cleanup_ok and await self._remove_managed_profile(profile_path)
        if browser_response is None or browser_session is None or not cleanup_ok:
            return None
        cookies, user_agent = browser_session
        self._copy_browser_session(cookies, user_agent, session)
        return browser_response

    async def _browser_get_response(
        self,
        page: Any,
        method: str,
        url: str,
        timeout: tuple[float, float],
        request_kwargs: dict[str, Any],
    ) -> requests.Response | None:
        prepared = requests.Request(
            method,
            url,
            params=request_kwargs.get("params"),
            headers=request_kwargs.get("headers"),
        ).prepare()
        prepared_url = str(prepared.url or url)
        navigation = await page.goto(
            prepared_url,
            wait_until="domcontentloaded",
            timeout=round(max(timeout) * 1000),
        )
        await self._settle_playwright_page(page)
        cleared = await self._page_is_clear(page, expected_url=prepared_url)
        if not cleared:
            cleared = await self._drag_slider_playwright(page) and await self._wait_until_clear(
                page, expected_url=prepared_url
            )
        if not cleared:
            return None
        headers = await navigation.all_headers() if navigation is not None else {}
        status_code = int(navigation.status) if navigation is not None else 200
        text = "" if method == "HEAD" else await page.content()
        return self._build_browser_response(
            method,
            page.url,
            status_code,
            headers,
            text,
        )

    async def _browser_post_response(
        self,
        page: Any,
        method: str,
        url: str,
        timeout: tuple[float, float],
        request_kwargs: dict[str, Any],
    ) -> requests.Response | None:
        parsed = urlsplit(url)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        await page.goto(
            origin,
            wait_until="domcontentloaded",
            timeout=round(max(timeout) * 1000),
        )
        await self._settle_playwright_page(page)
        cleared = await self._page_is_clear(page, expected_url=origin)
        if not cleared:
            cleared = await self._drag_slider_playwright(page) and await self._wait_until_clear(
                page, expected_url=origin
            )
        if not cleared:
            return None

        prepared = requests.Request(
            method,
            url,
            params=request_kwargs.get("params"),
            data=request_kwargs.get("data"),
            json=request_kwargs.get("json"),
            headers=request_kwargs.get("headers"),
        ).prepare()
        blocked_headers = {
            "accept-encoding",
            "connection",
            "content-length",
            "cookie",
            "host",
            "user-agent",
        }
        headers = {
            name: value
            for name, value in prepared.headers.items()
            if name.casefold() not in blocked_headers
        }
        body = prepared.body
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        payload = {
            "url": str(prepared.url or url),
            "method": method,
            "headers": headers,
            "body": body,
        }
        result = await asyncio.wait_for(
            page.evaluate(
                """async payload => {
                  const init = {
                    method: payload.method,
                    headers: payload.headers,
                    credentials: 'include',
                    redirect: 'follow'
                  };
                  if (payload.body !== null) init.body = payload.body;
                  const response = await fetch(payload.url, init);
                  return {
                    status: response.status,
                    url: response.url,
                    headers: Object.fromEntries(response.headers.entries()),
                    text: await response.text()
                  };
                }""",
                payload,
            ),
            timeout=max(timeout),
        )
        if not isinstance(result, dict):
            return None
        final_url = str(result.get("url", ""))
        text = str(result.get("text", ""))
        status_code = int(result.get("status", 0))
        if not self._is_same_origin(final_url, url) or not self._browser_text_is_clear(
            status_code, text
        ):
            return None
        result_headers = result.get("headers", {})
        return self._build_browser_response(
            method,
            final_url,
            status_code,
            result_headers if isinstance(result_headers, dict) else {},
            text,
        )

    @classmethod
    def _browser_text_is_clear(cls, status_code: int, text: str) -> bool:
        challenge = detect_security_challenge(status_code, text)
        if challenge is None:
            return True
        if not any(marker.casefold() in challenge.casefold() for marker in cls.CLEAR_PAGE_MARKERS):
            return False
        lowered = text.casefold()
        return not any(
            marker.casefold() in lowered
            for marker in (
                cls.HANDLE_SELECTOR.removeprefix("#"),
                'id="captcha-element"',
                "id='captcha-element'",
                *cls.CHALLENGE_TITLES,
            )
        )

    @staticmethod
    def _build_browser_response(
        method: str,
        url: str,
        status_code: int,
        headers: dict[str, Any],
        text: str,
    ) -> requests.Response:
        response = requests.Response()
        response.status_code = status_code
        response.url = url
        response.headers.update({str(name): str(value) for name, value in headers.items()})
        response.encoding = requests.utils.get_encoding_from_headers(response.headers) or "utf-8"
        response._content = text.encode(response.encoding, errors="replace")
        response.request = requests.Request(method, url).prepare()
        return response

    def _resolve_without_running_loop(
        self,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool:
        # The pinned free build must not start its background updater. Besides changing
        # reproducibility, that daemon can crash Python during process shutdown on Linux.
        os.environ.setdefault("CLOAKBROWSER_AUTO_UPDATE", "false")
        try:
            browser_module = importlib.import_module("cloakbrowser")
        except ImportError:
            # Keep the legacy test seam available while deployments migrate to CloakBrowser.
            try:
                browser_module = importlib.import_module("nodriver")
            except ImportError:
                log_step(
                    "esa_cloakbrowser_import",
                    site="minebbs",
                    status="failed",
                    resolved=False,
                )
                return False
        log_step("esa_cloakbrowser_import", site="minebbs", status="completed")

        try:
            for attempt in range(1, self.max_attempts + 1):
                log_step(
                    "esa_attempt",
                    site="minebbs",
                    status="started",
                    url=url,
                    attempt=attempt,
                    max_attempts=self.max_attempts,
                )
                try:
                    resolved = asyncio.run(
                        self._resolve_async(browser_module, url, session, timeout)
                    )
                except Exception as exc:
                    log_step(
                        "esa_attempt",
                        site="minebbs",
                        status="failed",
                        url=url,
                        attempt=attempt,
                        max_attempts=self.max_attempts,
                        resolved=False,
                        exception_type=type(exc).__name__,
                    )
                    continue
                log_step(
                    "esa_attempt",
                    site="minebbs",
                    status="completed" if resolved else "failed",
                    url=url,
                    attempt=attempt,
                    max_attempts=self.max_attempts,
                    resolved=resolved,
                )
                if resolved:
                    log_step(
                        "esa_resolution",
                        site="minebbs",
                        status="completed",
                        url=url,
                        resolved=True,
                        attempts=attempt,
                        max_attempts=self.max_attempts,
                    )
                    return True
            log_step(
                "esa_resolution",
                site="minebbs",
                status="failed",
                url=url,
                resolved=False,
                attempts=self.max_attempts,
                max_attempts=self.max_attempts,
            )
            return False
        except Exception as exc:
            # Browser availability and protocol errors are deliberately fail-closed. The
            # transport will classify the unresolved challenge as manual intervention.
            log_step(
                "esa_resolution",
                site="minebbs",
                status="failed",
                url=url,
                resolved=False,
                exception_type=type(exc).__name__,
            )
            return False

    async def _resolve_async(
        self,
        browser_module: Any,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool:
        if hasattr(browser_module, "launch_persistent_context_async"):
            return await self._resolve_cloak_async(browser_module, url, session, timeout)

        # Keep the old fake-compatible branch for contract tests and local migration tools.
        nodriver = browser_module
        browser: Any = None
        browser_session: tuple[list[Any], str] | None = None
        cleanup_ok = False
        profile_path = Path(tempfile.mkdtemp(prefix="mc-automation-esa-browser-"))
        try:
            start_kwargs: dict[str, Any] = {
                "user_data_dir": str(profile_path),
                "headless": self.headless,
                # Keep nodriver's optional expert/debug patches disabled. This resolver only
                # uses the public browser protocol and never changes fingerprint signals.
                "expert": False,
            }
            if self.browser_executable_path:
                start_kwargs["browser_executable_path"] = self.browser_executable_path
            log_step(
                "esa_browser_start",
                site="minebbs",
                status="started",
                headless=self.headless,
                browser_fallback=False,
            )
            try:
                browser = await nodriver.start(**start_kwargs)
            except FileNotFoundError:
                fallback = (
                    None if self.browser_executable_path else self._fallback_browser_executable()
                )
                if fallback is None:
                    raise
                start_kwargs["browser_executable_path"] = fallback
                log_step(
                    "esa_browser_start",
                    site="minebbs",
                    status="retrying",
                    headless=self.headless,
                    browser_fallback=True,
                )
                browser = await nodriver.start(**start_kwargs)
            log_step("esa_browser_start", site="minebbs", status="completed")

            request_cookies = self._request_cookies(session, url, nodriver.cdp)
            log_step(
                "esa_request_session",
                site="minebbs",
                status="completed",
                cookie_count=len(request_cookies),
            )
            if request_cookies:
                await browser.cookies.set_all(request_cookies)

            navigation_timeout = max(timeout)
            log_step(
                "esa_navigation",
                site="minebbs",
                status="started",
                url=url,
                navigation_timeout_ms=round(navigation_timeout * 1000),
            )
            tab = await asyncio.wait_for(browser.get(url), timeout=navigation_timeout)
            log_step("esa_navigation", site="minebbs", status="completed", url=url)
            await self._settle_tab(tab)
            cleared = await self._page_is_clear(tab, expected_url=url)
            log_step(
                "esa_challenge_check",
                site="minebbs",
                status="completed",
                already_clear=cleared,
                resolved=cleared,
            )
            if not cleared:
                cleared = await self._drag_slider(
                    tab, nodriver.cdp
                ) and await self._wait_until_clear(tab, expected_url=url)
            if cleared:
                browser_session = await self._read_browser_session(browser, tab)
        finally:
            if browser is not None:
                cleanup_ok = await self._close_browser(nodriver, browser, profile_path)
            else:
                cleanup_ok = await self._remove_managed_profile(profile_path)
            log_step(
                "esa_browser_cleanup",
                site="minebbs",
                status="completed" if cleanup_ok else "failed",
                cleanup_ok=cleanup_ok,
            )

        if browser_session is None or not cleanup_ok:
            return False
        cookies, user_agent = browser_session
        self._copy_browser_session(cookies, user_agent, session)
        log_step(
            "esa_session_sync",
            site="minebbs",
            status="completed",
            cookie_count=len(cookies),
            session_synced=True,
        )
        return True

    async def _resolve_cloak_async(
        self,
        cloakbrowser: Any,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool:
        context: Any = None
        profile_path = Path(tempfile.mkdtemp(prefix="mc-automation-esa-browser-"))
        browser_session: tuple[list[Any], str] | None = None
        cleanup_ok = False
        try:
            log_step("esa_browser_start", site="minebbs", status="started", headless=self.headless)
            context = await cloakbrowser.launch_persistent_context_async(
                str(profile_path),
                headless=self.headless,
                humanize=True,
                human_preset="careful",
            )
            log_step("esa_browser_start", site="minebbs", status="completed")
            cookies = self._request_cookies_for_playwright(session, url)
            if cookies:
                await context.add_cookies(cookies)
            page = await context.new_page()
            navigation_timeout = max(timeout)
            log_step(
                "esa_navigation",
                site="minebbs",
                status="started",
                url=url,
                navigation_timeout_ms=round(navigation_timeout * 1000),
            )
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=round(navigation_timeout * 1000),
            )
            log_step("esa_navigation", site="minebbs", status="completed", url=url)
            await self._settle_playwright_page(page)
            await self._log_playwright_page_state(page, expected_url=url)
            cleared = await self._page_is_clear(page, expected_url=url)
            log_step(
                "esa_challenge_check",
                site="minebbs",
                status="completed",
                already_clear=cleared,
                resolved=cleared,
            )
            if not cleared:
                cleared = await self._drag_slider_playwright(page) and await self._wait_until_clear(
                    page, expected_url=url
                )
            if cleared:
                browser_session = await self._read_playwright_session(context, page)
        except Exception as exc:
            log_step(
                "esa_cloakbrowser",
                site="minebbs",
                status="failed",
                exception_type=type(exc).__name__,
            )
        finally:
            if context is not None:
                try:
                    await asyncio.wait_for(
                        context.close(), timeout=self.BROWSER_CLEANUP_TIMEOUT_SECONDS
                    )
                except Exception:
                    cleanup_ok = False
                else:
                    cleanup_ok = True
            else:
                cleanup_ok = True
            cleanup_ok = cleanup_ok and await self._remove_managed_profile(profile_path)
            log_step(
                "esa_browser_cleanup",
                site="minebbs",
                status="completed" if cleanup_ok else "failed",
                cleanup_ok=cleanup_ok,
            )
        if browser_session is None or not cleanup_ok:
            return False
        cookies, user_agent = browser_session
        self._copy_browser_session(cookies, user_agent, session)
        log_step(
            "esa_session_sync",
            site="minebbs",
            status="completed",
            cookie_count=len(cookies),
            session_synced=True,
        )
        return True

    async def _drag_slider_playwright(self, page: Any) -> bool:
        deadline = self._monotonic() + self.wait_seconds
        frame: Any = None
        handle_box: Any = None
        track_box: Any = None
        while self._monotonic() <= deadline:
            for candidate in [page, *getattr(page, "frames", [])]:
                try:
                    handle_box = await candidate.locator(self.HANDLE_SELECTOR).first.bounding_box(
                        timeout=250
                    )
                    track_box = await candidate.locator(self.TRACK_SELECTOR).first.bounding_box(
                        timeout=250
                    )
                except Exception:
                    continue
                if handle_box and track_box:
                    frame = candidate
                    break
            if frame is not None:
                break
            await self._sleep_ms(250)
        if frame is None or handle_box is None or track_box is None:
            await self._log_playwright_page_state(page)
            log_step("esa_dom_geometry", site="minebbs", status="failed", resolved=False)
            return False
        mouse = self._raw_playwright_mouse(page)
        handle_width = float(handle_box["width"])
        handle_height = float(handle_box["height"])
        track_width = float(track_box["width"])
        if handle_width <= 0 or handle_height <= 0 or track_width <= handle_width:
            log_step("esa_dom_geometry", site="minebbs", status="failed", resolved=False)
            return False
        grab_offset_x = handle_width * self._random.uniform(*self.HORIZONTAL_GRAB_RANGE)
        grab_offset_y = handle_height * self._random.uniform(*self.VERTICAL_GRAB_RANGE)
        start_x = float(handle_box["x"]) + grab_offset_x
        start_y = float(handle_box["y"]) + grab_offset_y
        clamp_end_x = float(track_box["x"]) + track_width - handle_width + grab_offset_x
        distance = clamp_end_x - start_x
        overshoot = distance * self._random.uniform(*self.END_OVERSHOOT_RANGE)
        drag_path = self._bezier_drag_path(
            start_x,
            start_y,
            clamp_end_x + overshoot,
            point_count=self._randomized_drag_steps(),
            vertical_radius=min(8.0, max(2.0, handle_height * 0.15)),
            progress_values=self._manual_sample_progress(self._randomized_drag_steps()),
        )
        approach = self._bezier_drag_path(
            start_x + track_width * 1.35,
            start_y - handle_height * 2.7,
            start_x,
            end_y=start_y,
            point_count=self.APPROACH_STEPS,
            vertical_radius=handle_height,
        )
        approach_started = self._monotonic()
        for step, (x, y) in enumerate(approach, 1):
            await self._wait_for_drag_deadline(
                approach_started, self.APPROACH_DURATION_MS * step / len(approach)
            )
            await mouse.move(x, y)
        await mouse.down()
        drag_started = self._monotonic()
        deadlines = self._humanized_drag_deadlines(
            len(drag_path), duration_ms=self.drag_duration_ms
        )
        for step, (x, y) in enumerate(drag_path, 1):
            await self._wait_for_drag_deadline(drag_started, deadlines[step - 1])
            await mouse.move(x, y)
        log_step(
            "esa_drag",
            site="minebbs",
            status="completed",
            path_points=len(drag_path),
            duration_ms=self.drag_duration_ms,
            distance=round(distance, 2),
        )
        return True

    @staticmethod
    def _raw_playwright_mouse(page: Any) -> Any:
        """Avoid expanding every sampled ESA point into another humanized trajectory."""

        return getattr(page, "_human_raw_mouse", page.mouse)

    @staticmethod
    async def _settle_playwright_page(page: Any) -> None:
        await page.wait_for_timeout(500)

    @classmethod
    async def _log_playwright_page_state(
        cls, page: Any, *, expected_url: str | None = None
    ) -> None:
        try:
            state = await page.evaluate(
                """() => {
                  const containers = [...document.querySelectorAll(
                    '#captcha-element,#h5_captcha-element,#waf_nc_block,#waf_nc_h5_block'
                  )];
                  const names = [...document.querySelectorAll('[id],[class]')]
                    .flatMap(e => [e.id, ...e.classList])
                    .filter(v => v && /captcha|slider|slide|track|drag|aliyun|waf|nc-/i.test(v));
                  return {
                    href: location.href,
                    readyState: document.readyState,
                    hasBody: !!document.body,
                    containerCount: containers.length,
                    descendantCount: containers.reduce(
                      (n, e) => n + e.querySelectorAll('*').length, 0
                    ),
                    markerNames: [...new Set(names)].slice(0, 20)
                  };
                }"""
            )
        except Exception as exc:
            log_step(
                "esa_page_state",
                site="minebbs",
                status="failed",
                exception_type=type(exc).__name__,
                frame_count=len(getattr(page, "frames", [])),
            )
            return
        href = str(state.get("href", "")) if isinstance(state, dict) else ""
        log_step(
            "esa_page_state",
            site="minebbs",
            status="observed",
            frame_count=len(getattr(page, "frames", [])),
            container_count=int(state.get("containerCount", 0)),
            descendant_count=int(state.get("descendantCount", 0)),
            marker_names=state.get("markerNames", []),
            ready_state=str(state.get("readyState", "")),
            has_body=bool(state.get("hasBody")),
            final_origin_matches=(
                cls._is_same_origin(href, expected_url) if expected_url is not None else None
            ),
        )

    @staticmethod
    async def _read_playwright_session(context: Any, page: Any) -> tuple[list[Any], str]:
        browser_cookies = await context.cookies()
        return browser_cookies, await page.evaluate("navigator.userAgent")

    @staticmethod
    def _request_cookies_for_playwright(
        session: requests.Session, url: str
    ) -> list[dict[str, Any]]:
        hostname = urlsplit(url).hostname or ""
        return [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain or hostname,
                "path": c.path or "/",
                "secure": bool(c.secure),
            }
            for c in session.cookies
        ]

    async def _drag_slider(self, tab: Any, cdp: Any) -> bool:
        try:
            handle = await tab.select(self.HANDLE_SELECTOR, timeout=self.wait_seconds)
            track = await tab.select(self.TRACK_SELECTOR, timeout=self.wait_seconds)
        except Exception as exc:
            log_step(
                "esa_dom_geometry",
                site="minebbs",
                status="failed",
                exception_type=type(exc).__name__,
            )
            return False
        if handle is None or track is None:
            log_step("esa_dom_geometry", site="minebbs", status="failed", resolved=False)
            return False

        try:
            handle_position = await handle.get_position()
            track_position = await track.get_position()
        except Exception as exc:
            log_step(
                "esa_dom_geometry",
                site="minebbs",
                status="failed",
                exception_type=type(exc).__name__,
            )
            return False
        if handle_position is None or track_position is None:
            log_step("esa_dom_geometry", site="minebbs", status="failed", resolved=False)
            return False

        handle_width = float(handle_position.width)
        handle_height = float(handle_position.height)
        track_width = float(track_position.width)
        log_step(
            "esa_dom_geometry",
            site="minebbs",
            status="completed",
            handle_width=round(handle_width, 2),
            handle_height=round(handle_height, 2),
            track_width=round(track_width, 2),
        )
        if handle_width <= 0 or handle_height <= 0 or track_width <= handle_width:
            return False

        grab_offset_x = handle_width * self._random.uniform(*self.HORIZONTAL_GRAB_RANGE)
        grab_offset_y = handle_height * self._random.uniform(*self.VERTICAL_GRAB_RANGE)
        start_x = float(handle_position.x) + grab_offset_x
        start_y = float(handle_position.y) + grab_offset_y
        clamp_end_x = float(track_position.x) + track_width - handle_width + grab_offset_x
        if clamp_end_x <= start_x:
            return False

        distance = clamp_end_x - start_x
        overshoot = distance * self._random.uniform(*self.END_OVERSHOOT_RANGE)
        point_count = self._randomized_drag_steps()
        duration_ms = self._randomized_drag_duration(point_count)
        progress_values = self._manual_sample_progress(point_count)
        drag_path = self._bezier_drag_path(
            start_x,
            start_y,
            clamp_end_x + overshoot,
            point_count=point_count,
            vertical_radius=min(8.0, max(2.0, handle_height * 0.15)),
            progress_values=progress_values,
        )
        drag_deadlines = self._humanized_drag_deadlines(len(drag_path), duration_ms=duration_ms)
        log_step(
            "esa_drag",
            site="minebbs",
            status="started",
            path_points=len(drag_path),
            duration_ms=duration_ms,
            distance=round(distance, 2),
        )
        approach_path = self._bezier_drag_path(
            start_x + track_width * 1.35,
            start_y - handle_height * 2.7,
            start_x,
            end_y=start_y,
            point_count=self.APPROACH_STEPS,
            vertical_radius=handle_height,
        )
        approach_started = self._monotonic()
        for step, (x, y) in enumerate(approach_path, 1):
            await self._wait_for_drag_deadline(
                approach_started,
                self.APPROACH_DURATION_MS * step / len(approach_path),
            )
            await self._dispatch_mouse_event(
                tab,
                cdp,
                "mouseMoved",
                x,
                y,
                button=cdp.input_.MouseButton.NONE,
                buttons=0,
            )

        await self._dispatch_mouse_event(
            tab,
            cdp,
            "mousePressed",
            start_x,
            start_y,
            button=cdp.input_.MouseButton.LEFT,
            buttons=1,
            click_count=1,
        )
        drag_started = self._monotonic()
        # Schedule every held point against the successful manual trace. Waiting for
        # each acknowledgement prevents Chromium from coalescing several CDP moves.
        for step, (x, y) in enumerate(drag_path, 1):
            target_elapsed_ms = drag_deadlines[step - 1]
            await self._wait_for_drag_deadline(drag_started, target_elapsed_ms)
            await self._dispatch_mouse_event(
                tab,
                cdp,
                "mouseMoved",
                x,
                y,
                button=cdp.input_.MouseButton.NONE,
                buttons=1,
            )
        log_step(
            "esa_drag",
            site="minebbs",
            status="completed",
            path_points=len(drag_path),
            duration_ms=duration_ms,
            distance=round(distance, 2),
        )
        return True

    async def _wait_for_drag_deadline(self, drag_started: float, target_elapsed_ms: float) -> None:
        deadline = drag_started + target_elapsed_ms / 1000.0
        remaining = deadline - self._monotonic()
        if remaining > 0.012:
            await asyncio.sleep(remaining - 0.006)
        # Windows' default asyncio timer commonly rounds 3-5 ms waits into 15.6 ms
        # batches. A short bounded spin preserves the sampled event cadence.
        while self._monotonic() < deadline:
            pass

    async def _dispatch_mouse_event(
        self,
        tab: Any,
        cdp: Any,
        event_type: str,
        x: float,
        y: float,
        *,
        button: Any,
        buttons: int,
        click_count: int | None = None,
    ) -> None:
        command = cdp.input_.dispatch_mouse_event(
            event_type,
            x,
            y,
            button=button,
            buttons=buttons,
            click_count=click_count,
            pointer_type="mouse",
        )
        await tab.send(command)

    async def _sleep_ms(self, milliseconds: int) -> None:
        if milliseconds > 0:
            await asyncio.sleep(milliseconds / 1000.0)

    async def _settle_tab(self, tab: Any) -> None:
        """Give dynamic challenge markup one bounded render tick before classification."""

        wait = getattr(tab, "wait", None)
        if callable(wait):
            await wait(min(0.5, self.wait_seconds))

    @staticmethod
    def _monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def _fallback_browser_executable() -> str | None:
        """Find Microsoft Edge when nodriver's Chrome/Chromium discovery has no match."""

        for command in ("msedge", "microsoft-edge", "microsoft-edge-stable"):
            candidate = shutil.which(command)
            if candidate:
                return candidate
        if os.name != "nt":
            return None
        roots = (
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("LOCALAPPDATA"),
        )
        for root in roots:
            if not root:
                continue
            candidate_path = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate_path.is_file():
                return str(candidate_path)
        return None

    def _bezier_drag_path(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        *,
        point_count: int | None = None,
        vertical_radius: float = 4.0,
        progress_values: Sequence[float] | None = None,
        end_y: float | None = None,
    ) -> list[tuple[float, float]]:
        """Build a monotonic cubic Bezier path between the sampled pointer positions."""

        distance = end_x - start_x
        if distance == 0:
            return [(end_x, start_y)]
        samples = max(1, self.drag_steps if point_count is None else point_count)
        radius = max(0.0, vertical_radius)

        if end_y is None:
            resolved_end_y = start_y - radius * 1.5
            control_1_y = start_y - radius * 2.28
            control_2_y = start_y - radius * 1.01
        else:
            resolved_end_y = end_y
            control_1_y = start_y + (resolved_end_y - start_y) * 0.25
            control_2_y = resolved_end_y - radius * 0.25
        control_1 = (start_x + distance / 3.0, control_1_y)
        control_2 = (start_x + distance * 2.0 / 3.0, control_2_y)

        path: list[tuple[float, float]] = []
        progress = progress_values or tuple(step / samples for step in range(1, samples + 1))
        if len(progress) != samples:
            raise ValueError("progress_values must match point_count")
        for t in progress:
            inverse = 1.0 - t
            x = (
                inverse**3 * start_x
                + 3 * inverse**2 * t * control_1[0]
                + 3 * inverse * t**2 * control_2[0]
                + t**3 * end_x
            )
            y = (
                inverse**3 * start_y
                + 3 * inverse**2 * t * control_1[1]
                + 3 * inverse * t**2 * control_2[1]
                + t**3 * resolved_end_y
            )
            path.append((x, y))
        return path

    def _randomized_drag_steps(self) -> int:
        return self.drag_steps

    def _randomized_drag_duration(self, point_count: int) -> int:
        return max(point_count, self.drag_duration_ms)

    @classmethod
    def _manual_sample_progress(cls, point_count: int) -> list[float]:
        return cls._interpolate_profile(
            tuple(value / cls.MANUAL_X_PROGRESS[-1] for value in cls.MANUAL_X_PROGRESS),
            point_count,
        )

    @staticmethod
    def _interpolate_profile(profile: Sequence[float], point_count: int) -> list[float]:
        if point_count <= 0:
            return []
        if point_count == 1:
            return [float(profile[-1])]
        if point_count == len(profile):
            return [float(value) for value in profile]
        last_index = len(profile) - 1
        values: list[float] = []
        for index in range(point_count):
            position = index * last_index / (point_count - 1)
            lower = int(position)
            upper = min(last_index, lower + 1)
            fraction = position - lower
            values.append(float(profile[lower] + (profile[upper] - profile[lower]) * fraction))
        return values

    def _humanized_drag_deadlines(
        self, point_count: int, *, duration_ms: int | None = None
    ) -> list[float]:
        """Return irregular absolute deadlines that still end at the configured budget."""

        if point_count <= 0:
            return []
        total_duration = self.drag_duration_ms if duration_ms is None else duration_ms
        if point_count < 8:
            intervals = [self._random.uniform(0.72, 1.28) for _index in range(point_count)]
            total = sum(intervals)
            elapsed = 0.0
            deadlines: list[float] = []
            for interval in intervals:
                elapsed += total_duration * interval / total
                deadlines.append(elapsed)
            deadlines[-1] = float(total_duration)
            return deadlines
        normalized = tuple(
            value / self.MANUAL_DEADLINES_MS[-1] for value in self.MANUAL_DEADLINES_MS
        )
        return [
            value * total_duration for value in self._interpolate_profile(normalized, point_count)
        ]

    async def _wait_until_clear(self, tab: Any, *, expected_url: str) -> bool:
        deadline = self._monotonic() + self.wait_seconds
        while True:
            if await self._page_is_clear(tab, expected_url=expected_url):
                return True
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            await self._sleep_ms(min(500, max(1, int(remaining * 1000))))

    @classmethod
    async def _page_is_clear(cls, tab: Any, *, expected_url: str) -> bool:
        content_method = getattr(tab, "content", None)
        try:
            if callable(content_method):
                content = await content_method()
            else:
                content = await tab.get_content()
        except Exception:
            return False
        challenge = detect_security_challenge(200, content)
        if challenge is not None and not any(
            marker.casefold() in challenge.casefold() for marker in cls.CLEAR_PAGE_MARKERS
        ):
            return False
        script = (
            f"({{slider:!!document.querySelector({cls.HANDLE_SELECTOR!r}),"
            "title:document.title,href:location.href,readyState:document.readyState,"
            "hasBody:!!document.body})"
        )
        try:
            if callable(content_method):
                parsed = await tab.evaluate(script)
                for frame in getattr(tab, "frames", []):
                    try:
                        frame_state = await frame.evaluate(
                            f"({{slider:!!document.querySelector({cls.HANDLE_SELECTOR!r})}})"
                        )
                    except Exception:
                        continue
                    if isinstance(frame_state, dict) and frame_state.get("slider"):
                        parsed = {**parsed, "slider": True}
                        break
            else:
                state = await tab.evaluate("JSON.stringify(" + script + ")", return_by_value=True)
                parsed = json.loads(state) if isinstance(state, str) else None
        except Exception:
            return False
        if not isinstance(parsed, dict):
            return False
        title = str(parsed.get("title", ""))
        href = str(parsed.get("href", ""))
        ready_state = str(parsed.get("readyState", ""))
        return (
            not bool(parsed.get("slider"))
            and not any(marker in title for marker in cls.CHALLENGE_TITLES)
            and ready_state != "loading"
            and bool(parsed.get("hasBody"))
            and cls._is_same_origin(href, expected_url)
        )

    @staticmethod
    def _is_same_origin(current_url: str, expected_url: str) -> bool:
        try:
            current = urlsplit(current_url)
            expected = urlsplit(expected_url)
            current_port = current.port or (443 if current.scheme.lower() == "https" else 80)
            expected_port = expected.port or (443 if expected.scheme.lower() == "https" else 80)
        except ValueError:
            return False
        return (
            current.scheme.lower() in {"http", "https"}
            and current.scheme.lower() == expected.scheme.lower()
            and (current.hostname or "").lower() == (expected.hostname or "").lower()
            and current_port == expected_port
        )

    @staticmethod
    def _request_cookies(session: requests.Session, url: str, cdp: Any) -> list[Any]:
        hostname = urlsplit(url).hostname or ""
        cookies: list[Any] = []
        for cookie in session.cookies:
            cookies.append(
                cdp.network.CookieParam(
                    name=cookie.name,
                    value=cookie.value,
                    domain=cookie.domain or hostname,
                    path=cookie.path or "/",
                    secure=bool(cookie.secure),
                )
            )
        return cookies

    @staticmethod
    async def _read_browser_session(browser: Any, tab: Any) -> tuple[list[Any], str]:
        cookies = await browser.cookies.get_all(requests_cookie_format=True)
        user_agent = await tab.evaluate("navigator.userAgent", return_by_value=True)
        return cookies, user_agent if isinstance(user_agent, str) else ""

    @staticmethod
    def _copy_browser_session(
        cookies: Sequence[Any], user_agent: str, session: requests.Session
    ) -> None:
        for cookie in cookies:
            if isinstance(cookie, dict):
                session.cookies.set(
                    str(cookie["name"]),
                    str(cookie["value"]),
                    domain=str(cookie.get("domain", "")),
                    path=str(cookie.get("path", "/")),
                    secure=bool(cookie.get("secure", False)),
                )
            else:
                session.cookies.set_cookie(cookie)
        if user_agent:
            session.headers["User-Agent"] = user_agent

    @staticmethod
    async def _close_browser(nodriver: Any, browser: Any, profile_path: Path) -> bool:
        process = getattr(browser, "_process", None)
        cleanup_ok = True
        try:
            cleanup = nodriver.util.deconstruct_browser(browser)
            if inspect.isawaitable(cleanup):
                await asyncio.wait_for(
                    cleanup,
                    timeout=EsaSliderChallengeResolver.BROWSER_CLEANUP_TIMEOUT_SECONDS,
                )
        except Exception:
            cleanup_ok = False
            # deconstruct_browser is the normal path; stop is a last-resort process cleanup
            # when the browser socket has already failed.
            with contextlib.suppress(Exception):
                browser.stop()
        await EsaSliderChallengeResolver._wait_for_browser_process(process)
        profile_ok = await EsaSliderChallengeResolver._remove_managed_profile(profile_path)
        return cleanup_ok and profile_ok

    @staticmethod
    async def _wait_for_browser_process(process: Any) -> None:
        if process is None:
            return
        # subprocess.Popen.wait() is synchronous and can block the event loop forever.
        # Poll instead; asyncio subprocesses expose returncode directly.
        poll = getattr(process, "poll", None)
        for _attempt in range(20):
            if callable(poll):
                with contextlib.suppress(Exception):
                    if poll() is not None:
                        return
            elif getattr(process, "returncode", None) is not None:
                return
            await asyncio.sleep(0.1)

    @staticmethod
    async def _remove_managed_profile(profile_path: Path | None) -> bool:
        if profile_path is None:
            return True
        profile_path = profile_path.resolve()
        temp_path = Path(tempfile.gettempdir()).resolve()
        if profile_path.parent != temp_path or not profile_path.name.startswith(
            ("mc-automation-esa-browser-", "uc_")
        ):
            return False
        for _attempt in range(20):
            if not profile_path.exists():
                return True
            try:
                shutil.rmtree(profile_path)
            except OSError:
                await asyncio.sleep(0.25)
            else:
                return True
        return not profile_path.exists()
