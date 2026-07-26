from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

STATE_VERSION = 1


@dataclass(slots=True)
class SiteState:
    last_paid_bump: str | None = None
    gold_purchase_date: str | None = None
    challenge_count: int = 0
    suspended_until: str | None = None

    def paid_bump_at(self) -> datetime | None:
        if not self.last_paid_bump:
            return None
        try:
            return datetime.fromisoformat(self.last_paid_bump)
        except ValueError:
            return None

    def suspension_at(self) -> datetime | None:
        if not self.suspended_until:
            return None
        try:
            return datetime.fromisoformat(self.suspended_until)
        except ValueError:
            return None


@dataclass(slots=True)
class AppState:
    version: int = STATE_VERSION
    recovered: bool = False
    sites: dict[str, SiteState] = field(default_factory=dict)

    def for_site(self, name: str) -> SiteState:
        return self.sites.setdefault(name, SiteState())


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AppState:
        if not self.path.exists():
            return AppState(recovered=False)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return self._decode(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return AppState(recovered=False)

    def save(self, state: AppState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            "recovered": True,
            "sites": {name: asdict(site) for name, site in state.sites.items()},
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _decode(payload: Any) -> AppState:
        if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
            raise ValueError("unsupported state schema")
        raw_sites = payload.get("sites", {})
        if not isinstance(raw_sites, dict):
            raise TypeError("sites must be an object")
        sites: dict[str, SiteState] = {}
        allowed = set(SiteState.__dataclass_fields__)
        for name, raw in raw_sites.items():
            if not isinstance(name, str) or not isinstance(raw, dict):
                raise TypeError("invalid site state")
            clean = {key: value for key, value in raw.items() if key in allowed}
            sites[name] = SiteState(**clean)
        return AppState(
            version=STATE_VERSION, recovered=bool(payload.get("recovered", True)), sites=sites
        )
