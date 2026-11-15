# How we built Memo

Two people, one weekend, and a lot of AI coding agents. This page is about how we kept that
organised: the contracts we wrote first, the workspaces we each worked in, and the task cards
that moved work between us.

## The ground rules

- **Contracts first.** Before any feature code, we wrote the shared data shapes and the API in
  `contracts/` (`contracts/API.md` and the `hearth_contracts` package). The backend, the voice agent
  and the web app all build against them. A contract change goes into one commit across all three, so
  no side drifts.
- **One workspace each.** We self-hosted Coder with a single template (`infra/coder/`). We each worked
  in our own workspace built from that template, and the app, its tests and the voice agent ran
  there.
- **Agents do the typing, people decide.** AI coding agents wrote much of the code. We chose
  what to build, wrote the cards and contracts, reviewed every merge, and did the tests on a real
  iPad and webcam ourselves. The agents and models we used are listed under Credits in the
  README.
- **Every claim gets a check.** Each card ends in a done-check: commands that must pass on the merge
  result, not just on the branch.

## Task cards

Work for the second workspace went through task cards: one Markdown file per piece of work, in a
folder both workspaces could see. A coding agent in Yusuf's workspace picked up each card, built it
on its own branch and opened a pull request. The other side reviewed it, re-ran the done-check
on the merge result, and merged.

A card has a fixed shape:

```
owner: <who works on it>
status: open
branch: feat/<slug>

# <title>
## Goal
## Owned paths        the only files this card may change
## Contracts to read  read them, don't edit them; ask in the thread
## Done-check         commands that must pass
## Thread             append-only conversation, one timestamped line each
```

The status line is the whole protocol:

| Status | Set by | Meaning |
|---|---|---|
| `open` | reviewer | Ready to pick up |
| `in-progress` | card owner | Being worked on |
| `blocked` | card owner | Needs an answer; the question is the last Thread line |
| `answered` | reviewer | The answer is in the Thread; work resumes |
| `review` | card owner | Pull request open, link in the Thread |
| `done` | reviewer | Merged |

Two rules kept it safe to run unattended: a card may only touch its owned paths, and nobody edits
the contracts from inside a card.

### The seven cards

All seven were built in Yusuf's workspace and merged through pull requests:

| Card | Merged as |
|---|---|
| 01 Week view: the seven-day picture | Merge pull request #1: Week view page |
| 02 Privacy and consent page | Merge pull request #2: Privacy and consent page |
| 03 CI and an accessibility report | Merge pull request #3: CI and accessibility report |
| 04 Kitchen iPad setup checklist | Merge pull request #4: Setup: kitchen iPad checklist |
| 05 Reflow at 320 px, reduced motion, keyboard | Merge pull request #5: Accessibility: reflow, reduced motion, keyboard |
| 06 Screen-reader semantics, forced colours | Merge pull request #6: Accessibility: semantics and forced colours |
| 07 Printable weekly summary | Merge pull request #7: Week: printable weekly summary |

The core (calls, the voice agent, the backend, the family app's main screens) was built on Diana's
side in a similar way: agents worked in separate git worktrees against the contracts, and each branch
was reviewed and merged with a merge commit you can see in the history.

## One card, start to finish

Card 03, shortened. The thread shows a real snag. The agent's GitHub token couldn't push a
workflow file, so the agent asked, and the reviewer changed the plan instead of waiting for a fix.

> **owner:** Yusuf's agent · **status:** done · **branch:** feat/ci
>
> **GitHub Actions CI and an accessibility report in the repo**
>
> **Goal.** Every push and pull request runs the whole test suite on GitHub, and the repo holds a
> short, honest accessibility report generated from the browser tests.
>
> **What to build.**
> 1. `.github/workflows/ci.yml` with three parallel jobs, `backend` (ruff + pytest), `agent` (pytest)
>    and `web` (lint, tsc, Playwright e2e). It uploads the Playwright report and screenshots even when
>    the job fails, has a 15-minute timeout per job, and cancels superseded runs. It uses no secrets:
>    every part of the app has a no-key mode.
> 2. Make the e2e axe helper write one JSON line per scan, and add `pnpm a11y:report` to turn those
>    lines into `docs/ACCESSIBILITY.md`: every page and state at phone and laptop size, the counts,
>    and an honest "checked by hand / not checked" section.
> 3. A CI badge and a link to the report at the top of the README.
>
> **Owned paths.** `.github/workflows/ci.yml`, `web/e2e/helpers.ts`, `web/e2e/a11y-report.ts`,
> `web/scripts/a11y-report.mjs`, `web/package.json` (scripts only), `docs/ACCESSIBILITY.md`, two
> README lines.
>
> **Done-check.**
> ```
> cd web && pnpm lint && pnpm exec tsc --noEmit && pnpm e2e && pnpm a11y:report
> cd backend && uv run pytest -q tests && cd ../agent && uv run pytest -q
> ```
>
> **Thread**
> - [00:40] Card created.
> - [00:42] Picked up, working on `feat/ci`. Heads-up: my GitHub token has no `workflow` scope yet.
> - [00:49] Work committed. Local done-check passes: e2e 27 passed, 44 axe scans with 0 violations,
>   backend 95 passed, agent 58 passed. The push was refused for the missing `workflow` scope.
> - [00:59] QUESTION: the scope still isn't approved. Should a maintainer push the workflow instead?
> - [01:00] ANSWER: don't wait. Move the workflow file to `infra/ci/ci.yml`, amend so no pushed
>   commit touches `.github/workflows/`, and push. The reviewer moves it into place on merge and
>   checks the run on `main`.
> - [01:04] PR open, one commit, no workflow path in it. Over to review.
> - [01:13] Reviewed: done-check green on the merge result (33 e2e, 112 backend, 67 agent, 46 axe
>   scans, 0 violations). Merged with a merge commit; workflow moved to `.github/workflows/ci.yml`.

The test and scan counts in that thread are from when the card merged. Later cards added more:
the current numbers are in the README and `docs/ACCESSIBILITY.md`.
