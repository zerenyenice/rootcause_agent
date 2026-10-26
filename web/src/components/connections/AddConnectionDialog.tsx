"use client";

import { CheckCircle2, Database, FileBox, XCircle } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Field, Input, Select } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import {
  createConnection,
  deleteConnection,
  testConnection,
  type ConnectionKind,
  type ConnectionProbe,
  type SslMode,
} from "@/lib/api";
import {
  EMPTY_FORM,
  FIELD_LABELS,
  SSL_MODES,
  toCreateBody,
  validateConnectionForm,
  type ConnectionFormValues,
  type FieldName,
  type FormErrors,
} from "@/lib/connectionForm";
import { apiErrorMessage } from "@/lib/messages";

const KIND_OPTIONS = [
  { value: "postgres", label: "Postgres", icon: <Database className="h-3.5 w-3.5" /> },
  { value: "duckdb", label: "DuckDB", icon: <FileBox className="h-3.5 w-3.5" /> },
] as const;

const PLACEHOLDERS: Partial<Record<FieldName, string>> = {
  name: "Production warehouse",
  host: "db.example.com",
  database: "warehouse",
  user: "readonly_user",
  path: "/data/warehouse.duckdb",
};

interface Draft {
  id: string;
  probe: ConnectionProbe;
}

interface AddConnectionDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called once the connection is kept; `probe` is set when it was tested. */
  onAdded: (connectionId: string, probe: ConnectionProbe | null) => void;
}

function probeFailure(probe: ConnectionProbe): string {
  return probe.error ?? "The database could not be reached with these settings.";
}

/**
 * Add-connection form. The API can only test a saved connection, so "Test connection"
 * saves a draft, probes it, and removes it again if the probe fails, the user edits
 * the form, or the dialog is cancelled; "Add connection" keeps a tested draft.
 */
export function AddConnectionDialog({
  open,
  onClose,
  onAdded,
}: AddConnectionDialogProps) {
  const [values, setValues] = useState<ConnectionFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<FormErrors>({});
  const [draft, setDraft] = useState<Draft | null>(null);
  const [testFailure, setTestFailure] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "testing" | "saving">("idle");

  async function discardDraft(current: Draft | null) {
    if (!current) return;
    try {
      await deleteConnection(current.id);
    } catch {
      // Best effort: a leftover draft is visible in the list and can be deleted there.
    }
  }

  function set<K extends keyof ConnectionFormValues>(
    key: K,
    value: ConnectionFormValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    setTestFailure(null);
    if (draft) {
      void discardDraft(draft);
      setDraft(null);
    }
  }

  function close() {
    void discardDraft(draft);
    setDraft(null);
    setValues(EMPTY_FORM);
    setErrors({});
    setTestFailure(null);
    setApiError(null);
    onClose();
  }

  function validate(): boolean {
    const found = validateConnectionForm(values);
    setErrors(found);
    return Object.keys(found).length === 0;
  }

  async function runTest(): Promise<Draft | null> {
    setApiError(null);
    setTestFailure(null);
    const created = await createConnection(toCreateBody(values));
    const probe = await testConnection(created.id);
    const result = { id: created.id, probe };
    if (!probe.reachable) {
      await discardDraft(result);
      setTestFailure(probeFailure(probe));
      return null;
    }
    return result;
  }

  async function onTest() {
    if (phase !== "idle" || !validate()) return;
    setPhase("testing");
    try {
      setDraft(await runTest());
    } catch (err) {
      setApiError(apiErrorMessage(err));
    } finally {
      setPhase("idle");
    }
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (phase !== "idle" || !validate()) return;
    setPhase("saving");
    try {
      const kept = draft ?? (await createThenProbe());
      setDraft(null);
      setValues(EMPTY_FORM);
      onAdded(kept.id, kept.probe);
    } catch (err) {
      setApiError(apiErrorMessage(err));
    } finally {
      setPhase("idle");
    }
  }

  /** Save without a prior test; the probe result is a bonus, never a blocker. */
  async function createThenProbe(): Promise<{
    id: string;
    probe: ConnectionProbe | null;
  }> {
    setApiError(null);
    const created = await createConnection(toCreateBody(values));
    let probe: ConnectionProbe | null = null;
    try {
      probe = await testConnection(created.id);
    } catch {
      probe = null;
    }
    return { id: created.id, probe };
  }

  function text(field: FieldName, type = "text") {
    return (
      <Field id={`conn-${field}`} label={FIELD_LABELS[field]} error={errors[field]}>
        {(props) => (
          <Input
            {...props}
            type={type}
            value={values[field]}
            onChange={(event) => set(field, event.target.value)}
            autoComplete={field === "password" ? "new-password" : "off"}
            placeholder={PLACEHOLDERS[field]}
            aria-required="true"
          />
        )}
      </Field>
    );
  }

  const busy = phase !== "idle";

  return (
    <Dialog
      open={open}
      title="Add connection"
      description="RootCause only needs read-only access to your data."
      onClose={close}
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button
            variant="secondary"
            loading={phase === "testing"}
            disabled={busy}
            onClick={() => void onTest()}
          >
            Test connection
          </Button>
          <Button
            variant="primary"
            loading={phase === "saving"}
            disabled={busy}
            type="submit"
            form="add-connection-form"
          >
            Add connection
          </Button>
        </>
      }
    >
      <form
        id="add-connection-form"
        aria-label="Add connection"
        noValidate
        onSubmit={(event) => void onSubmit(event)}
        className="flex flex-col gap-4"
      >
        <Segmented
          name="connection-kind"
          label="Database type"
          value={values.kind}
          onChange={(kind: ConnectionKind) => set("kind", kind)}
          options={KIND_OPTIONS}
        />
        {text("name")}
        {values.kind === "postgres" ? (
          <>
            <div className="grid grid-cols-[1fr_6rem] gap-3">
              {text("host")}
              {text("port", "number")}
            </div>
            {text("database")}
            <div className="grid grid-cols-2 gap-3">
              {text("user")}
              {text("password", "password")}
            </div>
            <Field id="conn-sslmode" label="SSL mode">
              {(props) => (
                <Select
                  {...props}
                  value={values.sslmode}
                  onChange={(event) => set("sslmode", event.target.value as SslMode)}
                >
                  {SSL_MODES.map((mode) => (
                    <option key={mode} value={mode}>
                      {mode}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </>
        ) : (
          text("path")
        )}
        {draft && <TestResult probe={draft.probe} />}
        {testFailure && <Banner title="Test failed">{testFailure}</Banner>}
        {apiError && <Banner>{apiError}</Banner>}
      </form>
    </Dialog>
  );
}

function TestResult({ probe }: { probe: ConnectionProbe }) {
  const Icon = probe.read_only ? CheckCircle2 : XCircle;
  return (
    <div
      role="status"
      className={`flex items-center gap-2 rounded-control border p-3 ${
        probe.read_only
          ? "border-ok/30 bg-ok/10 text-ok"
          : "border-warn/30 bg-warn/10 text-warn"
      }`}
    >
      <Icon aria-hidden="true" className="h-4 w-4" />
      <span>
        {probe.read_only
          ? "Connected. Read-only access verified."
          : "Connected, but the account is not read-only."}
        {probe.server_version ? ` Server ${probe.server_version}.` : ""}
      </span>
    </div>
  );
}
