from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import TypeAlias
from urllib.parse import urlsplit, urlunsplit

LOGGER_NAME = "mc_automation.steps"
REDACTED = "[REDACTED]"
LOG_FORMAT_ENV = "MC_AUTOMATION_LOG_FORMAT"

SITE_LABELS = {
    "system": "系统",
    "klpbbs": "KLPBBS",
    "minebbs": "MineBBS",
    "wdsjfwq": "WDSJFWQ",
    "mclists": "MCLists",
}

PHASE_LABELS = {
    "application": "程序",
    "configuration": "配置",
    "state_load": "读取状态",
    "adapter_setup": "初始化适配器",
    "orchestration": "执行任务",
    "site_run": "站点任务",
    "authenticate": "登录",
    "daily_sign_in": "签到",
    "promotion": "推广任务",
    "promotion_proxy_source": "代理源",
    "promotion_proxy_visit": "推广访问",
    "promotion_task_progress": "推广进度",
    "rank_check": "排名检查",
    "ownership_check": "所有权检查",
    "inventory": "道具库存",
    "purchase_bump_item": "购买顶帖道具",
    "apply_bump_item": "使用顶帖道具",
    "security_challenge": "安全验证",
    "challenge_resolution": "验证处理",
    "esa_attempt": "ESA 滑块尝试",
    "site_action": "站点操作",
    "page_fetch": "读取页面",
    "like_count": "点赞数",
    "like_count_refresh": "复查点赞数",
    "like_control_discovery": "识别操作控件",
    "form_inspection": "检查表单",
    "captcha_form_inspection": "检查验证码表单",
    "captcha_image": "获取验证码图片",
    "captcha_recognition": "验证码识别",
    "captcha_solution": "验证码结果",
    "ai_request": "AI 识别请求",
    "ai_response": "AI 识别响应",
    "ai_response_parse": "解析 AI 响应",
    "form_submission": "提交表单",
    "like_response_classification": "判断提交结果",
    "rank_page_parse": "解析排名页面",
    "action_result": "任务结果",
    "state_save": "保存状态",
    "summary_write": "生成摘要",
}

STATUS_LABELS = {
    "started": "开始",
    "completed": "完成",
    "failed": "失败",
    "skipped": "跳过",
    "detected": "检测到",
    "observed": "已读取",
    "received": "已收到",
    "info": "信息",
}

RESULT_STATUS_LABELS = {
    "success": "成功",
    "skipped": "已跳过",
    "insufficient_resources": "资源不足",
    "manual_intervention": "需要人工处理",
    "technical_failure": "技术错误",
}

ACTION_LABELS = {
    "authenticate": "登录",
    "daily_sign_in": "签到",
    "promotion_task": "推广任务",
    "eligibility": "资格检查",
    "site_run": "站点任务",
    "security_challenge": "安全验证",
    "like": "点赞/投票",
    "purchase_bump_item": "购买顶帖道具",
    "apply_bump_item": "使用顶帖道具",
}

QUIET_HUMAN_PHASES = frozenset(
    {
        "http_request",
        "http_response",
        "promotion_proxy_visit_failed",
    }
)

SafeScalar: TypeAlias = str | int | float | bool | None
SafeValue: TypeAlias = SafeScalar | list[SafeScalar] | dict[str, SafeScalar]

# Step metadata is deliberately closed: adding a new field requires deciding that it is safe.
ALLOWED_METADATA_KEYS = frozenset(
    {
        "action",
        "adapter_count",
        "already_clear",
        "attempts",
        "attempt",
        "browser_fallback",
        "challenge_kind",
        "cleanup_ok",
        "code_length",
        "confidence",
        "content_length",
        "content_type",
        "control_count",
        "control_kind",
        "cookie_count",
        "cooldown_active",
        "distance",
        "duration_ms",
        "elapsed_ms",
        "enabled_sites",
        "exception_type",
        "exit_code",
        "field_count",
        "field_names",
        "frame_count",
        "final_origin_matches",
        "format_valid",
        "form_count",
        "handle_height",
        "handle_width",
        "headless",
        "has_body",
        "image_bytes",
        "initial_count",
        "marker_names",
        "inventory_total",
        "item_types",
        "json_response",
        "link_count",
        "max_attempts",
        "method",
        "model",
        "navigation_timeout_ms",
        "normal_thread_count",
        "owned",
        "path_points",
        "progress_changed",
        "progress_percent",
        "promotion_parameter_preserved",
        "proxy_count",
        "proxy_successes",
        "rank",
        "ready_state",
        "recovered",
        "redirect_count",
        "redirect_target",
        "refreshed_count",
        "remaining_seconds",
        "requires_interaction",
        "resolved",
        "response_count",
        "result_count",
        "result_status",
        "session_synced",
        "source_name",
        "state_exists",
        "status_code",
        "submit_method",
        "subject_link_count",
        "success_marker",
        "threshold",
        "track_width",
        "container_count",
        "descendant_count",
        "url",
    }
)


def safe_url(value: str) -> str:
    """Retain only the public origin and path of an HTTP(S) URL."""

    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return REDACTED
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return REDACTED
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme.casefold(), host, parsed.path or "/", "", ""))


def _scalar(value: object) -> SafeScalar:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    return type(value).__name__


def _safe_value(key: str, value: object) -> SafeValue:
    if key in {"url", "redirect_target"}:
        return safe_url(str(value))
    if isinstance(value, Mapping):
        return {str(name): _scalar(item) for name, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_scalar(item) for item in value]
    return _scalar(value)


def _human_metadata(metadata: Mapping[str, SafeValue]) -> str:
    preferred = (
        ("action", "操作", ""),
        ("progress_percent", "进度", "%"),
        ("proxy_successes", "有效访问", ""),
        ("attempts", "已尝试", ""),
        ("proxy_count", "代理数", ""),
        ("source_name", "来源", ""),
        ("rank", "排名", ""),
        ("threshold", "阈值", ""),
        ("result_status", "结果", ""),
        ("status_code", "HTTP", ""),
        ("exception_type", "错误", ""),
        ("exit_code", "退出码", ""),
        ("challenge_kind", "验证类型", ""),
        ("frame_count", "Frame", ""),
        ("container_count", "验证码容器", ""),
        ("descendant_count", "容器子节点", ""),
        ("marker_names", "DOM 标识", ""),
        ("initial_count", "操作前", ""),
        ("refreshed_count", "操作后", ""),
    )
    parts: list[str] = []
    for key, label, suffix in preferred:
        value = metadata.get(key)
        if value is not None:
            if key == "result_status":
                value = RESULT_STATUS_LABELS.get(str(value), value)
            elif key == "action":
                value = ACTION_LABELS.get(str(value), value)
            parts.append(f"{label} {value}{suffix}")
    return " | ".join(parts)


def _human_message(
    phase: str, site: str, status: str, metadata: Mapping[str, SafeValue]
) -> str | None:
    if phase == "promotion_proxy_visit" and status != "completed":
        return None
    if phase in QUIET_HUMAN_PHASES:
        return None
    site_label = SITE_LABELS.get(site, site)
    phase_label = PHASE_LABELS.get(phase, phase.replace("_", " "))
    status_label = STATUS_LABELS.get(status, status)
    details = _human_metadata(metadata)
    message = f"[{site_label}] {phase_label}：{status_label}"
    return f"{message} | {details}" if details else message


def log_step(
    phase: str,
    *,
    site: str = "system",
    status: str = "info",
    **metadata: object,
) -> None:
    """Emit one stable JSON log record without accepting arbitrary metadata."""

    safe_metadata = {
        key: _safe_value(key, value)
        for key, value in metadata.items()
        if key in ALLOWED_METADATA_KEYS
    }
    rejected = sorted(set(metadata) - ALLOWED_METADATA_KEYS)
    if rejected:
        safe_metadata["rejected_fields"] = [REDACTED for _key in rejected]
    record = {
        "event": "step",
        "timestamp": datetime.now(UTC).isoformat(),
        "site": site,
        "phase": phase,
        "status": status,
        "metadata": safe_metadata,
    }
    logger = logging.getLogger(LOGGER_NAME)
    if os.environ.get(LOG_FORMAT_ENV, "human").strip().casefold() == "json":
        logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
        return
    message = _human_message(phase, site, status, safe_metadata)
    if message is not None:
        logger.info(message)
