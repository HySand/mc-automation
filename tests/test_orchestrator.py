from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from mc_automation.models import ActionResult, ActionStatus, Inventory, Resources
from mc_automation.orchestrator import Orchestrator
from mc_automation.state import AppState
from mc_automation.transport import SecurityChallenge


def result(site: str, action: str, status: ActionStatus = ActionStatus.SUCCESS) -> ActionResult:
    return ActionResult(site, action, status, action)


@dataclass
class FakeAdapter:
    name: str = "fake"
    thread_id: str = "1"
    supports_promotion: bool = False
    uses_rank_eligibility: bool = True
    rank: int = 11
    inventory: int = 0
    challenge: bool = False
    challenge_after_auth: bool = False
    purchased_item: str = "purple"
    promotion_status: ActionStatus = ActionStatus.SUCCESS
    excluded_purchase_items: frozenset[str] = field(default_factory=frozenset)
    calls: list[str] = field(default_factory=list)

    def authenticate(self) -> ActionResult:
        self.calls.append("authenticate")
        if self.challenge:
            raise SecurityChallenge("challenge")
        return result(self.name, "authenticate")

    def daily_sign_in(self) -> ActionResult:
        self.calls.append("daily_sign_in")
        if self.challenge_after_auth:
            raise SecurityChallenge("challenge")
        return result(self.name, "daily_sign_in", ActionStatus.SKIPPED)

    def run_promotion_task(self) -> ActionResult:
        self.calls.append("run_promotion_task")
        return result(self.name, "promotion_task", self.promotion_status)

    def get_thread_rank(self) -> int:
        self.calls.append("get_thread_rank")
        return self.rank

    def verify_target_ownership(self) -> None:
        self.calls.append("verify_target_ownership")

    def get_resources(self) -> Resources:
        return Resources({})

    def get_inventory(self) -> Inventory:
        self.calls.append("get_inventory")
        return Inventory({"bump": self.inventory})

    def purchase_bump_item(self, *, excluded_items: frozenset[str] = frozenset()) -> ActionResult:
        self.calls.append("purchase_bump_item")
        self.excluded_purchase_items = excluded_items
        return ActionResult(
            self.name,
            "purchase_bump_item",
            ActionStatus.SUCCESS,
            "purchase_bump_item",
            metadata={"item": self.purchased_item},
        )

    def apply_bump_item(self) -> ActionResult:
        self.calls.append("apply_bump_item")
        return result(self.name, "apply_bump_item")


@dataclass
class FakeOneShotAdapter:
    name: str = "like"
    is_one_shot_action: bool = True
    status: ActionStatus = ActionStatus.SUCCESS
    calls: int = 0

    def run_one_shot_action(self) -> ActionResult:
        self.calls += 1
        return result(self.name, "like", self.status)


NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)


def test_first_run_never_spends() -> None:
    adapter = FakeAdapter()
    report = Orchestrator([adapter], AppState(recovered=False), now=NOW).run()
    assert report.results[-1].action == "state_recovery"
    assert "purchase_bump_item" not in adapter.calls


def test_rank_at_threshold_skips() -> None:
    adapter = FakeAdapter(rank=8)
    report = Orchestrator([adapter], AppState(recovered=True), now=NOW).run()
    assert report.results[-1].action == "eligibility"
    assert "get_inventory" not in adapter.calls


def test_minebbs_skips_before_sixteen_hour_interval_without_reading_rank() -> None:
    adapter = FakeAdapter(name="minebbs", uses_rank_eligibility=False, rank=1)
    state = AppState(recovered=True)
    state.for_site("minebbs").last_paid_bump = (NOW - timedelta(hours=15)).isoformat()

    report = Orchestrator(
        [adapter],
        state,
        bump_intervals_seconds={"minebbs": 16 * 60 * 60},
        now=NOW,
    ).run()

    assert report.results[-1].action == "bump_interval"
    assert "get_thread_rank" not in adapter.calls
    assert "get_inventory" not in adapter.calls


def test_minebbs_bumps_at_sixteen_hours_without_reading_rank() -> None:
    adapter = FakeAdapter(
        name="minebbs",
        uses_rank_eligibility=False,
        rank=1,
        inventory=1,
    )
    state = AppState(recovered=True)
    state.for_site("minebbs").last_paid_bump = (NOW - timedelta(hours=16)).isoformat()

    Orchestrator(
        [adapter],
        state,
        bump_intervals_seconds={"minebbs": 16 * 60 * 60},
        now=NOW,
    ).run()

    assert "get_thread_rank" not in adapter.calls
    assert adapter.calls[-2:] == ["get_inventory", "apply_bump_item"]
    assert state.for_site("minebbs").last_paid_bump == NOW.isoformat()


def test_promotion_task_runs_after_sign_in_when_supported() -> None:
    adapter = FakeAdapter(supports_promotion=True, rank=1)
    Orchestrator([adapter], AppState(recovered=True), now=NOW).run()
    assert adapter.calls[:4] == [
        "authenticate",
        "daily_sign_in",
        "run_promotion_task",
        "get_thread_rank",
    ]


def test_promotion_technical_failure_stops_later_side_effects() -> None:
    adapter = FakeAdapter(
        supports_promotion=True,
        promotion_status=ActionStatus.TECHNICAL_FAILURE,
    )
    report = Orchestrator([adapter], AppState(recovered=True), now=NOW).run()
    assert report.results[-1].action == "promotion_task"
    assert adapter.calls == ["authenticate", "daily_sign_in", "run_promotion_task"]


def test_purchase_and_apply_are_one_transaction() -> None:
    adapter = FakeAdapter()
    state = AppState(recovered=True)
    Orchestrator([adapter], state, now=NOW).run()
    assert adapter.calls[-3:] == ["get_inventory", "purchase_bump_item", "apply_bump_item"]
    assert state.for_site("fake").last_paid_bump == NOW.isoformat()


def test_minebbs_gold_purchase_is_recorded_and_excluded_for_same_day() -> None:
    adapter = FakeAdapter(name="minebbs", purchased_item="gold")
    state = AppState(recovered=True)
    Orchestrator([adapter], state, now=NOW).run()
    assert state.for_site("minebbs").gold_purchase_date == "2026-07-25"

    adapter.calls.clear()
    Orchestrator([adapter], state, now=NOW.replace(hour=14)).run()
    assert adapter.excluded_purchase_items == frozenset({"gold"})


def test_repeated_challenges_are_retried_without_persistent_suspension() -> None:
    adapter = FakeAdapter(challenge=True)
    state = AppState(recovered=True)
    for hour in range(3):
        Orchestrator([adapter], state, now=NOW.replace(hour=12 + hour)).run()
    assert adapter.calls == ["authenticate"] * 3
    assert not hasattr(state.for_site("fake"), "challenge_count")
    assert not hasattr(state.for_site("fake"), "suspended_until")


def test_repeated_post_authentication_challenges_are_not_skipped() -> None:
    adapter = FakeAdapter(challenge_after_auth=True)
    state = AppState(recovered=True)
    for hour in range(3):
        Orchestrator([adapter], state, now=NOW.replace(hour=12 + hour)).run()
    assert adapter.calls == ["authenticate", "daily_sign_in"] * 3


def test_failure_is_isolated_between_sites() -> None:
    blocked = FakeAdapter(name="blocked", challenge=True)
    healthy = FakeAdapter(name="healthy", rank=1)
    report = Orchestrator([blocked, healthy], AppState(recovered=True), now=NOW).run()
    assert {item.site for item in report.results} == {"blocked", "healthy"}


def test_one_shot_like_adapter_runs_without_entering_bump_flow() -> None:
    like = FakeOneShotAdapter()
    normal = FakeAdapter(name="normal", rank=1)

    report = Orchestrator([like, normal], AppState(recovered=True), now=NOW).run()

    assert like.calls == 1
    assert report.results[0].action == "like"
    assert normal.calls[:2] == ["authenticate", "daily_sign_in"]
