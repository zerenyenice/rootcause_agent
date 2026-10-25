import type { Connection, ConnectionProbe } from "@/lib/api";

export type ProbeState = { phase: "testing" } | { phase: "done"; probe: ConnectionProbe };

interface ConnectionRowProps {
  connection: Connection;
  probeState: ProbeState | undefined;
  onTest: (connection: Connection) => void;
  onDelete: (connection: Connection) => void;
}

function describeTarget(connection: Connection): string {
  if (connection.kind === "duckdb") return connection.path ?? "";
  const hostPort = [connection.host, connection.port].filter((v) => v !== null).join(":");
  return `${hostPort} / ${connection.database ?? ""}`;
}

function badgeFor(probe: ConnectionProbe): { text: string; className: string } {
  if (!probe.reachable)
    return { text: "Unreachable", className: "bg-red-100 text-red-800" };
  if (!probe.read_only)
    return { text: "Not read-only", className: "bg-yellow-100 text-yellow-800" };
  return { text: "Read-only verified", className: "bg-green-100 text-green-800" };
}

export function ConnectionRow({
  connection,
  probeState,
  onTest,
  onDelete,
}: ConnectionRowProps) {
  const isTesting = probeState?.phase === "testing";
  const badge = probeState?.phase === "done" ? badgeFor(probeState.probe) : null;
  const version = probeState?.phase === "done" ? probeState.probe.server_version : null;

  return (
    <li className="flex flex-wrap items-center gap-3 rounded border border-gray-300 p-3">
      <div className="flex-1">
        <p className="font-medium">{connection.name}</p>
        <p className="text-sm text-gray-600">
          <span>{connection.kind}</span> · <span>{describeTarget(connection)}</span>
        </p>
      </div>
      {isTesting && (
        <span role="status" className="text-sm text-gray-600">
          Testing...
        </span>
      )}
      {badge && (
        <span className={`rounded px-2 py-1 text-sm ${badge.className}`}>
          {badge.text}
        </span>
      )}
      {version && <span className="text-sm text-gray-600">Server version {version}</span>}
      <button
        type="button"
        onClick={() => onTest(connection)}
        disabled={isTesting}
        className="rounded border border-gray-400 px-3 py-1 disabled:opacity-50"
      >
        Test
      </button>
      <button
        type="button"
        onClick={() => onDelete(connection)}
        className="rounded border border-red-600 px-3 py-1 text-red-700"
      >
        Delete
      </button>
    </li>
  );
}
