# Technical Design

## Architecture

Implement a Python 3.11 command-line application with four boundaries:

1. `config` reads and validates environment-only configuration.
2. `core` owns typed results, persisted state, eligibility rules, retries, redaction, and orchestration.
3. `sites` contains one adapter per site behind a shared protocol.
4. `.github/workflows` restores state, runs the same CLI for schedule/manual triggers, saves state, and publishes a summary.

No frontend or database is required for the MVP.

## Proposed Layout

```text
src/mc_automation/
  cli.py
  config.py
  models.py
  orchestrator.py
  security.py
  state.py
  transport.py
  sites/
    base.py
    klpbbs.py
    minebbs.py
tests/
  fixtures/
  test_*.py
.github/workflows/automation.yml
pyproject.toml
.env.example
README.md
```

## Contracts

### Site adapter

Each adapter exposes:

- `authenticate()`
- `daily_sign_in()`
- `get_resources()`
- `get_inventory()`
- `get_thread_rank()`
- `purchase_bump_item()`
- `apply_bump_item()`

Each adapter also declares `uses_rank_eligibility`. KLPBBS sets it to `True`; MineBBS sets it
to `False`, so the orchestrator skips ranking as an eligibility gate for MineBBS.

Methods return typed `ActionResult` values instead of raising for expected site outcomes. Transport/programming failures may raise typed exceptions which the orchestrator isolates per site.

### Result model

`ActionStatus` values are `success`, `skipped`, `insufficient_resources`, `manual_intervention`, and `technical_failure`. A result contains site, action, status, timestamp, public message, optional resource delta, and safe metadata. It cannot contain raw response bodies, credentials, cookies, or tokens.

### Persisted state

`state.json` is versioned and contains only non-secret operational data:

- last successful paid bump per site/thread
- MineBBS daily gold-card purchase date
- last run/result summary

Writes are atomic. Missing/corrupt state starts in conservative recovery mode: read-only checks and sign-in may run, but purchase/use waits until site evidence reconstructs eligibility.

## Data Flow

```text
GitHub schedule/manual trigger
  -> restore non-secret state cache
  -> validate Secrets/Variables
  -> for each enabled site (isolated)
      -> authenticate
      -> challenge detection
      -> daily sign-in
      -> rank check when the adapter uses rank eligibility
      -> site-specific eligibility/interval check
      -> one bump transaction at most
      -> structured result
  -> atomically save state
  -> write Markdown Job Summary
  -> non-zero exit for manual intervention or technical failure
  -> save state cache even when the CLI reports failure
```

## HTTP and Security-Challenge Handling

- KLPBBS uses a dedicated authenticated `cloudscraper.CloudScraper`; all other adapters use a
  normal `requests.Session`. `HttpTransport` preserves the CloudScraper User-Agent and
  `CipherSuiteAdapter` while applying the same bounded retry and fail-closed challenge checks.
  Proxy support exists only in the separate authorized-promotion visitor described below.
- Retry only connection errors, timeouts, and selected 5xx responses with bounded exponential backoff.
- Inspect status, title, and bounded body markers for Cloudflare, Alibaba ESA, sliders, CAPTCHA, access denied, and login throttling before parsing business data.
- Challenge detection returns `manual_intervention`, increments challenge state, and prevents all subsequent side effects for that site.
- HTML parsers fail closed when expected forms, CSRF tokens, ownership markers, balances, inventory, or success messages are absent.

### Non-AI Alibaba ESA slider handling

- `MINEBBS_ESA_SLIDER_ENABLED` is an explicit opt-in and is valid only when MineBBS is enabled.
- The resolver launches visible Chromium through free `CloakBrowser`, reuses request-session cookies, and locates
  `#aliyunCaptcha-sliding-slider` plus `#aliyunCaptcha-sliding-wrapper` after the page renders.
- Drag coordinates come only from the handle and track bounding boxes. A 232-point cubic Bezier
  approach first supplies the pointer history ESA evaluates before `mousedown`; the held path is a
  61-point cubic Bezier curve scaled from the successful manual sample. Its
  press point is sampled within the handle, and its actual grab offset determines the clamp
  coordinate. The final point crosses that boundary by 36-40% of effective travel.
  The input sequence contains unheld `mouseMoved` approach events, one `mousePressed`, and held
  `mouseMoved` events; it does not inject a release. Tests inject a seeded random source while
  production uses local entropy. Each
  sample is scheduled against one absolute monotonic timeline so synchronous or CDP call cost is
  deducted from the remaining wait instead of extending the total drag. No screenshot, model
  request, coordinate guess, extension, or fabricated verification token is used.
- The rotating `dynamicJS/3.28.0/sg.*.js` filename and encrypted verification fields are treated as
  implementation details. The stable contract is the fixed DOM geometry plus challenge clearance;
  HTTP `Success=true` with `Result.VerifyResult=false` remains an unresolved challenge.
- Live acceptance is not inferred from path-shape tests or a hidden slider. A focused Windows
  `SendInput` replay can produce trusted DOM events, hide the control, and still receive
  `VerifyResult=false`; only disappearance of the challenge page establishes clearance. A live
  public probe proved that preserving the pre-press approach history clears the same challenge.
- A Runner may receive a Cloudflare managed-challenge Turnstile frame instead of ESA. The browser
  treats that Frame as unresolved until one unique visible standard checkbox/label can be approached
  with a bounded Bezier pointer path and clicked once; missing or ambiguous controls are never guessed.
- Cookies and browser User-Agent are synchronized only after challenge markers disappear, and later
  same-origin MineBBS requests remain on the Chromium transport. Missing geometry, invalid dimensions,
  unavailable `CloakBrowser`/Chromium, failed verification, and all challenged POST requests remain
  `manual_intervention`.
- Promotion authentication, task apply/status, and reward draw continue through the guarded authenticated transport.
- The GitHub Actions job establishes a system-level Cloudflare WARP full tunnel before dependency installation and verifies `warp=on`; failure aborts the job instead of using the runner's original egress.
- Promotion visits use a separate session with no cookies, credentials, CSRF tokens, environment proxies, or authenticated Referer. The visitor fetches bounded candidate lists from the reference sources through WARP, isolates source failures, validates public IP-literal HTTP proxy endpoints, deduplicates them, and consumes each endpoint once.
- After the KLPBBS adapter validates the discovered promotion URL against its configured origin, the visitor sends that exact URL through the selected HTTP proxy. The authenticated task transport never uses the dynamic proxy pool.
- Proxy attempts run in bounded batches of at most 20 workers, with a delay between effective
  batches; redirects are rejected and normal TLS certificate verification remains enabled. Failed
  proxies are skipped until the pool is exhausted. A successful 2xx response is not progress; a
  completed batch only triggers one authenticated read of Discuz `#csc_1`. Completion is based on
  server progress, not request count.

## Site-Specific Design

### KLPBBS

- Discuz session login and `formhash` extraction.
- Sign-in follows the exact server-provided sign-in link.
- Rank is parsed from forum 56 normal-thread IDs; absent/ambiguous targets produce a safe failure unless the page conclusively proves the target is beyond the first page.
- No forum reply method or reply-text configuration exists.
- Official `bump` magic inventory, purchase, and application forms are parsed/submitted with current `formhash`; no forum-reply path is implemented.
- The adapter discovers the official promotion task from server HTML, applies it when available,
  follows only the guarded promotion URL, rechecks authoritative task progress after candidate
  visits, and draws only after the server reports 100% or an explicit completed state.

### MineBBS

- XenForo login form and `_xfToken` are discovered from server-rendered forms.
- Sign-in, tool purchase, inventory, and use actions submit only unique forms/links discovered on authenticated pages; ambiguous or absent controls fail closed.
- Rank parsing remains available for diagnostics, but orchestration does not call it as an
  eligibility gate.
- No forum reply method exists in the shared adapter contract.
- Existing unexpired purple/gold bump cards are preferred. Purchase priority is purple, then gold; only one card is bought for immediate use per run.
- `MINEBBS_BUMP_INTERVAL_HOURS` defaults to 16. The interval is measured from the last successful
  card application, not from fixed wall-clock slots. Sign-in still runs on every scheduled job.

## GitHub Actions

- Python 3.11, dependency caching, hourly UTC cron, and `workflow_dispatch`.
- `concurrency` group with `cancel-in-progress: false` prevents overlapping spend.
- Secrets: usernames/passwords and thread IDs. Variables enable/disable each site.
- `actions/cache/restore` and `actions/cache/save` use unique run keys plus a stable restore prefix for `state.json`.
- State save runs with `if: always()`; the automation exit code is captured so state and summary are preserved before the job is failed.
- ESA browser support is opt-in through `MINEBBS_ESA_SLIDER_ENABLED`; the workflow installs the
  `nodriver` browser extra only when enabled. Each resolution uses at most three independent visible
  Chromium profiles, stops at the first successful clearance, and then performs one bounded GET/HEAD retry.

## Compatibility and Rollback

- Site HTML is an external contract and can change without notice. Parsers use narrowly scoped fixtures and explicit marker checks.
- Endpoint/form changes should result in safe skips or technical failures, never guessed submissions.
- Rollback is disabling the affected site variable or reverting the adapter/workflow change. State schema migrations must retain backward-compatible readers or create a backup before conversion.

## Trade-offs

- GitHub-hosted runners are operationally simple but may be classified as IDC traffic. The design accepts intermittent manual-intervention failures rather than evading defenses.
- Dynamic form discovery is more tolerant of token changes but deliberately refuses ambiguous pages, favoring account safety over availability.
- Cache persistence is best-effort; conservative recovery may skip one cycle after eviction rather than risk duplicate spending.
