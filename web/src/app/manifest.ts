import type { MetadataRoute } from "next";
import { withBase } from "@/lib/base";

export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Memo",
    short_name: "Memo",
    description: "Gentle check-ins for Mum, and a quiet word to you when it matters.",
    start_url: withBase("/"),
    scope: withBase("/"),
    display: "standalone",
    background_color: "#F6F1E9",
    theme_color: "#F6F1E9",
    icons: [
      { src: withBase("/icons/icon-192.png"), sizes: "192x192", type: "image/png", purpose: "any" },
      { src: withBase("/icons/icon-512.png"), sizes: "512x512", type: "image/png", purpose: "any" },
      { src: withBase("/icons/maskable-512.png"), sizes: "512x512", type: "image/png", purpose: "maskable" },
      { src: withBase("/icons/icon.svg"), sizes: "any", type: "image/svg+xml", purpose: "any" },
    ],
  };
}
