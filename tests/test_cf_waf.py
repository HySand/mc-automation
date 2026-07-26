from __future__ import annotations

import requests

from mc_automation.cf_waf import ChallengeKind, CloudflareWafGuard


def response(status: int, body: str, *, server: str = "") -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = body.encode()
    result.headers["Server"] = server
    return result


def test_classifies_cloudflare_and_waf() -> None:
    guard = CloudflareWafGuard()
    assert (
        guard.inspect(response(403, "cf-chl-test", server="cloudflare")).kind
        == ChallengeKind.CLOUDFLARE
    )
    assert guard.inspect(response(429, "access denied")).kind == ChallengeKind.WAF


def test_only_get_can_use_resolver() -> None:
    class Resolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, *_args: object, **_kwargs: object) -> bool:
            self.calls += 1
            return True

    resolver = Resolver()
    guard = CloudflareWafGuard(resolver)
    session = requests.Session()
    assert guard.resolve_once(
        info=guard.inspect(response(403, "cf-chl-test")),
        method="GET",
        url="https://example.test",
        session=session,
        timeout=(1, 1),
    )
    assert not guard.resolve_once(
        info=guard.inspect(response(403, "cf-chl-test")),
        method="POST",
        url="https://example.test",
        session=session,
        timeout=(1, 1),
    )
    assert resolver.calls == 1
