from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ActionStatus(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    INSUFFICIENT_RESOURCES = "insufficient_resources"
    MANUAL_INTERVENTION = "manual_intervention"
    TECHNICAL_FAILURE = "technical_failure"


@dataclass(frozen=True, slots=True)
class ActionResult:
    site: str
    action: str
    status: ActionStatus
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, str | int | bool | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["timestamp"] = self.timestamp.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class Resources:
    balances: dict[str, int | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Inventory:
    items: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class RunReport:
    results: list[ActionResult] = field(default_factory=list)

    def add(self, result: ActionResult) -> None:
        self.results.append(result)

    @property
    def exit_code(self) -> int:
        statuses = {result.status for result in self.results}
        if ActionStatus.TECHNICAL_FAILURE in statuses:
            return 1
        if ActionStatus.MANUAL_INTERVENTION in statuses:
            return 2
        return 0

    def to_markdown(self) -> str:
        lines = [
            "# Minecraft 宣传自动化运行结果",
            "",
            "| 站点 | 动作 | 状态 | 说明 |",
            "|---|---|---|---|",
        ]
        for result in self.results:
            message = result.message.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {result.site} | {result.action} | `{result.status.value}` | {message} |"
            )
        return "\n".join(lines) + "\n"
