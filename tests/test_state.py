from __future__ import annotations

import json
from pathlib import Path

from mc_automation.state import AppState, SiteState, StateStore


def test_missing_state_is_conservative(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "missing.json").load()
    assert not state.recovered


def test_state_round_trip_contains_only_operational_fields(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = AppState(
        recovered=True,
        sites={"klpbbs": SiteState(last_paid_bump="2026-07-25T12:00:00+00:00")},
    )
    StateStore(path).save(state)
    loaded = StateStore(path).load()
    assert loaded.recovered
    assert loaded.for_site("klpbbs").last_paid_bump == "2026-07-25T12:00:00+00:00"
    assert "password" not in path.read_text(encoding="utf-8").lower()


def test_corrupt_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("not-json", encoding="utf-8")
    assert not StateStore(path).load().recovered


def test_legacy_reply_state_is_ignored_and_removed_on_save(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "recovered": True,
                "sites": {"klpbbs": {"last_reply_date": "2026-07-25"}},
            }
        ),
        encoding="utf-8",
    )
    state = StateStore(path).load()
    StateStore(path).save(state)
    assert "last_reply_date" not in path.read_text(encoding="utf-8")
