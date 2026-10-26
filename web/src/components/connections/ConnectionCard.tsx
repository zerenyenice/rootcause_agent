"use client";

import {
  AlertCircle,
  CheckCircle2,
  MoreHorizontal,
  ShieldQuestion,
  ShieldAlert,
  TableProperties,
  Trash2,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Menu } from "@/components/ui/Menu";
import { Spinner } from "@/components/ui/Spinner";
import { KindIcon } from "@/components/chat/ConnectionPill";
import type { Connection, ConnectionProbe } from "@/lib/api";

const KIND_LABELS = { postgres: "Postgres", duckdb: "DuckDB" } as const;

export type ProbeState = { phase: "testing" } | { phase: "done"; probe: ConnectionProbe };

interface ConnectionCardProps {
  connection: Connection;
  probeState: ProbeState | undefined;
  onTest: (connection: Connection) => void;
  onDelete: (connection: Connection) => void;
}

export function describeTarget(connection: Connection): string {
  if (connection.kind === "duckdb") return connection.path ?? "";
  const hostPort = [connection.host, connection.port].filter((v) => v !== null).join(":");
  return `${hostPort} / ${connection.database ?? ""}`;
}

interface Status {
  tone: BadgeTone;
  text: string;
  icon: React.ReactNode;
  reason: string | null;
}

export function statusFor(state: ProbeState | undefined): Status {
  if (state?.phase !== "done") {
    return {
      tone: "neutral",
      text: "Not verified",
      icon: <ShieldQuestion className="h-3 w-3" />,
      reason: null,
    };
  }
  const { probe } = state;
  if (!probe.reachable) {
    return {
      tone: "bad",
      text: "Test failed",
      icon: <AlertCircle className="h-3 w-3" />,
      reason: probe.error ?? "The database could not be reached.",
    };
  }
  if (!probe.read_only) {
    return {
      tone: "warn",
      text: "Not read-only",
      icon: <ShieldAlert className="h-3 w-3" />,
      reason: "This account can write to the database. Use a read-only user.",
    };
  }
  return {
    tone: "ok",
    text: "Read-only verified",
    icon: <CheckCircle2 className="h-3 w-3" />,
    reason: null,
  };
}

export function ConnectionCard({
  connection,
  probeState,
  onTest,
  onDelete,
}: ConnectionCardProps) {
  const isTesting = probeState?.phase === "testing";
  const status = statusFor(probeState);
  const version = probeState?.phase === "done" ? probeState.probe.server_version : null;

  return (
    <li>
      <Card className="flex h-full flex-col gap-4 p-4">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control border border-line bg-subtle text-muted">
            <KindIcon kind={connection.kind} className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="truncate font-semibold">{connection.name}</h2>
            <p className="truncate text-[13px] text-muted">
              <span>{KIND_LABELS[connection.kind]}</span> · {describeTarget(connection)}
            </p>
          </div>
          <Menu
            align="end"
            items={[
              {
                label: "Delete",
                danger: true,
                icon: <Trash2 className="h-4 w-4" />,
                onSelect: () => onDelete(connection),
              },
            ]}
            trigger={(props) => (
              <Button
                size="icon"
                variant="ghost"
                {...props}
                aria-label={`More actions for ${connection.name}`}
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            )}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2" aria-live="polite">
          {isTesting ? (
            <Badge icon={<Spinner className="h-3 w-3" />}>Testing…</Badge>
          ) : (
            <Badge tone={status.tone} icon={status.icon}>
              {status.text}
            </Badge>
          )}
          {version && <span className="text-xs text-muted">Server {version}</span>}
        </div>
        {status.reason && !isTesting && (
          <p className="text-[13px] text-muted">{status.reason}</p>
        )}

        <div className="mt-auto flex gap-2">
          <Button
            size="sm"
            icon={<Zap className="h-3.5 w-3.5" />}
            disabled={isTesting}
            onClick={() => onTest(connection)}
            aria-label={`Test ${connection.name}`}
          >
            Test
          </Button>
          <Link
            href={`/catalog?connection=${encodeURIComponent(connection.id)}`}
            className="inline-flex h-8 items-center gap-1.5 rounded-control border border-line-strong bg-surface px-3 text-[13px] font-medium hover:bg-subtle"
          >
            <TableProperties aria-hidden="true" className="h-3.5 w-3.5" />
            Open catalog
          </Link>
        </div>
      </Card>
    </li>
  );
}
