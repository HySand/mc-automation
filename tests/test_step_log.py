from __future__ import annotations

import json
import logging

from mc_automation.step_log import LOGGER_NAME, REDACTED, log_step, safe_url


def test_safe_url_removes_credentials_query_and_fragment() -> None:
    assert (
        safe_url("https://user:secret@example.test:8443/path?q=token#fragment")
        == "https://example.test:8443/path"
    )
    assert safe_url("not-a-url") == REDACTED
    assert safe_url("https://example.test:invalid/path") == REDACTED


def test_step_log_is_json_and_rejects_unapproved_metadata(
    caplog: object,
    monkeypatch: object,
) -> None:
    monkeypatch.setenv("MC_AUTOMATION_LOG_FORMAT", "json")  # type: ignore[attr-defined]
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)  # type: ignore[attr-defined]

    log_step(
        "example",
        site="wdsjfwq",
        status="completed",
        url="https://user:secret@example.test/path?token=abc#x",
        field_names=["server_id", "captcha"],
        password="do-not-log",
        captcha_code="Z9K2",
        body="raw-body",
    )

    output = caplog.records[-1].message  # type: ignore[attr-defined]
    payload = json.loads(output)
    assert payload["event"] == "step"
    assert payload["site"] == "wdsjfwq"
    assert payload["metadata"]["url"] == "https://example.test/path"
    assert payload["metadata"]["field_names"] == ["server_id", "captcha"]
    assert payload["metadata"]["rejected_fields"] == [REDACTED, REDACTED, REDACTED]
    assert "secret" not in output
    assert "token=abc" not in output
    assert "Z9K2" not in output
    assert "raw-body" not in output


def test_step_log_defaults_to_human_readable_and_hides_failed_proxy_noise(
    caplog: object, monkeypatch: object
) -> None:
    monkeypatch.delenv("MC_AUTOMATION_LOG_FORMAT", raising=False)  # type: ignore[attr-defined]
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)  # type: ignore[attr-defined]

    log_step(
        "promotion_task_progress",
        site="klpbbs",
        status="completed",
        progress_percent=30,
        proxy_successes=3,
        attempts=40,
    )
    log_step("promotion_proxy_visit", site="klpbbs", status="failed", action=41)

    assert len(caplog.records) == 1  # type: ignore[attr-defined]
    assert caplog.records[0].message == (  # type: ignore[attr-defined]
        "[KLPBBS] 推广进度：完成 | 进度 30% | 有效访问 3 | 已尝试 40"
    )
