from mc_automation.security import detect_security_challenge, redact


def test_detects_aliyun_slider() -> None:
    assert detect_security_challenge(200, "<title>滑动验证页面</title>") is not None


def test_detects_forbidden_status() -> None:
    assert detect_security_challenge(403, "denied") is not None


def test_detects_cloudflare_managed_challenge_copy() -> None:
    assert detect_security_challenge(200, "<title>Just a moment...</title>") is not None
    assert detect_security_challenge(200, "Verify you are human") is not None


def test_allows_cloudflare_telemetry_script_on_normal_page() -> None:
    html = '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
    assert detect_security_challenge(200, html) is None


def test_redacts_explicit_and_named_secrets() -> None:
    value = redact("password=hunter2 token=abc user hunter2", ["hunter2"])
    assert "hunter2" not in value
    assert "token=***" in value
