import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "RootCause",
  description: "Root cause analysis chat",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="flex h-screen flex-col bg-white text-gray-900 antialiased">
        <Nav />
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
      </body>
    </html>
  );
}
