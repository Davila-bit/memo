import type { TestInfo } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * One JSON line per axe scan, appended to e2e/.results/axe.jsonl. Playwright empties .results at
 * the start of every run, so the file always describes the latest run only.
 * `pnpm a11y:report` turns it into docs/ACCESSIBILITY.md.
 */
export const AXE_LOG = path.join(__dirname, ".results", "axe.jsonl");

export type Impact = "critical" | "serious" | "moderate" | "minor";

export interface AxeScan {
  state: string;
  project: string;
  viewport: { width: number; height: number } | null;
  path: string;
  counts: Record<Impact, number>;
  violations: { id: string; impact: Impact | null; nodes: number }[];
  rules_passed: number;
  axe_version: string;
  at: string;
}

interface AxeResultsLike {
  violations: { id: string; impact?: string | null; nodes: unknown[] }[];
  passes: unknown[];
  testEngine: { version: string };
}

export function recordAxe(info: TestInfo, state: string, pagePath: string, res: AxeResultsLike): void {
  const counts: Record<Impact, number> = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  for (const v of res.violations) if (v.impact && v.impact in counts) counts[v.impact as Impact] += 1;
  const scan: AxeScan = {
    state,
    project: info.project.name,
    viewport: info.project.use.viewport ?? null,
    path: pagePath,
    counts,
    violations: res.violations.map((v) => ({ id: v.id, impact: (v.impact as Impact | null) ?? null, nodes: v.nodes.length })),
    rules_passed: res.passes.length,
    axe_version: res.testEngine.version,
    at: new Date().toISOString(),
  };
  try {
    fs.mkdirSync(path.dirname(AXE_LOG), { recursive: true });
    fs.appendFileSync(AXE_LOG, JSON.stringify(scan) + "\n");
  } catch (e) {
    // The report is evidence, not a gate: never fail a test because the log couldn't be written.
    console.warn(`[axe] couldn't append to ${AXE_LOG}:`, e);
  }
}
