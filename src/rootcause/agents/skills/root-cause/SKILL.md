---
name: root-cause
description: Query-backed 5 Whys root-cause analysis over a SQL database. Use when asked why a metric moved, what caused an anomaly, scrap, downtime or yield loss, or to dig past a symptom to an actionable cause. Returns a WhyChain JSON object.
license: MIT (adapted from awesome-skills/5-whys-skill)
---

# Root Cause (5 Whys, query-backed)

Trace a measured problem down to an actionable root cause by asking "why?"
repeatedly. Unlike classic 5 Whys, every why is answered by an executed query
against the connected database, never by reasoning alone.

Adapted from the 5-Whys Root Cause Analysis skill by awesome-skills
(https://github.com/awesome-skills/5-whys-skill), MIT licence; see `LICENSE`
in this skill directory for the full notice.

## Ground rules

- **Every why is answered by an executed query**, cited in that level's
  `evidence` (the SQL you ran plus a short summary of the rows). A claim with
  no executed query does not enter the report.
- Read-only: only single `SELECT` / `WITH` statements via `run_sql`.
- Never name individuals or blame people. Name processes, pipelines, controls,
  data defects or decisions ("no SOP for die change", not "operator X").
- Restating the symptom at another grain is not a cause. "Scrap rose on Line 3"
  under "scrap rose" is a location, not a mechanism; ask why again.
- A level with no supporting query is a hypothesis: it cannot be the root cause.
- Depth is 3 to 7 levels. Five is a default, not a target.

## Tools

| Tool | Use it to |
|------|-----------|
| `search_catalog` | Find tables and columns by meaning |
| `list_tables` | See which tables exist |
| `describe_table` | Read columns, types and keys before writing SQL |
| `column_values` | Check real categorical values before filtering on them |
| `explain_sql(sql)` | Estimate rows and cost of a query without running it |
| `run_sql(sql, purpose)` | Execute a read-only query |

Estimate with `explain_sql` before any heavy query (wide joins, long windows,
unaggregated scans). `run_sql` returns a summary (columns, row_count,
truncated, sql) and a `path`. Large results are not inlined: read the rows from
that file path with `read_file` (in slices) instead of re-running a wider
query. A query over the row threshold returns `approval_required` and waits for
human approval. If a query is rejected or not approved, never retry the same
SQL: narrow it with filters, aggregation or a shorter window instead, and do
not work around the check.

## Method: describe first, never give up on a SQL error

You have a generous budget; a partial chain because of SQL errors is a failure
of method, not of the data.

1. **Describe before you query.** Never write SQL against a table or column you
   have not seen in this run. If the task description lists tables and columns,
   trust only what it lists; for any other table call `describe_table` first
   (one call per table is cheap). Call `list_tables` once at the start: the
   table descriptions tell you which tables hold which kind of evidence. Use
   `column_values` before filtering on a categorical value (reason codes, event
   types, shift names), never guess the spelling.
2. **A rejection or error is a hint, not a verdict.** When `run_sql` is rejected
   or fails (unknown table/column, validation), read the error detail: it names
   the real columns. Fix the query and run it again. Do this for every
   hypothesis. Never drop a hypothesis, and never stop the investigation,
   because of a schema mismatch.
3. **Return partial only** when roughly 85% of the tool-call budget is used, or
   when you have swept every family in Phase 1b and the data holds no evidence. Say
   in `notes` which queries failed and what you would run next.

## Phase 1: Define the problem

State the problem with numbers, using the template:

- **What**: the observable change (metric, from baseline to current value)
- **When**: the anomaly window and the baseline it is compared to
- **Where**: the line, plant, product or system affected
- **Impact**: the measurable consequence

Inspect the schema first (`list_tables`, `describe_table`, `search_catalog`),
then run quick aggregate queries that measure the problem yourself. Resolve
the four fields from the data rather than asking: pick the metric the question
names (or the obvious one, e.g. output = units produced), use the full date
range in the data unless told otherwise, find the segment with the largest
movement, and size the impact from the numbers. State each assumption in
`problem` (e.g. "assumed: output = units produced, full date range").

You cannot ask the user anything directly; your only output is the WhyChain.
Hand a question back through the orchestrator only when the data genuinely
cannot resolve an ambiguity (for example two equally plausible metrics) or
contradicts the user's claim. Never hand back a list of questions.

In that case return a WhyChain with `partial: true`, the best `problem` you
can fill (every field non-empty; write "unspecified" where unknown),
`levels: []`, `root_cause: null`, and in `notes` ONE short question that
offers the concrete options you found in the data (with the SQL behind
them), or the disagreement (what the user claimed versus what the query
measured). The orchestrator relays it to the user.

## Phase 1b: Evidence sweep (mandatory, before any deeper "why")

Once Phase 1 has located the segment and the window, and BEFORE you commit to
any cause at Why 2 or deeper, run one window-vs-baseline query on every table
family the schema offers, in this order, and keep a one-line verdict per
family (found / nothing / table absent) for `notes`. Tables keyed by machine
or asset are tied to the segment through the table that maps assets to lines
or plants (describe it); a table without a line column is not "unlinked".

1. events and calendars (business or plant events, holidays, releases):
   is any event recorded for that segment around that date?
2. downtime or incident logs: reason codes, minutes and machines in the
   window against baseline (use `column_values` for the reason codes);
3. maintenance and work-order logs: kind, parts replaced, dates near the
   window for the machines of the segment;
4. the single machine or asset: compare each machine of the segment with
   its peers and with its own baseline on telemetry (temperature,
   vibration, power) and breakdown minutes; one outlier machine is a very
   common mechanism;
5. energy and meter readings: a flat zero or a spike over the window;
6. planned work: work orders, product families (join the products table:
   family, complexity score, standard cycle time) and product mix, window
   against baseline, compared at the family level as well as per product (a
   whole line moved to a heavier family shows up per family, not per product);
7. materials, suppliers, lots, recipes and specs;
8. staffing and shift assignments.

Never skip a family whose table exists: "not queried" is acceptable only for
an absent table. Skipping the telemetry or energy family because the
downtime family looked quiet is exactly how a cause is missed.
Families 1 to 6 are the usual home of the real mechanism: do not return a
root cause from family 7 or 8 (materials, staffing) unless families 1 to 6
were each queried and showed nothing for the segment. Batch independent
calls in one turn (several `describe_table` calls, or the sweep queries for
several families) instead of one call per turn; this saves your budget.
Make every sweep query a compact comparison, never a raw dump: group by the
entity (machine, reason code, event type, part) with the baseline and window
values side by side and their difference, `ORDER BY ABS(difference) DESC
LIMIT 15`. Rows are not inlined, so each query costs a `run_sql` plus a
`read_file`; a result of 15-20 rows is read whole in one `read_file` call
(`limit=100`), while raw event rows read in slices burn the budget and hide
the outlier. Where an entity only exists in the window (a new reason code,
an event row, a repair), list it explicitly.
Follow the sharpest lead. When any family shows ONE entity that deviates
from its peers and from its own baseline only in the window (one machine
running 10 degrees hotter, a repair record, a new reason code, an event row,
a zero meter), that is the lead: drill into it before anything else (does its
timing match the symptom's day-by-day curve, what do its downtime and
maintenance records say, does the metric recover when it is excluded). Never
dismiss such an outlier because "other entities are stable": that is what an
outlier is. When two or more families flag the same entity (for example
one machine that is hotter, has extra breakdown minutes and an unplanned repair
record), that entity is the leading cause until a query refutes it: your
intuition about what "should" cause the symptom is not a refutation, a query is
(the effect starts before the deviation, or persists after it ends, or the
segment without it is unchanged). Control-group check for every slice-based candidate (a lot, product,
supplier or shift that looks bad): compare the same slice on unaffected
segments (other lines or plants) in the same window, and the affected
segment's other slices in the window. If the slice is bad only because it ran
on the affected segment in the window (every lot, product or shift there is
bad), it is a bystander, not a cause. Statistical slices come last, and a sharp, window-only deviation
outranks them.
Slicing the metric by product, lot or shift (the columns that sit on the
fact table itself) comes after the sweep, never instead of it: a statistical
slice of a diluted metric always shows some "worst" lot or product, and
that is a starting point, not a cause. Ask what the worst items have in
common (same machine, same period, same upstream event) and check that in
the family tables. A candidate is supported only if its trace is specific
to the window (the lot, spec or process story must be absent or harmless in
the baseline) and a counterfactual (segment with and without it) agrees.
Prefer the mechanism with a direct logged or physical trace (a repair
record, a downtime reason code, a flat-zero meter, an event row) over a
statistical association, and cross-check it in a second family before you
call it validated (for example an outage event, matching downtime reason
codes and zero energy readings agree). Then continue asking why the
confirmed mechanism happened: a chain needs at least 3 evidenced levels.

## Phase 2: Ask why, level by level

For each level:

1. Propose 2-4 candidate causes for the previous answer. Draw them from
   segment decomposition (which line, shift, product, supplier, machine
   carries the delta), data quality checks (null spikes, duplicates, late or
   missing loads), related metrics, and known events (changeovers,
   maintenance, supplier or recipe changes).
2. Test each candidate with a query. Keep only candidates a query supports;
   drop the rest and do not mention them as findings.
3. Name the concrete identifiers in each `answer` (the table and column,
   the machine, line, product family or reason-code values the evidence rests
   on), not just a general statement.
4. Record the level: `question`, `answer`, `evidence` (one entry per supporting
   query, with `result_path` when you used the result file) and `confidence`
   (0-1, how strongly the data supports it).

Quality check per level: is it factual, does it explain the previous level, is
it a mechanism rather than the same symptom at a finer grain?

**Branching**: when more than one supported cause explains a share of the
delta, use this convention:

- The parent chain follows the largest-share cause all the way to its own
  root cause, validation and countermeasures.
- Each additional cause that explains more than 25% of the delta becomes a
  branch: a nested WhyChain in `branches`. Causes at or below 25% are named in
  `notes`, not branched.
- A branch is a complete WhyChain. Its `problem` is the parent's problem, and
  its `levels` start at Why 1 and repeat the shared prefix (the levels it has
  in common with the parent) before diverging, so every branch on its own has
  3-7 evidenced levels, a `root_cause` and `validation` checks. The only
  exception is a branch marked `partial: true` with the reason in `notes`.

## Phase 3: Identify the root cause

Stop when the cause is actionable, preventable, fundamental (asking why again
gives nothing actionable) and verifiable, or it hits a stop condition:

| Type | Stop condition |
|------|----------------|
| `process` | A process or policy that can be changed, or a missing control |
| `design` | A design flaw in equipment, product or system |
| `data` | A data pipeline defect (the anomaly is in the data, not the plant) |
| `external` | An external event (supplier, weather, demand) |
| `knowledge` | A knowledge or training gap in a procedure (never a person) |

## Phase 4: Validate backwards

Walk the chain from the root cause up to the problem. For each link run a
counterfactual query: exclude the suspected segment, batch or period and check
that the effect above disappears or shrinks in proportion. Record each as a
`validation` entry (`link` such as "Why 3 -> Why 2", `check_sql`, `passed`).
If a link fails, revisit that level; if it still fails, report it as failed.
Never drop a failed check.

## Phase 5: Define countermeasures

For the root cause, propose:

1. **immediate**: stop the bleeding
2. **preventive**: make sure it does not recur
3. **detection**: a query-based monitor that would catch it early

Give an `owner` as a role or team ("maintenance planning"), never a person.

## Budget

You work within a tool-call and time budget. If it runs out, stop and return
what you have: set `partial` to true, keep only evidenced levels, set
`root_cause` to null unless it is already validated, and explain in `notes`
what is missing and which query would come next. Never pad a partial chain.

## Final answer

Return only a WhyChain JSON object matching `rootcause.agents.reports`:

```json
{
  "problem": {"what": "", "when": "", "where": "", "impact": ""},
  "levels": [
    {"question": "", "answer": "", "confidence": 0.8,
     "evidence": [{"sql": "", "rows_summary": "", "result_path": null}]}
  ],
  "root_cause": {"statement": "", "type": "process", "confidence": 0.7},
  "validation": [{"link": "Why 2 -> Why 1", "check_sql": "", "passed": true}],
  "countermeasures": [{"type": "preventive", "action": "", "owner": null}],
  "branches": [],
  "partial": false,
  "notes": ""
}
```

For worked examples and the pitfalls in detail, see `references/pitfalls.md`
(appended below as a reference section) when a chain gets stuck.

## Checklist

- [ ] Problem measured by a query and stated in the template, or a partial
      chain with `levels: []` and the question for the user in `notes`
- [ ] 3-7 levels, each with at least one executed query in `evidence`
- [ ] No level restates the symptom at another grain
- [ ] Parent follows the largest-share cause; every other cause above 25% of
      the delta is a complete branch (shared prefix repeated) or marked partial
- [ ] Root cause is actionable and names a process, never an individual
- [ ] Every link validated backwards; failures reported
- [ ] Immediate, preventive and detection countermeasures
- [ ] `partial` and `notes` honest about anything unfinished
