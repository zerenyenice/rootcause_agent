import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

interface SegmentedProps<T extends string> {
  name: string;
  label: string;
  value: T;
  onChange: (value: T) => void;
  options: readonly { value: T; label: string; icon?: ReactNode }[];
  /** Stretch to the container, options sharing the width equally. */
  fullWidth?: boolean;
}

/** Segmented control built on native radios: keyboard and screen-reader support for free. */
export function Segmented<T extends string>({
  name,
  label,
  value,
  onChange,
  options,
  fullWidth = false,
}: SegmentedProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn(
        "gap-0.5 rounded-control border border-line bg-subtle p-0.5",
        fullWidth ? "flex w-full" : "inline-flex",
      )}
    >
      {options.map((option) => (
        <label
          key={option.value}
          className={cn(
            "inline-flex h-8 cursor-pointer items-center justify-center gap-1.5 rounded-md text-[13px] font-medium transition-colors",
            fullWidth ? "flex-1 px-1.5" : "px-3",
            "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent",
            option.value === value
              ? "bg-surface text-fg shadow-xs"
              : "text-muted hover:text-fg",
          )}
        >
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={option.value === value}
            onChange={() => onChange(option.value)}
            className="sr-only"
          />
          {option.icon}
          {option.label}
        </label>
      ))}
    </div>
  );
}
