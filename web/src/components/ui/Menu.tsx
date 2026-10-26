"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  icon?: ReactNode;
  danger?: boolean;
  /** Renders as a radio item (for pickers). */
  checked?: boolean;
}

interface MenuProps {
  /** Renders the trigger; spread `props` onto the button element. */
  trigger: (props: {
    "aria-haspopup": "menu";
    "aria-expanded": boolean;
    "aria-controls": string;
    onClick: () => void;
  }) => ReactNode;
  items: readonly MenuItem[];
  align?: "start" | "end";
  placement?: "bottom" | "top";
}

/** Small dropdown menu: closes on outside click, Escape or selection; arrows move focus. */
export function Menu({
  trigger,
  items,
  align = "start",
  placement = "bottom",
}: MenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector<HTMLElement>("[role^=menuitem]")?.focus();
    function onPointer(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [open]);

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      setOpen(false);
      rootRef.current?.querySelector<HTMLElement>("[aria-haspopup]")?.focus();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const nodes = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>("[role^=menuitem]") ?? [],
    );
    const index = nodes.indexOf(document.activeElement as HTMLElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    nodes[(index + step + nodes.length) % nodes.length]?.focus();
  }

  return (
    <div ref={rootRef} className="relative inline-block" onKeyDown={onKeyDown}>
      {trigger({
        "aria-haspopup": "menu",
        "aria-expanded": open,
        "aria-controls": id,
        onClick: () => setOpen((current) => !current),
      })}
      {open && (
        <div
          ref={listRef}
          id={id}
          role="menu"
          className={cn(
            "rc-fade-in absolute z-40 min-w-44 rounded-card border border-line bg-surface p-1 shadow-lg",
            align === "end" ? "right-0" : "left-0",
            placement === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5",
          )}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role={item.checked === undefined ? "menuitem" : "menuitemradio"}
              aria-checked={item.checked}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-subtle focus:bg-subtle focus:outline-none",
                item.danger && "text-bad",
              )}
            >
              {item.icon}
              <span className="flex-1 truncate">{item.label}</span>
              {item.checked && <span aria-hidden="true">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
