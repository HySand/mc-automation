# Alibaba ESA slider reverse-engineering record

Date: 2026-07-26  
Scope: MineBBS public GET challenge, one bounded DOM-based browser attempt, and the
`EsaSliderChallengeResolver` implementation.  No credentials, cookies, tokens, encrypted
payloads, raw authenticated HTML, or verification codes are recorded here.

## Method

1. Loaded the public MineBBS challenge page in Chromium and captured only request metadata,
   DOM geometry, browser event metadata, and response field names.
2. Inspected the rendered ESA configuration and event listeners through the browser protocol.
3. Compared the observed timing with the resolver's configured movement duration.
4. Repeated the check against the current dynamic script family without depending on a fixed
   script filename or hash.

## Observed contract

### Page and module selection

- The rendered configuration reports `CaptchaType=SLIDING`, `verifyType=1.0`, and `mode=embed`.
- The slider element has `mousedown` and `touchstart` listeners.
- The document has `mousemove`/`mouseup` and `touchmove`/`touchend` listeners.  Listener source
  offsets change when the dynamic module changes, so offsets and variable names are not stable
  integration points.
- The page loads the stable FeiLin module plus a rotating `dynamicJS/3.28.0/sg.*.js` module.
  Observed names include `sg.002`, `sg.004`, `sg.011`, `sg.016`, `sg.017`, `sg.029`, `sg.040`,
  `sg.041`, and `sg.046`.  The implementation therefore relies only on the fixed DOM selectors.
- The stable FeiLin bundle contains an explicitly named `selenuimWebdriver` device-detection
  routine. An older automated Chromium probe reported `navigator.webdriver=true`; the current
  `nodriver==0.50.3` Edge probe reports `navigator.webdriver=false`, `plugins=5`, normal language
  values, and a regular Edge user agent while ESA still returns `VerifyResult=false`. The single
  webdriver flag is therefore not a sufficient explanation; broader browser/session or injected
  event provenance remains the concrete risk-signal hypothesis.

### Event data visible to the page

The probe recorded the same browser-level fields used by the page's event path:

```text
x, y, time, isTrusted, button (down/up)
```

The rotating module routes input through internal buckets such as `mc`, `mm`, `mp`, `mu`, `tc`,
`tmv`, and `te`.  Those short names are useful reverse-engineering evidence but are not used by the
resolver because their surrounding functions and offsets rotate with `sg.*`.

The browser delivered integer `clientX/clientY` values after the resolver supplied floating
point coordinates.  `isTrusted` was `1` and the mouse button was `0`.  A live probe produced:

| Measurement | Result |
|---|---:|
| `mousemove` events | 121 |
| unique integer move points | 119 |
| touch events | 0 |
| event `isTrusted` values | `{1}` |
| event `button` values | `{0}` |
| first-to-last event span | about 3032 ms |
| final handle `left` | `320px` |
| final filled width | `360px` |

The page computes relative event time from wall-clock milliseconds (`Date.now()`); it does not
use the browser driver's internal timing as an input contract.

### Network decision boundary

One normal drag caused the expected sequence: challenge/device initialization, encrypted device
collection, one encrypted verification request, and no replay.  For the verification response,
only these non-secret facts were retained:

| Request role | Public endpoint family | Retained field names |
|---|---|---|
| challenge init | `*.captcha-pro-open.aliyuncs.com` | `SceneId`, `DeviceData`, mode/language/signature metadata |
| client log | `upload.captcha-pro-open.aliyuncs.com` | `log` plus signature metadata |
| device collection (twice) | `device.captcha-open.aliyuncs.com` | encrypted `Data` plus signature metadata |
| verification (once) | `*-verify.captcha-pro-open.aliyuncs.com` | encrypted `CaptchaVerifyParam`, `CertifyId`, `SceneId`, signature metadata |

```text
HTTP 200
Code = Success
Success = true
Result.VerifyResult = false
Result.VerifyCode exists (value discarded)
```

This proves that the request shape and DOM endpoint were accepted by the service, while the final
decision was a risk rejection.  It is not evidence of an incorrect endpoint or an unfinished
geometric drag.

## Historical timing correction

The previous loop waited a fixed per-point delay after every synchronous
`page.mouse.move()` call. The cross-process call cost was therefore added 120 times; the then-current
2600 ms movement could take roughly 4.2--4.4 seconds in the live browser.

The resolver now schedules each point against an absolute monotonic deadline:

```text
target_elapsed = drag_duration_ms * step / drag_steps
remaining = target_elapsed - (monotonic_now - drag_start)
wait(remaining) only when remaining > 0
```

The historical live probe after that change completed the whole press/move/release sequence in about
3.03 s instead of accumulating the browser-call overhead. The current implementation retains the
absolute-deadline rule but uses the later successful-manual-sample profile of about 465 ms. A
deterministic unit test injects a fake monotonic clock and verifies that 3 ms of mouse-call cost is
subtracted from each subsequent wait.

## Decision and limits

- Keep the DOM-derived, successful-manual-sample-shaped path, integer-effective coordinates,
  bounded vertical drift, and one-shot GET/HEAD retry.
- Keep the resolver visible and fail closed when the challenge remains.
- Do not hide `navigator.webdriver`, spoof plugins or other device signals, fabricate
  `CaptchaVerifyParam`, replay verification POSTs, or rotate proxies.  The live result indicates
  that those browser/device signals are a separate risk factor from trajectory timing.
- Dynamic script names, hashes, internal variable names, and encrypted field values are not
  treated as stable contracts.

## nodriver migration probe

Date: 2026-07-26

- Replaced the previous browser runtime with `nodriver==0.50.3`; the resolver now dispatches
  `mouseMoved`, `mousePressed`, and `mouseReleased` through CDP `Input.dispatchMouseEvent`.
- A real browser smoke test using an existing Chromium executable successfully opened a normal
  HTTPS page, read DOM content and `navigator.userAgent`, and removed the temporary profile.
- On the current Windows runner, the default path also succeeds by falling back from missing Chrome
  discovery to the installed Microsoft Edge executable; no browser path is required in `.env`.
- Isolated visible, public MineBBS GET challenge attempts completed through the new resolver (one
  explicit Chromium run and one default Edge-fallback run), but the challenge remained
  (`resolve=False`) in both. No browser Cookie or User-Agent was synchronized and no challenged
  POST was replayed.
- This result verifies the migration and fail-closed behavior, but it is not evidence that ESA will
  accept the current browser/device risk profile. Passing remains an external service decision.

## Manual-vs-CDP discriminating probe

Date: 2026-07-26

- In the same visible `nodriver` process, temporary profile, public URL, and network path, an
  automated CDP drag reached `left=320px` but produced the authoritative page message
  `验证失败，请刷新`.
- A subsequent user-operated physical mouse drag in the same diagnostic setup cleared the
  challenge. This rules out the temporary profile, browser binary, and exit IP as sufficient causes
  for that comparison; the remaining defect is in the automated input behavior.
- The previous implementation sent every movement with `button=left, buttons=1`, used perfectly
  uniform event deadlines, and held the pointer motionless at the endpoint for 180 ms before
  release. Native `mousemove` semantics use no transition button (`button=0` / CDP `none`) while
  `buttons=1` carries the held-left state.
- The resolver now sends movement frames as `button=none, buttons=1`, uses irregular absolute
  deadlines within the same bounded duration, and releases immediately when the endpoint is
  reached. Unit tests assert these protocol semantics, but only a fresh live ESA attempt can prove
  acceptance because numeric path tests are not an ESA behavior oracle.

## Focused Windows native-input probe

Date: 2026-07-26

- Earlier native-input observations made while another application had focus were discarded. The
  valid probes explicitly foregrounded the resolver-owned browser and required page-observed
  down/move/up events plus a final `left=320px` before interpreting the result.
- The successful physical-mouse drag slice contained one down, 61 moves, and one up over about
  487 ms. X was monotonic, Y drifted smoothly to about -9 px, and the pointer finished about 121 px
  beyond the 320 px clamped track distance.
- High-resolution `SendInput` replay of those relative coordinates and deadlines hid the control
  and issued one verification request. The response was HTTP-successful with `Code=Success` and
  `Success=true`, but `Result.VerifyResult=false`.
- A second replay used `MOUSEEVENTF_MOVE_NOCOALESCE`. The page observed one down, 62 moves, one up,
  a roughly 481 ms down-to-up span, and the same endpoint. ESA again returned
  `Result.VerifyResult=false`.
- These probes separate input delivery from service acceptance. Focus, endpoint geometry, native
  button semantics, event density, and request dispatch are no longer plausible primary defects.
  Injected-input provenance or another device/risk signal remains the likely differentiator. The
  known-failing native route is therefore not integrated into production, and further numeric
  curve tuning is not considered a meaningful fix.

## Bug analysis: path tests mistaken for ESA acceptance

### 1. Root cause category

- **Category D/E - Test coverage gap and implicit assumption.** Unit tests validated geometric and
  timing properties, while the actual contract is an external risk decision. A realistic path was
  incorrectly treated as a proxy for live clearance.

### 2. Why earlier fixes failed

1. Bezier and four-stage profiles changed the surface shape but did not test the service decision.
2. Native `button/buttons` corrections fixed event semantics but not injected-input provenance.
3. Manual-trace CDP and Windows replays matched the accepted coordinates while retaining an
   automation-origin signal that the service could still score.

### 3. Prevention mechanisms

| Priority | Mechanism | Specific action | Status |
|---|---|---|---|
| P0 | Integration evidence | Require challenge disappearance and the live verify result for acceptance claims | Done |
| P0 | Fail-closed architecture | Never sync Cookie/User-Agent after `VerifyResult=false` | Done |
| P1 | Review checklist | Label path-shape tests as contract tests, not an ESA oracle | Done |
| P1 | Diagnostic validity | Foreground the owned browser and verify DOM event receipt before using native-input evidence | Done |

### 4. Systematic expansion

- CAPTCHA/WAF tests elsewhere must distinguish “input submitted” from “provider accepted.”
- UI state such as a hidden control is not equivalent to server-side authorization or clearance.
- Repeated parameter tuning should stop once a discriminating live probe rejects the same accepted
  sample through multiple injected-input layers.

## Expanded trajectory and session matrix

Date: 2026-07-27

- Seven visible CDP probes covered 60-190 movement points, 360-1250 ms total duration, and
  slow-start, balanced, and fast-start/slow-end cubic Bezier controls. Every probe emitted exactly
  one verification request and received `Success=true` with `Result.VerifyResult=false`.
- Press positions were randomized inside the handle, clamp coordinates preserved the actual grab
  offset, and endpoint overshoot varied from 4-18%. Three further samples were rejected.
- An A/B comparison with and without `mouseReleased` produced the same single verification request
  and the same rejection. The page therefore submits before or independently of release for this
  challenge version.
- Reusing one temporary Edge profile for a second launch and warming a fresh profile by visiting a
  public MineBBS page did not change the result. Both reused-profile rounds exposed
  `navigator.webdriver=false`, `plugins=5`, normal language values, and the regular Edge UA.
- At the same time, the persistent in-app browser session displayed the normal MineBBS homepage on
  the same machine/network while each fresh controlled Edge session received the ESA challenge.
  This is consistent with a broader session/browser-control reputation difference rather than a
  remaining endpoint, density, duration, release, or Bezier-shape defect.

## Pre-press pointer-history root cause and successful production probe

Date: 2026-07-27

- The earlier scheduling loop sent each move before waiting for that move's deadline. Correcting it
  proved the long 97 ms pause belonged before the second move, not the third, but the held-only
  sequence still returned `VerifyResult=false`.
- The accepted physical sample contained 253 `mousemove` events before `mousedown`. Its final
  approach segment lasted about 1.2 seconds over 232 events and crossed roughly 488 px horizontally
  and 107 px vertically before ending on the handle. Every rejected automated path began directly
  with `mousePressed`, so ESA never received equivalent pre-press pointer history.
- Replaying the complete approach plus held drag through the legacy Windows mouse API changed the
  page title to `MineBBS 我的世界中文论坛`, removed the slider DOM, and navigated away from the
  verification page. Replaying only the held portion through the same API returned
  `VerifyResult=false`. This isolates the pre-press history as the decisive input difference; OS
  input provenance is not required.
- Production now generates a DOM-scaled cubic Bezier approach, then one `mousePressed`, then the
  61-point cubic Bezier held path derived from the successful sample. It sends no release event,
  changes no browser fingerprint, and fabricates no token.
- A final public GET through the complete `EsaSliderChallengeResolver` returned `resolve=True`.
  The slider DOM disappeared, the normal page session supplied cookies, and the browser User-Agent
  synchronized into the request session after cleanup.
- Normal MineBBS HTML can retain passive `aliyunCaptcha` script text after navigation. Clearance is
  therefore established by absent slider DOM plus a non-verification title, not by scanning that
  passive string alone.

## Bug Analysis: Held-only tests omitted the input-history window

### 1. Root Cause Category

- **Category D/E - Test coverage gap and implicit assumption.** The implementation and tests treated
  `mousedown` as the start of the ESA input contract even though the provider records pointer motion
  before the press. The accepted trace already contained the missing evidence, but analysis sliced it
  at the press boundary.

### 2. Why fixes failed

1. Bezier controls, duration, density, endpoint, and button semantics only changed the held segment.
2. CDP and two native-input APIs replayed the same incomplete segment, so changing the delivery layer
   could not supply the missing history.
3. Unit tests asserted realistic held geometry but had no assertion for an approach ending exactly at
   the sampled press point.

### 3. Prevention mechanisms

| Priority | Mechanism | Specific action | Status |
|---|---|---|---|
| P0 | Integration evidence | Require slider DOM disappearance and normal-page navigation | Done |
| P0 | Test coverage | Assert unheld Bezier approach, exact press landing, and held-only ordering | Done |
| P1 | Fail-closed detection | Permit only passive `aliyunCaptcha` text to use the DOM/title clearance check | Done |
| P1 | Documentation | Define the complete input contract from approach through held drag | Done |

### 4. Systematic expansion

- Browser challenges may score event history before the visible interaction starts. Diagnostic traces
  must preserve the full observation window, not only the obvious gesture slice.
- Replaying the same incomplete sample through multiple input APIs is not discriminating evidence.
  Compare the full accepted and rejected event streams before changing delivery technology.

### 5. Knowledge capture

- [x] Update the backend site-automation contract.
- [x] Add approach and replacement-challenge regression tests.
- [x] Record the positive live production result alongside historical negative probes.
