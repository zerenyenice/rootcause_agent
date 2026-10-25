"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/connections", label: "Connections" },
  { href: "/catalog", label: "Catalog" },
] as const;

export function Nav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="border-b border-gray-200 px-4 py-2">
      <ul className="mx-auto flex max-w-3xl gap-4">
        {LINKS.map(({ href, label }) => {
          const isCurrent = pathname === href;
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={isCurrent ? "page" : undefined}
                className={isCurrent ? "font-semibold underline" : "text-blue-700"}
              >
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
