from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from mc_automation.ai_solver import (
    AISolverConfig,
    AISolverError,
    OpenAICompatibleVisionSolver,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = "response text"

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("bad status")


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        timeout: float,
    ) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.responses.pop(0)


def config() -> AISolverConfig:
    return AISolverConfig(
        enabled=True,
        endpoint="https://ai.example.test/v1",
        api_key="secret-key",
        model="vision-model",
        timeout_seconds=12,
        max_attempts=1,
        wdsjfwq_captcha_enabled=True,
    )


def chat_payload(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


def test_wdsjfwq_captcha_solver_builds_openai_compatible_vision_request() -> None:
    client = FakeClient([FakeResponse(chat_payload('{"code":"AB12","confidence":0.91}'))])
    solver = OpenAICompatibleVisionSolver(config(), client=client)

    result = solver.solve_wdsjfwq_captcha(b"image", content_type="image/png")

    assert result.code == "AB12"
    assert result.confidence == 0.91
    call = client.calls[0]
    assert call["url"] == "https://ai.example.test/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer secret-key"
    assert call["timeout"] == 12
    assert call["json"]["model"] == "vision-model"
    image_url = call["json"]["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url == "data:image/png;base64,aW1hZ2U="


def test_solver_fails_closed_on_non_json_model_output() -> None:
    client = FakeClient([FakeResponse(chat_payload("captcha is AB12"))])
    solver = OpenAICompatibleVisionSolver(config(), client=client)

    with pytest.raises(AISolverError, match="request failed"):
        solver.solve_wdsjfwq_captcha(b"image")
