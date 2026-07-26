# Implementation Plan

## 1. Project foundation

- [x] Create Python 3.11 package metadata, runtime/dev dependencies, README, `.env.example`, and ignore rules.
- [x] Implement typed configuration with site-level enable flags and secret-safe validation errors.
- [x] Implement result/status models, redaction, and structured logging.

Validation:

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests/test_config.py tests/test_security.py
```

Rollback point: foundation files only; no site operations exist yet.

## 2. Core orchestration and state

- [x] Implement versioned atomic JSON state with corrupt/missing-state recovery.
- [x] Implement Shanghai-date helpers, rank threshold, paid cooldown, single-transaction guard, challenge suspension, and per-site isolation.
- [x] Implement normal HTTP transport with bounded retry and challenge-page detection.
- [x] Implement Markdown/JSON run summaries and deterministic exit codes.

Validation:

```powershell
python -m pytest tests/test_state.py tests/test_policy.py tests/test_transport.py tests/test_orchestrator.py
```

Rollback point: core remains testable with fake adapters.

## 3. KLPBBS adapter

- [x] Implement Discuz authentication and `formhash` parsing.
- [x] Implement sign-in and forum-56 rank parsing; remove the forum-reply path and reply-text configuration.
- [x] Implement official bump-card inventory/purchase/use; omit every proxy/promotion-click path from the reference project.
- [x] Add sanitized HTML fixtures for success, insufficient balance, flood control, login failure, and changed markup.

Validation:

```powershell
python -m pytest tests/test_klpbbs.py
```

Rollback point: `KLPBBS_ENABLED=false` disables the adapter independently.

## 4. MineBBS adapter

- [x] Implement XenForo login form discovery and authentication verification.
- [x] Implement sign-in, `/servers/` rank parsing, balances, inventory, and challenge detection.
- [x] Implement unique-form discovery for purple/gold card purchase and use; do not add a reply path.
- [x] Add sanitized fixtures for card inventory, purchases, daily gold limit, insufficient resources, WAF challenge, and changed markup.

Validation:

```powershell
python -m pytest tests/test_minebbs.py
```

Rollback point: `MINEBBS_ENABLED=false` disables the adapter independently.

## 5. GitHub Actions integration

- [x] Add hourly/manual workflow, Python setup, dependency cache, state restore/save, concurrency, summary publication, and delayed failure propagation.
- [x] Document required Secrets/Variables and first-run conservative behavior.
- [x] Add a no-network dry-run path using fixtures/fake adapters for CI validation.

Validation:

```powershell
python -m mc_automation.cli --dry-run
python -m pytest
```

Review workflow YAML for secret interpolation and confirm state cache contains no secrets.

## 6. Full quality gate

- [x] Run formatter/linter, type checker, and full tests.
- [x] Verify every PRD acceptance criterion with tests or an explicit manual check.
- [x] Search repository for prohibited mechanisms and accidental secrets.
- [x] Run Trellis quality check; update specs with durable conventions learned during implementation.

Validation:

```powershell
ruff format --check .
ruff check .
mypy src
python -m pytest --cov=mc_automation --cov-report=term-missing
rg -n -i "cloudscraper|flaresolverr|captcha.*solver|proxy[_ -]?pool|checkerproxy|proxyscrape" .
```

Final rollback: disable both site variables to leave the workflow installed but inert, or revert the workflow and adapter commits while retaining non-secret state for diagnosis.

## 7. Browser challenge transport

- [x] Add explicit browser-challenge configuration and bounded promotion visit settings.
- [x] Validate configured initial URLs and redirects before requests.
- [x] Add a single-attempt `nodriver` challenge resolver with low-level CDP mouse events and cookie/session synchronization.
- [x] Preserve the existing fail-closed `SecurityChallenge` behavior when the extension is disabled or cannot clear a challenge.

Validation:

```powershell
python -m pytest tests/test_config.py tests/test_transport.py tests/test_security.py
```

## 8. KLPBBS official promotion task

- [x] Extend the adapter contract and orchestrator with an optional promotion-task step after sign-in.
- [x] Discover apply/draw/progress/promotion controls from authenticated server HTML without a hard-coded task ID.
- [x] Visit promotion links serially with a configured cap and delay; recheck after each visit and draw only after conclusive completion.
- [x] Add tests for available, already doing, incomplete, complete/draw, unknown markup, challenge, and visit cap behavior.

Validation:

```powershell
python -m pytest tests/test_klpbbs.py tests/test_orchestrator.py
```

## 9. Documentation and full regression

- [x] Document router-DNS/self-hosted-runner requirements, browser installation, defaults, and public-target refusal.
- [x] Update the workflow and `.env.example` without adding credentials or cookies.
- [x] Run the complete format, lint, type, test, dependency, and target-safety checks.

## 10. MineBBS interval eligibility

- [x] Add `MINEBBS_BUMP_INTERVAL_HOURS` with a default of 16 and pass it through CLI configuration.
- [x] Add an adapter capability flag so MineBBS bypasses rank eligibility while KLPBBS keeps it.
- [x] Measure MineBBS eligibility from the last successful card application; sign-in and target ownership verification remain active.
- [x] Test that MineBBS skips before 16 hours, runs at 16 hours without requesting rank, and KLPBBS still uses rank eligibility.
- [x] Update README, workflow variables, code-spec, and verification evidence.

## 11. Isolated dynamic proxy promotion

- [ ] Add an explicit public IP-literal promotion target and response-marker configuration; reject domains, credentials, non-root paths, non-global addresses, and missing markers.
- [ ] Implement the reference project's six dynamic HTTP proxy sources with bounded reads/timeouts, source failure isolation, strict public IP/port parsing, deterministic deduplication, and a candidate cap.
- [ ] Add a cookie-free promotion visitor that maps only the discovered path/query to the isolated target, uses each proxy once, keeps TLS verification enabled, rejects redirects, and requires the target marker.
- [ ] Integrate the visitor into KLPBBS promotion while keeping apply/status/draw on the authenticated private transport; recheck status only after successful marked visits.
- [ ] Update workflow/environment documentation, backend contracts, and tests for source parsing/failure isolation, configuration rejection, target mapping, marker failure, single-use proxies, cap behavior, and credential separation.

Validation:

```powershell
python -m pytest tests/test_promotion_proxy.py tests/test_klpbbs.py tests/test_config.py
ruff format --check .
ruff check .
mypy src
python -m pytest --cov=mc_automation --cov-report=term-missing
```

## 12. Authorized one-shot like adapters

- [ ] Add independently configured wiring for WDSJFWQ and MCLISTS target pages.
- [ ] Add a one-shot adapter contract and orchestrator branch that preserves site isolation and challenge suspension without entering bump/card flows.
- [ ] Parse explicit already-liked markers and exactly one like link/form; fail closed on ambiguous markup.
- [ ] Add tests for independently configured target pages, link/form submissions, already-liked skips, ambiguous markup, and per-site failure isolation.

## 13. Non-AI Alibaba ESA slider integration

- [x] Remove the ESA screenshot prompt, slider output schema, and model-based coordinate parser.
- [x] Add `MINEBBS_ESA_SLIDER_ENABLED` as an independent opt-in that does not require AI secrets.
- [x] Resolve the slide-to-end distance from fixed Alibaba ESA handle/track DOM geometry and execute
  a successful-manual-sample-shaped `nodriver` CDP mouse path with bounded vertical motion.
- [x] Synchronize cookies and User-Agent only after challenge markers disappear and retain the
  one-shot GET/HEAD retry plus POST no-replay contract.
- [x] Update workflow, environment template, README, tests, and backend code-spec; verify that no ESA
  path calls `OpenAICompatibleVisionSolver`.

### 13.1 Reverse-evidence timing refinement

- [x] Inspect the live rotating ESA module, rendered configuration, browser listeners, event
  metadata, request field names, and verification result without retaining encrypted values.
- [x] Replace fixed per-point waits with absolute monotonic scheduling that subtracts synchronous
  `mouse.move()` overhead from the remaining drag budget.
- [x] Add a fake-clock regression proving browser-call latency is not accumulated after every point.
- [x] Record the sanitized reverse-engineering evidence under `research/` and update the backend
  contract and verification record.

### 13.2 nodriver migration

- [x] Replace the previous browser optional dependency and workflow installation with `nodriver`.
- [x] Preserve DOM-only geometry, absolute drag timing, one-shot GET/HEAD retry, fail-closed POST
  handling, and post-clear Cookie/User-Agent synchronization.
- [x] Add an optional `MINEBBS_BROWSER_EXECUTABLE_PATH` for environments where nodriver cannot
  auto-discover Chrome/Chromium.
- [x] Rewrite resolver fakes and regressions around asynchronous CDP dispatch, browser cleanup,
  startup failure, and event-loop boundaries.

### 13.3 Humanized Alibaba drag profile

- [x] Capture a sanitized successful physical-mouse sample and replace the speculative four-stage
  profile with its observed shape: about 465 ms, 61 moves, monotonic X, smooth upward drift, and an
  intentional release beyond the clamped track endpoint.
- [x] Inject a seeded random source in tests and cover probe shape, monotonic movement, bounded
  vertical drift, endpoint overshoot, reproducibility, event order, and CDP failure cleanup.

### 13.4 Manual-vs-CDP behavior correction

- [x] Prove that a physical mouse drag clears ESA in the same temporary `nodriver` environment where
  the automated CDP drag is rejected.
- [x] Match native held-move semantics with `button=none, buttons=1` instead of reporting a left
  transition button on every frame.
- [x] Randomize absolute movement deadlines without exceeding the configured drag budget and release
  immediately at the endpoint instead of adding a 180 ms stationary hold.

### 13.5 Native-input discriminating probe

- [x] Bring the resolver-owned browser to the foreground before every Windows native-input probe so
  unrelated foreground applications cannot invalidate the result.
- [x] Replay the successful manual trace through `SendInput`, then repeat with
  `MOUSEEVENTF_MOVE_NOCOALESCE`; require actual DOM down/move/up events and `left=320px` as the
  validity gate.
- [x] Capture only non-secret verification result fields. Both valid native-input probes issued one
  verify request and returned `Success=true`, `VerifyResult=false`; do not integrate this known-failing
  input route into the production resolver or continue tuning numeric path parameters.

## 14. Complete redacted step logging

- [x] Add a single JSONL step logger with a closed metadata allowlist and URL sanitization.
- [x] Instrument CLI, orchestration, HTTP, WDSJFWQ captcha/model/form/count confirmation, and ESA
  browser/DOM/drag/session lifecycle.
- [x] Add regression tests proving complete phase visibility without credentials, captcha text,
  image bytes/Base64, form values, URL query values, or raw bodies.
- [x] Document the logging contract and required negative tests.

Validation:

```powershell
python -m pytest tests/test_challenge.py
ruff format --check .
ruff check .
mypy src
python -m pytest --cov=mc_automation --cov-report=term-missing
```
