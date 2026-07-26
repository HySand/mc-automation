from __future__ import annotations

import asyncio
import ctypes
import json
import os
import tempfile
import time
from collections import Counter
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import nodriver
from dotenv import load_dotenv

from mc_automation.challenge import EsaSliderChallengeResolver
from mc_automation.security import detect_security_challenge

TARGET_URL = "https://www.minebbs.com/"
TRACE_PATH = Path(__file__).with_name("esa-manual-trace.json")
STORAGE_KEY = "mcAutomationEsaNativeTrace"

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SW_RESTORE = 9


class MouseInput(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class InputUnion(ctypes.Union):
    _fields_ = (("mi", MouseInput),)


class Input(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("union", InputUnion))


user32 = ctypes.WinDLL("user32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)


def _send_mouse(flags: int, x: int = 0, y: int = 0) -> None:
    virtual_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    virtual_y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    virtual_width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    virtual_height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    absolute_x = round((x - virtual_x) * 65535 / max(1, virtual_width - 1))
    absolute_y = round((y - virtual_y) * 65535 / max(1, virtual_height - 1))
    mouse = MouseInput(
        dx=absolute_x,
        dy=absolute_y,
        mouseData=0,
        dwFlags=flags,
        time=0,
        dwExtraInfo=None,
    )
    event = Input(type=INPUT_MOUSE, union=InputUnion(mi=mouse))
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def _descendant_pids(root_pid: int) -> set[int]:
    # Chromium can assign the visible top-level window to a child process.
    rows = os.popen(
        'powershell -NoProfile -Command "Get-CimInstance Win32_Process | '
        "Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress\""
    ).read()
    if not rows.strip():
        return {root_pid}
    parsed = json.loads(rows)
    processes = parsed if isinstance(parsed, list) else [parsed]
    children: dict[int, list[int]] = {}
    for process in processes:
        children.setdefault(int(process["ParentProcessId"]), []).append(int(process["ProcessId"]))
    descendants = {root_pid}
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, []):
            if child not in descendants:
                descendants.add(child)
                pending.append(child)
    return descendants


def _foreground_browser(root_pid: int) -> bool:
    candidate_pids = _descendant_pids(root_pid)
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(hwnd: int, _lparam: int) -> bool:
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value in candidate_pids and user32.IsWindowVisible(hwnd):
            handles.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    if not handles:
        return False
    hwnd = handles[0]
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return user32.GetForegroundWindow() == hwnd


def _drag_trace() -> list[dict[str, Any]]:
    payload = json.loads(TRACE_PATH.read_text(encoding="utf-8"))
    trace = payload["trace"]
    down = next(index for index, event in enumerate(trace) if event["type"] == "mousedown")
    up = next(
        index for index, event in enumerate(trace[down:], down) if event["type"] == "mouseup"
    )
    selected = trace[down : up + 1]
    origin_x = selected[0]["dx"]
    origin_y = selected[0]["dy"]
    origin_time = selected[0]["dt_ms"]
    return [
        {
            **event,
            "dx": event["dx"] - origin_x,
            "dy": event["dy"] - origin_y,
            "dt_ms": event["dt_ms"] - origin_time,
        }
        for event in selected
    ]


def _safe_verify_result(body: str) -> dict[str, Any]:
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end < start:
        return {"parsed": False}
    try:
        payload = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return {"parsed": False}
    result = payload.get("Result")
    result_object = result if isinstance(result, dict) else {}
    return {
        "parsed": True,
        "code": payload.get("Code"),
        "success": payload.get("Success"),
        "verify_result": result_object.get("VerifyResult"),
        "verify_code_present": "VerifyCode" in result_object,
    }


async def main() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    profile_path = Path(tempfile.mkdtemp(prefix="mc-automation-esa-browser-native-probe-"))
    start_kwargs: dict[str, object] = {
        "headless": False,
        "expert": False,
        "user_data_dir": str(profile_path),
    }
    executable = os.environ.get("MINEBBS_BROWSER_EXECUTABLE_PATH", "").strip()
    if executable:
        start_kwargs["browser_executable_path"] = executable
    try:
        browser = await nodriver.start(**start_kwargs)
    except FileNotFoundError:
        fallback = EsaSliderChallengeResolver._fallback_browser_executable()
        if fallback is None:
            print("BROWSER_NOT_FOUND", flush=True)
            return
        start_kwargs["browser_executable_path"] = fallback
        browser = await nodriver.start(**start_kwargs)

    verify_responses: list[tuple[Any, str]] = []
    try:
        tab = await browser.get(TARGET_URL)

        def on_response(event: Any) -> None:
            parsed = urlsplit(event.response.url)
            if "captcha-pro-open.aliyuncs.com" in parsed.netloc and "verify" in parsed.netloc:
                verify_responses.append((event.request_id, f"{parsed.netloc}{parsed.path}"))

        tab.add_handler(nodriver.cdp.network.ResponseReceived, on_response)
        await tab.send(nodriver.cdp.network.enable())
        handle = await tab.select(EsaSliderChallengeResolver.HANDLE_SELECTOR, timeout=15)
        track = await tab.select(EsaSliderChallengeResolver.TRACK_SELECTOR, timeout=15)
        if handle is None or track is None:
            print("SLIDER_MISSING", flush=True)
            return
        handle_box = await handle.get_position()
        track_box = await track.get_position()
        if handle_box is None or track_box is None:
            print("GEOMETRY_MISSING", flush=True)
            return
        await tab.evaluate(
            f"""
            (() => {{
              const key = {json.dumps(STORAGE_KEY)};
              localStorage.setItem(key, '[]');
              for (const type of ['mousedown', 'mousemove', 'mouseup']) {{
                document.addEventListener(type, event => {{
                  const trace = JSON.parse(localStorage.getItem(key) || '[]');
                  trace.push({{type, x:event.clientX, y:event.clientY, time:Date.now(),
                    button:event.button, buttons:event.buttons, trusted:event.isTrusted}});
                  localStorage.setItem(key, JSON.stringify(trace));
                }}, true);
              }}
            }})()
            """
        )
        encoded_metrics = await tab.evaluate(
            "JSON.stringify({screenX,screenY,outerWidth,outerHeight,innerWidth,innerHeight,devicePixelRatio})",
            return_by_value=True,
        )
        if not isinstance(encoded_metrics, str):
            print("WINDOW_METRICS_MISSING", flush=True)
            return
        metrics = json.loads(encoded_metrics)
        viewport_left = metrics["screenX"] + (metrics["outerWidth"] - metrics["innerWidth"]) / 2
        viewport_top = metrics["screenY"] + metrics["outerHeight"] - metrics["innerHeight"]
        start_client_x = float(handle_box.x) + float(handle_box.width) / 2
        start_client_y = float(handle_box.y) + float(handle_box.height) / 2
        start_screen_x = round(viewport_left + start_client_x)
        start_screen_y = round(viewport_top + start_client_y)
        root_pid = int(browser._process.pid)
        focused = _foreground_browser(root_pid)
        print(f"FOCUSED={focused}", flush=True)
        if not focused:
            return

        trace = _drag_trace()
        winmm.timeBeginPeriod(1)
        try:
            _send_mouse(
                MOUSEEVENTF_MOVE
                | MOUSEEVENTF_MOVE_NOCOALESCE
                | MOUSEEVENTF_ABSOLUTE
                | MOUSEEVENTF_VIRTUALDESK,
                start_screen_x,
                start_screen_y,
            )
            time.sleep(0.15)
            started = time.perf_counter()
            for event in trace:
                deadline = started + event["dt_ms"] / 1000.0
                while True:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    if remaining > 0.002:
                        time.sleep(remaining - 0.001)
                x = start_screen_x + int(event["dx"])
                y = start_screen_y + int(event["dy"])
                if event["type"] == "mousedown":
                    _send_mouse(MOUSEEVENTF_LEFTDOWN)
                elif event["type"] == "mousemove":
                    _send_mouse(
                        MOUSEEVENTF_MOVE
                        | MOUSEEVENTF_MOVE_NOCOALESCE
                        | MOUSEEVENTF_ABSOLUTE
                        | MOUSEEVENTF_VIRTUALDESK,
                        x,
                        y,
                    )
                else:
                    _send_mouse(MOUSEEVENTF_LEFTUP)
        finally:
            winmm.timeEndPeriod(1)

        await asyncio.sleep(8)
        encoded = await tab.evaluate(
            f"localStorage.getItem({json.dumps(STORAGE_KEY)}) || '[]'",
            return_by_value=True,
        )
        page_trace = json.loads(encoded) if isinstance(encoded, str) else []
        drag_down = next(
            (index for index, event in enumerate(page_trace) if event["type"] == "mousedown"),
            None,
        )
        actual_drag = page_trace[drag_down:] if drag_down is not None else []
        counts = Counter(event["type"] for event in actual_drag)
        if actual_drag:
            x0, y0, t0 = actual_drag[0]["x"], actual_drag[0]["y"], actual_drag[0]["time"]
            compact = [
                (event["type"], event["x"] - x0, event["y"] - y0, event["time"] - t0)
                for event in actual_drag
            ]
        else:
            compact = []
        encoded_slider_state = await tab.evaluate(
            f"""(() => {{
              const handle = document.querySelector(
                {json.dumps(EsaSliderChallengeResolver.HANDLE_SELECTOR)}
              );
              return JSON.stringify({{left: handle ? getComputedStyle(handle).left : null,
                visible: handle ? !!(
                  handle.offsetWidth || handle.offsetHeight || handle.getClientRects().length
                ) : false,
                title: document.title}});
            }})()""",
            return_by_value=True,
        )
        slider_state = (
            json.loads(encoded_slider_state) if isinstance(encoded_slider_state, str) else None
        )
        print(f"EVENT_COUNTS={dict(counts)}", flush=True)
        print(f"ACTUAL_TRACE={compact}", flush=True)
        print(f"SLIDER_STATE={slider_state}", flush=True)
        remains = not await EsaSliderChallengeResolver._page_is_clear(tab)
        print(f"CHALLENGE_REMAINS={remains}", flush=True)
        print(f"VERIFY_RESPONSE_COUNT={len(verify_responses)}", flush=True)
        for request_id, endpoint in verify_responses:
            try:
                body, _base64 = await tab.send(nodriver.cdp.network.get_response_body(request_id))
            except Exception as error:
                print(f"VERIFY={endpoint} body_unavailable={type(error).__name__}", flush=True)
            else:
                print(f"VERIFY={endpoint} {_safe_verify_result(body)}", flush=True)
        content = await tab.get_content()
        print(f"DETECTED={detect_security_challenge(200, content) is not None}", flush=True)
    finally:
        await nodriver.util.deconstruct_browser(browser)
        await EsaSliderChallengeResolver._remove_managed_profile(profile_path)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
