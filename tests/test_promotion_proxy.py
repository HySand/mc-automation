from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
import requests

from mc_automation.promotion_proxy import (
    TARGET_MARKER_HEADER,
    DynamicProxyPool,
    IsolatedPromotionTarget,
    ProxyPromotionVisitor,
    ProxySource,
    normalize_http_proxy,
)
from mc_automation.transport import UnsafeTarget


@dataclass
class StubResponse:
    body: bytes
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    closed: bool = False

    @property
    def is_redirect(self) -> bool:
        return 300 <= self.status_code < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, *, chunk_size: int) -> list[bytes]:
        return [self.body]

    def close(self) -> None:
        self.closed = True


class SourceSession:
    def __init__(self, responses: dict[str, StubResponse | Exception]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}
        self.trust_env = True

    def get(self, url: str, **kwargs: Any) -> StubResponse:
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class StubCookies:
    def __init__(self) -> None:
        self.cleared = False

    def clear(self) -> None:
        self.cleared = True


class VisitSession:
    def __init__(self, response: StubResponse) -> None:
        self.response = response
        self.headers: dict[str, str] = {"Cookie": "must-be-cleared"}
        self.cookies = StubCookies()
        self.trust_env = True
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append((url, kwargs))
        return self.response

    def close(self) -> None:
        self.closed = True


class StaticPool:
    def __init__(self, proxies: tuple[str, ...]) -> None:
        self.proxies = proxies

    def load(self) -> tuple[str, ...]:
        return self.proxies


def test_normalize_http_proxy_accepts_only_public_ip_literal_endpoints() -> None:
    assert normalize_http_proxy("8.8.8.8:8080") == "http://8.8.8.8:8080"
    assert normalize_http_proxy("http://1.1.1.1:80/") == "http://1.1.1.1:80"
    assert normalize_http_proxy("http://10.0.0.1:8080") is None
    assert normalize_http_proxy("https://8.8.8.8:443") is None
    assert normalize_http_proxy("proxy.example:8080") is None


def test_dynamic_pool_isolates_source_failures_and_deduplicates_candidates() -> None:
    bad_url = "https://source.invalid/bad"
    text_url = "https://source.invalid/text"
    geonode_url = "https://source.invalid/geonode"
    session = SourceSession(
        {
            bad_url: requests.ConnectionError("offline"),
            text_url: StubResponse(b"8.8.8.8:8080\ninvalid\n8.8.8.8:8080\n"),
            geonode_url: StubResponse(
                b'{"data": [{"ip": "1.1.1.1", "port": "80"}, {"ip": "10.0.0.1", "port": 1}]}'
            ),
        }
    )
    pool = DynamicProxyPool(
        sources=(
            ProxySource("bad", bad_url),
            ProxySource("text", text_url),
            ProxySource("geonode", geonode_url, "geonode"),
        ),
        session=session,
    )

    assert pool.load() == ("http://8.8.8.8:8080", "http://1.1.1.1:80")
    assert not session.trust_env


def test_isolated_target_requires_public_ip_and_preserves_only_path_and_query() -> None:
    with pytest.raises(UnsafeTarget, match="IP 字面量"):
        IsolatedPromotionTarget("https://klpbbs.com", "crackme-marker")
    with pytest.raises(UnsafeTarget, match="公网 IP"):
        IsolatedPromotionTarget("https://192.168.1.2", "crackme-marker")

    target = IsolatedPromotionTarget("https://93.184.216.34", "crackme-marker")
    assert (
        target.map_promotion_url("https://klpbbs.com/promotion?fromuid=5#ignored")
        == "https://93.184.216.34/promotion?fromuid=5"
    )


def test_proxy_visitor_uses_each_proxy_once_without_cookies_or_redirects() -> None:
    queued_sessions = [
        VisitSession(StubResponse(b"", headers={TARGET_MARKER_HEADER: "crackme-marker"})),
        VisitSession(StubResponse(b"", headers={TARGET_MARKER_HEADER: "crackme-marker"})),
    ]
    created_sessions: list[VisitSession] = []

    def session_factory() -> VisitSession:
        session = queued_sessions.pop(0)
        created_sessions.append(session)
        return session

    visitor = ProxyPromotionVisitor(
        IsolatedPromotionTarget("https://93.184.216.34", "crackme-marker"),
        StaticPool(("http://8.8.8.8:8080", "http://1.1.1.1:80")),
        session_factory=session_factory,
    )

    assert visitor.visit("https://klpbbs.com/promotion?fromuid=5")
    assert visitor.visit("https://klpbbs.com/promotion?fromuid=5")

    first, second = created_sessions
    assert [call[0] for call in first.calls + second.calls] == [
        "https://93.184.216.34/promotion?fromuid=5",
        "https://93.184.216.34/promotion?fromuid=5",
    ]
    assert first.calls[0][1]["proxies"] == {
        "http": "http://8.8.8.8:8080",
        "https": "http://8.8.8.8:8080",
    }
    assert second.calls[0][1]["proxies"] == {
        "http": "http://1.1.1.1:80",
        "https": "http://1.1.1.1:80",
    }
    assert all(session.cookies.cleared and not session.trust_env for session in created_sessions)
    assert all("Cookie" not in session.headers and session.closed for session in created_sessions)
    assert all(
        call[1]["allow_redirects"] is False
        for session in created_sessions
        for call in session.calls
    )
    assert all(call[1]["verify"] is True for session in created_sessions for call in session.calls)


def test_proxy_visitor_stops_when_a_success_response_lacks_the_target_marker() -> None:
    session = VisitSession(StubResponse(b"landing"))
    visitor = ProxyPromotionVisitor(
        IsolatedPromotionTarget("http://93.184.216.34", "crackme-marker"),
        StaticPool(("http://8.8.8.8:8080",)),
        session_factory=lambda: session,
    )

    with pytest.raises(UnsafeTarget, match="靶场标记"):
        visitor.visit("https://klpbbs.com/promotion?fromuid=5")
