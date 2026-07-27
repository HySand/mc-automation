from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .ai_solver import OpenAICompatibleVisionSolver
from .challenge import EsaSliderChallengeResolver
from .config import AppConfig, ConfigurationError
from .models import ActionResult, ActionStatus, RunReport
from .orchestrator import Orchestrator
from .promotion_proxy import DynamicProxyPool, ProxyPromotionVisitor
from .security import redact
from .sites.base import OneShotAdapter, SiteAdapter
from .sites.klpbbs import KLPBBSAdapter
from .sites.like import LikeAdapter
from .sites.minebbs import MineBBSAdapter
from .state import StateStore
from .step_log import ACTION_LABELS, LOG_FORMAT_ENV, RESULT_STATUS_LABELS, SITE_LABELS, log_step
from .transport import ChallengeResolver, HttpTransport, create_cloudscraper_session


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合规多站点顶贴自动化")
    parser.add_argument("--dry-run", action="store_true", help="不访问网络，仅验证程序入口")
    parser.add_argument("--state", type=Path, help="覆盖状态文件路径")
    parser.add_argument("--summary", type=Path, help="覆盖 Markdown 摘要路径")
    return parser


def _write_summary(report: RunReport, path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.to_markdown(), encoding="utf-8")


def _environment_summary_path() -> Path | None:
    raw = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    return Path(raw) if raw else None


def _load_local_environment() -> None:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)


def _print_report(report: RunReport) -> None:
    if os.environ.get(LOG_FORMAT_ENV, "human").strip().casefold() == "json":
        print(json.dumps([item.to_dict() for item in report.results], ensure_ascii=False))
        return
    for item in report.results:
        site = SITE_LABELS.get(item.site, item.site)
        action = ACTION_LABELS.get(item.action, item.action)
        status = RESULT_STATUS_LABELS.get(item.status.value, item.status.value)
        print(f"[{site}] {action}：{status} - {item.message}")


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    log_step("application", status="started")
    if args.dry_run:
        report = RunReport(
            [ActionResult("system", "dry_run", ActionStatus.SKIPPED, "dry-run 未访问网络")]
        )
        _write_summary(report, args.summary)
        _print_report(report)
        log_step("application", status="completed", exit_code=0, result_count=1)
        return 0

    try:
        config = AppConfig.from_env()
    except ConfigurationError as exc:
        log_step(
            "configuration",
            status="failed",
            exception_type=type(exc).__name__,
        )
        report = RunReport(
            [
                ActionResult(
                    "system",
                    "configuration",
                    ActionStatus.MANUAL_INTERVENTION,
                    f"配置错误：{exc}",
                )
            ]
        )
        _write_summary(report, args.summary or _environment_summary_path())
        _print_report(report)
        log_step(
            "application",
            status="completed",
            exit_code=report.exit_code,
            result_count=len(report.results),
        )
        return report.exit_code

    enabled_sites = [
        name
        for name, enabled in (
            ("klpbbs", config.klpbbs.enabled),
            ("minebbs", config.minebbs.enabled),
            ("wdsjfwq", config.wdsjfwq.enabled),
            ("mclists", config.mclists.enabled),
        )
        if enabled
    ]
    log_step(
        "configuration",
        status="completed",
        enabled_sites=enabled_sites,
        adapter_count=len(enabled_sites),
    )

    state_path = args.state or config.state_path
    summary_path = args.summary or config.summary_path
    store = StateStore(state_path)
    log_step("state_load", status="started", state_exists=state_path.exists())
    state = store.load()
    log_step(
        "state_load",
        status="completed",
        state_exists=state_path.exists(),
        recovered=state.recovered,
    )
    adapters: list[SiteAdapter | OneShotAdapter] = []
    secrets: list[str] = []
    ai_solver = OpenAICompatibleVisionSolver(config.ai_solver) if config.ai_solver.enabled else None
    esa_challenge_resolver = (
        EsaSliderChallengeResolver(
            browser_executable_path=config.minebbs_browser_executable_path,
        )
        if config.minebbs_esa_slider_enabled
        else None
    )

    def transport(
        *,
        site: str,
        use_cloudscraper: bool = False,
        challenge_resolver: ChallengeResolver | None = None,
    ) -> HttpTransport:
        return HttpTransport(
            session=create_cloudscraper_session() if use_cloudscraper else None,
            challenge_resolver=challenge_resolver,
            site=site,
        )

    if config.klpbbs.enabled:
        promotion_visitor = None
        if config.klpbbs.promotion_enabled:
            promotion_visitor = ProxyPromotionVisitor(DynamicProxyPool())
        adapters.append(
            KLPBBSAdapter(
                config.klpbbs,
                transport(site="klpbbs", use_cloudscraper=True),
                base_url=config.klpbbs.base_url,
                promotion_visitor=promotion_visitor,
            )
        )
        secrets.extend([config.klpbbs.username, config.klpbbs.password])
    if config.minebbs.enabled:
        adapters.append(
            MineBBSAdapter(
                config.minebbs,
                transport(site="minebbs", challenge_resolver=esa_challenge_resolver),
                base_url=config.minebbs.base_url,
            )
        )
        secrets.extend([config.minebbs.username, config.minebbs.password])
    if config.wdsjfwq.enabled:
        adapters.append(
            LikeAdapter(
                config.wdsjfwq,
                transport(site="wdsjfwq"),
                captcha_solver=(
                    ai_solver
                    if ai_solver is not None and config.ai_solver.wdsjfwq_captcha_enabled
                    else None
                ),
            )
        )
    if config.mclists.enabled:
        adapters.append(LikeAdapter(config.mclists, transport(site="mclists")))
    if config.ai_solver.enabled:
        secrets.extend([config.ai_solver.api_key, config.ai_solver.endpoint])
    if not adapters:
        report = RunReport(
            [ActionResult("system", "configuration", ActionStatus.SKIPPED, "没有启用任何站点")]
        )
        _write_summary(report, summary_path)
        _print_report(report)
        log_step(
            "application",
            status="completed",
            exit_code=report.exit_code,
            result_count=len(report.results),
        )
        return report.exit_code

    log_step("adapter_setup", status="completed", adapter_count=len(adapters))

    report = Orchestrator(
        adapters,
        state,
        rank_threshold=config.rank_threshold,
        paid_cooldown_seconds=config.paid_bump_cooldown_seconds,
        bump_intervals_seconds={
            "minebbs": config.minebbs_bump_interval_hours * 60 * 60,
        },
    ).run()
    log_step("state_save", status="started", result_count=len(state.sites))
    store.save(state)
    log_step("state_save", status="completed", result_count=len(state.sites))
    log_step("summary_write", status="started" if summary_path is not None else "skipped")
    _write_summary(report, summary_path)
    log_step("summary_write", status="completed" if summary_path is not None else "skipped")
    if os.environ.get(LOG_FORMAT_ENV, "human").strip().casefold() == "json":
        for result in report.results:
            safe = redact(json.dumps(result.to_dict(), ensure_ascii=False), secrets)
            logging.info(safe)
    log_step(
        "application",
        status="completed",
        exit_code=report.exit_code,
        result_count=len(report.results),
    )
    return report.exit_code


def main() -> None:
    _load_local_environment()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(run())


if __name__ == "__main__":
    main()
