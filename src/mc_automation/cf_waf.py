from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import requests

from .security import detect_security_challenge
from .transport import SecurityChallenge


class ChallengeKind(StrEnum):
    CLOUDFLARE = "cloudflare"
    WAF = "waf"
    CAPTCHA = "captcha"
    ACCESS = "access"


@dataclass(frozen=True, slots=True)
class ChallengeInfo:
    kind: ChallengeKind
    reason: str


class Resolver(Protocol):
    def resolve(
        self,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool: ...


class CloudflareWafGuard:
    """Fail-closed CF/WAF handling with one optional browser-assisted GET retry.

    This guard deliberately does not generate tokens, alter browser fingerprints, replay POSTs,
    or route requests through third-party proxies. A resolver may only clear a supported challenge
    in a visible browser session; unresolved challenges remain security failures.
    """

    def __init__(self, resolver: Resolver | None = None) -> None:
        self.resolver = resolver

    def inspect(self, response: requests.Response) -> ChallengeInfo | None:
        reason = detect_security_challenge(response.status_code, response.text)
        if reason is None:
            return None
        lowered = f"{response.headers.get('Server', '')} {response.text[:200_000]}".casefold()
        if "cloudflare" in lowered or "cf-chl-" in lowered or "challenge-platform" in lowered:
            kind = ChallengeKind.CLOUDFLARE
        elif "captcha" in lowered or "turnstile" in lowered or "hcaptcha" in lowered:
            kind = ChallengeKind.CAPTCHA
        elif response.status_code in {401, 403, 429}:
            kind = ChallengeKind.WAF
        else:
            kind = ChallengeKind.ACCESS
        return ChallengeInfo(kind, reason)

    def resolve_once(
        self,
        *,
        info: ChallengeInfo,
        method: str,
        url: str,
        session: requests.Session,
        timeout: tuple[float, float],
    ) -> bool:
        del info  # Classification is retained for callers and logs; policy is method-based.
        if self.resolver is None or method.upper() not in {"GET", "HEAD"}:
            return False
        return self.resolver.resolve(url, session, timeout)

    @staticmethod
    def failure(info: ChallengeInfo) -> SecurityChallenge:
        return SecurityChallenge(f"{info.kind.value}: {info.reason}")
