export const IS_STATIC = process.env.NEXT_PUBLIC_HEARTH_STATIC === "1";
const BASE_PATH = IS_STATIC ? (process.env.NEXT_PUBLIC_HEARTH_BASE_PATH ?? "").replace(/\/+$/, "") : "";

/** Prefix root-relative URLs used outside Next's Link/router. Leave blobs and external URLs alone. */
export function withBase(path: string): string {
  if (!BASE_PATH || !path.startsWith("/") || path.startsWith("//")) return path;
  if (path === BASE_PATH || path.startsWith(`${BASE_PATH}/`)) return path;
  return `${BASE_PATH}${path}`;
}
