# Verification Record

Validated on Python 3.11.14 with the local `.env` on 2026-07-26. Credentials, endpoint values,
captcha text, cookies, tokens, and raw authenticated HTML were not written to this record.

| Check | Evidence | Result |
|---|---|---|
| Local configuration | CLI loads `.env` without overriding exported variables; MineBBS numeric ID, `slug.ID`, path, and same-origin URL normalization tests | Pass |
| Configuration migration | Empty optional booleans inherit their documented defaults; ESA remains independent from AI; `MINEBBS_ESA_SLIDER_ENABLED` and optional `MINEBBS_BROWSER_EXECUTABLE_PATH` are validated | Pass |
| KLPBBS live flow | Authentication succeeded; an incomplete HTTP 200 forum shell was reproduced; one bounded read-only reload produced 17 normal threads and resolved the configured target at rank 4 | Pass; correctly skipped bumping |
| MineBBS ESA path | Non-AI `nodriver` generates a 120-sample cubic Bezier curve from live handle/track geometry over about 465 ms. X control points remain ordered, Y control points provide bounded smooth deviation, and the endpoint is exact. Input contains only one `mousePressed` followed by held `mouseMoved` events; irregular absolute deadlines deduct protocol-call overhead from later waits | Pass as an input contract; live ESA acceptance requires a fresh probe |
| MineBBS ESA reverse probe | Runtime reported `CaptchaType=SLIDING`, `verifyType=1.0`, rotating `dynamicJS/3.28.0/sg.*.js`, slider down listeners and document move/up listeners. One visible-browser drag produced 121 moves (119 unique integer points), trusted primary-button events, `left=320px`, `fill=360px`, and about 3032 ms first-to-last event time | Pass; sanitized evidence in `research/esa-slider-reverse-engineering.md` |
| MineBBS live ESA smoke | Isolated visible public GET attempts through `nodriver` (explicit Chromium and the default Edge fallback) completed the DOM drag but the page remained challenged; no session data was synchronized and no POST was replayed | Pass (fail-closed `manual_intervention`) |
| MineBBS cubic Bezier live probe | Two isolated visible public GET attempts used the pressed/moved-only cubic Bezier implementation: first to the DOM clamp boundary, then 12% beyond it for page clamping. Both retained the `aliyunCaptcha` challenge marker and returned `resolve=False`; no session state was synchronized | Input contract implemented; live ESA acceptance remains negative |
| MineBBS randomized endpoint probe | Three isolated visible public GET attempts randomized the safe in-handle press point, preserved its grab offset at the track clamp, varied overshoot by 4-18%, varied point count by 82-115%, and varied duration by 90-115%. One observed sample used 132 points over 437 ms. All three retained the `aliyunCaptcha` marker and returned `resolve=False` | Endpoint precision is not the primary rejection cause; no session state synchronized |
| MineBBS trajectory matrix | Seven further visible public probes covered 60/90/140/190 points over 360/650/900/1250 ms plus slow-start, balanced, and fast-start/slow-end cubic Bezier controls. Every attempt emitted exactly one ESA verification request; every HTTP response had `Success=true` with `Result.VerifyResult=false`, and every challenge remained | Missing release, submission, point density, total duration, and Bezier speed shape are excluded as primary causes; browser/event provenance risk remains |
| MineBBS input/session A/B | No-release, explicit-release, and public-page-warmup plus release each emitted one verification request and returned `VerifyResult=false`. Reusing the same temporary profile twice also failed. Current Edge signals were `webdriver=false`, five plugins, normal languages and UA. The persistent in-app browser simultaneously opened the normal MineBBS homepage on the same machine/network | Single webdriver flag, missing release, cold navigation, and one-launch profile lifetime excluded; broader controlled-session/event provenance remains |
| MineBBS focused native-input probe | Resolver-owned Edge was actively foregrounded. High-resolution `SendInput` replay reached `left=320px`, hid the slider, issued one verify request, and returned `Success=true`, `VerifyResult=false`. Repeating with `MOUSEEVENTF_MOVE_NOCOALESCE` delivered `1 down + 62 moves + 1 up` and received the same rejection | Pass as discriminating evidence; native injection is not integrated because it does not clear ESA |
| MineBBS pre-press history A/B | Held-only replay through CDP, `SendInput`, and legacy `mouse_event` was rejected. Adding the 232-event, about 1.2 s approach segment from the accepted manual sample before the same held drag navigated to the normal MineBBS title and removed the slider DOM | Root cause confirmed: ESA evaluates pointer history before `mousedown` |
| MineBBS production resolver live probe | Visible public GET used the DOM-scaled cubic Bezier approach, one `mousePressed`, and the 61-point held cubic Bezier path with no release. The complete resolver returned `resolve=True`, synchronized six non-secret browser cookies and User-Agent, and cleaned its temporary profile | Pass; live ESA acceptance positive |
| nodriver browser smoke | `nodriver==0.50.3` opened a normal HTTPS page with Chromium, read DOM content and `navigator.userAgent`, and cleaned its temporary profile | Pass |
| WDSJFWQ model path | Captcha image downloaded in-session; configured model was rejected with HTTP 400; a provider-listed vision model returned a valid 5-character alphanumeric result | Pass after `.env` model correction |
| WDSJFWQ form path | Real form requires a named submit button and returns an opaque HTTP 302 body; adapter now sends the button and confirms only an internally consistent public like-count increase in the response or one fresh GET | Pass in regression tests; prior live submission cannot be retrospectively distinguished from an unchanged opaque response |
| MCLISTS live flow | Real one-shot like request returned the site's explicit success response | Pass |
| State and redaction | State schema contains only operational fields; exact-value scan found no configured credentials or AI endpoint/key outside `.env` | Pass |
| Workflow and dependency wiring | `uv lock --check`, CLI dry-run, and Workflow YAML parsing | Pass |
| Complete step logging | JSONL logs cover CLI, orchestration, HTTP, WDSJFWQ AI/form/count confirmation, and ESA browser/DOM/drag/session stages; sentinel secrets, captcha text, image data, form values, raw bodies, and URL query values are absent | Pass |
| WARP and promotion routing | Actions pins WARP setup to commit `691f6aa5a251ed89ea27a85e890f6f5313c1a3b5`, requires Cloudflare trace `warp=on`/`warp=plus`, and fails before application execution otherwise. Only the final same-origin KLPBBS promotion click uses a fresh `trust_env=False` session with an explicit dynamic HTTP proxy | Pass in workflow and unit contract tests; live Actions run pending push |
| Marker removal and rank policy | Obsolete promotion target/marker fields are absent from runtime configuration and workflow; the former marker value is migrated to required same-origin `KLPBBS_PROMOTION_URL`; local/GitHub promotion switch is enabled and `RANK_THRESHOLD=8` | Pass |
| KLPBBS forum-list regression | First WARP run returned two 116584-byte HTTP 200 pages but no recognized normal rows. Rank parsing now probes the rewritten and canonical forum paths, accepts only normal-row IDs or their `th.new a.xst` subject links, and logs a non-sensitive row count | Pass in regression tests; live Actions recheck pending |
| KLPBBS opaque promotion draw | Live task reached draw but returned no known success text. Adapter now confirms success only if a fresh `item=doing` page no longer contains task ID 1 | Pass in regression tests; live Actions recheck pending |

Final gate:

```text
ruff format --check .: pass (34 files)
ruff check .: pass
mypy src: pass (18 source files)
pytest: 145 passed
uv lock --check: pass
Workflow YAML parse: pass
secret scan: 0 matches
```

MineBBS ESA now clears in the complete local production resolver. The implementation uses
`nodriver` as its ESA browser runtime, does not use AI for ESA, does not fabricate tokens, and does
not replay challenged POST requests. Historical negative rows remain to document the rejected
held-only alternatives that isolated the missing pre-press pointer history.
