-- THE PER-GATE BURIED-PAYER RATIOS (task 1, Sep 28 spec): for ONE
-- rotation window, which gate buried the most payers and by how much.
--   B_g = measurable burials by this gate (a peak could be computed)
--   P_g = of those, the ones that reached the goal (the gate was WRONG)
--   r_g = P_g / B_g -- the payer-rate; the auto-adjust's input
-- Adapted from the spec's Postgres to the machine's SQLite:
--   ::float          -> CAST(... AS REAL)
--   FILTER (WHERE ..) native since SQLite 3.30 (the repo's sqlite is newer)
--   NULLS LAST        native since 3.30
-- Params: :rotation_id (e.g. '2026-09-28-12-16'), :goal_pct (1.5 = +1.5%).
WITH distinct_candidates AS (
  SELECT rotation_id, candidate_id, gate, MIN(ts) AS first_ts
  FROM popup_suppressions
  WHERE rotation_id = :rotation_id
  GROUP BY rotation_id, candidate_id, gate
),
joined AS (
  SELECT d.rotation_id, d.candidate_id, d.gate,
         COALESCE(h.max_run_pct, NULL) AS max_run_pct,
         CASE WHEN h.max_run_pct >= :goal_pct THEN 1 ELSE 0 END AS reached_goal
  FROM distinct_candidates d
  LEFT JOIN hindsight_peaks h ON h.candidate_id = d.candidate_id
)
SELECT gate,
       COUNT(*) FILTER (WHERE max_run_pct IS NOT NULL) AS measurable_B_g,
       SUM(reached_goal) AS P_g,
       CASE WHEN COUNT(*) FILTER (WHERE max_run_pct IS NOT NULL) = 0 THEN NULL
            ELSE CAST(SUM(reached_goal) AS REAL)
                 / COUNT(*) FILTER (WHERE max_run_pct IS NOT NULL)
       END AS r_g
FROM joined
GROUP BY gate
ORDER BY r_g DESC NULLS LAST;
