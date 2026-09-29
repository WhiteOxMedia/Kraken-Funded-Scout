# POSTMORTEM TEMPLATE — (Sep 28)

Fill within 48h of a rollback, a gate refusal with real money impact,
or any silent-degradation class escape. One page. Facts before story.

```markdown
# PM-<date>-<slug>

## The one-line truth
<What broke, in one sentence a trader could read.>

## Impact
- Money: <realized $ impact, or "none — caught before fill">
- Exposure: <how long, how many rotations/books, which cohort>
- Detection: <who/what saw it first: the gate, the drive, the panel,
  the operator — name the detector; a find with no detector is the
  real finding>

## Timeline (MST)
- HH:MM — <event>
- HH:MM — <event>
- HH:MM — resolved

## The mechanism
<Why the machine did the wrong thing, in terms of its own seams:
which file, which state key, which comparison. No blame, no
anthropomorphism — "the cap read 0 because the counter write failed
silently", not "the system thought".>

## The class
<Which named class is this? silent-degradation / masked-failure /
merge-across-days / naive-stamp / fail-open-where-closed / new class.
If new: name it and add it to this list in the next commit.>

## The fix
- Commit(s): <sha(s), gated, drives listed>
- Red-first? <yes/no — the test that failed before the fix existed>
- Regression pin: <which check in which drive now names this case>

## The audit trail
- Evidence bundle: <checksum, path>
- Telemetry: <rotation_id(s), event row(s)>
- Ledger rows: <applied/reverted/refused rows by rotation>

## Follow-ups
- [ ] <item — owner — by when>
- [ ] Sweep for the same class elsewhere: <where looked, what found>

## What we got lucky on
<Honesty section. Every incident had luck; name it or it becomes
"resilience" in the retelling.>
```
