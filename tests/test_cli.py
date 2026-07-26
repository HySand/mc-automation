from __future__ import annotations

import json
import os
from pathlib import Path

from mc_automation.cli import _load_local_environment, run


def test_dry_run_needs_no_configuration_and_uses_no_network(capsys: object) -> None:
    assert run(["--dry-run"]) == 0


def test_missing_credentials_emit_manual_intervention_summary(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("KLPBBS_ENABLED", "true")
    monkeypatch.setenv("KLPBBS_USERNAME", "owner")
    monkeypatch.setenv("KLPBBS_THREAD_ID", "42")

    assert run(["--summary", str(summary)]) == 2

    output = json.loads(capsys.readouterr().out)
    assert output[0]["status"] == "manual_intervention"
    assert "KLPBBS_PASSWORD" in output[0]["message"]
    assert "owner" not in summary.read_text(encoding="utf-8")


def test_all_sites_disabled_is_a_successful_safe_skip(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("KLPBBS_ENABLED", "false")
    monkeypatch.setenv("MINEBBS_ENABLED", "false")
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.json"))

    assert run(["--summary", str(summary)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output[0]["status"] == "skipped"
    assert "没有启用任何站点" in summary.read_text(encoding="utf-8")


def test_local_dotenv_is_loaded_without_overriding_exported_values(
    tmp_path: Path, monkeypatch: object
) -> None:
    (tmp_path / ".env").write_text(
        "KLPBBS_ENABLED=true\nMINEBBS_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KLPBBS_ENABLED", raising=False)
    monkeypatch.setenv("MINEBBS_ENABLED", "false")

    _load_local_environment()

    assert os.environ["KLPBBS_ENABLED"] == "true"
    assert os.environ["MINEBBS_ENABLED"] == "false"
