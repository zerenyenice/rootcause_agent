import { AlertCircle, AlertTriangle, Info } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type BannerTone = "bad" | "warn" | "info";

const TONES: Record<BannerTone, string> = {
  bad: "border-bad/30 bg-bad/10 text-bad",
  warn: "border-warn/30 bg-warn/10 text-warn",
  info: "border-line bg-subtle text-fg",
};

const ICONS = { bad: AlertCircle, warn: AlertTriangle, info: Info } as const;

interface BannerProps {
  tone?: BannerTone;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  /** Use role="alert" for errors that appear in response to an action. */
  alert?: boolean;
}

export function Banner({
  tone = "bad",
  title,
  children,
  action,
  alert = true,
}: BannerProps) {
  const Icon = ICONS[tone];
  return (
    <div
      role={alert ? "alert" : "status"}
      className={cn("flex items-start gap-3 rounded-card border p-3", TONES[tone])}
    >
      <Icon aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium">{title}</p>}
        {children && (
          <div className={cn(title && "mt-0.5", "text-fg/80")}>{children}</div>
        )}
      </div>
      {action}
    </div>
  );
}
