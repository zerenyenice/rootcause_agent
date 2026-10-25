"use client";

import { useState } from "react";

interface DescriptionFieldProps {
  /** Accessible name, e.g. "Description for public.orders". */
  label: string;
  value: string | null;
  /** Persist the text; resolves false on failure so the field can revert. */
  onSave: (text: string) => Promise<boolean>;
}

/** Saves on blur, and only when the text changed. */
export function DescriptionField({ label, value, onSave }: DescriptionFieldProps) {
  const saved = value ?? "";
  const [draft, setDraft] = useState(saved);

  async function onBlur() {
    if (draft === saved) return;
    const ok = await onSave(draft);
    if (!ok) setDraft(saved);
  }

  return (
    <input
      type="text"
      aria-label={label}
      placeholder="Description"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => void onBlur()}
      className="w-full rounded border border-gray-300 p-1 text-sm"
    />
  );
}
