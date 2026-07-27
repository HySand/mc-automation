# Verification Record

Validated on Python 3.11.14 with the local `.env` on 2026-07-26. Credentials, endpoint values,
captcha text, cookies, tokens, and raw authenticated HTML were not written to this record.

| Check | Evidence | Result |
|---|---|---|
| Local configuration | CLI loads `.env` without overriding exported variables; MineBBS numeric ID, `slug.ID`, path, and same-origin URL normalization tests | Pass |
| Configuration migration | Empty optional booleans inherit their documented defaults; ESA remains independent from AI; `MINEBBS_ESA_SLIDER_ENABLED` and optional `MINEBBS_BROWSER_EXECUTABLE_PATH` are validated | Pass |
| KLPBBS live flow | Authentication succeeded; an incomplete HTTP 200 forum shell was reproduced; one bounded read-only reload produced 17 normal threads and resolved the configured target at rank 4 | Pass; correctly skipped bumping |
| MineBBS ESA path | Non-AI `nodriver` uses a seeded-testable path derived from a successful physical-mouse sample: about 465 ms, 61 moves, initial 1 px probing, monotonic X acceleration, smooth 7-10 px upward drift, and release beyond the clamped track endpoint. Points use irregular absolute deadlines so protocol-call overhead is deducted from later waits; held moves use native-like `button=none, buttons=1` | Pass as an input contract; live ESA acceptance remains negative |
| MineBBS ESA reverse probe | Runtime reported `CaptchaType=SLIDING`, `verifyType=1.0`, rotating `dynamicJS/3.28.0/sg.*.js`, slider down listeners and document move/up listeners. One visible-browser drag produced 121 moves (119 unique integer points), trusted primary-button events, `left=320px`, `fill=360px`, and about 3032 ms first-to-last event time | Pass; sanitized evidence in `research/esa-slider-reverse-engineering.md` |
| MineBBS live ESA smoke | Isolated visible public GET attempts through `nodriver` (explicit Chromium and the default Edge fallback) completed the DOM drag but the page remained challenged; no session data was synchronized and no POST was replayed | Pass (fail-closed `manual_intervention`) |
| MineBBS focused native-input probe | Resolver-owned Edge was actively foregrounded. High-resolution `SendInput` replay reached `left=320px`, hid the slider, issued one verify request, and returned `Success=true`, `VerifyResult=false`. Repeating with `MOUSEEVENTF_MOVE_NOCOALESCE` delivered `1 down + 62 moves + 1 up` and received the same rejection | Pass as discriminating evidence; native injection is not integrated because it does not clear ESA |
| nodriver browser smoke | `nodriver==0.50.3` opened a normal HTTPS page with Chromium, read DOM content and `navigator.userAgent`, and cleaned its temporary profile | Pass |
| WDSJFWQ model path | Captcha image downloaded in-session; configured model was rejected with HTTP 400; a provider-listed vision model returned a valid 5-character alphanumeric result | Pass after `.env` model correction |
| WDSJFWQ form path | Real form requires a named submit button and returns an opaque HTTP 302 body; adapter now sends the button and confirms only an internally consistent public like-count increase in the response or one fresh GET | Pass in regression tests; prior live submission cannot be retrospectively distinguished from an unchanged opaque response |
| MCLISTS live flow | Real one-shot like request returned the site's explicit success response | Pass |
| State and redaction | State schema contains only operational fields; exact-value scan found no configured credentials or AI endpoint/key outside `.env` | Pass |
| Workflow and dependency wiring | `uv lock --check`, CLI dry-run, and Workflow YAML parsing | Pass |
| Complete step logging | JSONL logs cover CLI, orchestration, HTTP, WDSJFWQ AI/form/count confirmation, and ESA browser/DOM/drag/session stages; sentinel secrets, captcha text, image data, form values, raw bodies, and URL query values are absent | Pass |
| WARP and promotion routing | Actions pins WARP setup to commit `691f6aa5a251ed89ea27a85e890f6f5313c1a3b5`, requires Cloudflare trace `warp=on`/`warp=plus`, and fails before application execution otherwise. Only the final same-origin KLPBBS promotion click uses a fresh `trust_env=False` session with an explicit dynamic HTTP proxy | Pass in workflow and unit contract tests; live Actions run pending push |
| Marker removal and rank policy | Obsolete promotion target/marker fields are absent from runtime configuration and workflow; local/GitHub promotion switch is enabled and `RANK_THRESHOLD=8` | Pass |

Final gate:

```text
ruff format --check .: pass (34 files)
ruff check .: pass
mypy src: pass (18 source files)
pytest --cov=mc_automation: 126 passed, 85% total coverage
uv lock --check: pass
Workflow YAML parse: pass
secret scan: 0 matches
```

MineBBS remains an external ESA decision, not an unresolved code exception. The implementation uses
`nodriver` as its ESA browser runtime, does not use AI for ESA, does not fabricate tokens, and does
not replay challenged POST requests.
