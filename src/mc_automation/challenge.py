from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import os
import random
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .security import detect_security_challenge
from .step_log import log_step


class EsaSliderChallengeResolver:
    """Clear one Alibaba ESA slide-to-end challenge using nodriver and DOM geometry."""

    HANDLE_SELECTOR = "#aliyunCaptcha-sliding-slider"
    TRACK_SELECTOR = "#aliyunCaptcha-sliding-wrapper"
    PRE_PRESS_DELAY_RANGE_MS = (90, 190)
    PRESS_SETTLE_DELAY_RANGE_MS = (4, 10)
    RELEASE_DELAY_RANGE_MS = (15, 30)
    BROWSER_CLEANUP_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        *,
        wait_seconds: float = 15.0,
        drag_steps: int = 120,
        drag_duration_ms: int = 465,
        headless: bool = False,
        browser_executable_path: str | Path | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        self.wait_seconds = max(0.0, wait_seconds)
        self.drag_steps = max(1, drag_steps)
        self.drag_duration_ms = max(self.drag_steps, drag_duration_ms)
        self.headless = headless
        self.browser_executable_path = (
            str(browser_executable_path) if browser_executable_path is not None else None
        )
        self._random = random_source or random.Random()

    def resolve(
        self,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool:
        """Run the one-shot async browser attempt from the synchronous transport boundary."""

        log_step(
            "esa_resolution",
            site="minebbs",
            status="started",
            url=url,
            headless=self.headless,
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

        try:
            nodriver = importlib.import_module("nodriver")
        except ImportError:
            log_step(
                "esa_nodriver_import",
                site="minebbs",
                status="failed",
                resolved=False,
            )
            return False
        log_step("esa_nodriver_import", site="minebbs", status="completed")

        try:
            resolved = asyncio.run(self._resolve_async(nodriver, url, session, timeout))
            log_step(
                "esa_resolution",
                site="minebbs",
                status="completed" if resolved else "failed",
                url=url,
                resolved=resolved,
            )
            return resolved
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
        nodriver: Any,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool:
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
            cleared = await self._page_is_clear(tab)
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
                ) and await self._wait_until_clear(tab)
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

        start_x = float(handle_position.x) + handle_width / 2
        start_y = float(handle_position.y) + handle_height / 2
        end_x = float(track_position.x) + track_width - handle_width / 2
        if end_x <= start_x:
            return False

        drag_path = self._humanized_drag_path(start_x, start_y, end_x)
        drag_deadlines = self._humanized_drag_deadlines(len(drag_path))
        log_step(
            "esa_drag",
            site="minebbs",
            status="started",
            path_points=len(drag_path),
            duration_ms=self.drag_duration_ms,
            distance=round(end_x - start_x, 2),
        )
        await self._dispatch_mouse_event(
            tab,
            cdp,
            "mouseMoved",
            start_x,
            start_y,
            button=cdp.input_.MouseButton.NONE,
            buttons=0,
        )
        await self._sleep_ms(self._random.randint(*self.PRE_PRESS_DELAY_RANGE_MS))
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
        try:
            await self._sleep_ms(self._random.randint(*self.PRESS_SETTLE_DELAY_RANGE_MS))
            # Schedule every point against one monotonic deadline. CDP round-trip latency
            # consumes the movement budget instead of stretching it once per point.
            drag_started = self._monotonic()
            for step, (x, y) in enumerate(drag_path, 1):
                await self._dispatch_mouse_event(
                    tab,
                    cdp,
                    "mouseMoved",
                    x,
                    y,
                    # Native mousemove reports no transition button while the buttons
                    # bitmask carries the held left button.
                    button=cdp.input_.MouseButton.NONE,
                    buttons=1,
                )
                target_elapsed_ms = drag_deadlines[step - 1]
                remaining_ms = target_elapsed_ms - (self._monotonic() - drag_started) * 1000.0
                if remaining_ms > 0:
                    await self._sleep_ms(max(1, int(remaining_ms + 0.5)))
            await self._sleep_ms(self._random.randint(*self.RELEASE_DELAY_RANGE_MS))
        finally:
            release_x, release_y = drag_path[-1]
            await self._dispatch_mouse_event(
                tab,
                cdp,
                "mouseReleased",
                release_x,
                release_y,
                button=cdp.input_.MouseButton.LEFT,
                buttons=0,
                click_count=1,
            )
        log_step(
            "esa_drag",
            site="minebbs",
            status="completed",
            path_points=len(drag_path),
            duration_ms=self.drag_duration_ms,
            distance=round(end_x - start_x, 2),
        )
        return True

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

    def _humanized_drag_path(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
    ) -> list[tuple[float, float]]:
        """Build a path shaped from the successful manual drag sample."""

        distance = end_x - start_x
        if distance <= 0:
            return [(end_x, start_y)]

        if self.drag_steps < 8:
            x_positions = [
                start_x + distance * step / self.drag_steps
                for step in range(1, self.drag_steps + 1)
            ]
        else:
            sample_count = min(self.drag_steps, max(8, round(distance / 5.25)))
            probe_count = max(3, round(sample_count * 0.1))
            regular_count = sample_count - probe_count - 2
            regular_end = start_x + distance * self._random.uniform(1.01, 1.03)
            probe_positions = [start_x + index for index in range(probe_count)]

            ramp_count = max(3, round(regular_count * 0.35))
            weights: list[float] = []
            for index in range(regular_count):
                if index < ramp_count:
                    progress = index / max(1, ramp_count - 1)
                    weight = 2.0 + 5.0 * progress
                else:
                    weight = self._random.uniform(5.5, 8.0)
                weights.append(weight)
            scale = (regular_end - probe_positions[-1]) / sum(weights)
            x_positions = list(probe_positions)
            current_x = probe_positions[-1]
            for weight in weights:
                current_x += weight * scale
                x_positions.append(current_x)

            overshoot = distance * self._random.uniform(0.35, 0.40)
            x_positions.extend((end_x + overshoot, end_x + overshoot + self._random.uniform(1, 2)))

        drift = -self._random.uniform(7.0, 10.0)
        path: list[tuple[float, float]] = []
        for index, x in enumerate(x_positions):
            progress = index / max(1, len(x_positions) - 1)
            y = start_y + drift * min(1.0, progress * 1.8) ** 0.8
            path.append((x, y))
        return path

    def _humanized_drag_deadlines(self, point_count: int) -> list[float]:
        """Return irregular absolute deadlines that still end at the configured budget."""

        if point_count <= 0:
            return []
        if point_count < 8:
            intervals = [self._random.uniform(0.72, 1.28) for _index in range(point_count)]
        else:
            intervals = [self._random.uniform(4.0, 10.0)]
            intervals.append(self._random.uniform(80.0, 110.0))
            intervals.extend(self._random.uniform(3.0, 6.0) for _index in range(point_count - 4))
            intervals.append(self._random.uniform(90.0, 130.0))
            intervals.append(self._random.uniform(2.0, 5.0))
        total = sum(intervals)
        elapsed = 0.0
        deadlines: list[float] = []
        for interval in intervals:
            elapsed += self.drag_duration_ms * interval / total
            deadlines.append(elapsed)
        deadlines[-1] = float(self.drag_duration_ms)
        return deadlines

    async def _wait_until_clear(self, tab: Any) -> bool:
        deadline = self._monotonic() + self.wait_seconds
        while True:
            if await self._page_is_clear(tab):
                return True
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            await self._sleep_ms(min(500, max(1, int(remaining * 1000))))

    @staticmethod
    async def _page_is_clear(tab: Any) -> bool:
        return detect_security_challenge(200, await tab.get_content()) is None

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
