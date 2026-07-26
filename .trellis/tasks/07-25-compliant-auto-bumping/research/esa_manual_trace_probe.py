from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import nodriver
from dotenv import load_dotenv

from mc_automation.challenge import EsaSliderChallengeResolver
from mc_automation.security import detect_security_challenge

OUTPUT_PATH = Path(__file__).with_name("esa-manual-trace.json")
TARGET_URL = "https://www.minebbs.com/"
TRACE_STORAGE_KEY = "mcAutomationEsaManualTrace"


async def main() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    start_kwargs: dict[str, object] = {"headless": False, "expert": False}
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

    result: dict[str, object] = {"status": "timeout", "trace": []}
    try:
        tab = await browser.get(TARGET_URL)
        handle = await tab.select(EsaSliderChallengeResolver.HANDLE_SELECTOR, timeout=15)
        if handle is None:
            result["status"] = "slider_missing"
            return
        await tab.evaluate(
            f"""
            (() => {{
              const storageKey = {json.dumps(TRACE_STORAGE_KEY)};
              localStorage.setItem(storageKey, '[]');
              for (const type of ['mousedown', 'mousemove', 'mouseup']) {{
                document.addEventListener(type, event => {{
                  const trace = JSON.parse(localStorage.getItem(storageKey) || '[]');
                  trace.push({{
                    type,
                    x: event.clientX,
                    y: event.clientY,
                    time: Date.now(),
                    trusted: event.isTrusted,
                    button: event.button,
                    buttons: event.buttons
                  }});
                  localStorage.setItem(storageKey, JSON.stringify(trace));
                }}, true);
              }}
            }})()
            """
        )
        print("READY_MANUAL_SLIDE", flush=True)

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            content = await tab.get_content()
            if "验证失败，请刷新" in content:
                result["status"] = "failed"
                break
            if detect_security_challenge(200, content) is None:
                result["status"] = "cleared"
                break
            await asyncio.sleep(0.25)

        encoded = await tab.evaluate(
            f"localStorage.getItem({json.dumps(TRACE_STORAGE_KEY)}) || '[]'",
            return_by_value=True,
        )
        if isinstance(encoded, str):
            trace = json.loads(encoded)
            if trace:
                origin_x = trace[0]["x"]
                origin_y = trace[0]["y"]
                origin_time = trace[0]["time"]
                result["trace"] = [
                    {
                        "type": event["type"],
                        "dx": event["x"] - origin_x,
                        "dy": event["y"] - origin_y,
                        "dt_ms": event["time"] - origin_time,
                        "trusted": event["trusted"],
                        "button": event["button"],
                        "buttons": event["buttons"],
                    }
                    for event in trace
                ]
    finally:
        OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        await nodriver.util.deconstruct_browser(browser)
        print(f"RESULT={result['status']}", flush=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
