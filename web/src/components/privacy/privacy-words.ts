import type { Consent } from "@/lib/contracts";

/** Plain names for the keys `GET /api/privacy` counts. Unknown keys fall back to their own words. */
const COUNT_WORDS: Record<string, [one: string, many: string]> = {
  events: ["check-in note", "check-in notes"],
  checkins: ["check-in", "check-ins"],
  alerts: ["alert", "alerts"],
  cue_photos: ["cue photo", "cue photos"],
  frames_stored: ["image from her camera", "images from her camera"],
  recordings: ["recording", "recordings"],
  transcripts: ["transcript", "transcripts"],
};

export function countLabel(key: string, n: number): string {
  const words = COUNT_WORDS[key];
  if (words) return n === 1 ? words[0] : words[1];
  return key.replace(/_/g, " ");
}

export const RELATION: Record<Consent["relation"], string> = {
  self: "herself",
  healthcare_agent: "as her healthcare agent",
  guardian: "as her guardian",
};

/** "November 3, 2026" */
export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric" }).format(new Date(iso));
}
