import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Self-contained server bundle (.next/standalone) for the web/Dockerfile
  // runtime image; `next dev` and `next start` are unaffected.
  output: "standalone",
};

export default nextConfig;
