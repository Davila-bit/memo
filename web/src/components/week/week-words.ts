import type { DayStats } from "@/lib/contracts";

const num = new Intl.NumberFormat("en-US");

/** "1,420" */
export function formatMl(ml: number): string {
  return num.format(Math.round(ml));
}

/** "2026-11-09" → a local Date (no UTC shift: the API's day is already her calendar day). */
export function parseDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function fmt(day: string, opts: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat("en-US", opts).format(parseDay(day));
}

/** "Mon" */
export function weekdayShort(day: string): string {
  return fmt(day, { weekday: "short" });
}

/** "Monday" */
export function weekdayLong(day: string): string {
  return fmt(day, { weekday: "long" });
}

/** "Monday, November 9" */
export function dayLong(day: string): string {
  return fmt(day, { weekday: "long", month: "long", day: "numeric" });
}

/** "Mon, Nov 9" */
export function dayShort(day: string): string {
  return fmt(day, { weekday: "short", month: "short", day: "numeric" });
}

/** "November 9 to November 15, 2026" for the seven days shown. */
export function weekRange(days: DayStats[]): string {
  const first = days[0]?.day;
  const last = days.at(-1)?.day;
  if (!first || !last) return "";
  return `${fmt(first, { month: "long", day: "numeric" })} to ${fmt(last, { month: "long", day: "numeric", year: "numeric" })}`;
}

/** Anything at all happened that day (a check-in ran, even if she said no or didn't answer). */
export function hasActivity(d: DayStats): boolean {
  return (
    d.fluid_ml > 0 ||
    d.drinks_confirmed > 0 ||
    d.drinks_refused > 0 ||
    d.meals_confirmed > 0 ||
    d.meds_confirmed > 0 ||
    d.checkins_missed > 0
  );
}

/** Oldest first; the last day is today. */
export function sortDays(days: DayStats[]): DayStats[] {
  return [...days].sort((a, b) => a.day.localeCompare(b.day));
}

/**
 * The big sentence. Averages only finished days that had check-ins, so a quiet first day of
 * use or today's half-finished total doesn't drag the number down.
 */
export function weekHeadline(days: DayStats[], mum: string): string {
  const today = days.at(-1);
  const past = days.slice(0, -1).filter(hasActivity);
  if (past.length === 0) {
    const ml = today ? Math.round(today.fluid_ml) : 0;
    return ml > 0 ? `${mum} has had ${formatMl(ml)} ml today.` : `${mum} hasn't had a drink the helper has seen yet this week.`;
  }
  const avg = past.reduce((t, d) => t + d.fluid_ml, 0) / past.length;
  if (past.length === 1) return `${mum} had ${formatMl(past[0].fluid_ml)} ml on ${weekdayLong(past[0].day)}.`;
  if (past.length === days.length - 1) return `${mum} averaged ${formatMl(avg)} ml a day this week.`;
  return `${mum} averaged ${formatMl(avg)} ml a day over ${past.length} days of check-ins.`;
}

/** The quieter line under the headline. */
export function weekSub(days: DayStats[], target: number): string {
  const today = days.at(-1);
  const past = days.slice(0, -1).filter(hasActivity);
  const parts = [`Her goal is ${formatMl(target)} ml a day.`];
  if (past.length > 0) {
    const met = past.filter((d) => d.fluid_ml >= target).length;
    if (past.length === 1) parts.push(met === 1 ? "She reached it that day." : "She didn't reach it that day.");
    else if (met === 0) parts.push("She hasn't reached it on any of those days.");
    else parts.push(`She reached it on ${met} of those ${past.length} days.`);
  }
  if (today && hasActivity(today) && past.length > 0) parts.push(`Today so far: ${formatMl(today.fluid_ml)} ml.`);
  return parts.join(" ");
}

export interface WeekTotals {
  drinks: number;
  refused: number;
  medsTaken: number;
  medsExpected: number;
  missed: number;
}

export function weekTotals(days: DayStats[]): WeekTotals {
  return days.reduce<WeekTotals>(
    (t, d) => ({
      drinks: t.drinks + d.drinks_confirmed,
      refused: t.refused + d.drinks_refused,
      medsTaken: t.medsTaken + d.meds_confirmed,
      // Days with no check-ins at all aren't counted as missed tablets: Memo wasn't watching.
      medsExpected: t.medsExpected + (hasActivity(d) ? d.meds_expected : 0),
      missed: t.missed + d.checkins_missed,
    }),
    { drinks: 0, refused: 0, medsTaken: 0, medsExpected: 0, missed: 0 },
  );
}

export function streakLine(n: number): string | null {
  if (n <= 0) return null;
  return `Tablets observed ${n} ${n === 1 ? "day" : "days"} running`;
}
