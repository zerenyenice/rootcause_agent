"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { Segmented } from "@/components/ui/Segmented";
import type { ThemePreference } from "@/lib/theme";
import { useTheme } from "@/lib/useTheme";

const OPTIONS = [
  { value: "light", label: "Light", icon: <Sun className="h-3.5 w-3.5" /> },
  { value: "system", label: "Auto", icon: <Monitor className="h-3.5 w-3.5" /> },
  { value: "dark", label: "Dark", icon: <Moon className="h-3.5 w-3.5" /> },
] as const satisfies readonly {
  value: ThemePreference;
  label: string;
  icon: React.ReactNode;
}[];

export function ThemeToggle() {
  const { preference, setPreference } = useTheme();
  return (
    <Segmented
      name="theme"
      label="Theme"
      value={preference}
      onChange={setPreference}
      options={OPTIONS}
      fullWidth
    />
  );
}
