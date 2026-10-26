const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Short relative time such as "just now", "5m ago", "3d ago", else a date. */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const seconds = Math.max(0, Math.round((now.getTime() - then.getTime()) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < HOUR) return `${Math.max(1, Math.round(seconds / MINUTE))}m ago`;
  if (seconds < DAY) return `${Math.round(seconds / HOUR)}h ago`;
  if (seconds < 7 * DAY) return `${Math.round(seconds / DAY)}d ago`;
  return then.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** "42s" or "2m 5s". */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < MINUTE) return `${total}s`;
  return `${Math.floor(total / MINUTE)}m ${total % MINUTE}s`;
}
