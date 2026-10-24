# RootCause backlog

Generated from `backlog.yaml`. Sync to GitHub with `python3 scripts/backlog_to_github.py --repo OWNER/REPO --project RootCause`.

| Phase | Tasks | Gate |
| --- | --- | --- |
| Phase 0 - Foundations | 30 | question-to-SQL works on the demo warehouse |
| Phase 1 - Anomaly agent | 14 | scan finds 90% of seeded anomalies |
| Phase 2 - Root-cause agent | 12 | 70% accepted on 20 seeded cases |
| Phase 3 - Memory | 11 | a repeated case is solved using a lesson |
| Phase 4 - Catalog UI and hardening | 17 | pilot on one real customer database |

## EP0 Repository, tooling and CI foundations

Monorepo scaffolding, Python workspace, quality tooling and the CI pipeline that every later task relies on.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T001 | Scaffold monorepo layout and uv workspace | 0 | M | - |
| T002 | Docker Compose for local stack (Postgres+pgvector, OpenViking, API, web) | 0 | M | T001 |
| T003 | CI pipeline: lint, typecheck, tests with 80% coverage gate, security audit | 0 | M | T001 |
| T004 | Shared settings, logging and OpenTelemetry bootstrap | 0 | S | T001 |
| T005 | FastAPI app skeleton beside LangGraph Server with health and version endpoints | 0 | M | T001, T004 |
| T006 | Database migrations with Alembic for app, catalog and audit schemas | 0 | S | T002 |
| T007 | ADR process and initial ADRs (DeepAgents, pgvector, OpenViking, read-only access) | 0 | S | - |

## EP1 Database connection service and query tools

Read-only, audited access to customer databases through a small tool surface used by every agent.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T010 | Connection registry with encrypted credentials and read-only verification | 0 | M | T006 |
| T011 | SQL validator with sqlglot: allow list, transpile, LIMIT injection, referenced objects | 0 | M | T001 |
| T012 | run_sql tool with limits, Arrow results, filesystem handles and audit logging | 0 | L | T010, T011 |
| T013 | Introspection tools: list_schemas, list_tables, describe_table, sample_rows, column_profile | 0 | M | T010 |
| T014 | explain_sql tool and cost estimate for approval prompts | 0 | S | T012 |
| T015 | Connections API endpoints (CRUD, test, limits, audit log query) | 0 | M | T005, T010, T012 |

## EP2 Metadata catalog (indexing, storage, retrieval)

The semantic layer: tables, columns, categorical values, joins, metrics, glossary, events; hybrid search; versions.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T020 | Catalog data model and migrations (tables, columns, values, joins, metrics, glossary, events, versions) | 0 | M | T006 |
| T021 | Structural crawl job with snapshot diff and schema-change events | 0 | M | T013, T020 |
| T022 | Profiling job with cost caps and categorical value index | 0 | M | T021 |
| T023 | Join inference job (name heuristics, type compatibility, inclusion dependency test) | 0 | M | T022 |
| T024 | LLM enrichment job: descriptions, semantic types, candidate metrics and dimensions | 0 | M | T022 |
| T025 | Embedding and hybrid search (tsvector + pgvector) over catalog objects | 0 | M | T020, T024 |
| T026 | Catalog retrieval tools: get_table_context, resolve_value, find_join_path, get_metric, propose_enrichment | 0 | M | T023, T025 |
| T027 | Catalog publish and versioning, JSON export and import | 0 | S | T020 |
| T028 | Catalog API endpoints (browse, search, edit, approve, jobs) | 0 | M | T026, T027 |

## EP3 Orchestrator and metadata agent (DeepAgents)

The DeepAgents orchestrator, backends, subagent contracts, and the metadata agent that turns questions into SQL.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T030 | DeepAgents orchestrator skeleton with CompositeBackend and TodoList middleware | 0 | M | T005 |
| T031 | Subagent contracts: Pydantic report models and shared SubAgent registry | 0 | S | T030 |
| T032 | Metadata agent: question-to-SQL with catalog context and repair loop | 0 | L | T026, T012, T031 |
| T033 | Orchestrator problem framing and delegation flow for 'why did X change?' | 0 | M | T030, T032 |
| T034 | Context engineering: large results to filesystem, L0/L1 catalog disclosure, token budget | 0 | M | T030, T012 |
| T035 | Agent event streaming API (messages, todos, tool calls, subagent steps) | 0 | M | T030, T005 |

## EP4 Evaluation dataset and scoring harness

Synthetic manufacturing warehouse with 20 seeded incidents at three difficulty levels, ground-truth manifest, scoring rubric, CI integration.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T040 | Manufacturing warehouse generator: dimensions and baseline production, telemetry, quality | 0 | L | T001 |
| T041 | Incident layer: easy cases E1 to E3 with ground-truth manifest | 0 | M | T040 |
| T042 | Incident layer: medium cases M1 to M3 with decoys | 1 | M | T041 |
| T043 | Incident layer: difficult cases D1 to D4 (lag, duplicates, mix effect, delayed material) | 2 | L | T042 |
| T044 | Benign events and data traps (holidays, planned maintenance, late data, local timestamps, null lots) | 1 | S | T040 |
| T045 | Scoring harness: detection, root cause (LLM judge + keywords), chain validity, decoys, efficiency | 1 | L | T041, T035 |
| T046 | CI integration of the evaluation suite with per-level pass-rate thresholds | 1 | S | T045, T003 |
| T047 | 10x scale variant of the warehouse for performance tests | 4 | S | T040 |

## EP5 Anomaly detection agent

Confirm and scan modes, SQL-first aggregation, detection skills, decomposition, ranking, anomaly records.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T050 | list_metrics and aggregate_series tools (grain-correct SQL from the catalog) | 1 | M | T026, T012 |
| T051 | Detection skills: ts_robust_zscore and ts_stl_residual | 1 | M | T050 |
| T052 | Detection skills: ts_changepoint (ruptures) and ts_forecast_interval | 1 | M | T050 |
| T053 | Detection skills: data_quality_anomaly and distribution_shift | 1 | M | T050 |
| T054 | Segment contribution decomposition with Simpson's paradox check | 1 | M | T050 |
| T055 | Ensemble, ranking and deduplication of anomalies; AnomalyRecord persistence | 1 | M | T051, T052, T053, T054, T031 |
| T056 | Anomaly subagent (confirm and scan modes) wired into the orchestrator | 1 | M | T055, T033 |
| T057 | multivariate_outlier skill (Isolation Forest on entity features) | 2 | S | T050 |

## EP6 Root-cause agent (5 Whys)

Port of the 5 Whys skill with query-backed evidence, branching, backward validation, countermeasures, WhyChain contract.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T060 | Port the 5 Whys skill to a RootCause SKILL.md with query-backed evidence rules | 2 | M | T031 |
| T061 | Root-cause tools: ask_sql, compare_windows, data_quality_probe, recall_lessons stub | 2 | M | T032, T054, T053 |
| T062 | Why-loop with hypothesis generation, ranking and branching | 2 | L | T060, T061 |
| T063 | Backward validation with counterfactual queries and countermeasures | 2 | M | T062 |
| T064 | WhyChain report rendering (markdown per the skill's output format) with evidence links | 2 | S | T063 |
| T065 | Root-cause subagent wired into the orchestrator with budgets and partial reporting | 2 | M | T064, T056 |
| T066 | Verdict capture API (accept, reject, partial, note) on reports | 2 | S | T035 |

## EP7 Investigate (chat) UI

Streaming chat, anomaly feed, evidence panel, verdict controls, approval prompts, memory transparency.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T070 | Next.js app scaffold with auth placeholder, layout and LangGraph SDK client | 1 | M | T035 |
| T071 | Streaming chat thread with todo plan and collapsible subagent steps | 1 | L | T070 |
| T072 | Anomaly feed cards with sparkline and 'Investigate' action | 1 | M | T071, T056 |
| T073 | Evidence panel: why-chain tree, charts, result tables, click-through to queries | 2 | L | T071, T064 |
| T074 | Verdict controls and human-in-the-loop approval prompts | 2 | M | T071, T066 |
| T075 | 'What informed this' drawer listing retrieved cases, lessons and skills | 3 | S | T073, T085 |

## EP8 Memory layer on OpenViking

OpenViking deployment, namespaces, session commit, lesson extraction, deduplication, skill promotion, DeepAgents integration, governance.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T080 | OpenViking client adapter behind a MemoryStore interface | 3 | M | T002 |
| T081 | Namespace layout, tenant and connection scoping | 3 | S | T080 |
| T082 | Session commit on investigation end with verdict | 3 | M | T080, T066 |
| T083 | Lesson extractor and negative lessons | 3 | M | T082 |
| T084 | Lesson deduplication, support counter and decay | 3 | S | T083 |
| T085 | Memory tools for agents (recall_lessons, search_memory) and retrieval at framing | 3 | M | T081, T033, T062 |
| T086 | Skill promotion into DeepAgents skill directories with versioning | 3 | M | T084 |
| T087 | Mount OpenViking memories as a DeepAgents backend at /memories/ | 3 | M | T080, T030 |
| T088 | Catalog corrections and anomaly dismissals written as memories | 3 | S | T082, T028 |
| T089 | Memory admin API and UI (browse viking://, delete, retire skills, retention) | 3 | M | T081, T086, T070 |

## EP9 Catalog and connections management UI

Schema browser, table and column pages, join graph, review queue, metrics and glossary editors, connections and audit views.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T090 | Connections UI: add, edit, test, limits, health | 4 | M | T070, T015 |
| T091 | Query audit log view with filters and export | 4 | S | T090 |
| T092 | Catalog schema browser and table page | 4 | L | T070, T028 |
| T093 | Column page with categorical values, labels and synonyms editor | 4 | M | T092 |
| T094 | Join graph view with approve or reject | 4 | M | T092 |
| T095 | Metrics and glossary editors with SQL preview; events editor with CSV import | 4 | M | T092 |
| T096 | Review queue for machine-generated enrichments and catalog version diff | 4 | M | T092 |
| T097 | Indexing controls with job progress and logs | 4 | S | T092 |

## EP10 Security, auth, multi-dialect and pilot hardening

Auth and roles, tenant scoping, secrets, Snowflake and BigQuery dialects, performance tests, pilot runbook.

| Id | Task | Phase | Size | Depends on |
| --- | --- | --- | --- | --- |
| T100 | Authentication and roles (analyst, data engineer, admin) with per-connection access | 4 | L | T005, T070 |
| T101 | Tenant isolation across threads, catalog, memory and audit | 4 | M | T100, T081 |
| T102 | Column masking and privacy controls in the catalog | 4 | S | T020 |
| T103 | Snowflake and BigQuery dialect support with test matrix | 4 | L | T011, T013 |
| T104 | Performance tests on the 10x warehouse (indexing, scan, search latency) | 4 | M | T047, T056, T025 |
| T105 | Model configuration, prompt caching and per-role model routing | 2 | S | T031 |
| T106 | English and Turkish language support in chat and reports | 4 | M | T065 |
| T107 | Pilot runbook, deployment guide and AGPL review note for OpenViking | 4 | S | T002 |
| T108 | Security review of the full stack before pilot | 4 | M | T100, T101, T102, T012 |
