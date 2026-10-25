import type { Connection } from "@/lib/api";

interface ConnectionPickerProps {
  connections: Connection[];
  value: string;
  onChange: (id: string) => void;
}

export function ConnectionPicker({
  connections,
  value,
  onChange,
}: ConnectionPickerProps) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="connection-picker">Connection</label>
      <select
        id="connection-picker"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded border border-gray-400 p-1"
      >
        {connections.map((connection) => (
          <option key={connection.id} value={connection.id}>
            {connection.name}
          </option>
        ))}
      </select>
    </div>
  );
}
