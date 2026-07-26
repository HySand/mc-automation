from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import ActionResult, ActionStatus, RunReport
from .sites.base import OneShotAdapter, SiteAdapter, SiteParseError
from .state import AppState, SiteState
from .transport import SecurityChallenge, TransportError

SHANGHAI = ZoneInfo("Asia/Shanghai")


class Orchestrator:
    def __init__(
        self,
        adapters: list[SiteAdapter | OneShotAdapter],
        state: AppState,
        *,
        rank_threshold: int = 10,
        paid_cooldown_seconds: int = 3600,
        bump_intervals_seconds: Mapping[str, int] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.adapters = adapters
        self.state = state
        self.rank_threshold = rank_threshold
        self.paid_cooldown = timedelta(seconds=paid_cooldown_seconds)
        self.bump_intervals = {
            site: timedelta(seconds=seconds)
            for site, seconds in (bump_intervals_seconds or {}).items()
        }
        self.now = now or datetime.now(UTC)

    def run(self) -> RunReport:
        report = RunReport()
        for adapter in self.adapters:
            if isinstance(adapter, OneShotAdapter):
                results = self._run_one_shot_site(adapter)
            else:
                results = self._run_site(adapter)
            for result in results:
                report.add(result)
        self.state.recovered = True
        return report

    def _run_one_shot_site(self, adapter: OneShotAdapter) -> list[ActionResult]:
        site_state = self.state.for_site(adapter.name)
        suspended_until = site_state.suspension_at()
        if suspended_until and suspended_until > self.now:
            return [
                ActionResult(
                    adapter.name,
                    "suspension",
                    ActionStatus.SKIPPED,
                    f"安全挑战暂停至 {suspended_until.isoformat()}",
                )
            ]
        try:
            result = adapter.run_one_shot_action()
            if result.status is ActionStatus.MANUAL_INTERVENTION:
                self._record_challenge(site_state)
            else:
                self._clear_challenge(site_state)
            return [result]
        except SecurityChallenge:
            self._record_challenge(site_state)
            return [
                ActionResult(
                    adapter.name,
                    "security_challenge",
                    ActionStatus.MANUAL_INTERVENTION,
                    "检测到验证码或安全防护，已停止该站点操作",
                )
            ]
        except (TransportError, SiteParseError) as exc:
            return [ActionResult(adapter.name, "like", ActionStatus.TECHNICAL_FAILURE, str(exc))]

    def _run_site(self, adapter: SiteAdapter) -> list[ActionResult]:
        site_state = self.state.for_site(adapter.name)
        suspended_until = site_state.suspension_at()
        if suspended_until and suspended_until > self.now:
            return [
                ActionResult(
                    adapter.name,
                    "suspension",
                    ActionStatus.SKIPPED,
                    f"安全挑战暂停至 {suspended_until.isoformat()}",
                )
            ]
        try:
            results = self._run_authenticated_site(adapter, site_state)
            if results and results[0].status is ActionStatus.MANUAL_INTERVENTION:
                self._record_challenge(site_state)
            else:
                self._clear_challenge(site_state)
            return results
        except SecurityChallenge:
            self._record_challenge(site_state)
            return [
                ActionResult(
                    adapter.name,
                    "security_challenge",
                    ActionStatus.MANUAL_INTERVENTION,
                    "检测到验证码或安全防护，已停止该站点操作",
                )
            ]
        except (TransportError, SiteParseError) as exc:
            return [
                ActionResult(
                    adapter.name,
                    "site_run",
                    ActionStatus.TECHNICAL_FAILURE,
                    str(exc),
                )
            ]
        except Exception:
            return [
                ActionResult(
                    adapter.name,
                    "site_run",
                    ActionStatus.TECHNICAL_FAILURE,
                    "未预期的适配器错误",
                )
            ]

    def _run_authenticated_site(
        self, adapter: SiteAdapter, site_state: SiteState
    ) -> list[ActionResult]:
        results: list[ActionResult] = []
        auth = adapter.authenticate()
        results.append(auth)
        if auth.status is not ActionStatus.SUCCESS:
            return results
        results.append(adapter.daily_sign_in())
        if adapter.supports_promotion:
            promotion = adapter.run_promotion_task()
            results.append(promotion)
            if promotion.status in {
                ActionStatus.MANUAL_INTERVENTION,
                ActionStatus.TECHNICAL_FAILURE,
            }:
                return results

        if adapter.uses_rank_eligibility:
            rank = adapter.get_thread_rank()
            if rank <= self.rank_threshold:
                results.append(
                    ActionResult(
                        adapter.name,
                        "eligibility",
                        ActionStatus.SKIPPED,
                        f"帖子当前第 {rank} 名，无需顶贴",
                    )
                )
                return results

        adapter.verify_target_ownership()

        if not self.state.recovered:
            results.append(
                ActionResult(
                    adapter.name,
                    "state_recovery",
                    ActionStatus.SKIPPED,
                    "首次或状态缓存缺失，本轮只读检查后初始化状态",
                )
            )
            return results

        today = self.now.astimezone(SHANGHAI).date().isoformat()
        last_paid = site_state.paid_bump_at()
        bump_interval = self.bump_intervals.get(adapter.name, self.paid_cooldown)
        if last_paid and self.now - last_paid < bump_interval:
            interval_action = (
                "bump_interval" if adapter.name in self.bump_intervals else "paid_cooldown"
            )
            interval_message = (
                "预设顶贴间隔尚未结束"
                if adapter.name in self.bump_intervals
                else "付费顶贴冷却尚未结束"
            )
            results.append(
                ActionResult(
                    adapter.name,
                    interval_action,
                    ActionStatus.SKIPPED,
                    interval_message,
                )
            )
            return results

        inventory = adapter.get_inventory().items
        if sum(inventory.values()) <= 0:
            excluded_items = (
                frozenset({"gold"})
                if adapter.name == "minebbs" and site_state.gold_purchase_date == today
                else frozenset()
            )
            purchase = adapter.purchase_bump_item(excluded_items=excluded_items)
            results.append(purchase)
            if purchase.status is not ActionStatus.SUCCESS:
                return results
            if adapter.name == "minebbs" and purchase.metadata.get("item") == "gold":
                site_state.gold_purchase_date = today
        applied = adapter.apply_bump_item()
        results.append(applied)
        if applied.status is ActionStatus.SUCCESS:
            site_state.last_paid_bump = self.now.isoformat()
        return results

    def _record_challenge(self, state: SiteState) -> None:
        state.challenge_count += 1
        if state.challenge_count >= 3:
            state.suspended_until = (self.now + timedelta(hours=24)).isoformat()

    @staticmethod
    def _clear_challenge(state: SiteState) -> None:
        state.challenge_count = 0
        state.suspended_until = None
