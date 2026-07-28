from __future__ import annotations

import http.cookiejar
import time
from typing import Any, Protocol, cast

import cloudscraper  # type: ignore[import-untyped]
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .step_log import log_step

KLPBBS_REFERENCE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 Edg/116.0.1938.81"
)


class TransportError(RuntimeError):
    pass


class HttpStatusError(TransportError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        category = "server" if status_code >= 500 else "client"
        super().__init__(f"HTTP {status_code} {category} error")


class SecurityChallenge(TransportError):
    pass


class UnsafeTarget(TransportError):
    pass


class ChallengeResolver(Protocol):
    def resolve(
        self,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool: ...


def create_cloudscraper_session() -> requests.Session:
    session = cast(
        requests.Session,
        cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        ),
    )
    session.cookies = cast(Any, http.cookiejar.LWPCookieJar())
    session.headers["User-Agent"] = KLPBBS_REFERENCE_USER_AGENT
    return session


class HttpTransport:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10.0, 20.0),
        challenge_resolver: ChallengeResolver | None = None,
        site: str = "system",
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.challenge_resolver = challenge_resolver
        self.site = site
        from .cf_waf import CloudflareWafGuard

        self.cf_waf_guard = CloudflareWafGuard(challenge_resolver)
        self._browser_mode = False
        current_user_agent = str(self.session.headers.get("User-Agent", ""))
        if not current_user_agent or current_user_agent.startswith("python-requests/"):
            self.session.headers["User-Agent"] = (
                "mc-automation/0.1 (+rule-aware GitHub Actions client)"
            )
        self.session.headers["Accept"] = (
            "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"
        )
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            backoff_factor=0.75,
            status_forcelist=(500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 552),
            allowed_methods=frozenset({"GET", "HEAD"}),
            raise_on_status=False,
        )
        for prefix in ("https://", "http://"):
            adapter = self.session.get_adapter(prefix)
            if not isinstance(adapter, HTTPAdapter):
                raise TypeError(f"{prefix} session adapter must inherit HTTPAdapter")
            if type(adapter) is HTTPAdapter:
                self.session.mount(prefix, HTTPAdapter(max_retries=retry))
            else:
                adapter.max_retries = retry

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        current_method = method.upper()
        if self._browser_mode:
            browser_response = self._browser_request(current_method, url, kwargs)
            if browser_response is None:
                raise SecurityChallenge("browser transport could not complete the request")
            return browser_response
        challenge_attempted = False
        while True:
            started = time.monotonic()
            log_step(
                "http_request",
                site=self.site,
                status="started",
                method=current_method,
                url=url,
            )
            try:
                response = self.session.request(current_method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                log_step(
                    "http_request",
                    site=self.site,
                    status="failed",
                    method=current_method,
                    url=url,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    exception_type=type(exc).__name__,
                )
                raise TransportError("network request failed after bounded retries") from exc

            log_step(
                "http_response",
                site=self.site,
                status="completed",
                method=current_method,
                url=url,
                status_code=response.status_code,
                content_type=response.headers.get("Content-Type", "").split(";", 1)[0],
                content_length=len(response.content),
                elapsed_ms=round((time.monotonic() - started) * 1000),
                redirect_count=len(response.history),
                redirect_target=response.url,
                cookie_count=len(self.session.cookies),
            )

            info = self.cf_waf_guard.inspect(response)
            if info is not None:
                log_step(
                    "security_challenge",
                    site=self.site,
                    status="detected",
                    method=current_method,
                    url=url,
                    challenge_kind=info.kind.value,
                )
                if not challenge_attempted:
                    challenge_attempted = True
                    browser_bridge_available = current_method in {"GET", "HEAD"} and callable(
                        getattr(self.challenge_resolver, "browser_request", None)
                    )
                    if browser_bridge_available:
                        log_step(
                            "challenge_resolution",
                            site=self.site,
                            status="started",
                            method=current_method,
                            url=url,
                            challenge_kind=info.kind.value,
                        )
                        browser_response = self._browser_request(current_method, url, kwargs)
                        log_step(
                            "challenge_resolution",
                            site=self.site,
                            status="completed" if browser_response is not None else "failed",
                            method=current_method,
                            url=url,
                            challenge_kind=info.kind.value,
                            resolved=browser_response is not None,
                        )
                        if browser_response is not None:
                            self._browser_mode = True
                            return browser_response
                        raise self.cf_waf_guard.failure(info)
                    log_step(
                        "challenge_resolution",
                        site=self.site,
                        status="started",
                        method=current_method,
                        url=url,
                        challenge_kind=info.kind.value,
                    )
                    resolved = self.cf_waf_guard.resolve_once(
                        info=info,
                        method=current_method,
                        url=url,
                        session=self.session,
                        timeout=self.timeout,
                    )
                    log_step(
                        "challenge_resolution",
                        site=self.site,
                        status="completed" if resolved else "failed",
                        method=current_method,
                        url=url,
                        challenge_kind=info.kind.value,
                        resolved=resolved,
                    )
                    if resolved:
                        continue
                raise self.cf_waf_guard.failure(info)
            if response.status_code >= 400:
                raise HttpStatusError(response.status_code)
            return response

    def _browser_request(
        self, method: str, url: str, kwargs: dict[str, Any]
    ) -> requests.Response | None:
        if self.challenge_resolver is None:
            return None
        request_method = getattr(self.challenge_resolver, "browser_request", None)
        if not callable(request_method):
            return None
        browser_kwargs = dict(kwargs)
        browser_kwargs.pop("timeout", None)
        return cast(
            requests.Response | None,
            request_method(method, url, self.session, self.timeout, **browser_kwargs),
        )

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)
