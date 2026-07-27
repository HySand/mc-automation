# Site Automation Safety Contracts

## Scope

Apply this contract whenever code authenticates to KLPBBS or MineBBS, reads a target thread,
purchases/applies an official item, handles a browser challenge, or changes GitHub Actions wiring.
The automation is fail-closed: ambiguous external HTML never becomes a guessed submission.

## Configuration contract

- Site switches: `KLPBBS_ENABLED`, `MINEBBS_ENABLED` (default `false`).
- Credentials and thread IDs: `KLPBBS_USERNAME`, `KLPBBS_PASSWORD`, `KLPBBS_THREAD_ID`,
  `MINEBBS_USERNAME`, `MINEBBS_PASSWORD`, `MINEBBS_THREAD_ID`.
- `MINEBBS_THREAD_ID` accepts a positive numeric ID, a XenForo `slug.ID` segment, a
  `/threads/slug.ID/` path, or a full thread URL on `MINEBBS_BASE_URL`; configuration normalizes it
  once to the positive numeric ID before adapters run. Wrong hosts and non-thread paths fail closed.
- The local CLI loads `.env` without overriding already exported environment variables. CI remains
  environment-only because no `.env` file is checked in.
- Policy: `RANK_THRESHOLD` (default `8`), `PAID_BUMP_COOLDOWN_SECONDS` (default `3600`),
  `MINEBBS_BUMP_INTERVAL_HOURS` (default `16`).
- Runtime paths: `STATE_PATH` and `GITHUB_STEP_SUMMARY`.
- AI solver option: `AI_SOLVER_ENABLED` (default `false`) controls only the WDSJFWQ image captcha
  unless `AI_SOLVER_WDSJFWQ_CAPTCHA_ENABLED` is set explicitly. Required secrets are
  `AI_SOLVER_ENDPOINT` and `AI_SOLVER_API_KEY`; required variable is `AI_SOLVER_MODEL`. Optional
  controls are `AI_SOLVER_TIMEOUT_SECONDS` and `AI_SOLVER_MAX_ATTEMPTS`.
- Empty optional boolean values inherit their documented default; specifically, an empty
  `AI_SOLVER_WDSJFWQ_CAPTCHA_ENABLED` inherits `AI_SOLVER_ENABLED` instead of disabling the captcha
  solver.
- MineBBS ESA option: `MINEBBS_ESA_SLIDER_ENABLED` (default `false`) enables one `nodriver`
  slide-to-end attempt from DOM geometry. It requires `MINEBBS_ENABLED=true` and does not require or
  consume AI endpoint, key, model, prompt, or screenshot data.
- Optional `MINEBBS_BROWSER_EXECUTABLE_PATH` points to an existing Chromium-family browser executable when
  `nodriver` cannot auto-discover one. An empty value leaves discovery to `nodriver`.
- On Windows, if the default Chrome/Chromium discovery raises `FileNotFoundError`, the resolver may
  retry browser startup once with an installed Microsoft Edge executable. This is browser selection,
  not a second ESA challenge attempt; if no fallback exists, the resolver returns `False`.
- Site base URLs remain explicit configuration. There is no host allowlist, DNS-address restriction,
  bypass header, proxy-based WAF bypass, or public/private routing switch.
- KLPBBS promotion needs only `KLPBBS_PROMOTION_ENABLED`,
  `KLPBBS_PROMOTION_VISIT_DELAY_SECONDS`, and a required same-origin `KLPBBS_PROMOTION_URL`.
  There is no configured visit cap; one run consumes each proxy at most once and ends when the
  authenticated task page proves completion or the loaded pool is exhausted. The obsolete visit
  cap, proxy-target URL, and marker keys must not be restored.

## Transport and challenge behavior

- KLPBBS uses an authenticated `cloudscraper.CloudScraper`; other sites use `requests.Session`.
  Both run through `HttpTransport` with explicit timeouts and the same fail-closed challenge checks.
- GitHub Actions establishes a system-level Cloudflare WARP full tunnel before dependency setup and
  requires Cloudflare trace to report `warp=on` or `warp=plus`. Setup or verification failure aborts
  the job; ordinary application traffic never falls back to the runner's original egress.
- Only KLPBBS promotion-link clicks use the dynamic HTTP proxy pool. Proxy-source downloads,
  authenticated promotion task operations, and every other site request continue through WARP.
- A promotion click uses a fresh session with `trust_env=False`, cleared cookies, explicit
  `http`/`https` proxy arguments, TLS verification enabled, and the reference client's browser
  `User-Agent`, HTML `Accept`, and same-origin homepage `Referer`. It carries no authenticated
  Cookie or CSRF token. The adapter validates the initial
  promotion URL before passing it to the visitor. Discuz `index.php` redirects root promotion URLs
  before `misc_promotion.php` records the source IP, so the visitor manually follows at most three
  strictly same-origin redirects while keeping `requests` automatic redirects disabled. Missing,
  cross-origin, or excessive redirects fail that candidate.
- Free proxy probes use a short `2s` connect and `5s` read timeout. A timeout is an expected failed
  candidate: it consumes that proxy and immediately advances to the next one.
- The pool globally deduplicates and randomly shuffles every candidate returned by the bounded
  finite sources. It has no global candidate count or per-source quota. A source failure is isolated;
  otherwise all valid candidates from that source remain eligible until task completion or natural
  pool exhaustion.
- Fresh checked sources (`openproxylist-https`, `yakumo-http-checked`, and `kangproxy-https`) are
  loaded before the reference project's older aggregate sources. The source URLs are finite static
  files and remain subject to the same response-size, public-IP, port, deduplication, and timeout
  checks.
- KLPBBS promotion uses stable task ID `1`, and the doing-task list is
  `home.php?mod=task&item=doing` (not `do=doing`). The configured same-origin promotion URL supplies
  the click target because the task center does not necessarily render a per-account promotion URL.
- Discuz renders authoritative task progress in `#csc_1`. A successful proxy HTTP response is only
  a candidate visit and must not be counted as task progress. An incomplete task may still expose a
  `do=draw&id=1` link with a `rewardless.gif`; draw is allowed only when `#csc_1` is at least 100 or
  the task scope has an explicit completed marker. Failed proxy responses consume that proxy and
  continue immediately without a delay or task-page read. Proxy clicks run in bounded batches of at
  most 20 workers; authenticated task operations remain single-threaded. A batch with any successful
  response triggers one fresh authenticated progress read, and pool exhaustion triggers a final read
  before the run is skipped.
- A draw response may be opaque. It counts as successful only when it contains a known success
  marker or one fresh authenticated `item=doing` read proves task ID 1's `do=draw` link has
  disappeared. Other task ID 1 links, such as `do=apply`, do not mean the completed task remains.
- Challenge markers, HTTP 401/403/429, CAPTCHA, WAF, and access-denied pages raise
  `SecurityChallenge` and stop that site's side effects.
- If `MINEBBS_ESA_SLIDER_ENABLED=true`, only a GET/HEAD challenge may invoke visible Chromium once.
  The resolver reads the Alibaba ESA handle/track bounding boxes, moves the handle to the track end
  with a seeded-testable path shaped from a successful manual sample, synchronizes cookies and User-Agent
  only after challenge markers disappear, and retries the original GET/HEAD once. POST challenges
  are never replayed.
- WDSJFWQ image CAPTCHAs are handled only inside the WDSJFWQ like form path when
  `AI_SOLVER_WDSJFWQ_CAPTCHA_ENABLED=true`; the adapter downloads the captcha image with the same
  session, asks the model for strict JSON, validates the code shape, fills a random username, and
  submits the form once.
- If a challenge remains, the site reports `manual_intervention`; no token is guessed and no bypass
  header is sent.

## Scenario: KLPBBS Cloudscraper Transport

### 1. Scope / Trigger

- Trigger: constructing the authenticated KLPBBS transport in the CLI.
- Scope: KLPBBS only. MineBBS and one-shot like adapters retain normal `requests.Session` objects.

### 2. Signatures

- `create_cloudscraper_session() -> requests.Session`
- `HttpTransport(session=create_cloudscraper_session(), challenge_resolver=resolver)`

### 3. Contracts

- Dependency: `cloudscraper>=1.2.71`; do not substitute the unrelated `cfscraper` package.
- Preserve the CloudScraper-generated User-Agent and `CipherSuiteAdapter`.
- Apply the existing retry policy to the preserved adapter: two retries for GET/HEAD connection,
  read, and selected 5xx failures; never automatically replay POST.
- Run every response through `CloudflareWafGuard`; `cloudscraper` success does not bypass the
  application's challenge classification or one-shot AI browser policy.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| KLPBBS normal page with passive `/challenge-platform/scripts/jsd/` telemetry | Parse normally |
| Explicit `cf-chl-`, CAPTCHA, 401, 403, or 429 remains | Raise `SecurityChallenge` |
| Session adapter is not an `HTTPAdapter` subclass | Raise `TypeError` during construction |
| `cfscraper` requests a `gb-dl` license or recurses | Dependency is forbidden; do not invoke it |

### 5. Good/Base/Bad Cases

- Good: KLPBBS gets a `CloudScraper` and keeps its `CipherSuiteAdapter` after transport setup.
- Base: MineBBS/WDSJFWQ/MCLISTS get ordinary `requests.Session` transports.
- Bad: install `cloudscraper` but then replace its TLS adapter with a plain `HTTPAdapter`.

### 6. Tests Required

- Assert `create_cloudscraper_session()` returns a session accepted by `HttpTransport`.
- Assert its User-Agent and concrete HTTPS adapter class survive transport construction.
- Assert the preserved adapter receives `Retry(total=2)`.
- Probe KLPBBS homepage, login page, and forum list with read-only GET requests before release.
- KLPBBS may alternate between a complete forum list and an incomplete HTTP 200 shell. Rank parsing
  tries both the rewritten `forum-56-1.html` path and canonical
  `forum.php?mod=forumdisplay&fid=56&page=1` path. It accepts explicit `normalthread_<ID>` DOM rows,
  with a fallback to the same table's `th.new a.xst` subject links. Both incomplete responses remain
  a parse failure and never lead to inventory or spending actions. Logs record only the parsed normal
  thread count, not titles, authors, or HTML.

### 7. Wrong vs Correct

#### Wrong

```python
HttpTransport()  # KLPBBS silently uses the generic session
```

#### Correct

```python
HttpTransport(session=create_cloudscraper_session())
```

## Scenario: OpenAI-Compatible WDSJFWQ Captcha Solver

### 1. Scope / Trigger

- Trigger: `AI_SOLVER_ENABLED=true` or `AI_SOLVER_WDSJFWQ_CAPTCHA_ENABLED=true`.
- Scope: WDSJFWQ one-shot like image captcha only.
- Out of scope: arbitrary CAPTCHA fields, challenged POST replay, token fabrication, proxy bypass,
  and using AI output when the page or response cannot be validated.

### 2. Signatures

- `AISolverConfig(enabled, endpoint, api_key, model, timeout_seconds, max_attempts,
  wdsjfwq_captcha_enabled)`
- `OpenAICompatibleVisionSolver.solve_wdsjfwq_captcha(image: bytes, content_type: str | None) -> CaptchaSolution`
- `LikeAdapter(config, transport, captcha_solver=solver, username_factory=...)`

### 3. Contracts

- Endpoint is an OpenAI-compatible Chat Completions base URL such as `https://host/v1`, or a full
  `/chat/completions` URL. The client sends `Authorization: Bearer <AI_SOLVER_API_KEY>` and never
  prints the key, endpoint, image bytes, captcha code, or raw model response.
- Prompt output must be strict JSON: `{"code":"TEXT","confidence":0.0}`.
- WDSJFWQ accepts only 3-8 alphanumeric captcha characters, fills exactly one username field with a
  generated `PlayerNNNNNN`, includes the unique named like-submit button as a browser would, and
  submits the discovered form once.
- WDSJFWQ may answer a processed form with an opaque HTTP 302 body and no success message. The
  adapter records the unambiguous public like count before submission and accepts the result only if
  the response body or one fresh read-only GET shows a strictly larger, internally consistent count.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| AI feature enabled without endpoint, key, or model | `ConfigurationError` naming only missing keys |
| Model output is not strict JSON | `AISolverError`; no form/challenge submission |
| WDSJFWQ code empty or non-alphanumeric | `manual_intervention`; no POST |
| WDSJFWQ captcha image cannot be located | `manual_intervention`; no POST |
| Challenged POST | Resolver is not invoked; POST is never replayed |

### 5. Good/Base/Bad Cases

- Good: WDSJFWQ form contains username, captcha, and one captcha image; solver returns `AB12`; adapter
  posts hidden fields plus generated username and `AB12`.
- Base: AI solver disabled; WDSJFWQ interactive form returns `manual_intervention`.
- Bad: Treat a prose response like `captcha is AB12` as usable.

### 6. Tests Required

- Assert OpenAI-compatible request body includes model, prompt text, image data URL, timeout, and bearer
  auth without logging the secret.
- Assert WDSJFWQ successful solver path downloads the image and submits username/captcha data.
- Assert invalid WDSJFWQ model output returns `manual_intervention` without POST.

### 7. Wrong vs Correct

#### Wrong

```python
data["captcha"] = ""  # submit an empty or guessed value
```

#### Correct

```python
solution = solver.solve_wdsjfwq_captcha(image.content, content_type="image/png")
if CAPTCHA_CODE_PATTERN.fullmatch(solution.code):
    data["captcha"] = solution.code
```

## Scenario: Non-AI Alibaba ESA Slider Resolver

### 1. Scope / Trigger

- Trigger: `MINEBBS_ESA_SLIDER_ENABLED=true` and a MineBBS GET/HEAD response matches ESA slider
  challenge markers.
- Scope: Alibaba ESA slide-to-end pages exposing the fixed handle and track DOM elements.
- Out of scope: screenshot recognition, model calls, jigsaw-gap estimation, token fabrication,
  challenged POST replay, fingerprint spoofing, and coordinate guessing when DOM geometry is absent.

### 2. Signatures

- `EsaSliderChallengeResolver(wait_seconds=15.0, drag_steps=120, drag_duration_ms=465, headless=False, browser_executable_path=None, random_source=None)`
- `EsaSliderChallengeResolver.resolve(url, session, timeout) -> bool`
- `EsaSliderChallengeResolver.HANDLE_SELECTOR = "#aliyunCaptcha-sliding-slider"`
- `EsaSliderChallengeResolver.TRACK_SELECTOR = "#aliyunCaptcha-sliding-wrapper"`

### 3. Contracts

- Configuration is rejected unless `MINEBBS_ENABLED=true`; no `AI_SOLVER_*` value is required.
- The resolver imports `nodriver` lazily, creates and owns a temporary profile before browser
  startup, launches visible Chromium with that explicit profile, copies request cookies into the
  browser, and navigates only to the challenged URL. The profile is removed after successful use,
  failed startup, navigation failure, or protocol failure.
- Start is the handle center. The track clamp point is `track.right - handle.width / 2`. For the
  observed 320 px track distance, the generated path has about 61 points over 465 ms: an initial
  stationary/1 px probe, monotonic X movement, smooth 7-10 px upward drift, and a final pointer
  position 35-40% beyond the clamp point before release. The page clamps the handle itself at the
  track end. Production entropy is local; tests inject a seeded `random.Random` instance for
  reproducibility.
- Each movement point is scheduled against an absolute monotonic deadline:
  `target_elapsed = drag_duration_ms * step / drag_steps`; the wait after each CDP mouse event is the
  remaining time only. Do not add a fixed delay after every browser protocol call, because cross-process
  call overhead otherwise stretches a configured drag once per event. The resolver
  uses low-level `Input.dispatchMouseEvent`; nodriver's high-level `mouse_move()` helper is not used
  because it releases the button after each move.
- Movement deadlines are mildly irregular rather than evenly spaced. Press uses
  `button=left, buttons=1`; held movement uses native-like `button=none, buttons=1`; release uses
  `button=left, buttons=0`. Release occurs immediately at the endpoint: do not add a motionless
  endpoint dwell to the sampled drag behavior.
- ESA's current dynamic module rotates (`dynamicJS/3.28.0/sg.*.js`) and the browser reduces
  effective `clientX/clientY` values to integers. Dynamic filenames, hashes, internal variable
  names, and encrypted payload values are not implementation contracts. A successful HTTP verify
  response (`Code=Success`, `Success=true`) can still contain `Result.VerifyResult=false`; only a
  clear challenge page permits session synchronization and the single original GET/HEAD retry.
- A hidden slider or numerically human-like event trace is not clearance evidence. A focused
  Windows `SendInput` replay of the successful manual trace, including a run with mouse-move
  coalescing disabled, produced trusted DOM events and `left=320px` but still received
  `Result.VerifyResult=false`. Do not promote an input route based only on unit path tests; require a
  live service decision that removes the challenge page. Do not repeatedly tune path parameters
  after the service has rejected both CDP and native injected replays of the same accepted sample.
- Browser cookies and `navigator.userAgent` are copied back only after
  `detect_security_challenge(200, await tab.get_content())` reports clear and browser cleanup
  succeeds.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| `nodriver` missing or browser launch fails | Return `False`; transport raises `SecurityChallenge` |
| Handle or track missing/not visible | Return `False`; no mouse input or session sync |
| Non-positive dimensions or track not wider than handle | Return `False`; no drag |
| Drag completes but challenge remains | Return `False`; no cookie/User-Agent sync |
| Browser/profile cleanup fails | Return `False`; no cookie/User-Agent sync |
| Challenge clears | Sync browser session and retry the original GET/HEAD once |
| Challenged POST | Resolver is not invoked; POST is never replayed |

### 5. Good/Base/Bad Cases

- Good: a 40 px handle on a 360 px track yields a 320 px slide; the challenge clears and session
  state is synchronized.
- Base: feature disabled; the detected ESA challenge immediately becomes `manual_intervention`.
- Bad: send a viewport screenshot to `OpenAICompatibleVisionSolver` or use hard-coded viewport
  coordinates unrelated to the current DOM geometry.

### 6. Tests Required

- Assert the resolver queries both fixed selectors and calculates the final handle center at the
  track end.
- Assert the successful-sample shape: first-frame probing, monotonic X, smooth bounded upward drift,
  endpoint overshoot, mouse down, dense intermediate moves, and mouse up without any solver call.
- Assert the absolute timing schedule subtracts injected CDP event-call overhead and keeps the
  movement segment within the configured duration budget.
- Assert movement events use native `button/buttons` semantics, deadlines are non-uniform, and no
  endpoint dwell occurs before release.
- Assert cookies/User-Agent copy only after clear, the browser profile closes on all paths, missing
  geometry is unresolved, and missing `nodriver`/Chromium is unresolved.
- Assert the resolver-owned temporary profile is removed after both successful startup and failed
  browser launch; no `uc_*` or resolver-prefixed profile is retained by the normal path.
- Assert configuration/workflow install browser support from `MINEBBS_ESA_SLIDER_ENABLED`, not from
  any `AI_SOLVER_*` ESA key.
- Assert a missing Chrome/Chromium binary can use the single Edge fallback and that a second browser
  launch is not attempted when no fallback is available.
- Treat these as contract tests, not an ESA acceptance oracle. A live probe must additionally prove
  that the verification response is accepted and the challenge marker disappears.

### 7. Wrong vs Correct

#### Wrong

```python
screenshot = await tab.screenshot()
solution = ai_solver.solve_esa_slider(screenshot)
await tab.send(cdp.input_.dispatch_mouse_event("mouseMoved", solution.end.x, solution.end.y))
```

#### Correct

```python
handle = await tab.select(HANDLE_SELECTOR)
track = await tab.select(TRACK_SELECTOR)
handle_box = await handle.get_position()
track_box = await track.get_position()
end_x = track_box.x + track_box.width - handle_box.width / 2
```

### Common Mistake: Fixed per-point waits

```python
# Wrong: every browser protocol call adds its own unbudgeted latency.
for point in points:
    await tab.send(cdp.input_.dispatch_mouse_event("mouseMoved", *point, buttons=1))
    await asyncio.sleep(drag_duration_ms / len(points) / 1000)
```

```python
# Correct: schedule against one monotonic timeline.
drag_started = resolver._monotonic()
for step, point in enumerate(points, 1):
    await tab.send(cdp.input_.dispatch_mouse_event("mouseMoved", *point, buttons=1))
    target_ms = drag_duration_ms * step / len(points)
    remaining_ms = target_ms - (resolver._monotonic() - drag_started) * 1000
    if remaining_ms > 0:
        await asyncio.sleep(max(1, round(remaining_ms)) / 1000)
```

## State and side effects

State is versioned, atomic JSON containing only operational timestamps, challenge count, and suspension
deadline. Credentials, cookies, CSRF tokens, response bodies, API keys, and extension paths are never
written to state, summaries, or ordinary logs. Unknown rank, owner, balance, inventory, form, CSRF
token, or target option raises `SiteParseError` before a side effect.

All execution paths emit JSONL step logs through the closed metadata allowlist in `step_log.py`.
HTTP URLs retain only scheme, host, port, and path. WDSJFWQ logs image size, model attempt status,
confidence, code length, field names, and public count changes without image/code/form values. ESA
logs browser lifecycle, DOM dimensions, drag point count/duration, clearance, and cookie counts
without cookie values, browser profile paths, or page bodies.

## Validation matrix

| Condition | Required behavior |
|---|---|
| Enabled site lacks a required key | Redacted `manual_intervention`; perform no network calls |
| Both sites disabled | `skipped`, exit zero |
| Challenge in default mode | Raise `SecurityChallenge`; do not retry |
| GET/HEAD MineBBS ESA challenge with DOM slider enabled | One DOM-derived drag attempt, synchronize session only after clear, retry once |
| WDSJFWQ captcha form with AI solver enabled | Download captcha image, require strict JSON code, submit one filled form |
| Challenged POST or interactive challenge not cleared | Raise `SecurityChallenge`; never replay |
| Three consecutive challenges | Persist a 24-hour suspension |
| Unknown/ambiguous markup or ownership mismatch | Raise `SiteParseError`; no purchase/use submission |
| Transient timeout or selected GET 5xx | Bounded transport retry; POST is not automatically retried |
| KLPBBS rank at or below threshold | `skipped`; do not query inventory or spend |
| MineBBS interval has not elapsed | `skipped`; do not query rank, inventory, or balances |

## Required tests

Tests must cover configuration redaction, disabled-site behavior, challenge detection, one-shot
ESA DOM browser retry, WDSJFWQ captcha form filling, strict model JSON parsing, Cookie/User-Agent
synchronization, challenged POST no-replay, challenge accumulation, cooldown/interval boundaries,
ownership and parser ambiguity, state redaction, cross-site failure isolation, WARP fail-closed
workflow wiring, and promotion-click proxy isolation. The quality gate is:

```text
ruff format --check .
ruff check .
mypy src
python -m pytest --cov=mc_automation --cov-report=term-missing
```
