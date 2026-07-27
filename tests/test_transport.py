from __future__ import annotations

import json
import logging

import pytest
import requests
import responses
from requests.adapters import HTTPAdapter

from mc_automation.step_log import LOGGER_NAME
from mc_automation.transport import HttpTransport, SecurityChallenge, create_cloudscraper_session


class FakeChallengeResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(
        self,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool:
        self.calls += 1
        session.cookies.set("clearance", "ok")
        return True


@responses.activate
def test_transport_rejects_challenge_before_parser_sees_it() -> None:
    responses.get("https://example.test/", body="aliyunCaptcha", status=200)
    with pytest.raises(SecurityChallenge):
        HttpTransport(session=requests.Session()).get("https://example.test/")


@responses.activate
def test_transport_returns_normal_page() -> None:
    responses.get("https://example.test/", body="ok", status=200)
    assert HttpTransport(session=requests.Session()).get("https://example.test/").text == "ok"


@responses.activate
def test_transport_logs_request_and_response_without_query_values(
    caplog: object, monkeypatch: object
) -> None:
    monkeypatch.setenv("MC_AUTOMATION_LOG_FORMAT", "json")  # type: ignore[attr-defined]
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)  # type: ignore[attr-defined]
    responses.get("https://example.test/path?secret=value", body="private-body", status=200)

    HttpTransport(session=requests.Session(), site="example").get(
        "https://example.test/path?secret=value"
    )

    records = [json.loads(record.message) for record in caplog.records]  # type: ignore[attr-defined]
    assert [record["phase"] for record in records] == ["http_request", "http_response"]
    output = "\n".join(record.message for record in caplog.records)  # type: ignore[attr-defined]
    assert "https://example.test/path" in output
    assert "secret=value" not in output
    assert "private-body" not in output


def test_cloudscraper_session_is_usable_by_transport() -> None:
    session = create_cloudscraper_session()
    user_agent = session.headers["User-Agent"]
    adapter = session.get_adapter("https://")

    transport = HttpTransport(session=session)

    assert transport.session.headers["User-Agent"] == user_agent
    assert type(transport.session.get_adapter("https://")) is type(adapter)
    assert transport.session.get_adapter("https://").max_retries.total == 2
    assert type(adapter) is not HTTPAdapter


@responses.activate
def test_challenge_resolver_retries_get_once() -> None:
    responses.get("https://example.test/", body="cf-chl-test", status=403)
    responses.get("https://example.test/", body="ok", status=200)
    resolver = FakeChallengeResolver()
    response = HttpTransport(session=requests.Session(), challenge_resolver=resolver).get(
        "https://example.test/"
    )
    assert response.text == "ok"
    assert resolver.calls == 1


@responses.activate
def test_post_challenge_is_never_replayed() -> None:
    responses.post("https://example.test/action", body="cf-chl-test", status=403)
    resolver = FakeChallengeResolver()
    with pytest.raises(SecurityChallenge):
        HttpTransport(session=requests.Session(), challenge_resolver=resolver).post(
            "https://example.test/action"
        )
    assert resolver.calls == 0
