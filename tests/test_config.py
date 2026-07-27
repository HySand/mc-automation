from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from mc_automation.config import AppConfig, ConfigurationError, parse_bool


def test_sites_are_disabled_by_default() -> None:
    config = AppConfig.from_env({})
    assert not config.klpbbs.enabled
    assert not config.minebbs.enabled
    assert config.rank_threshold == 8
    assert config.minebbs_bump_interval_hours == 16
    assert not config.minebbs_esa_slider_enabled
    assert config.minebbs_browser_executable_path is None
    assert not config.ai_solver.enabled


def test_enabled_site_requires_all_secrets_without_echoing_values() -> None:
    with pytest.raises(ConfigurationError, match="KLPBBS_PASSWORD") as error:
        AppConfig.from_env(
            {
                "KLPBBS_ENABLED": "true",
                "KLPBBS_USERNAME": "secret-user",
                "KLPBBS_THREAD_ID": "12",
            }
        )
    assert "secret-user" not in str(error.value)


def test_invalid_thread_id_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="正整数 ID"):
        AppConfig.from_env(
            {
                "MINEBBS_ENABLED": "true",
                "MINEBBS_USERNAME": "u",
                "MINEBBS_PASSWORD": "p",
                "MINEBBS_THREAD_ID": "not-an-id",
            }
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00123", "123"),
        ("my-server.123", "123"),
        ("threads/my-server.123/", "123"),
        ("https://www.minebbs.com/threads/my-server.123/", "123"),
        ("https://www.minebbs.com:443/threads/my-server.123/", "123"),
        ("https://www.minebbs.com/threads/123/?utm_source=test#post-1", "123"),
    ],
)
def test_minebbs_thread_reference_is_normalized(raw: str, expected: str) -> None:
    config = AppConfig.from_env(
        {
            "MINEBBS_ENABLED": "true",
            "MINEBBS_USERNAME": "u",
            "MINEBBS_PASSWORD": "p",
            "MINEBBS_THREAD_ID": raw,
        }
    )

    assert config.minebbs.thread_id == expected


@pytest.mark.parametrize(
    "raw",
    [
        "my-server-123",
        "https://example.test/threads/my-server.123/",
        "http://www.minebbs.com/threads/my-server.123/",
        "https://www.minebbs.com/resources/my-server.123/",
        "https://user:password@www.minebbs.com/threads/my-server.123/",
        "https://www.minebbs.com:invalid/threads/my-server.123/",
    ],
)
def test_minebbs_thread_reference_rejects_wrong_or_ambiguous_targets(raw: str) -> None:
    with pytest.raises(ConfigurationError, match="MINEBBS_THREAD_ID") as error:
        AppConfig.from_env(
            {
                "MINEBBS_ENABLED": "true",
                "MINEBBS_USERNAME": "u",
                "MINEBBS_PASSWORD": "p",
                "MINEBBS_THREAD_ID": raw,
            }
        )

    assert raw not in str(error.value)


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        ("yes", False, True),
        ("0", True, False),
        (None, False, False),
        ("", True, True),
        ("   ", True, True),
    ],
)
def test_parse_bool(raw: str | None, default: bool, expected: bool) -> None:
    assert parse_bool(raw, default=default) is expected


def test_ai_solver_configuration_does_not_change_site_configuration() -> None:
    config = AppConfig.from_env(
        {
            "AI_SOLVER_ENABLED": "true",
            "AI_SOLVER_ENDPOINT": "https://ai.example.test/v1",
            "AI_SOLVER_API_KEY": "secret-key",
            "AI_SOLVER_MODEL": "vision-model",
            "AI_SOLVER_WDSJFWQ_CAPTCHA_ENABLED": "",
            "AI_SOLVER_TIMEOUT_SECONDS": "2.5",
            "AI_SOLVER_MAX_ATTEMPTS": "2",
            "KLPBBS_ENABLED": "true",
            "KLPBBS_USERNAME": "u",
            "KLPBBS_PASSWORD": "p",
            "KLPBBS_THREAD_ID": "12",
            "KLPBBS_PROMOTION_ENABLED": "true",
            "KLPBBS_PROMOTION_URL": "https://klpbbs.com/?fromuid=5",
            "KLPBBS_PROMOTION_VISIT_DELAY_SECONDS": "0.5",
        }
    )
    assert config.klpbbs.base_url == "https://klpbbs.com"
    assert not hasattr(config.klpbbs, "promotion_max_visits")
    assert config.klpbbs.promotion_visit_delay_seconds == 0.5
    assert config.ai_solver.enabled
    assert config.ai_solver.endpoint == "https://ai.example.test/v1"
    assert config.ai_solver.model == "vision-model"
    assert config.ai_solver.timeout_seconds == 2.5
    assert config.ai_solver.max_attempts == 2
    assert config.ai_solver.wdsjfwq_captcha_enabled


def test_minebbs_esa_slider_is_independent_from_ai_configuration(tmp_path: Path) -> None:
    browser = tmp_path / "chrome.exe"
    browser.write_bytes(b"")
    config = AppConfig.from_env(
        {
            "MINEBBS_ENABLED": "true",
            "MINEBBS_USERNAME": "u",
            "MINEBBS_PASSWORD": "p",
            "MINEBBS_THREAD_ID": "12",
            "MINEBBS_ESA_SLIDER_ENABLED": "true",
            "MINEBBS_BROWSER_EXECUTABLE_PATH": str(browser),
        }
    )

    assert config.minebbs_esa_slider_enabled
    assert config.minebbs_browser_executable_path == browser
    assert not config.ai_solver.enabled


def test_minebbs_esa_slider_requires_minebbs() -> None:
    with pytest.raises(ConfigurationError, match="MINEBBS_ENABLED"):
        AppConfig.from_env({"MINEBBS_ESA_SLIDER_ENABLED": "true"})


def test_minebbs_browser_path_must_exist_when_slider_is_enabled() -> None:
    with pytest.raises(ConfigurationError, match="MINEBBS_BROWSER_EXECUTABLE_PATH"):
        AppConfig.from_env(
            {
                "MINEBBS_ENABLED": "true",
                "MINEBBS_USERNAME": "u",
                "MINEBBS_PASSWORD": "p",
                "MINEBBS_THREAD_ID": "12",
                "MINEBBS_ESA_SLIDER_ENABLED": "true",
                "MINEBBS_BROWSER_EXECUTABLE_PATH": "missing-browser.exe",
            }
        )


def test_ai_solver_requires_secret_configuration_without_echoing_values() -> None:
    with pytest.raises(ConfigurationError, match="AI_SOLVER_API_KEY") as error:
        AppConfig.from_env(
            {
                "AI_SOLVER_ENABLED": "true",
                "AI_SOLVER_ENDPOINT": "https://private-ai.example.test/v1",
                "AI_SOLVER_MODEL": "vision-model",
            }
        )
    assert "private-ai.example.test" not in str(error.value)


def test_promotion_requires_a_same_origin_url() -> None:
    config = AppConfig.from_env(
        {
            "KLPBBS_ENABLED": "true",
            "KLPBBS_USERNAME": "u",
            "KLPBBS_PASSWORD": "p",
            "KLPBBS_THREAD_ID": "12",
            "KLPBBS_PROMOTION_ENABLED": "true",
            "KLPBBS_PROMOTION_URL": "https://klpbbs.com/?fromuid=5",
        }
    )

    assert config.klpbbs.promotion_enabled
    assert config.klpbbs.promotion_url == "https://klpbbs.com/?fromuid=5"

    with pytest.raises(ConfigurationError, match="KLPBBS_PROMOTION_URL"):
        AppConfig.from_env(
            {
                "KLPBBS_ENABLED": "true",
                "KLPBBS_USERNAME": "u",
                "KLPBBS_PASSWORD": "p",
                "KLPBBS_THREAD_ID": "12",
                "KLPBBS_PROMOTION_ENABLED": "true",
                "KLPBBS_PROMOTION_URL": "https://outside.example/?fromuid=5",
            }
        )


def test_minebbs_bump_interval_must_be_positive() -> None:
    with pytest.raises(ConfigurationError, match="MINEBBS_BUMP_INTERVAL_HOURS"):
        AppConfig.from_env({"MINEBBS_BUMP_INTERVAL_HOURS": "0"})


def test_like_sites_have_no_private_target_restriction() -> None:
    config = AppConfig.from_env(
        {
            "WDSJFWQ_ENABLED": "true",
            "MCLISTS_ENABLED": "true",
        }
    )
    assert config.wdsjfwq.url == "https://www.wdsjfwq.com/server-1991/vote.html"
    assert config.mclists.url == "https://www.mclists.cn/server/9969.html"


def test_browser_extra_and_workflow_use_nodriver() -> None:
    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    browser_dependencies = project["project"]["optional-dependencies"]["browser"]
    workflow = (root / ".github" / "workflows" / "automation.yml").read_text(encoding="utf-8")

    assert browser_dependencies == ["nodriver>=0.50.3,<0.51"]
    legacy_browser_command = "python -m " + "play" + "wright"
    assert legacy_browser_command not in workflow.casefold()
    assert "MINEBBS_ESA_SLIDER_ENABLED" in workflow
    assert "MINEBBS_BROWSER_EXECUTABLE_PATH" in workflow
    assert (
        "for candidate in google-chrome google-chrome-stable chromium-browser chromium" in workflow
    )
    assert 'echo "MINEBBS_BROWSER_EXECUTABLE_PATH=$browser_path" >> "$GITHUB_ENV"' in workflow
    assert "mc-automation-${{ matrix.site }}-state-v3-${{ github.run_id }}" in workflow
    assert "Clear legacy MineBBS ESA suspension" not in workflow
    assert "challenge_count" not in workflow
    assert "suspended_until" not in workflow


def test_workflow_enforces_warp_and_keeps_promotion_proxy_configuration_separate() -> None:
    root = Path(__file__).parents[1]
    workflow = (root / ".github" / "workflows" / "automation.yml").read_text(encoding="utf-8")

    assert "viperadnan-git/setup-warp@691f6aa5a251ed89ea27a85e890f6f5313c1a3b5" in workflow
    assert "^warp=(on|plus)$" in workflow
    assert "KLPBBS_PROMOTION_PROXY_TARGET_URL" not in workflow
    assert "KLPBBS_PROMOTION_TARGET_MARKER" not in workflow
    assert "KLPBBS_PROMOTION_URL" in workflow


def test_workflow_runs_each_adapter_in_an_independent_non_fail_fast_job() -> None:
    root = Path(__file__).parents[1]
    workflow = (root / ".github" / "workflows" / "automation.yml").read_text(encoding="utf-8")

    assert "fail-fast: false" in workflow
    for site in ("klpbbs", "minebbs", "wdsjfwq", "mclists"):
        assert f"- site: {site}" in workflow
    assert "KLPBBS_ENABLED: ${{ matrix.klpbbs_enabled }}" in workflow
    assert "MINEBBS_ENABLED: ${{ matrix.minebbs_enabled }}" in workflow
    assert "WDSJFWQ_ENABLED: ${{ matrix.wdsjfwq_enabled }}" in workflow
    assert "MCLISTS_ENABLED: ${{ matrix.mclists_enabled }}" in workflow
    assert "其他站点任务不受影响" in workflow
