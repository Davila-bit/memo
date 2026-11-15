import type { NextConfig } from "next";

// The API runs on another port; proxy it so the session cookie stays same-origin.
const API_ORIGIN = (process.env.HEARTH_API_ORIGIN ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const STATIC_EXPORT = process.env.HEARTH_STATIC_EXPORT === "1";
const BASE_PATH = (process.env.HEARTH_BASE_PATH ?? "").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  ...(STATIC_EXPORT ? {
    output: "export",
    basePath: BASE_PATH,
    trailingSlash: true,
    images: { unoptimized: true },
    env: {
      NEXT_PUBLIC_HEARTH_STATIC: "1",
      NEXT_PUBLIC_HEARTH_BASE_PATH: BASE_PATH,
    },
  } : {
    async rewrites() {
      return [
        { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
        { source: "/media/:path*", destination: `${API_ORIGIN}/media/:path*` },
      ];
    },
    async headers() {
      return [
        {
          source: "/sw.js",
          headers: [
            { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
            { key: "Service-Worker-Allowed", value: "/" },
          ],
        },
      ];
    },
  }),
};

export default nextConfig;
