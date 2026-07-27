# Logging Guidelines

## Format and API

- Operational steps use Python `logging` through `mc_automation.step_log.log_step` and logger
  `mc_automation.steps`.
- Every step is one compact JSONL record with `event`, UTC `timestamp`, `site`, `phase`, `status`,
  and `metadata`. Keep phase names stable, lowercase, and action-oriented.
- Use `started`, `completed`, `observed`, `skipped`, `detected`, `retrying`, or `failed` status values.
- Metadata is a closed allowlist in `step_log.py`. New metadata keys require a security review and
  tests before being accepted. Rejected keys appear only as `[REDACTED]` placeholders.

## Required Coverage

- CLI: application start/end, configuration result, enabled site names, state load/save, adapter
  setup, summary write, result count, and exit code.
- Orchestrator: per-site start/end, authentication, sign-in, promotion, rank,
  ownership, recovery/cooldown, inventory count, purchase/apply start, action result, and exception
  type.
- HTTP: method, sanitized URL, response status, content type/length, duration, redirect count and
  sanitized target, challenge classification/resolution, and bounded network failure type.
- WDSJFWQ: page/control/form discovery, initial/response/refreshed public count, captcha image byte
  count/type, model attempt/status/duration, confidence/code length/shape validity, form field names,
  response classification, and final result.
- ESA: nodriver import, browser start/fallback, cookie counts, navigation, challenge check, DOM
  dimensions, drag distance/point count/duration, cleanup, session sync, and final result.

## Sensitive-data Contract

- Never log usernames, passwords, cookies, tokens, CSRF values, authorization headers, captcha
  text, image bytes/Base64, AI endpoint/key, form values, request/response bodies, raw model output,
  browser profile paths, or exception messages.
- HTTP URLs are normalized to `scheme://host[:port]/path`; userinfo, query, and fragment are removed.
- Field names and aggregate sizes/counts are allowed; field values are not.
- "Complete step logging" means full control-flow observability, not raw traffic dumps.

## Tests

- Use `caplog` against `mc_automation.steps`, decode every record as JSON, assert expected phases,
  and assert known sentinel secrets never occur.
- Every new request/form/model/browser path needs both presence checks and negative redaction checks.
