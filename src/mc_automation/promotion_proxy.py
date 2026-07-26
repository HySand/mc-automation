from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ProxyError, SSLError, Timeout

from .transport import TransportError, UnsafeTarget

TARGET_MARKER_HEADER = "X-MC-Automation-Target"
DEFAULT_PROXY_LIMIT = 500
DEFAULT_SOURCE_MAX_BYTES = 2_000_000


class ProxyPoolExhausted(TransportError):
    pass


class ProxySourceError(TransportError):
    pass


class PromotionVisitor(Protocol):
    def visit(self, promotion_url: str) -> bool: ...


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
    return checker_sources + (
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
        candidate_limit: int = DEFAULT_PROXY_LIMIT,
        source_timeout: tuple[float, float] = (5.0, 15.0),
        source_max_bytes: int = DEFAULT_SOURCE_MAX_BYTES,
    ) -> None:
        self.sources = tuple(default_proxy_sources() if sources is None else sources)
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "mc-automation/0.1 proxy-source-client"})
        self.candidate_limit = candidate_limit
        self.source_timeout = source_timeout
        self.source_max_bytes = source_max_bytes
        self._loaded: tuple[str, ...] | None = None

    def load(self) -> tuple[str, ...]:
        if self._loaded is not None:
            return self._loaded

        unique: dict[str, None] = {}
        for source in self.sources:
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
                        unique.setdefault(proxy, None)
                    if len(unique) >= self.candidate_limit:
                        break
            except (
                requests.RequestException,
                json.JSONDecodeError,
                ProxySourceError,
                TypeError,
                AttributeError,
            ):
                continue
            if len(unique) >= self.candidate_limit:
                break

        if not unique:
            raise ProxyPoolExhausted("所有动态代理源均不可用或未返回有效公共 HTTP 代理")
        self._loaded = tuple(unique)
        return self._loaded


@dataclass(frozen=True, slots=True)
class IsolatedPromotionTarget:
    base_url: str
    marker: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeTarget("推广代理目标必须是 HTTP(S) 根地址")
        if parsed.username or parsed.password or parsed.path not in {"", "/"}:
            raise UnsafeTarget("推广代理目标不能包含凭据或路径")
        if parsed.query or parsed.fragment:
            raise UnsafeTarget("推广代理目标不能包含查询参数或片段")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise UnsafeTarget("推广代理目标必须使用 IP 字面量，不能使用域名") from exc
        if not address.is_global:
            raise UnsafeTarget("推广代理目标必须是公共代理可达的公网 IP")
        if not self.marker or len(self.marker) > 128 or any(char in self.marker for char in "\r\n"):
            raise UnsafeTarget("推广靶场响应标记必须为 1 至 128 个无换行字符")

    def map_promotion_url(self, promotion_url: str) -> str:
        promotion = urlsplit(promotion_url)
        if promotion.scheme not in {"http", "https"} or not promotion.hostname:
            raise UnsafeTarget("任务提供的推广链接不是有效的 HTTP(S) 地址")
        target = urlsplit(self.base_url)
        return urlunsplit(
            (target.scheme, target.netloc, promotion.path or "/", promotion.query, "")
        )


class ProxyPromotionVisitor:
    def __init__(
        self,
        target: IsolatedPromotionTarget,
        pool: ProxyPool,
        *,
        session_factory: Callable[[], requests.Session] = requests.Session,
        timeout: tuple[float, float] = (5.0, 10.0),
    ) -> None:
        self.target = target
        self.pool = pool
        self.session_factory = session_factory
        self.timeout = timeout
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

    def visit(self, promotion_url: str) -> bool:
        target_url = self.target.map_promotion_url(promotion_url)
        proxy = self._next_proxy()
        session = self.session_factory()
        session.trust_env = False
        session.cookies.clear()
        session.headers.clear()
        session.headers.update(
            {
                "User-Agent": "mc-automation/0.1 isolated-promotion-client",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            }
        )
        response: requests.Response | None = None
        try:
            response = session.get(
                target_url,
                proxies={"http": proxy, "https": proxy},
                timeout=self.timeout,
                allow_redirects=False,
                verify=True,
                stream=True,
            )
            if response.is_redirect:
                raise UnsafeTarget("推广靶场返回重定向，已拒绝继续访问")
            if not 200 <= response.status_code < 300:
                return False
            if response.headers.get(TARGET_MARKER_HEADER) != self.target.marker:
                raise UnsafeTarget("推广响应缺少预期靶场标记，已停止代理访问")
            return True
        except (ProxyError, RequestsConnectionError, Timeout, SSLError):
            return False
        finally:
            if response is not None:
                response.close()
            session.close()
