# THE HINDSIGHT PANEL — UI SPEC (task 3, Sep 28)

The operator's surface over the four endpoints in `hindsight_api.py`.
One page, five zones, top to bottom. Every number is read-only except
the four actions; every action writes telemetry.

## Zone 1 — the rotation header (GET .../summary)

```
ROTATION 2026-09-28 16:00–20:00 MST          GF 2.00 → 1.92 proposed
B 14 measurable burials · P 6 payers · r 42.9%
proposed delta −0.08 (loosens the guard; time-boxed 3 rotations)
[Auto Apply] [Queue for Review] [Reject] [Rollback]
```

- `r` renders as a percent with one decimal; `r = null` renders as
  `—` (never 0% — an unmeasurable rotation is not a free rotation).
- `proposed_delta < 0` renders LOOSENS in the share color; a refusal
  reason (`insufficient_samples`, `low_ratio`) renders in place of the
  buttons — a refused rotation has no actions.

## Zone 2 — the per-gate table (GET .../gates)

| Gate | B_g | P_g | r_g | expected payoff | action |
|---|---|---|---|---|---|
| deep-veto | 8 | 4 | 50% | +4.0 | [inspect] |
| tide-out | 4 | 2 | 50% | +2.0 | [inspect] |
| stale-move | 1 | 0 | 0% | +1.0 | [inspect] |
| popup-floor | 0 | 0 | — | — | [inspect] |

- Sorted payers-desc, rate tiebreak (the API's order — the UI never
  re-sorts; one brain).
- `expected payoff = B_g × (1 − r_g)`: the duds this gate would have
  correctly caught at the proposed width — equivalently `B_g − P_g`,
  the missed pays (per the example rows: deep-veto 8 − 4 = +4.0,
  tide-out 4 − 2 = +2.0, stale-move 1 − 0 = +1.0 with r = 0%; a 0/0
  gate is unmeasurable and renders —, never a bare 0).
- `[inspect]` scrolls to the candidate list pre-filtered to the gate.

## Zone 3 — the candidate list (per gate, from `gates[].top_candidates`)

```
06:11  HBAR/USD   deep-veto   quick 67 → +27.4% in 7h   [evidence]
06:14  ALGO/USD   tide-out    quick 84 → +15.1% in 7h   [evidence]
```

- `[evidence]` opens GET .../evidence/:candidate_id: the OHLCV snippet
  (7h, chart + table), the 30-line log excerpt, the trace id, and the
  payload checksum (shown, so a screenshot is self-certifying).

## Zone 4 — the actions (POST .../apply)

| button | body | guard |
|---|---|---|
| Auto Apply | `{action:"auto_apply", confirm:true, operator_id:<HINDSIGHT_OPERATOR_TOKEN>}` — `override_concentration` is NOT sent by default: omitting it means the concentration queue wins; the field is only added by an explicit operator choice at the confirm step (auth alone never lifts the queue) | every state-changing POST requires operator_id to match the token; the EXPLICIT override field is what lifts the concentration queue |
| Queue for Review | `{action:"queue_review", operator_id:<token>}` | authenticated too; computes + bundles, never touches GF |
| Reject | client-side | closes the panel; the bundle stays (evidence outlives the decision) |
| Rollback | POST `/api/hindsight/rotation/:id/rollback` `{operator_id:<token>}` | restores the ledger row's `before` (its own route — a rollback is not an apply; authenticated like every POST) |

- Auto Apply without `confirm` is refused by the API
  (`confirmation_required`) — the UI still sends the confirm only from
  a two-step click (press → summary of what will change → confirm).

## Zone 5 — the audit trail (GET /api/hindsight/telemetry)

The last 20 `auto_adjust` events from
`artifacts/auto_adjust/telemetry.jsonl`, read by the console through
the API's history route (zone 5 of the wire slice; the file is never
touched by the panel directly), rendered as:

```
18:02  2026-09-28-16  APPLIED   2.00 → 1.92  B14 P6 r42.9%  by system  ev sha256:ab12…  commit d5fd30f
17:44  2026-09-28-12  QUEUED    (concentration 0.75)       by the-operator  ev sha256:9c44…
```

Every row links its bundle directory and its commit. A `refused` row
shows its named reason. Nothing on this page is ever deleted; the
stream is append-only.

## Data contract notes

- The panel reads ONLY the four endpoints — zones 1–3 through the
  rotation slice, zone 4's ledger and zone 5's audit trail through the
  history routes `GET /api/hindsight/ledger` and
  `GET /api/hindsight/telemetry` — no direct file reads, no second
  brain. A POST to either history route is a named 404: no mutation
  can ride a history URL.
- All payloads are JSON; the checksums are `sha256:…` strings.
- The wireframe ships as text (this file) by design: the console's
  streamlit panels (the funnel plate, the burial-hindsight expander)
  already render zones 1–3 read-only; this spec is the operator layer
  that goes on top once the canary (task 6) graduates the feature.
