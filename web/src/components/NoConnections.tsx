import Link from "next/link";

export function NoConnections() {
  return (
    <p>
      No connections yet.{" "}
      <Link href="/connections" className="text-blue-700 underline">
        Add a connection
      </Link>{" "}
      to get started.
    </p>
  );
}
