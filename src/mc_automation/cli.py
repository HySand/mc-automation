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
from .promotion_proxy import DynamicProxyPool, IsolatedPromotionTarget, ProxyPromotionVisitor
from .security import redact
from .sites.base import OneShotAdapter, SiteAdapter
from .sites.klpbbs import KLPBBSAdapter
from .sites.like import LikeAdapter
from .sites.minebbs import MineBBSAdapter
from .state import StateStore
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
    print(json.dumps([item.to_dict() for item in report.results], ensure_ascii=False))


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.dry_run:
        report = RunReport(
            [ActionResult("system", "dry_run", ActionStatus.SKIPPED, "dry-run 未访问网络")]
        )
        _write_summary(report, args.summary)
        _print_report(report)
        return 0

    try:
        config = AppConfig.from_env()
    except ConfigurationError as exc:
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
        return report.exit_code

    state_path = args.state or config.state_path
    summary_path = args.summary or config.summary_path
    store = StateStore(state_path)
    state = store.load()
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
        use_cloudscraper: bool = False,
        challenge_resolver: ChallengeResolver | None = None,
    ) -> HttpTransport:
        return HttpTransport(
            session=create_cloudscraper_session() if use_cloudscraper else None,
            challenge_resolver=challenge_resolver,
        )

    if config.klpbbs.enabled:
        promotion_visitor = None
        if config.klpbbs.promotion_enabled:
            target = IsolatedPromotionTarget(
                config.klpbbs.promotion_proxy_target_url,
                config.klpbbs.promotion_target_marker,
            )
            promotion_visitor = ProxyPromotionVisitor(target, DynamicProxyPool())
        adapters.append(
            KLPBBSAdapter(
                config.klpbbs,
                transport(use_cloudscraper=True),
                base_url=config.klpbbs.base_url,
                promotion_visitor=promotion_visitor,
            )
        )
        secrets.extend([config.klpbbs.username, config.klpbbs.password])
    if config.minebbs.enabled:
        adapters.append(
            MineBBSAdapter(
                config.minebbs,
                transport(challenge_resolver=esa_challenge_resolver),
                base_url=config.minebbs.base_url,
            )
        )
        secrets.extend([config.minebbs.username, config.minebbs.password])
    if config.wdsjfwq.enabled:
        adapters.append(
            LikeAdapter(
                config.wdsjfwq,
                transport(),
                captcha_solver=(
                    ai_solver
                    if ai_solver is not None and config.ai_solver.wdsjfwq_captcha_enabled
                    else None
                ),
            )
        )
    if config.mclists.enabled:
        adapters.append(LikeAdapter(config.mclists, transport()))
    if config.ai_solver.enabled:
        secrets.extend([config.ai_solver.api_key, config.ai_solver.endpoint])
    if not adapters:
        report = RunReport(
            [ActionResult("system", "configuration", ActionStatus.SKIPPED, "没有启用任何站点")]
        )
        _write_summary(report, summary_path)
        _print_report(report)
        return report.exit_code

    report = Orchestrator(
        adapters,
        state,
        rank_threshold=config.rank_threshold,
        paid_cooldown_seconds=config.paid_bump_cooldown_seconds,
        bump_intervals_seconds={
            "minebbs": config.minebbs_bump_interval_hours * 60 * 60,
        },
    ).run()
    store.save(state)
    _write_summary(report, summary_path)
    for result in report.results:
        safe = redact(json.dumps(result.to_dict(), ensure_ascii=False), secrets)
        logging.info(safe)
    return report.exit_code


def main() -> None:
    _load_local_environment()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(run())


if __name__ == "__main__":
    main()
