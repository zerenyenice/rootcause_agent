"use client";

import { useState, type FormEvent } from "react";
import { createConnection, type ConnectionKind, type SslMode } from "@/lib/api";
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
import { FormField } from "./FormField";

const INPUT_CLASS = "rounded border border-gray-400 p-2";

interface ConnectionFormProps {
  onCreated: () => Promise<void>;
  onError: (message: string | null) => void;
}

export function ConnectionForm({ onCreated, onError }: ConnectionFormProps) {
  const [values, setValues] = useState<ConnectionFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  function set<K extends keyof ConnectionFormValues>(
    key: K,
    value: ConnectionFormValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (isSubmitting) return;
    const found = validateConnectionForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setIsSubmitting(true);
    onError(null);
    try {
      await createConnection(toCreateBody(values));
      setValues(EMPTY_FORM);
      await onCreated();
    } catch (err) {
      onError(apiErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  }

  function text(field: FieldName, type = "text") {
    return (
      <FormField id={`conn-${field}`} label={FIELD_LABELS[field]} error={errors[field]}>
        {(props) => (
          <input
            {...props}
            type={type}
            value={values[field]}
            onChange={(event) => set(field, event.target.value)}
            autoComplete={field === "password" ? "new-password" : "off"}
            aria-required="true"
            className={INPUT_CLASS}
          />
        )}
      </FormField>
    );
  }

  return (
    <form
      aria-label="Add connection"
      onSubmit={(event) => void onSubmit(event)}
      noValidate
      className="flex flex-col gap-3 rounded border border-gray-300 p-4"
    >
      <h2 className="text-lg font-semibold">Add connection</h2>
      {text("name")}
      <FormField id="conn-kind" label="Kind">
        {(props) => (
          <select
            {...props}
            value={values.kind}
            onChange={(event) => set("kind", event.target.value as ConnectionKind)}
            className={INPUT_CLASS}
          >
            <option value="postgres">postgres</option>
            <option value="duckdb">duckdb</option>
          </select>
        )}
      </FormField>
      {values.kind === "postgres" ? (
        <>
          {text("host")}
          {text("port", "number")}
          {text("database")}
          {text("user")}
          {text("password", "password")}
          <FormField id="conn-sslmode" label="SSL mode">
            {(props) => (
              <select
                {...props}
                value={values.sslmode}
                onChange={(event) => set("sslmode", event.target.value as SslMode)}
                className={INPUT_CLASS}
              >
                {SSL_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            )}
          </FormField>
        </>
      ) : (
        text("path")
      )}
      <button
        type="submit"
        disabled={isSubmitting}
        className="self-start rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
      >
        Add connection
      </button>
    </form>
  );
}
