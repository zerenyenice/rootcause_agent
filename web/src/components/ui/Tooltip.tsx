import type { ReactNode } from "react";

/** CSS-only tooltip: shown on hover and keyboard focus of the wrapped control. */
export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="group/tip relative inline-flex">
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute top-full left-1/2 z-50 mt-1.5 hidden -translate-x-1/2 rounded-md bg-fg px-2 py-1 text-xs whitespace-nowrap text-bg group-focus-within/tip:block group-hover/tip:block"
      >
        {label}
      </span>
    </span>
  );
}
