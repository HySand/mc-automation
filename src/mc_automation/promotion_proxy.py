from __future__ import annotations

import ipaddress
import json
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlsplit

import requests
from requests.exceptions import RequestException
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning

from .step_log import log_step
from .transport import TransportError

DEFAULT_SOURCE_MAX_BYTES = 2_000_000
DEFAULT_PROXY_WORKERS = 20
PROMOTION_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 "
    "Safari/537.36 Edg/116.0.1938.81"
)

# The reference client disables certificate checks for public proxy compatibility.
disable_warnings(InsecureRequestWarning)


class ProxyPoolExhausted(TransportError):
    pass


class ProxySourceError(TransportError):
    pass


@dataclass(frozen=True, slots=True)
class PromotionVisitBatch:
    attempts: int
    proxy_successes: int
    exhausted: bool


class PromotionVisitor(Protocol):
    def visit_batch(self, promotion_url: str) -> PromotionVisitBatch: ...


class ProxyPool(Protocol):
    def load(self) -> tuple[str, ...]: ...


SourceKind = Literal["text", "geonode", "checkerproxy"]


@dataclass(frozen=True, slots=True)
class ProxySource:
    name: str
    url: str
    kind: SourceKind = "text"
    params: Mapping[str, str | int] | None = None


def default_proxy_sources(today: date | None = None) -> tuple[ProxySource, ...]:
    current_day = today or date.today()
    checker_sources = tuple(
        ProxySource(
            "checkerproxy",
            "https://api.checkerproxy.net/v1/landing/archive/"
            f"{(current_day - timedelta(days=offset)).isoformat()}",
            "checkerproxy",
        )
        for offset in range(1, 8)
    )
    fresh_sources = (
        ProxySource(
            "proxifly-http",
            "https://raw.githubusercontent.com/proxifly/free-proxy-list/"
            "main/proxies/protocols/http/data.txt",
        ),
        ProxySource(
            "openproxylist-https",
            "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        ),
        ProxySource(
            "yakumo-http-checked",
            "https://raw.githubusercontent.com/elliottophellia/yakumo/master/"
            "results/http/global/http_checked.txt",
        ),
        ProxySource(
            "kangproxy-https",
            "https://raw.githubusercontent.com/officialputuid/KangProxy/main/https/https.txt",
        ),
    )
    return (
        fresh_sources
        + checker_sources
        + (
            ProxySource(
                "proxyscrape",
                "https://api.proxyscrape.com/v2/",
                params={
                    "request": "getproxies",
                    "protocol": "http",
                    "timeout": 2000,
                    "country": "all",
                },
            ),
            ProxySource(
                "proxy-list.download",
                "https://www.proxy-list.download/api/v1/get",
                params={"type": "http"},
            ),
            ProxySource(
                "geonode",
                "https://proxylist.geonode.com/api/proxy-list",
                "geonode",
                {
                    "limit": 300,
                    "page": 1,
                    "sort_by": "lastChecked",
                    "sort_type": "desc",
                    "protocols": "http",
                },
            ),
            ProxySource(
                "speedx",
                "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            ),
            ProxySource(
                "monosans",
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
            ),
        )
    )


def normalize_http_proxy(raw: str) -> str | None:
    candidate = raw.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    try:
        parsed = urlsplit(candidate)
        if (
            parsed.scheme.casefold() != "http"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.port is None
        ):
            return None
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return None
    if not address.is_global:
        return None
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"http://{host}:{parsed.port}"


def _proxy_values(payload: bytes, source: ProxySource) -> Iterable[str]:
    if source.kind == "text":
        return payload.decode("utf-8", errors="replace").splitlines()

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ProxySourceError(f"{source.name} returned an invalid JSON object")
    if source.kind == "geonode":
        entries = data.get("data", [])
        if not isinstance(entries, list):
            raise ProxySourceError("geonode returned an invalid data field")
        return (
            f"{item.get('ip')}:{item.get('port')}"
            for item in entries
            if isinstance(item, dict) and item.get("ip") and item.get("port")
        )

    nested_data = data.get("data", {})
    if not isinstance(nested_data, dict):
        raise ProxySourceError("checkerproxy returned an invalid data field")
    proxy_list = nested_data.get("proxyList")
    if isinstance(proxy_list, dict):
        return (str(value) for value in proxy_list.values() if value)
    if isinstance(proxy_list, list):
        return (str(value) for value in proxy_list if value)
    raise ProxySourceError("checkerproxy returned an invalid proxy list")


def _bounded_body(response: requests.Response, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise ProxySourceError("proxy source response is too large")
        except ValueError as exc:
            raise ProxySourceError("proxy source Content-Length is invalid") from exc

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=16_384):
        total += len(chunk)
        if total > max_bytes:
            raise ProxySourceError("proxy source response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


class DynamicProxyPool:
    def __init__(
        self,
        *,
        sources: Sequence[ProxySource] | None = None,
        session: requests.Session | None = None,
        candidate_limit: int | None = None,
        source_timeout: tuple[float, float] = (5.0, 15.0),
        source_max_bytes: int = DEFAULT_SOURCE_MAX_BYTES,
        per_source_limit: int | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        self.sources = tuple(default_proxy_sources() if sources is None else sources)
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "mc-automation/0.1 proxy-source-client"})
        self.candidate_limit = candidate_limit
        self.source_timeout = source_timeout
        self.source_max_bytes = source_max_bytes
        self.per_source_limit = per_source_limit
        self.random_source = random_source or random.SystemRandom()
        self._loaded: tuple[str, ...] | None = None

    def load(self) -> tuple[str, ...]:
        if self._loaded is not None:
            return self._loaded

        unique: dict[str, None] = {}
        candidates: list[str] = []
        for source in self.sources:
            source_added = 0
            source_candidates: list[str] = []
            try:
                response = self.session.get(
                    source.url,
                    params=source.params,
                    timeout=self.source_timeout,
                    stream=True,
                )
                try:
                    response.raise_for_status()
                    payload = _bounded_body(response, self.source_max_bytes)
                finally:
                    response.close()
                for raw_proxy in _proxy_values(payload, source):
                    proxy = normalize_http_proxy(raw_proxy)
                    if proxy is not None:
                        before = len(unique)
                        unique.setdefault(proxy, None)
                        if len(unique) > before:
                            source_candidates.append(proxy)
                            source_added += 1
                    if (
                        self.candidate_limit is not None and len(unique) >= self.candidate_limit
                    ) or (
                        self.per_source_limit is not None and source_added >= self.per_source_limit
                    ):
                        break
            except (
                requests.RequestException,
                json.JSONDecodeError,
                ProxySourceError,
                TypeError,
                AttributeError,
            ):
                log_step(
                    "promotion_proxy_source",
                    site="klpbbs",
                    status="failed",
                    source_name=source.name,
                    proxy_count=source_added,
                )
                continue
            self.random_source.shuffle(source_candidates)
            candidates.extend(source_candidates)
            log_step(
                "promotion_proxy_source",
                site="klpbbs",
                status="completed",
                source_name=source.name,
                proxy_count=source_added,
            )
            if self.candidate_limit is not None and len(unique) >= self.candidate_limit:
                break

        if not unique:
            raise ProxyPoolExhausted("所有动态代理源均不可用或未返回有效公共 HTTP 代理")
        self._loaded = tuple(candidates)
        return self._loaded


class ProxyPromotionVisitor:
    def __init__(
        self,
        pool: ProxyPool,
        *,
        session_factory: Callable[[], requests.Session] = requests.Session,
        timeout: float = 10.0,
        workers: int = DEFAULT_PROXY_WORKERS,
    ) -> None:
        self.pool = pool
        self.session_factory = session_factory
        self.timeout = timeout
        self.workers = workers
        self._proxies: tuple[str, ...] | None = None
        self._next_index = 0

    def _next_proxy(self) -> str:
        if self._proxies is None:
            self._proxies = self.pool.load()
        if self._next_index >= len(self._proxies):
            raise ProxyPoolExhausted("本轮动态代理池已耗尽")
        proxy = self._proxies[self._next_index]
        self._next_index += 1
        return proxy

    def _visit_proxy(self, promotion_url: str, proxy: str, attempt: int) -> bool:
        session = self.session_factory()
        session.trust_env = False
        session.cookies.clear()
        session.headers.clear()
        session.headers.update(
            {
                "User-Agent": PROMOTION_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{urlsplit(promotion_url).scheme}://{urlsplit(promotion_url).netloc}/",
            }
        )
        response: requests.Response | None = None
        try:
            # Match the known-working reference request before applying stricter result checks.
            response = session.get(
                promotion_url,
                proxies={"http": proxy, "https": proxy},
                timeout=self.timeout,
                allow_redirects=True,
                verify=False,
            )
            original = urlsplit(promotion_url)
            final = urlsplit(response.url)
            same_host = final.hostname == original.hostname
            fromuid_preserved = bool(parse_qs(final.query).get("fromuid"))
            # The reference implementation treats the response status as the click result.
            # KLPBBS may rewrite the landing URL after consuming the promotion parameter.
            success = response.status_code == 200
            log_step(
                "promotion_proxy_visit",
                site="klpbbs",
                status="completed" if success else "failed",
                action=attempt,
                status_code=response.status_code,
                redirect_count=len(response.history),
                redirect_target=response.url,
                promotion_parameter_preserved=fromuid_preserved,
                final_origin_matches=same_host,
                content_type=response.headers.get("Content-Type"),
                content_length=len(response.content),
            )
            return success
        except RequestException:
            log_step(
                "promotion_proxy_visit",
                site="klpbbs",
                status="failed",
                action=attempt,
            )
            return False
        finally:
            if response is not None:
                response.close()
            session.close()

    def visit(self, promotion_url: str) -> bool:
        proxy = self._next_proxy()
        return self._visit_proxy(promotion_url, proxy, self._next_index)

    def visit_batch(self, promotion_url: str) -> PromotionVisitBatch:
        if self._proxies is None:
            self._proxies = self.pool.load()
        if self._next_index >= len(self._proxies):
            raise ProxyPoolExhausted("本轮动态代理池已耗尽")

        start = self._next_index
        end = min(start + self.workers, len(self._proxies))
        batch = self._proxies[start:end]
        self._next_index = end
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            outcomes = tuple(
                executor.map(
                    lambda item: self._visit_proxy(promotion_url, item[1], item[0]),
                    enumerate(batch, start=start + 1),
                )
            )
        return PromotionVisitBatch(
            attempts=len(batch),
            proxy_successes=sum(outcomes),
            exhausted=end >= len(self._proxies),
        )
