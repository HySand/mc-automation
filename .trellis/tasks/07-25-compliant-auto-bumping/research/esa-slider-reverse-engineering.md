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
  routine.  Together with the observed `navigator.webdriver=true`, `plugins=5`, and
  `chrome.runtime=false` in the visible automated Chromium session, this is a concrete risk-signal
  hypothesis.  The probe does not claim that this is the sole scoring input; it only separates a
  browser/device decision from a coordinate or endpoint defect.

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
