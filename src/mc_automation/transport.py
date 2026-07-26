from __future__ import annotations

from typing import Any, Protocol, cast

import cloudscraper  # type: ignore[import-untyped]
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class TransportError(RuntimeError):
    pass


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
    return cast(requests.Session, cloudscraper.create_scraper())


class HttpTransport:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10.0, 20.0),
        challenge_resolver: ChallengeResolver | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.challenge_resolver = challenge_resolver
        from .cf_waf import CloudflareWafGuard

        self.cf_waf_guard = CloudflareWafGuard(challenge_resolver)
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
            status_forcelist=(500, 502, 503, 504),
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
        challenge_attempted = False
        while True:
            try:
                response = self.session.request(current_method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                raise TransportError("network request failed after bounded retries") from exc

            info = self.cf_waf_guard.inspect(response)
            if info is not None:
                if not challenge_attempted:
                    challenge_attempted = True
                    if self.cf_waf_guard.resolve_once(
                        info=info,
                        method=current_method,
                        url=url,
                        session=self.session,
                        timeout=self.timeout,
                    ):
                        continue
                raise self.cf_waf_guard.failure(info)
            return response

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)
