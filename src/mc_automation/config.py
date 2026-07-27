from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .ai_solver import AISolverConfig


class ConfigurationError(ValueError):
    """Raised for invalid non-secret configuration."""


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError("站点开关必须是 true/false")


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} 必须大于 0")
    return value


def _bounded_positive_int(env: Mapping[str, str], name: str, default: int, maximum: int) -> int:
    value = _positive_int(env, name, default)
    if value > maximum:
        raise ConfigurationError(f"{name} 不能大于 {maximum}")
    return value


def _nonnegative_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是数字") from exc
    if value < 0:
        raise ConfigurationError(f"{name} 不能小于 0")
    return value


def _minimum_float(env: Mapping[str, str], name: str, default: float, minimum: float) -> float:
    value = _nonnegative_float(env, name, default)
    if value < minimum:
        raise ConfigurationError(f"{name} 不能小于 {minimum}")
    return value


def _optional_file(env: Mapping[str, str], name: str) -> Path | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise ConfigurationError(f"{name} 必须指向已存在的浏览器可执行文件")
    return path


def _base_url(raw: str, name: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError(f"{name} 必须是有效的 HTTP(S) 地址")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是有效的 HTTP(S) 地址") from exc
    if parsed.username or parsed.password:
        raise ConfigurationError(f"{name} 不能包含用户名或密码")
    return value


def _same_origin_url(raw: str, base_url: str, name: str) -> str:
    value = _base_url(raw, name)
    parsed = urlsplit(value)
    base = urlsplit(base_url)
    parsed_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    base_port = base.port or (443 if base.scheme == "https" else 80)
    if (
        parsed.scheme.casefold() != base.scheme.casefold()
        or (parsed.hostname or "").casefold() != (base.hostname or "").casefold()
        or parsed_port != base_port
    ):
        raise ConfigurationError(f"{name} 必须属于 KLPBBS_BASE_URL")
    return value


def _required(env: Mapping[str, str], names: tuple[str, ...], site: str) -> dict[str, str]:
    values = {name: env.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigurationError(f"{site} 已启用但缺少配置：{', '.join(missing)}")
    return values


def _thread_id(raw: str, name: str) -> str:
    if not raw.isdigit() or int(raw) <= 0:
        raise ConfigurationError(f"{name} 必须是正整数帖子 ID")
    return str(int(raw))


MINEBBS_THREAD_PATH_RE = re.compile(r"^/threads/(?:[^/?#]*\.)?([1-9]\d*)/?$")
MINEBBS_THREAD_SEGMENT_RE = re.compile(r"^(?:[^/?#]*\.)?([1-9]\d*)$")


def _minebbs_thread_id(raw: str, name: str, base_url: str) -> str:
    value = raw.strip()
    if value.isdigit():
        return _thread_id(value, name)

    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        expected = urlsplit(base_url)
        try:
            actual_port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
            expected_port = expected.port or (443 if expected.scheme.casefold() == "https" else 80)
        except ValueError as exc:
            raise ConfigurationError(f"{name} 必须是有效的 MineBBS 帖子 URL") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.scheme.casefold() != expected.scheme.casefold()
            or parsed.hostname.casefold() != (expected.hostname or "").casefold()
            or actual_port != expected_port
        ):
            raise ConfigurationError(f"{name} 必须属于 MINEBBS_BASE_URL")
        candidate = parsed.path
        match = MINEBBS_THREAD_PATH_RE.fullmatch(candidate)
    elif value.startswith(("/", "threads/")):
        candidate = value if value.startswith("/") else f"/{value}"
        match = MINEBBS_THREAD_PATH_RE.fullmatch(candidate)
    else:
        match = MINEBBS_THREAD_SEGMENT_RE.fullmatch(value)

    if match is None:
        raise ConfigurationError(f"{name} 必须是正整数 ID、MineBBS 帖子短名或完整帖子 URL")
    return match.group(1)


@dataclass(frozen=True, slots=True)
class SiteConfig:
    name: str
    enabled: bool
    username: str = ""
    password: str = ""
    thread_id: str = ""
    base_url: str = ""
    promotion_enabled: bool = False
    promotion_url: str = ""
    promotion_visit_delay_seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class LikeSiteConfig:
    name: str
    enabled: bool
    url: str = ""


@dataclass(frozen=True, slots=True)
class AppConfig:
    klpbbs: SiteConfig
    minebbs: SiteConfig
    wdsjfwq: LikeSiteConfig
    mclists: LikeSiteConfig
    state_path: Path
    summary_path: Path | None
    rank_threshold: int
    paid_bump_cooldown_seconds: int
    minebbs_bump_interval_hours: int
    minebbs_esa_slider_enabled: bool
    minebbs_browser_executable_path: Path | None
    ai_solver: AISolverConfig

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AppConfig:
        source = os.environ if env is None else env
        klp_enabled = parse_bool(source.get("KLPBBS_ENABLED"))
        mine_enabled = parse_bool(source.get("MINEBBS_ENABLED"))
        promotion_enabled = parse_bool(source.get("KLPBBS_PROMOTION_ENABLED"))
        wdsjfwq_enabled = parse_bool(source.get("WDSJFWQ_ENABLED"))
        mclists_enabled = parse_bool(source.get("MCLISTS_ENABLED"))
        minebbs_esa_slider_enabled = parse_bool(source.get("MINEBBS_ESA_SLIDER_ENABLED"))
        ai_solver_enabled = parse_bool(source.get("AI_SOLVER_ENABLED"))
        ai_solver_wdsjfwq_enabled = parse_bool(
            source.get("AI_SOLVER_WDSJFWQ_CAPTCHA_ENABLED"),
            default=ai_solver_enabled,
        )
        if promotion_enabled and not klp_enabled:
            raise ConfigurationError("KLPBBS_PROMOTION_ENABLED 需要同时启用 KLPBBS_ENABLED")
        if minebbs_esa_slider_enabled and not mine_enabled:
            raise ConfigurationError("MINEBBS_ESA_SLIDER_ENABLED 需要同时启用 MINEBBS_ENABLED")
        minebbs_browser_executable_path = (
            _optional_file(source, "MINEBBS_BROWSER_EXECUTABLE_PATH")
            if minebbs_esa_slider_enabled
            else None
        )

        ai_solver_required = ai_solver_enabled or ai_solver_wdsjfwq_enabled
        ai_solver = AISolverConfig(enabled=False)
        if ai_solver_required:
            ai_values = _required(
                source,
                ("AI_SOLVER_ENDPOINT", "AI_SOLVER_API_KEY", "AI_SOLVER_MODEL"),
                "AI solver",
            )
            ai_solver = AISolverConfig(
                enabled=True,
                endpoint=_base_url(ai_values["AI_SOLVER_ENDPOINT"], "AI_SOLVER_ENDPOINT"),
                api_key=ai_values["AI_SOLVER_API_KEY"],
                model=ai_values["AI_SOLVER_MODEL"],
                timeout_seconds=_minimum_float(source, "AI_SOLVER_TIMEOUT_SECONDS", 60.0, 1.0),
                max_attempts=_bounded_positive_int(source, "AI_SOLVER_MAX_ATTEMPTS", 1, 5),
                wdsjfwq_captcha_enabled=ai_solver_wdsjfwq_enabled,
            )

        def like_site(name: str, enabled: bool, variable: str, default_url: str) -> LikeSiteConfig:
            if not enabled:
                return LikeSiteConfig(name=name, enabled=False)
            url = _base_url(source.get(variable, default_url), variable)
            return LikeSiteConfig(name=name, enabled=True, url=url)

        klp = SiteConfig(name="klpbbs", enabled=False)
        if klp_enabled:
            values = _required(
                source,
                (
                    "KLPBBS_USERNAME",
                    "KLPBBS_PASSWORD",
                    "KLPBBS_THREAD_ID",
                ),
                "KLPBBS",
            )
            klp_base_url = _base_url(
                source.get("KLPBBS_BASE_URL", "https://klpbbs.com"),
                "KLPBBS_BASE_URL",
            )
            promotion_url = ""
            if promotion_enabled:
                promotion_value = _required(
                    source,
                    ("KLPBBS_PROMOTION_URL",),
                    "KLPBBS 推广任务",
                )["KLPBBS_PROMOTION_URL"]
                promotion_url = _same_origin_url(
                    promotion_value,
                    klp_base_url,
                    "KLPBBS_PROMOTION_URL",
                )
            klp = SiteConfig(
                name="klpbbs",
                enabled=True,
                username=values["KLPBBS_USERNAME"],
                password=values["KLPBBS_PASSWORD"],
                thread_id=_thread_id(values["KLPBBS_THREAD_ID"], "KLPBBS_THREAD_ID"),
                base_url=klp_base_url,
                promotion_enabled=promotion_enabled,
                promotion_url=promotion_url,
                promotion_visit_delay_seconds=_minimum_float(
                    source, "KLPBBS_PROMOTION_VISIT_DELAY_SECONDS", 2.0, 0.5
                ),
            )

        mine = SiteConfig(name="minebbs", enabled=False)
        if mine_enabled:
            values = _required(
                source,
                ("MINEBBS_USERNAME", "MINEBBS_PASSWORD", "MINEBBS_THREAD_ID"),
                "MineBBS",
            )
            mine_base_url = _base_url(
                source.get("MINEBBS_BASE_URL", "https://www.minebbs.com"),
                "MINEBBS_BASE_URL",
            )
            mine = SiteConfig(
                name="minebbs",
                enabled=True,
                username=values["MINEBBS_USERNAME"],
                password=values["MINEBBS_PASSWORD"],
                thread_id=_minebbs_thread_id(
                    values["MINEBBS_THREAD_ID"],
                    "MINEBBS_THREAD_ID",
                    mine_base_url,
                ),
                base_url=mine_base_url,
            )

        wdsjfwq = like_site(
            "wdsjfwq",
            wdsjfwq_enabled,
            "WDSJFWQ_LIKE_URL",
            "https://www.wdsjfwq.com/server-1991/vote.html",
        )
        mclists = like_site(
            "mclists",
            mclists_enabled,
            "MCLISTS_LIKE_URL",
            "https://www.mclists.cn/server/9969.html",
        )

        summary_raw = source.get("GITHUB_STEP_SUMMARY", "").strip()
        return cls(
            klpbbs=klp,
            minebbs=mine,
            wdsjfwq=wdsjfwq,
            mclists=mclists,
            state_path=Path(source.get("STATE_PATH", ".state/state.json")),
            summary_path=Path(summary_raw) if summary_raw else None,
            rank_threshold=_positive_int(source, "RANK_THRESHOLD", 8),
            paid_bump_cooldown_seconds=_positive_int(source, "PAID_BUMP_COOLDOWN_SECONDS", 3600),
            minebbs_bump_interval_hours=_positive_int(source, "MINEBBS_BUMP_INTERVAL_HOURS", 16),
            minebbs_esa_slider_enabled=minebbs_esa_slider_enabled,
            minebbs_browser_executable_path=minebbs_browser_executable_path,
            ai_solver=ai_solver,
        )
