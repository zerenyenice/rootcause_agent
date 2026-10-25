import type { ReactNode } from "react";

interface FormFieldProps {
  id: string;
  label: string;
  error?: string;
  children: (props: {
    id: string;
    "aria-invalid": boolean;
    "aria-describedby": string | undefined;
  }) => ReactNode;
}

/** Label + control + inline error wired together for assistive tech. */
export function FormField({ id, label, error, children }: FormFieldProps) {
  const errorId = `${id}-error`;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id}>{label}</label>
      {children({
        id,
        "aria-invalid": error !== undefined,
        "aria-describedby": error ? errorId : undefined,
      })}
      {error && (
        <p id={errorId} className="text-sm text-red-800">
          {error}
        </p>
      )}
    </div>
  );
}
