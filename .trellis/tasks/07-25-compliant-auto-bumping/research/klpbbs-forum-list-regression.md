# KLPBBS forum-list regression analysis

## 1. Root Cause Category

- **Category**: D/E - Test coverage gap and implicit external-page assumption.
- **Specific cause**: the rank parser treated one Discuz rewritten URL and one row-ID representation
  as the complete live contract. The first WARP deployment returned full-size HTTP 200 pages twice
  but neither produced accepted rows in that session, so the parser failed closed before bumping.

## 2. Why the initial implementation failed

1. The unit fixture represented only `normalthread_<ID>` rows and could not exercise a canonical
   Discuz URL or subject-link fallback.
2. Retrying the same rewritten URL repeated the same external representation; it did not provide
   discriminating evidence or a structurally different read-only fallback.
3. Promotion had only a `started` step log, so its completion result was hidden by the later rank
   exception and had to be inferred from elapsed time.

## 3. Prevention mechanisms

| Priority | Mechanism | Specific action | Status |
|---|---|---|---|
| P0 | Runtime | Try rewritten and canonical Discuz forum paths, then fail closed | Done |
| P0 | Test | Cover subject-link parsing without row IDs and canonical-path fallback | Done |
| P1 | Observability | Log parsed normal-row count and promotion completion visits/attempts | Done |
| P1 | Documentation | Preserve the multi-representation forum-list contract in backend spec | Done |

## 4. Systematic expansion

- **Similar issues**: inventory, ownership, and task pages also depend on external Discuz markup;
  they must retain explicit alternatives and fail closed rather than use broad text guessing.
- **Design improvement**: retries should change the read-only representation when the failure is a
  parser contract issue; repeating identical input is useful only for known incomplete shells.
- **Process improvement**: live acceptance must verify the complete action sequence and result logs,
  not only transport success or page byte length.

## 5. Knowledge capture

- Backend contract updated with both URLs, accepted selectors, fail-closed behavior, and safe logs.
- Regression tests added for the alternate representation and fallback URL.
