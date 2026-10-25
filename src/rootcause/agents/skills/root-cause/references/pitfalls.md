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
Problem: OEE fell from 78% to 65% (13 points)

Parent chain (largest share: downtime, 9 of 13 points = 69%)
  Why 1: the 13-point loss is unplanned downtime (9 points) and speed loss (4 points)
  Why 2: 80% of the extra downtime is bearing failures on press P2
  Why 3: P2 bearings failed after its PM interval was stretched from 500 to 900 hours
  Root cause (process): PM intervals can be changed without a reliability review
  Validation: Why 3 -> Why 2, Why 2 -> Why 1, Why 1 -> problem
  Countermeasures: immediate, preventive, detection

  branches[0] (speed loss, 4 of 13 points = 31% > 25%)
    Why 1: the 13-point loss is unplanned downtime (9 points) and speed loss (4 points)
                                                       <- shared prefix, repeated
    Why 2: cycle time is 8% longer on every run using resin lot R-2
    Why 3: lot R-2 melt flow index is outside the range of all earlier lots
    Root cause (process): there is no incoming material spec for melt flow index
    Validation: Why 3 -> Why 2, Why 2 -> Why 1, Why 1 -> problem
    Countermeasures: immediate, preventive, detection
```

The parent follows the largest-share cause to its own root cause. Speed loss
also explains more than 25% of the delta, so it becomes a branch, and the
branch is a complete chain on its own: it repeats the shared Why 1, then has
its own evidenced levels, root cause and validation checks. A cause at or below
25% would go in `notes` instead.

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
