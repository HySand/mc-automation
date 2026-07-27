from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

import requests

from mc_automation.promotion_proxy import (
    DynamicProxyPool,
    PromotionVisitBatch,
    ProxyPromotionVisitor,
    ProxySource,
    default_proxy_sources,
    normalize_http_proxy,
)


@dataclass
class StubResponse:
    body: bytes
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    closed: bool = False
    url: str = "https://klpbbs.com/forum.php?fromuid=5"
    history: list[StubResponse] = field(default_factory=list)

    @property
    def is_redirect(self) -> bool:
        return 300 <= self.status_code < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, *, chunk_size: int) -> list[bytes]:
        return [self.body]

    @property
    def content(self) -> bytes:
        return self.body

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
    def __init__(self, response: StubResponse | list[StubResponse]) -> None:
        self.responses = response if isinstance(response, list) else [response]
        self.headers: dict[str, str] = {"Cookie": "must-be-cleared"}
        self.cookies = StubCookies()
        self.trust_env = True
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

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


def test_default_sources_prioritize_fresh_checked_proxy_lists() -> None:
    names = [source.name for source in default_proxy_sources()]

    assert names.index("proxifly-http") < names.index("openproxylist-https")
    assert names.index("proxifly-http") < names.index("checkerproxy")
    assert names.index("openproxylist-https") < names.index("checkerproxy")
    assert names.index("yakumo-http-checked") < names.index("checkerproxy")
    assert names.index("kangproxy-https") < names.index("checkerproxy")
    assert names.index("openproxylist-https") < names.index("proxyscrape")
    assert names.index("yakumo-http-checked") < names.index("proxyscrape")
    assert names.index("kangproxy-https") < names.index("proxyscrape")


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
        random_source=random.Random(0),
    )

    assert set(pool.load()) == {"http://8.8.8.8:8080", "http://1.1.1.1:80"}
    assert not session.trust_env


def test_dynamic_pool_caps_each_source_to_preserve_source_diversity() -> None:
    first_url = "https://source.invalid/first"
    second_url = "https://source.invalid/second"
    session = SourceSession(
        {
            first_url: StubResponse(b"8.8.8.8:80\n1.1.1.1:80\n9.9.9.9:80\n"),
            second_url: StubResponse(b"208.67.222.222:80\n"),
        }
    )
    pool = DynamicProxyPool(
        sources=(ProxySource("first", first_url), ProxySource("second", second_url)),
        session=session,
        candidate_limit=3,
        per_source_limit=2,
        random_source=random.Random(0),
    )

    assert set(pool.load()) == {
        "http://8.8.8.8:80",
        "http://1.1.1.1:80",
        "http://208.67.222.222:80",
    }


def test_dynamic_pool_has_no_default_per_source_quota() -> None:
    source_url = "https://source.invalid/all"
    session = SourceSession(
        {source_url: StubResponse(b"8.8.8.8:80\n1.1.1.1:80\n9.9.9.9:80\n208.67.222.222:80\n")}
    )
    pool = DynamicProxyPool(
        sources=(ProxySource("all", source_url),),
        session=session,
        candidate_limit=4,
        random_source=random.Random(0),
    )

    assert len(pool.load()) == 4


def test_dynamic_pool_has_no_default_global_candidate_limit() -> None:
    source_url = "https://source.invalid/many"
    proxies = "\n".join(f"8.8.{index // 250}.{index % 250 + 1}:80" for index in range(600))
    session = SourceSession({source_url: StubResponse(proxies.encode())})
    pool = DynamicProxyPool(
        sources=(ProxySource("many", source_url),),
        session=session,
        random_source=random.Random(0),
    )

    assert len(pool.load()) == 600


def test_dynamic_pool_preserves_source_priority_while_shuffling_within_source() -> None:
    first_url = "https://source.invalid/fresh"
    second_url = "https://source.invalid/old"
    session = SourceSession(
        {
            first_url: StubResponse(b"8.8.8.8:80\n1.1.1.1:80\n"),
            second_url: StubResponse(b"9.9.9.9:80\n208.67.222.222:80\n"),
        }
    )
    pool = DynamicProxyPool(
        sources=(ProxySource("fresh", first_url), ProxySource("old", second_url)),
        session=session,
        random_source=random.Random(0),
    )

    loaded = pool.load()
    assert set(loaded[:2]) == {"http://8.8.8.8:80", "http://1.1.1.1:80"}
    assert set(loaded[2:]) == {"http://9.9.9.9:80", "http://208.67.222.222:80"}


def test_proxy_visitor_matches_reference_request_behavior() -> None:
    queued_sessions = [
        VisitSession(StubResponse(b"")),
        VisitSession(StubResponse(b"")),
    ]
    created_sessions: list[VisitSession] = []

    def session_factory() -> VisitSession:
        session = queued_sessions.pop(0)
        created_sessions.append(session)
        return session

    visitor = ProxyPromotionVisitor(
        StaticPool(("http://8.8.8.8:8080", "http://1.1.1.1:80")),
        session_factory=session_factory,
    )

    assert visitor.visit("https://klpbbs.com/promotion?fromuid=5")
    assert visitor.visit("https://klpbbs.com/promotion?fromuid=5")

    first, second = created_sessions
    assert [call[0] for call in first.calls + second.calls] == [
        "https://klpbbs.com/promotion?fromuid=5",
        "https://klpbbs.com/promotion?fromuid=5",
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
    assert all(session.headers["Referer"] == "https://klpbbs.com/" for session in created_sessions)
    assert all("Mozilla/5.0" in session.headers["User-Agent"] for session in created_sessions)
    assert all(
        call[1]["allow_redirects"] is True for session in created_sessions for call in session.calls
    )
    assert all(call[1]["verify"] is False for session in created_sessions for call in session.calls)
    assert all(call[1]["timeout"] == 10.0 for session in created_sessions for call in session.calls)


def test_proxy_visitor_matches_reference_status_check_after_redirect() -> None:
    session = VisitSession(
        StubResponse(
            b"",
            url="https://outside.example/landing?fromuid=5",
        )
    )
    visitor = ProxyPromotionVisitor(
        StaticPool(("http://8.8.8.8:8080",)),
        session_factory=lambda: session,
    )

    assert visitor.visit("https://klpbbs.com/promotion?fromuid=5")
    assert session.calls[0][1]["allow_redirects"] is True


def test_proxy_visitor_accepts_consumed_promotion_parameter() -> None:
    session = VisitSession(StubResponse(b"landing", url="https://klpbbs.com/forum.php"))
    visitor = ProxyPromotionVisitor(
        StaticPool(("http://8.8.8.8:8080",)),
        session_factory=lambda: session,
    )

    assert visitor.visit("https://klpbbs.com/?fromuid=5")
    assert [call[0] for call in session.calls] == ["https://klpbbs.com/?fromuid=5"]


def test_proxy_visitor_rejects_non_200_response() -> None:
    session = VisitSession(StubResponse(b"unavailable", status_code=503))
    visitor = ProxyPromotionVisitor(
        StaticPool(("http://8.8.8.8:8080",)),
        session_factory=lambda: session,
    )

    assert not visitor.visit("https://klpbbs.com/?fromuid=5")


def test_proxy_visitor_isolates_truncated_proxy_response() -> None:
    class TruncatedResponse(StubResponse):
        @property
        def content(self) -> bytes:
            raise requests.exceptions.ChunkedEncodingError("truncated proxy response")

    session = VisitSession(TruncatedResponse(b"partial"))
    visitor = ProxyPromotionVisitor(
        StaticPool(("http://8.8.8.8:8080",)),
        session_factory=lambda: session,
    )

    assert not visitor.visit("https://klpbbs.com/?fromuid=5")
    assert session.closed


def test_proxy_visitor_processes_one_concurrent_batch_and_reports_exhaustion() -> None:
    queued_sessions = [
        VisitSession(StubResponse(b"")),
        VisitSession(StubResponse(b"", status_code=503)),
        VisitSession(StubResponse(b"")),
    ]

    def session_factory() -> VisitSession:
        return queued_sessions.pop()

    visitor = ProxyPromotionVisitor(
        StaticPool(
            (
                "http://8.8.8.8:8080",
                "http://1.1.1.1:80",
                "http://9.9.9.9:80",
            )
        ),
        session_factory=session_factory,
        workers=2,
    )

    assert visitor.visit_batch("https://klpbbs.com/?fromuid=5") == PromotionVisitBatch(2, 1, False)
    assert visitor.visit_batch("https://klpbbs.com/?fromuid=5") == PromotionVisitBatch(1, 1, True)
