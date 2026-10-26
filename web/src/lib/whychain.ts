// Defensive parser for the WhyChain JSON the root-cause subagent returns
// (see src/rootcause/agents/reports.py). Anything that does not look like a chain
// yields null so the caller can fall back to rendering markdown.

export type RootCauseType = "process" | "design" | "data" | "external" | "knowledge";
export type CountermeasureType = "immediate" | "preventive" | "detection";

export interface WhyProblem {
  what: string;
  when: string;
  where: string;
  impact: string;
}

export interface WhyEvidence {
  sql: string;
  rowsSummary: string;
}

export interface WhyLevel {
  question: string;
  answer: string;
  evidence: WhyEvidence[];
  confidence: number | null;
}

export interface WhyRootCause {
  statement: string;
  type: RootCauseType | null;
  confidence: number | null;
}

export interface WhyValidation {
  link: string;
  checkSql: string;
  passed: boolean;
}

export interface WhyCountermeasure {
  type: CountermeasureType;
  action: string;
  owner: string | null;
}

export interface WhyChain {
  problem: WhyProblem;
  levels: WhyLevel[];
  rootCause: WhyRootCause | null;
  validation: WhyValidation[];
  countermeasures: WhyCountermeasure[];
  branches: WhyChain[];
  partial: boolean;
  notes: string;
}

const ROOT_CAUSE_TYPES: readonly RootCauseType[] = [
  "process",
  "design",
  "data",
  "external",
  "knowledge",
];
const COUNTERMEASURE_TYPES: readonly CountermeasureType[] = [
  "immediate",
  "preventive",
  "detection",
];
const MAX_BRANCH_DEPTH = 3;

type Rec = Record<string, unknown>;

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function confidence(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(1, Math.max(0, value))
    : null;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return allowed.find((candidate) => candidate === value) ?? null;
}

function parseLevel(raw: unknown): WhyLevel | null {
  if (!isRecord(raw)) return null;
  const question = str(raw.question);
  const answer = str(raw.answer);
  if (!question && !answer) return null;
  const evidence = list(raw.evidence).flatMap((item): WhyEvidence[] => {
    if (!isRecord(item) || !str(item.sql)) return [];
    return [{ sql: str(item.sql), rowsSummary: str(item.rows_summary) }];
  });
  return { question, answer, evidence, confidence: confidence(raw.confidence) };
}

function parseRootCause(raw: unknown): WhyRootCause | null {
  if (!isRecord(raw) || !str(raw.statement)) return null;
  return {
    statement: str(raw.statement),
    type: oneOf(raw.type, ROOT_CAUSE_TYPES),
    confidence: confidence(raw.confidence),
  };
}

function parseCountermeasure(raw: unknown): WhyCountermeasure[] {
  if (!isRecord(raw) || !str(raw.action)) return [];
  const type = oneOf(raw.type, COUNTERMEASURE_TYPES) ?? "immediate";
  return [{ type, action: str(raw.action), owner: str(raw.owner) || null }];
}

function parseValidation(raw: unknown): WhyValidation[] {
  if (!isRecord(raw) || !str(raw.link)) return [];
  return [
    { link: str(raw.link), checkSql: str(raw.check_sql), passed: raw.passed === true },
  ];
}

function parseChain(raw: unknown, depth: number): WhyChain | null {
  if (!isRecord(raw) || !isRecord(raw.problem) || !Array.isArray(raw.levels)) return null;
  const problem = raw.problem;
  const branches =
    depth < MAX_BRANCH_DEPTH
      ? list(raw.branches).flatMap((b): WhyChain[] => {
          const chain = parseChain(b, depth + 1);
          return chain ? [chain] : [];
        })
      : [];
  return {
    problem: {
      what: str(problem.what),
      when: str(problem.when),
      where: str(problem.where),
      impact: str(problem.impact),
    },
    levels: raw.levels.flatMap((l): WhyLevel[] => {
      const level = parseLevel(l);
      return level ? [level] : [];
    }),
    rootCause: parseRootCause(raw.root_cause),
    validation: list(raw.validation).flatMap(parseValidation),
    countermeasures: list(raw.countermeasures).flatMap(parseCountermeasure),
    branches,
    partial: raw.partial === true,
    notes: str(raw.notes),
  };
}

function stripFence(text: string): string {
  const match = /^```(?:json)?\s*([\s\S]*?)\s*```$/i.exec(text.trim());
  return match?.[1] ?? text.trim();
}

/** Parse a tool-message payload into a WhyChain; null when it is not one. */
export function parseWhyChain(content: unknown): WhyChain | null {
  let value: unknown = content;
  if (typeof content === "string") {
    const text = stripFence(content);
    if (!text.startsWith("{")) return null;
    try {
      value = JSON.parse(text);
    } catch {
      return null;
    }
  }
  return parseChain(value, 0);
}
