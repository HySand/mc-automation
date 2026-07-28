from __future__ import annotations

import json
import logging

import pytest
import requests
import responses
from requests.adapters import HTTPAdapter

from mc_automation.step_log import LOGGER_NAME
from mc_automation.transport import (
    KLPBBS_REFERENCE_USER_AGENT,
    HttpTransport,
    SecurityChallenge,
    create_cloudscraper_session,
)


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


class FakeBrowserBridgeResolver(FakeChallengeResolver):
    def __init__(self, *, failed_methods: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self.browser_calls: list[tuple[str, str]] = []
        self.failed_methods = failed_methods

    def browser_request(
        self,
        method: str,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
        **_kwargs: object,
    ) -> requests.Response | None:
        del session, timeout
        self.browser_calls.append((method, url))
        if method in self.failed_methods:
            return None
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = f"browser-{method.lower()}".encode()
        response.encoding = "utf-8"
        return response


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
    assert user_agent == KLPBBS_REFERENCE_USER_AGENT
    assert type(session.cookies).__name__ == "LWPCookieJar"
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


@responses.activate
def test_challenged_get_switches_to_browser_transport_before_any_post() -> None:
    responses.get("https://example.test/login/", body="cf-chl-test", status=403)
    resolver = FakeBrowserBridgeResolver()
    transport = HttpTransport(session=requests.Session(), challenge_resolver=resolver)

    get_response = transport.get("https://example.test/login/")
    post_response = transport.post(
        "https://example.test/login/login",
        data={"login": "owner", "password": "secret"},
    )

    assert get_response.text == "browser-get"
    assert post_response.text == "browser-post"
    assert resolver.calls == 0
    assert resolver.browser_calls == [
        ("GET", "https://example.test/login/"),
        ("POST", "https://example.test/login/login"),
    ]


@responses.activate
def test_initial_challenged_post_does_not_activate_browser_transport() -> None:
    responses.post("https://example.test/action", body="cf-chl-test", status=403)
    resolver = FakeBrowserBridgeResolver()

    with pytest.raises(SecurityChallenge):
        HttpTransport(session=requests.Session(), challenge_resolver=resolver).post(
            "https://example.test/action", data={"confirm": "1"}
        )

    assert resolver.calls == 0
    assert resolver.browser_calls == []


@responses.activate
def test_failed_browser_bridge_does_not_fall_back_to_a_second_resolver_cycle() -> None:
    responses.get("https://example.test/login/", body="cf-chl-test", status=403)
    resolver = FakeBrowserBridgeResolver(failed_methods=frozenset({"GET"}))

    with pytest.raises(SecurityChallenge):
        HttpTransport(session=requests.Session(), challenge_resolver=resolver).get(
            "https://example.test/login/"
        )

    assert resolver.calls == 0
    assert resolver.browser_calls == [("GET", "https://example.test/login/")]


@responses.activate
def test_browser_mode_does_not_replay_a_failed_post() -> None:
    responses.get("https://example.test/login/", body="cf-chl-test", status=403)
    resolver = FakeBrowserBridgeResolver(failed_methods=frozenset({"POST"}))
    transport = HttpTransport(session=requests.Session(), challenge_resolver=resolver)

    transport.get("https://example.test/login/")
    with pytest.raises(SecurityChallenge):
        transport.post("https://example.test/login/login", data={"login": "owner"})

    assert resolver.browser_calls == [
        ("GET", "https://example.test/login/"),
        ("POST", "https://example.test/login/login"),
    ]
