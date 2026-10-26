"use client";

import { Check } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Input, Textarea } from "@/components/ui/Field";

const SAVED_MS = 2000;

interface DescriptionFieldProps {
  /** Accessible name, e.g. "Description for public.orders". */
  label: string;
  value: string | null;
  /** Persist the text; resolves false on failure so the field can revert. */
  onSave: (text: string) => Promise<boolean>;
  multiline?: boolean;
  placeholder?: string;
}

/** Autosaves on blur, only when the text changed, and confirms with "Saved". */
export function DescriptionField({
  label,
  value,
  onSave,
  multiline = false,
  placeholder = "Add a description",
}: DescriptionFieldProps) {
  const saved = value ?? "";
  const [draft, setDraft] = useState(saved);
  const [justSaved, setJustSaved] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  async function onBlur() {
    if (draft === saved) return;
    const ok = await onSave(draft);
    if (!ok) {
      setDraft(saved);
      return;
    }
    setJustSaved(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setJustSaved(false), SAVED_MS);
  }

  const common = {
    "aria-label": label,
    placeholder,
    value: draft,
    onChange: (event: { target: { value: string } }) => setDraft(event.target.value),
    onBlur: () => void onBlur(),
  };

  return (
    <div className="relative">
      {multiline ? (
        <Textarea {...common} rows={3} className="py-2" />
      ) : (
        <Input {...common} className="h-8 text-[13px]" />
      )}
      <span
        role="status"
        className="pointer-events-none absolute top-1.5 right-2 flex items-center gap-1 text-xs text-ok"
      >
        {justSaved && (
          <>
            <Check aria-hidden="true" className="h-3 w-3" />
            Saved
          </>
        )}
      </span>
    </div>
  );
}
