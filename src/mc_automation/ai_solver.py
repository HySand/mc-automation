from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlsplit, urlunsplit

import requests

from .step_log import log_step

WDSJFWQ_CAPTCHA_PROMPT = """Read the verification code in this WDSJFWQ captcha image.

Return exactly one JSON object and no markdown:
{"code":"TEXT","confidence":0.0}

Rules:
- code must contain only the visible alphanumeric captcha characters.
- Preserve character order.
- Do not add spaces, punctuation, labels, or explanations.
- If the image is unreadable, return {"code":"","confidence":0.0}.
"""

SYSTEM_PROMPT = """You are a strict image transcription assistant. You only return valid JSON
that matches the requested schema. Never include markdown fences or natural-language
explanations outside the JSON object.
"""


class AISolverError(RuntimeError):
    """Raised when the model cannot return a safe, parseable solution."""


@dataclass(frozen=True, slots=True)
class AISolverConfig:
    enabled: bool = False
    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    timeout_seconds: float = 60.0
    max_attempts: int = 1
    wdsjfwq_captcha_enabled: bool = False


@dataclass(frozen=True, slots=True)
class CaptchaSolution:
    code: str
    confidence: float


class JsonResponse(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class ChatClient(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        timeout: float,
    ) -> JsonResponse: ...


class OpenAICompatibleVisionSolver:
    """Small OpenAI-compatible Chat Completions client for vision captcha prompts."""

    def __init__(
        self,
        config: AISolverConfig,
        *,
        client: ChatClient | None = None,
    ) -> None:
        if not config.endpoint or not config.api_key or not config.model:
            raise AISolverError("AI solver requires endpoint, API key, and model")
        self.config = config
        self.client = client or requests.Session()
        self.chat_url = self._chat_completions_url(config.endpoint)

    def solve_wdsjfwq_captcha(
        self,
        image: bytes,
        *,
        content_type: str | None = None,
    ) -> CaptchaSolution:
        payload = self._request_json(
            prompt=WDSJFWQ_CAPTCHA_PROMPT,
            image=image,
            content_type=content_type or "image/png",
        )
        code = str(payload.get("code", "")).strip()
        confidence = self._confidence(payload.get("confidence"))
        if not code:
            log_step(
                "captcha_solution",
                site="wdsjfwq",
                status="failed",
                confidence=confidence,
                code_length=0,
                format_valid=False,
            )
            raise AISolverError("AI solver returned an empty captcha code")
        log_step(
            "captcha_solution",
            site="wdsjfwq",
            status="completed",
            confidence=confidence,
            code_length=len(code),
            format_valid=code.isalnum(),
        )
        return CaptchaSolution(code=code, confidence=confidence)

    def _request_json(
        self,
        *,
        prompt: str,
        image: bytes,
        content_type: str,
    ) -> dict[str, Any]:
        data_url = self._data_url(image, content_type)
        request_body: dict[str, Any] = {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": 300,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            started = time.monotonic()
            log_step(
                "ai_request",
                site="wdsjfwq",
                status="started",
                model=self.config.model,
                image_bytes=len(image),
                content_type=content_type,
                max_attempts=self.config.max_attempts,
                action=attempt,
            )
            try:
                response = self.client.post(
                    self.chat_url,
                    headers=headers,
                    json=request_body,
                    timeout=self.config.timeout_seconds,
                )
                log_step(
                    "ai_response",
                    site="wdsjfwq",
                    status="received",
                    status_code=response.status_code,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    action=attempt,
                )
                response.raise_for_status()
                parsed = self._parse_chat_content(response.json())
                log_step(
                    "ai_response_parse",
                    site="wdsjfwq",
                    status="completed",
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    action=attempt,
                )
                return parsed
            except (requests.RequestException, AISolverError, ValueError, TypeError) as exc:
                last_error = exc
                log_step(
                    "ai_request",
                    site="wdsjfwq",
                    status="failed",
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    exception_type=type(exc).__name__,
                    action=attempt,
                )
        raise AISolverError("AI solver request failed") from last_error

    @staticmethod
    def _parse_chat_content(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AISolverError("AI solver response is not a JSON object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AISolverError("AI solver response missing choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise AISolverError("AI solver choice is invalid")
        message = first.get("message")
        if not isinstance(message, dict):
            raise AISolverError("AI solver message is invalid")
        content = message.get("content")
        if isinstance(content, list):
            text_parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            content_text = "".join(text_parts)
        elif isinstance(content, str):
            content_text = content
        else:
            raise AISolverError("AI solver content is invalid")
        try:
            parsed = json.loads(content_text.strip())
        except json.JSONDecodeError as exc:
            raise AISolverError("AI solver content is not strict JSON") from exc
        if not isinstance(parsed, dict):
            raise AISolverError("AI solver content must be a JSON object")
        return cast(dict[str, Any], parsed)

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError) as exc:
            raise AISolverError("AI solver confidence is invalid") from exc
        if confidence < 0 or confidence > 1:
            raise AISolverError("AI solver confidence must be between 0 and 1")
        return confidence

    @staticmethod
    def _data_url(image: bytes, content_type: str) -> str:
        if not image:
            raise AISolverError("AI solver image input is empty")
        encoded = base64.b64encode(image).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    @staticmethod
    def _chat_completions_url(endpoint: str) -> str:
        parsed = urlsplit(endpoint.strip())
        if not parsed.scheme or not parsed.netloc:
            raise AISolverError("AI solver endpoint must be an HTTP(S) URL")
        if parsed.path.rstrip("/").endswith("/chat/completions"):
            return endpoint.strip()
        path = parsed.path.rstrip("/")
        path = f"{path}/chat/completions" if path.endswith("/v1") else f"{path}/v1/chat/completions"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
