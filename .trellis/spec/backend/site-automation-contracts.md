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
- MineBBS ESA option: `MINEBBS_ESA_SLIDER_ENABLED` (default `false`) enables up to three independent
  `CloakBrowser` slide-to-end attempts from DOM geometry. After a challenged GET/HEAD succeeds, the
  MineBBS transport remains on the same-origin Chromium network stack instead of returning to
  `requests`. It requires `MINEBBS_ENABLED=true` and does not require or consume AI endpoint, key,
  model, prompt, or screenshot data.
- `MINEBBS_BROWSER_EXECUTABLE_PATH` remains accepted for backwards-compatible local configuration,
  but the free `cloakbrowser==0.3.32` package downloads and uses its pinned Chromium v146 binary.
- GitHub Actions installs the optional browser extra and runs `python -m cloakbrowser install`; no
  license key or external browser executable is required.
- On Windows, if the default Chrome/Chromium discovery raises `FileNotFoundError`, the resolver may
  retry browser startup once with an installed Microsoft Edge executable. This is browser selection,
  not a second ESA challenge attempt; if no fallback exists, the resolver returns `False`.
- Site base URLs remain explicit configuration. There is no host allowlist, DNS-address restriction,
  bypass header, proxy-based WAF bypass, or public/private routing switch.
- KLPBBS promotion needs only `KLPBBS_PROMOTION_ENABLED`,
  `KLPBBS_PROMOTION_VISIT_DELAY_SECONDS`, and a required same-origin `KLPBBS_PROMOTION_URL`.
  GitHub Actions injects `KLPBBS_PROMOTION_URL` from an Actions Secret so the account-specific
  referral value is masked in job environment output; local `.env` usage keeps the same key.
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
  `http`/`https` proxy arguments, and the reference client's browser
  `User-Agent`, HTML `Accept`, and same-origin homepage `Referer`. It carries no authenticated
  Cookie or CSRF token. The adapter validates the initial
  promotion URL before passing it to the visitor. The click request mirrors the known-working
  reference: a 10-second timeout, automatic redirects, and proxy-compatible disabled certificate
  verification. Matching the reference implementation, a response counts as a transport success
  when it is HTTP 200; KLPBBS may consume `fromuid` and rewrite the final landing URL. This transport
  result only triggers an authenticated task-progress read and never proves task completion itself.
- Any `requests` transport failure from an untrusted proxy, including timeout, connection, TLS,
  proxy, and truncated/chunked response errors, is an expected failed candidate: it consumes that
  proxy and immediately advances to the next one without failing the KLPBBS adapter.
- The pool globally deduplicates every candidate returned by the bounded finite sources and shuffles
  within each source. It preserves source order so fresh checked lists are consumed before older
  aggregates. It has no global candidate count or per-source quota. A source failure is isolated;
  otherwise all valid candidates from that source remain eligible until task completion or natural
  pool exhaustion.
- Fresh checked sources (`proxifly-http`, `openproxylist-https`, `yakumo-http-checked`, and
  `kangproxy-https`) are loaded before the reference project's older aggregate sources. The source
  URLs are finite static files and remain subject to the same response-size, public-IP, port,
  deduplication, and timeout checks. `proxifly-http` has a live acceptance probe proving one
  configured `/?fromuid=...` visit advanced Discuz `#csc_1` from 0 to 10.
- KLPBBS promotion uses stable task ID `1`, and the doing-task list is
  `home.php?mod=task&item=doing` (not `do=doing`). The configured same-origin promotion URL supplies
  the click target because the task center does not necessarily render a per-account promotion URL.
  A task-page `ac=promotion` link is an authenticated promotion-management page, not a public click
  target, and must never override `KLPBBS_PROMOTION_URL`; only a discovered URL that itself contains
  `fromuid` may be used when no configured URL exists.
- Discuz renders authoritative task progress in `#csc_1`. A successful proxy HTTP response is only
  a candidate visit and must not be counted as task progress. An incomplete task may still expose a
  `do=draw&id=1` link with a `rewardless.gif`; draw is allowed only when `#csc_1` is at least 100 or
  the task scope has an explicit completed marker. To match the working `klpAutomation` closure when
  the task page does not expose a parseable `#csc_1`, every 12 HTTP 200 candidate visits wait 15
  seconds and try task 1's authenticated draw endpoint; an unconfirmed draw continues with the
  remaining proxies. Failed proxy responses consume that proxy and continue immediately without a
  delay or task-page read. Proxy clicks run in bounded batches of at most 20 workers; authenticated
  task operations remain single-threaded. A batch with any successful response triggers one fresh
  authenticated progress read, and pool exhaustion triggers a final read before the run is skipped.
- A draw response may be opaque. It counts as successful only when it contains a known success
  marker or one fresh authenticated `item=doing` read proves task ID 1's `do=draw` link has
  disappeared. Other task ID 1 links, such as `do=apply`, do not mean the completed task remains.
- If the task-center or doing-task page is an incomplete HTTP 200 shell and three bounded parses
  cannot confirm task state, the promotion action returns `skipped` without proxy visits. This
  uncertainty must not block KLPBBS rank, ownership, inventory, purchase, or apply checks; the next
  independent run may retry task discovery. A confirmed apply followed by an explicit empty doing
  list remains a technical failure, because visiting an unconfirmed task would violate the progress
  contract.
- Challenge markers, HTTP 401/403/429, CAPTCHA, WAF, and access-denied pages raise
  `SecurityChallenge` and stop that site's side effects.
- If `MINEBBS_ESA_SLIDER_ENABLED=true`, only a GET/HEAD challenge may activate visible Chromium,
  with at most three independent browser/profile attempts for that safe request.
  The resolver reads the Alibaba ESA handle/track bounding boxes, moves the handle to the track end
  with a seeded-testable path shaped from a successful manual sample, and accepts the request only
  after challenge markers disappear. The transport then enters browser mode: later same-origin
  GET/HEAD/POST requests use Chromium rather than Python HTTP/TLS. GET/HEAD may use up to three fresh
  browser/profile attempts; POST has exactly one dispatch attempt. Browser failure never falls back
  to a second resolver cycle, and challenged POST requests are never replayed.
- A MineBBS HTTP 403 may render a Cloudflare managed-challenge Turnstile frame instead of the ESA
  slider. The same visible browser may wait for one unique visible standard checkbox/label inside a
  frame already classified as an active Cloudflare challenge. If the provider hides the internal
  control in closed shadow DOM, only the unique `body` geometry of an exact
  `challenges.cloudflare.com/.../turnstile/...` child Frame may supply the standard widget checkbox
  region. The browser approaches that DOM-derived point with a bounded Bezier path and performs one
  normal press/release click per browser attempt. Missing, hidden, or ambiguous geometry remains
  unresolved; no AI, token fabrication, whole-page coordinate guessing, or challenged POST replay
  is allowed.
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
- `HttpStatusError(status_code: int)`, a `TransportError` carrying the final HTTP status.

### 3. Contracts

- Dependency: `cloudscraper>=1.2.71`; do not substitute the unrelated `cfscraper` package.
- Preserve the CloudScraper-generated User-Agent and `CipherSuiteAdapter`.
- Apply the existing retry policy to the preserved adapter: two retries for GET/HEAD connection,
  read, and selected 5xx failures, including Cloudflare 520-527 and observed 552 gateway responses;
  never automatically replay POST.
- After challenge classification, every final HTTP status at least 400 raises `HttpStatusError`.
  A 5xx response is a technical server/transport failure and must never fall through to an HTML
  parser or be reported as an authentication/manual-intervention result.
- Run every response through `CloudflareWafGuard`; `cloudscraper` success does not bypass the
  application's challenge classification or bounded browser-resolution policy.
- The KLPBBS CloudScraper matches the known-working reference session: Windows Chrome browser
  profile, fixed Edge 116 User-Agent, and `LWPCookieJar`. `HttpTransport` must preserve that
  User-Agent and the scraper's TLS adapter.
- Authentication submits exactly `{"username": username, "password": password}` without a
  preliminary login-page GET to
  `member.php?mod=logging&action=login&loginsubmit=yes` with same-origin `Origin` and homepage
  `Referer` headers. Do not add `formhash`, `loginfield`, `questionid`, `cookietime`, or other
  browser-form fields unless a new live probe proves they are required.
- Match the reference session after login: keep `Origin` and `Referer` in the session headers and
  serialize the resulting `LWPCookieJar` into the persistent `Cookie` header before confirmation
  GETs. Logs expose only status, redirect count, response size, and cookie count, never cookie names
  or values.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| KLPBBS normal page with passive `/challenge-platform/scripts/jsd/` telemetry | Parse normally |
| Explicit `cf-chl-`, CAPTCHA, 401, 403, or 429 remains | Raise `SecurityChallenge` |
| Final login POST or confirmation GET is HTTP 5xx, including 552 | Raise `HttpStatusError`; orchestrator reports `technical_failure` |
| Final non-challenge HTTP 4xx | Raise `HttpStatusError`; do not parse the body |
| Session adapter is not an `HTTPAdapter` subclass | Raise `TypeError` during construction |
| `cfscraper` requests a `gb-dl` license or recurses | Dependency is forbidden; do not invoke it |
| Login POST completes but three read-only homepage checks cannot prove a non-zero Discuz UID | `manual_intervention`; do not continue account actions or replay credentials |

### 5. Good/Base/Bad Cases

- Good: KLPBBS gets a `CloudScraper` and keeps its `CipherSuiteAdapter` after transport setup.
- Base: MineBBS/WDSJFWQ/MCLISTS get ordinary `requests.Session` transports.
- Bad: install `cloudscraper` but then replace its TLS adapter with a plain `HTTPAdapter`.
- Good authentication: send the reference implementation's two-field payload once, then confirm
  the session from a fresh homepage GET with persistent origin/referer/cookie headers.
- Base: an HTTP 200 shell without a Discuz UID receives up to three read-only confirmation checks.
- Bad: interpret a 552 response body as an unauthenticated HTML page and return
  `manual_intervention`.
- Bad authentication: guess extra Discuz form fields or resend credentials when confirmation HTML
  is an incomplete HTTP 200 shell.

### 6. Tests Required

- Assert `create_cloudscraper_session()` returns a session accepted by `HttpTransport`.
- Assert its User-Agent and concrete HTTPS adapter class survive transport construction.
- Assert the preserved adapter receives `Retry(total=2)`.
- Probe KLPBBS homepage, login page, and forum list with read-only GET requests before release.
- KLPBBS may alternate between a complete forum list and an incomplete HTTP 200 shell. Rank parsing
  makes at most three read-only attempts in rewritten/canonical/rewritten order. It accepts explicit
  `normalthread_<ID>` DOM rows, with a fallback to the same table's `th.new a.xst` subject links.
  Three incomplete responses remain a parse failure and never lead to inventory or spending actions.
  Logs record only attempt numbers and parsed row/link counts, not titles, authors, or HTML.
- A structurally complete empty task center (`body.pg_task` plus task navigation) is not an unknown
  shell. The adapter reads `item=doing` before applying stable task ID 1, so an already-active task
  is never re-applied merely because the new-task list is empty. Unknown task-center and doing-page
  shells retain three bounded reads and skip only promotion when state remains unprovable.
- Authentication submits the credential-bearing login POST exactly once. Because the homepage can
  also be an incomplete HTTP 200 shell, session confirmation may perform at most three read-only
  homepage GETs; those confirmation attempts must never replay username/password fields.
- Assert the login payload has exactly `username` and `password`, plus same-origin `Origin` and
  homepage `Referer` headers.
- Assert the reference User-Agent and `LWPCookieJar` survive `HttpTransport` construction, and no
  login-page GET occurs before the credential POST.
- Assert a 552 POST raises `HttpStatusError(status_code=552)` after exactly one request.
- Assert login diagnostics include numeric status/redirect/cookie counts while sentinel cookie
  values never occur in JSONL or human-readable output.
- Assert persistent session headers contain same-origin `Origin`, homepage `Referer`, and the
  serialized post-login Cookie header before confirmation GETs.

### 7. Wrong vs Correct

#### Wrong

```python
HttpTransport()  # KLPBBS silently uses the generic session
```

#### Correct

```python
HttpTransport(session=create_cloudscraper_session())
```

#### Wrong: guess additional Discuz login fields

```python
data = {"username": username, "password": password, "formhash": formhash, "cookietime": "2592000"}
```

#### Correct: preserve the known-working reference request

```python
data = {"username": username, "password": password}
headers = {"Origin": base_url, "Referer": f"{base_url}/"}
```

#### Wrong: parse an unsuccessful response as login HTML

```python
response = session.post(login_url, data=data)
return parse_login(response.text)
```

#### Correct: classify the final status before parsing

```python
response = session.post(login_url, data=data)
if response.status_code >= 400:
    raise HttpStatusError(response.status_code)
```

#### Wrong: treat two HTTP 200 shells as a rank result

```python
for path in (rewritten_path, canonical_path):
    if parse_rows(get(path)):
        break
```

#### Correct: make one bounded fresh read after both variants are incomplete

```python
for path in (rewritten_path, canonical_path, rewritten_path):
    if parse_rows(get(path)):
        break
else:
    raise SiteParseError("forum structure is still incomplete")
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
- If and only if the response explicitly says the CAPTCHA is wrong, invalid, or expired, the
  adapter may refresh the vote page and dynamic image and repeat recognition/submission, for at
  most three total CAPTCHA submissions. An unchanged count, opaque response, transport failure, or
  any other unclassified result never authorizes a POST replay.
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
| Explicit CAPTCHA rejection response | Refresh page and image; retry up to three total submissions |
| Opaque/unknown response or unchanged count without explicit CAPTCHA rejection | Stop with `technical_failure`; do not replay POST |

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
- Assert one explicit CAPTCHA rejection causes one fresh page/image read and second POST, while an
  opaque unchanged response still performs exactly one POST.

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

- `EsaSliderChallengeResolver(wait_seconds=15.0, drag_steps=61, drag_duration_ms=465, headless=False, browser_executable_path=None, random_source=None)`
- `EsaSliderChallengeResolver(max_attempts=3).resolve(url, session, timeout) -> bool`
- `EsaSliderChallengeResolver.browser_request(method, url, session, timeout, **kwargs) -> requests.Response | None`
- `EsaSliderChallengeResolver.HANDLE_SELECTOR = "#aliyunCaptcha-sliding-slider"`
- `EsaSliderChallengeResolver.TRACK_SELECTOR = "#aliyunCaptcha-sliding-wrapper"`

### 3. Contracts

- Configuration is rejected unless `MINEBBS_ENABLED=true`; no `AI_SOLVER_*` value is required.
- The resolver imports `CloakBrowser` lazily, creates and owns a temporary profile before browser
  startup, launches visible Chromium with that explicit profile, copies request cookies into the
  browser, and navigates only to the challenged URL. The profile is removed after successful use,
  failed startup, navigation failure, or protocol failure.
- Each resolution call performs at most three independent attempts. Every failed attempt fully
  cleans its browser and temporary profile before the next attempt starts. The first cleared
  challenge stops the loop; only three failures produce the final unresolved result.
- Press starts at a random safe point inside the handle: 22-78% horizontally and 28-72%
  vertically. The clamp pointer coordinate preserves that sampled grab offset instead of assuming
  the handle center. Before pressing, a 232-point cubic Bezier approach crosses the viewport over
  about 1.2 seconds and ends on the sampled grab point. The held path crosses the clamp by 36-40%
  of travel and the page constrains the handle to the track. Its 61 normalized points and 465 ms
  deadlines are scaled from the successful manual sample. Tests inject a seeded `random.Random`
  instance for reproducibility.
- Each movement point is scheduled against an absolute monotonic deadline:
  `target_elapsed = drag_duration_ms * step / drag_steps`; the resolver waits for that deadline before
  sending the corresponding CDP event. Do not add a fixed delay after every browser protocol call,
  because cross-process call overhead otherwise stretches a configured drag once per event. The resolver
  uses CloakBrowser's exposed raw Playwright mouse; its humanized `page.mouse.move()` wrapper is not
  used because wrapping every sampled Bezier point expands one intended trajectory into dozens of
  nested trajectories. The legacy nodriver path continues to use low-level `Input.dispatchMouseEvent`.
- Movement deadlines are irregular rather than evenly spaced. The complete injected input sequence
  is unheld approach `mouseMoved` events (`button=none, buttons=0`), one `mousePressed` event
  (`button=left, buttons=1`), then held `mouseMoved` events (`button=none, buttons=1`). Do not inject
  `mouseReleased` or any other mouse event. ESA evaluates the pre-press pointer history: replaying
  the successful held trace without its approach history is rejected, while the complete sequence
  clears the public MineBBS challenge in the same `CloakBrowser` browser.
- The Playwright wait loop observes two valid transitions in parallel: the page can become clear
  through Cloudflare/ESA JavaScript without ever exposing the Alibaba slider, or the fixed slider
  geometry can appear and require the Bezier drag. Recheck structural clearance before every
  geometry probe. Do not spend the full slider timeout and fail merely because an auto-clearing
  challenge has no slider DOM.
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
- Browser cookies and `navigator.userAgent` are copied back only after the slider DOM is absent,
  the title has left the verification page, the document has a body and has finished loading on the
  expected origin, and browser cleanup succeeds. `about:blank`, browser error pages, cross-origin
  redirects, and uninitialized documents never count as clearance merely because no slider exists.
  The Playwright clearance expression must be executed by a real JavaScript engine in regression
  coverage; mocks that accept arbitrary strings cannot detect malformed object-literal syntax. Generic
  `安全验证` copy may remain on the normal MineBBS login page, so clearance is structural: no slider in
  any live frame, a non-challenge title, a loaded body, and the expected origin.
- `HttpTransport` calls `browser_request()` directly for the first challenged GET/HEAD. When it
  succeeds, `_browser_mode` becomes sticky for that MineBBS transport. The legacy `resolve()` path is
  used only when the resolver has no browser-request bridge; a failed bridge must not be followed by
  another three-attempt resolver cycle.
- The bridge binds its first accepted MineBBS origin. Every later target and final redirect must have
  the same scheme, host, and effective port. Cross-origin requests or redirects return `None` and the
  transport raises `SecurityChallenge`.
- Browser GET/HEAD prepares query parameters with `requests.Request` and navigates with Chromium to
  clear or confirm the page. It must not return the original navigation response: a client-side
  transition can leave that response at HTTP 403 even when the DOM changes. After structural
  clearance, the bridge performs a same-origin `fetch()` of the original safe request with
  `credentials='include'` and `cache='no-store'`; only the fetched status/body are returned in a
  synthetic `requests.Response`, and HEAD exposes an empty body. Browser POST first opens the bound
  origin, then uses the same fetch helper with the prepared form/JSON body and only browser-safe
  request headers. Response status, final URL, exposed headers, and text are copied into the
  synthetic response; browser cookies and User-Agent are synchronized after successful cleanup.
- POST is never retried after entering browser mode. Any browser startup, navigation, fetch,
  classification, session-read, or cleanup failure is treated as an indeterminate single dispatch
  and stops the site. This prevents duplicate login, purchase, sign-in, and bump submissions.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| `CloakBrowser` missing or browser launch fails | Return `False`; transport raises `SecurityChallenge` |
| Handle or track missing/not visible | Return `False`; no mouse input or session sync |
| Non-positive dimensions or track not wider than handle | Return `False`; no drag |
| Drag completes but challenge remains | Return `False`; no cookie/User-Agent sync |
| Cloudflare/ESA clears through iframe/JavaScript without slider DOM | Detect structural clearance during the geometry wait and continue without mouse input |
| Active Cloudflare Turnstile frame exposes one visible checkbox/label or one exact child-frame body | Click its DOM-derived checkbox region once with a bounded humanized approach and wait for structural clearance |
| Turnstile control is missing, hidden, or ambiguous | Leave the challenge unresolved; do not guess a click point |
| Browser/profile cleanup fails | Return `False`; no cookie/User-Agent sync |
| Initial challenged GET/HEAD clears through browser request | Enter sticky same-origin browser mode and return the Chromium response |
| Browser bridge fails its initial GET/HEAD | Raise `SecurityChallenge`; do not call `resolve()` for another three attempts |
| DOM looks clear but the same-context GET/HEAD refetch is HTTP 403 or challenge HTML | Reject the response and continue the bounded safe-request attempts |
| Browser-mode GET/HEAD fails | Use at most three fresh profiles, then raise `SecurityChallenge` |
| Browser-mode POST fails before or after dispatch | Raise `SecurityChallenge` after one attempt; never replay |
| Browser target or final redirect changes origin | Reject the response and raise `SecurityChallenge` |
| Challenged POST | Resolver is not invoked; POST is never replayed |

### 5. Good/Base/Bad Cases

- Good: a 40 px handle on a 360 px track yields a 320 px slide; the challenge clears and session
  state is synchronized; login and later MineBBS actions continue through Chromium on the same
  origin.
- Base: feature disabled; the detected ESA challenge immediately becomes `manual_intervention`.
- Bad: copy browser cookies back to `requests` and continue MineBBS through Python TLS, retry a
  browser POST after an unknown outcome, or send a viewport screenshot to
  `OpenAICompatibleVisionSolver`.

### 6. Tests Required

- Assert the resolver queries both fixed selectors and calculates the final handle center at the
  track end.
- Assert cubic Bezier geometry, monotonic X, bounded smooth Y, exact endpoint, one mouse down, and
  dense held mouse moves without any other mouse event or solver call.
- Assert the absolute timing schedule subtracts injected CDP event-call overhead and keeps the
  movement segment within the configured duration budget.
- Assert movement events use native `button/buttons` semantics, deadlines are non-uniform, and no
  endpoint dwell occurs before release.
- Assert a challenge that changes from Cloudflare HTML to a structurally clear same-origin page
  during the slider wait succeeds without querying slider geometry or emitting mouse input.
- Assert an active Cloudflare challenge frame prevents false clearance, a unique visible Turnstile
  control or exact child-frame body receives one Bezier-approach click, spoofed hosts are rejected,
  and missing/ambiguous geometry receives no click.
- Assert cookies/User-Agent copy only after clear, the browser profile closes on all paths, missing
  geometry is unresolved, and missing `CloakBrowser`/Chromium is unresolved.
- Assert a challenged GET switches `HttpTransport` to the browser bridge, later GET/HEAD/POST bypass
  `requests`, and the bridge rejects cross-origin targets and final redirects.
- Assert a failed initial browser bridge does not invoke the legacy resolver, safe browser methods
  use at most three attempts, and browser POST invokes exactly one attempt even when cleanup or
  response classification fails.
- Assert browser GET/HEAD refetches the prepared URL after DOM clearance, rejects a refetched HTTP
  403 before the adapter parser runs, and returns the refetched HTTP 200 body rather than the stale
  navigation response.
- Assert an unparseable KLPBBS task-center/doing page returns `skipped` without proxy visits and
  allows the orchestrator to continue rank, ownership, and bump checks; an explicit empty doing list
  after a confirmed apply remains a failure.
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

#### Wrong: return to Python HTTP after browser clearance

```python
if resolver.resolve(url, session, timeout):
    return session.get(url)  # ESA/WAF can reject the Python TLS fingerprint again.
```

#### Correct: keep the MineBBS request path on Chromium

```python
response = resolver.browser_request(method, url, session, timeout, **kwargs)
if response is None:
    raise SecurityChallenge("browser transport could not complete the request")
transport._browser_mode = True
return response
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

## Scenario: MineBBS XenForo Cart Checkout and Item Deployment

### 1. Scope / Trigger

- Trigger: purchasing or deploying a MineBBS bump card through XenForo forms, especially through
  the sticky Chromium transport where `fetch()` follows redirects.

### 2. Signatures

- `MineBBSAdapter._submit(submission: _FormSubmission) -> str`
- `MineBBSAdapter._json_submission_outcome(result: str) -> tuple[bool, bool | None]`
- `MineBBSAdapter._confirm_purchase_in_inventory(item_key: str) -> bool`
- `MineBBSAdapter._checkout_form(html, page_url, label) -> _FormSubmission | None`
- `MineBBSAdapter._deployment_submission(inventory_html, inventory_url, label) -> _FormSubmission | None`

### 3. Contracts

- Every XenForo AJAX POST includes `_xfResponseType=json`, `_xfWithData=1`, and the current page
  path in `_xfRequestUri`, plus `X-Requested-With: XMLHttpRequest`; form values remain excluded
  from logs.
- Browser-mode POST still has exactly one dispatch. An empty, redirected HTML, or otherwise opaque
  response never causes a second POST.
- `POST /tool-shop/<item-id>/purchase` only adds an item to the cart. The adapter must then parse
  `POST /tool-shop/checkout/update`, select the cart key belonging to the target label, force its
  `quantity[<cart-key>]` to `1`, include only the selected `purchase` submit control, and exclude
  `delete` and every other unselected submit control. If the target is already in the cart, skip
  the add step and perform only checkout.
- The orchestrator reads inventory before purchase. The adapter retains that non-secret count as a
  baseline. When checkout has no explicit business success or failure result, one fresh read-only
  inventory parse may prove success only when the purchased item count increased.
- Inventory counts recognize the live `.itemList-item` representation and either a `部署` or `使用`
  action. Deployment discovers exactly one same-origin
  `/tool-shop/inventory/<dynamic-id>/configure` overlay link inside the selected item, fetches that
  page, requires exactly one same-origin POST form on the same path with `code[contentid]`, replaces
  that field with `MINEBBS_THREAD_ID`, and submits once.
- Explicit `success=false`, `error(s)`, or insufficient-resource markers take precedence and cannot
  be converted to success by a later inventory read.
- Bare JSON `status=ok` proves only that XenForo processed the AJAX envelope. Purchase or deployment
  requires `success=true`, a known business-success marker, or an authoritative inventory delta.
- A missing baseline, unparseable inventory, unchanged count, ambiguous cart item, ambiguous
  configure link/form, or missing target field remains `technical_failure`/`SiteParseError` without
  replaying a POST.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Add-to-cart returns `道具已加入购物车` | Read checkout and submit the selected cart item once |
| Target item already exists in checkout | Skip add-to-cart; force quantity `1` and submit checkout once |
| Checkout/deployment returns bare JSON `status=ok` and inventory is unchanged | `technical_failure`; never infer business success |
| JSON `success=false` or `error(s)` | Failure; do not perform inventory confirmation |
| Opaque HTML after checkout and item count increased | `success`; continue to deploy |
| Opaque HTML and no baseline, parse failure, or unchanged count | `technical_failure`; never replay POST |
| More than one target cart item, configure link, or configure form | Raise `SiteParseError`; no side-effect POST |
| Configure form lacks `code[contentid]` or is cross-origin/path-mismatched | Raise `SiteParseError`; no deployment POST |
| Deployment returns `道具已部署` or inventory decreases | `success` |
| POST transport fails before or after dispatch | Stop the site through the transport contract; never replay |

### 5. Good/Base/Bad Cases

- Good: add one purple card to the cart, checkout only that cart key at quantity `1`, observe
  inventory move from `0` to `1`, fetch its dynamic configure overlay, and deploy it to the configured
  thread with one POST.
- Base: an item is already in the cart; perform checkout directly without adding a duplicate.
- Bad: treat `道具已加入购物车` as purchase completion, submit both `delete` and `purchase`, hard-code
  an inventory instance ID, or accept bare `status=ok` as proof of deployment.

### 6. Tests Required

- Assert effective POST data contains all three XenForo AJAX fields and the AJAX request header.
- Assert a live-shape checkout with server quantity `5` sends quantity `1`, its matching
  `cart_keys[]`, and `purchase`, while omitting `delete`.
- Assert an opaque response plus a cached `0 -> 1` inventory transition reports success with exactly
  one checkout POST.
- Assert explicit JSON failure remains failure and does not invoke inventory confirmation.
- Assert bare JSON `status=ok` plus unchanged inventory remains a technical failure.
- Assert `.itemList-item` with an arbitrary inventory ID is counted, its configure page is fetched,
  and `code[contentid]` receives the configured thread ID.
- Assert duplicate configure links and missing/ambiguous configure forms produce zero deployment
  POSTs.

### 7. Wrong vs Correct

#### Wrong

```python
if "success" not in response.text:
    transport.post(action, data=form)  # duplicates an indeterminate purchase
```

#### Correct

```python
response = transport.post(action, data={**form, "_xfResponseType": "json"})
if response_is_opaque:
    purchased = fresh_inventory[item_key] > cached_inventory[item_key]
```

#### Wrong: submit every named checkout button

```python
data = hidden_fields(checkout_form)  # includes both delete=1 and purchase=""
```

#### Correct: submit only the selected operation

```python
data = hidden_fields(checkout_form)
for control in all_submit_controls:
    data.pop(control.name, None)
data.update({quantity_name: "1", "cart_keys[]": cart_key, "purchase": purchase_value})
```

## State and side effects

State is versioned, atomic JSON containing only operational timestamps needed for cooldown and daily
purchase rules. Challenge counts and suspension deadlines are not persisted. Credentials, cookies,
CSRF tokens, response bodies, API keys, and extension paths are never
written to state, summaries, or ordinary logs. Unknown rank, owner, balance, inventory, form, CSRF
token, or target option raises `SiteParseError` before a side effect.

MineBBS purchase forms are identified by one same-origin POST action matching the current purchase
path, a `quantity` field, and exactly one submit control. Localized button text is not an identity
contract. The add-to-cart and checkout stages are distinct; checkout forces quantity `1` and sends
only the selected operation. Field names and counts may be logged, but field values may not.
XenForo AJAX JSON accepts explicit `success=true` or a known business-success message; bare
`status=ok` is not sufficient. Explicit `success=false`, `error(s)`, and insufficient-resource
markers take precedence. If checkout still returns an opaque body, only a read-only inventory
increase from the cached pre-purchase baseline can confirm success. Deployment uses the selected
inventory item's dynamic configure path and `code[contentid]`; it never hard-codes an inventory ID.

KLPBBS ownership compares the authenticated Discuz UID discovered after login with the first post
author UID encoded in `space-uid-<UID>.html` or `home.php?mod=space&uid=<UID>`. The configured login
identifier may be an email address and must never be compared directly with the public display name.

All execution paths emit JSONL step logs through the closed metadata allowlist in `step_log.py`.
HTTP URLs retain only scheme, host, port, and path. WDSJFWQ logs image size, model attempt status,
confidence, code length, field names, and public count changes without image/code/form values. ESA
logs browser lifecycle, DOM dimensions, drag point count/duration, clearance, and cookie counts
without cookie values, browser profile paths, or page bodies.

GitHub Actions runs one non-fail-fast matrix Job per adapter with exactly one site enabled and a
site-scoped state-cache key. One adapter's install, network, parser, challenge, or business failure
must not cancel or gate another adapter Job. Human-readable step logs are the default and suppress
per-proxy failures plus raw HTTP request/response noise; `MC_AUTOMATION_LOG_FORMAT=json` restores the
complete allowlisted JSONL diagnostic stream.

## Validation matrix

| Condition | Required behavior |
|---|---|
| Enabled site lacks a required key | Redacted `manual_intervention`; perform no network calls |
| Both sites disabled | `skipped`, exit zero |
| Challenge in default mode | Raise `SecurityChallenge`; do not retry |
| GET/HEAD MineBBS ESA challenge with DOM slider enabled | Up to three independent DOM-derived browser attempts; after first clear, keep all same-origin MineBBS traffic on Chromium |
| WDSJFWQ captcha form with AI solver enabled | Download captcha image, require strict JSON code, submit one filled form |
| Challenged POST, browser-mode POST uncertainty, or interactive challenge not cleared | Raise `SecurityChallenge`; never replay |
| Repeated challenges across runs | Report each run independently; never persist a suspension marker |
| Unknown/ambiguous markup or ownership mismatch | Raise `SiteParseError`; no purchase/use submission |
| Transient timeout or selected GET 5xx | Bounded transport retry; POST is not automatically retried |
| KLPBBS rank at or below threshold | `skipped`; do not query inventory or spend |
| MineBBS interval has not elapsed | `skipped`; do not query rank, inventory, or balances |
| KLPBBS promotion task page remains an unparseable shell after bounded retries | Return `skipped`, do not visit proxies, and continue the KLPBBS main flow |
| KLPBBS task center is structurally complete but has no available task | Read the doing list before applying stable task ID 1 |
| KLPBBS first two rank pages are incomplete HTTP 200 shells | Perform one final read-only primary-path attempt; fail closed if it is also incomplete |
| KLPBBS login POST is followed by incomplete HTTP 200 home shells | Confirm with at most three read-only home GETs; never replay credentials |
| MineBBS purchase page has one exact structural form without localized button text | Submit exactly quantity `1` once |
| MineBBS purchase form is absent/ambiguous/cross-origin or lacks quantity/submit control | Raise `SiteParseError`; do not submit |
| MineBBS checkout contains both delete and purchase controls | Submit only the selected target cart key and purchase control at quantity `1` |
| MineBBS inventory contains a dynamic configure overlay | Fetch it, require one same-origin target form, and submit `code[contentid]` once |
| MineBBS configure link/form is absent, ambiguous, cross-origin, or lacks `code[contentid]` | Raise `SiteParseError`; do not submit |
| MineBBS checkout/deployment returns bare `status=ok` with no state change | `technical_failure`; do not report success or replay POST |
| MineBBS AJAX response contains explicit failure plus a generic `status=ok` | Failure wins; never report purchase success |
| MineBBS purchase POST returns opaque HTML | Compare one fresh inventory read with the cached baseline; never replay the POST |

## Required tests

Tests must cover configuration redaction, disabled-site behavior, challenge detection, three-attempt
ESA DOM browser retry, sticky same-origin browser transport, WDSJFWQ captcha form filling, strict
model JSON parsing, Cookie/User-Agent synchronization, challenged/browser-mode POST no-replay,
repeated-run challenge handling, cooldown/interval boundaries,
ownership by authenticated Discuz UID, parser ambiguity, state redaction, cross-site failure isolation, WARP fail-closed
workflow wiring, and promotion-click proxy isolation.

MineBBS purchase regressions must use the live structural shape (`_xfToken`, `quantity`,
`_xfRedirect`, textless submit button), assert exact same-origin selection and quantity `1`, cover
ambiguous forms, JSON status handling, opaque-response inventory confirmation with exactly one POST,
and prove logged field names never expose values. Deployment regressions must use `.itemList-item`,
a dynamic `/inventory/<id>/configure` overlay, and `code[contentid]`; they must reject ambiguous or
missing controls without posting. KLPBBS regressions must cover a known empty task
center, doing-list precedence, primary/canonical/primary rank recovery, and one login POST followed
by up to three read-only session-confirmation GETs, with the exact two-field reference payload. The
quality gate is:

```text
ruff format --check .
ruff check .
mypy src
python -m pytest --cov=mc_automation --cov-report=term-missing
```
