from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import ActionResult, Inventory, Resources


class SiteParseError(RuntimeError):
    pass


class SiteAdapter(Protocol):
    name: str
    thread_id: str
    supports_promotion: bool
    uses_rank_eligibility: bool

    def authenticate(self) -> ActionResult: ...

    def daily_sign_in(self) -> ActionResult: ...

    def run_promotion_task(self) -> ActionResult: ...

    def get_thread_rank(self) -> int: ...

    def verify_target_ownership(self) -> None: ...

    def get_resources(self) -> Resources: ...

    def get_inventory(self) -> Inventory: ...

    def purchase_bump_item(
        self, *, excluded_items: frozenset[str] = frozenset()
    ) -> ActionResult: ...

    def apply_bump_item(self) -> ActionResult: ...


@runtime_checkable
class OneShotAdapter(Protocol):
    name: str
    is_one_shot_action: bool

    def run_one_shot_action(self) -> ActionResult: ...
