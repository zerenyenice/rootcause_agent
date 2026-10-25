# Pitfalls (from the upstream 5 Whys skill, adapted)

## Stopping too early: the symptom at another grain

```
Problem: scrap rate rose from 1.2% to 4.0% in week 12
Why 1: scrap rose on Line 3                      <- location, not a cause
Why 1: Line 3 rejects are 80% "dimension out of tolerance" after die change D-7
Why 2: rejects cluster in the first 200 parts after each die change
Why 3: no warm-up / first-article check is logged for die changes on Line 3
Root cause (process): the die-change procedure has no first-article check
```

Each answer is backed by a query: reject reasons by line and week, reject
position relative to changeover timestamps, presence of inspection records.

## Blame instead of cause

Wrong: "the night-shift operator set the wrong temperature".
Right: "the recipe allows manual temperature override without a limit check".
Shift, crew or operator columns may locate a segment; the cause is always the
process, control or decision behind it.

## Single thread when several causes exist

```
Problem: OEE fell from 78% to 65%
Why 1: unplanned downtime +9 points AND speed loss +4 points
  Branch A (downtime, 69% of delta) -> bearing failures on press P2 -> PM interval stretched
  Branch B (speed, 31% of delta)    -> new resin lot runs slower -> no incoming material spec
```

Both explain more than 25% of the delta, so both become branches.

## Unverified assumptions

| Level | Answer | Query that must exist |
|-------|--------|-----------------------|
| Why 1 | Rejects concentrate on Line 3 | rejects by line, baseline vs anomaly window |
| Why 2 | After die changes | rejects by minutes since changeover |
| Why 3 | No first-article check | inspection records joined to changeovers |

## Data, not the plant

Before blaming a machine, rule out the pipeline: row counts per load date,
null rates, duplicate keys and late-arriving data. A metric that moved because
a load was duplicated has a `data` root cause.

## Validation by counterfactual

Exclude the suspected segment and recompute the metric. If scrap without the
first 200 parts after die changes is back near 1.2%, the link holds. If it is
still 3.5%, the chain explains only part of the delta: report it and look for a
branch.
