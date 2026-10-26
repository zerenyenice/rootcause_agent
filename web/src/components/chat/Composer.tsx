"use client";

import { ArrowUp, Square } from "lucide-react";
import {
  useLayoutEffect,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { cn } from "@/lib/cn";

const MAX_HEIGHT_PX = 200;

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  isRunning: boolean;
  /** Input is unavailable; `hint` explains why. */
  disabled?: boolean;
  hint?: string | null;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  isRunning,
  disabled = false,
  hint,
  inputRef,
}: ComposerProps) {
  // Grow with the content, up to a cap.
  useLayoutEffect(() => {
    const area = inputRef.current;
    if (!area) return;
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value, inputRef]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      onSend();
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (isRunning) onStop();
    else onSend();
  }

  const canSend = !disabled && value.trim() !== "";

  return (
    <div className="bg-gradient-to-t from-bg from-70% to-transparent px-4 pt-3 pb-4">
      <form
        onSubmit={onSubmit}
        className={cn(
          "mx-auto flex w-full max-w-[760px] items-end gap-2 rounded-card border border-line-strong bg-surface p-2 shadow-sm transition-colors",
          "focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/20",
        )}
      >
        <label htmlFor="chat-input" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-input"
          ref={inputRef}
          value={value}
          rows={1}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={disabled ? "Chat is unavailable" : "Ask why something happened…"}
          className="max-h-[200px] min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 outline-none placeholder:text-faint disabled:cursor-not-allowed"
        />
        {isRunning ? (
          <button
            type="submit"
            aria-label="Stop"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control bg-fg text-bg transition-opacity hover:opacity-80"
          >
            <Square aria-hidden="true" className="h-3.5 w-3.5 fill-current" />
          </button>
        ) : (
          <button
            type="submit"
            aria-label="Send"
            disabled={!canSend}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control bg-accent text-accent-fg transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ArrowUp aria-hidden="true" className="h-4 w-4" />
          </button>
        )}
      </form>
      <p className="mx-auto mt-2 max-w-[760px] px-1 text-xs text-muted">
        {hint ?? "Enter to send · Shift+Enter for a new line"}
      </p>
    </div>
  );
}
