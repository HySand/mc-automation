from __future__ import annotations

import re
from collections.abc import Iterable

CHALLENGE_MARKERS = (
    "aliyunCaptcha",
    "滑动验证页面",
    "请拖动滑块",
    "拖动滑块完成验证",
    "向右滑动完成验证",
    "esa-captcha",
    "esa slider",
    "验证您是真人",
    "cf-chl-",
    "Attention Required! | Cloudflare",
    "Just a moment...",
    "Checking your browser",
    "Verify you are human",
    "captcha-element",
    "安全验证",
)


def detect_security_challenge(status_code: int, text: str) -> str | None:
    sample = text[:200_000]
    if status_code in {401, 403, 429}:
        return f"HTTP {status_code} security or access restriction"
    for marker in CHALLENGE_MARKERS:
        if marker.casefold() in sample.casefold():
            return f"security challenge marker: {marker}"
    return None


def redact(text: str, secrets: Iterable[str]) -> str:
    redacted = text
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        redacted = redacted.replace(secret, "***")
    redacted = re.sub(
        r"(?i)(password|passwd|cookie|authorization|token|formhash)(\s*[=:]\s*)[^\s,;]+",
        r"\1\2***",
        redacted,
    )
    return redacted
