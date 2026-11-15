"use client";

import { ChatCircleText, MagnifyingGlass } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import type { AskResponse } from "@/lib/contracts";
import { EVENT_LABEL, formatDay, formatTime } from "@/lib/format";
import { useHearth } from "@/lib/store";
import { PageHeader } from "../page-header";
import { EVENT_STYLE } from "../today/timeline";
import { Button } from "../ui/button";
import { Card, Eyebrow } from "../ui/card";
import { Skeleton } from "../ui/skeleton";

const SUGGESTIONS = [
  "How much has she drunk today?",
  "Did she take her evening tablets on Tuesday?",
  "When did she last say no to a drink?",
  "Did she eat lunch yesterday?",
  "Any missed check-ins this week?",
];

export function AskView() {
  const resident = useHearth((s) => s.me?.resident);
  const [q, setQ] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  async function ask(question: string) {
    if (busy) return;
    const text = question.trim();
    setQ(question);
    if (text.length < 2 || text.length > 300) {
      setError("Please write a question between 2 and 300 characters.");
      input.current?.focus();
      return;
    }
    setError(null);
    setResult(null);
    setBusy(true);
    setHistory((old) => [text, ...old.filter((item) => item !== text)].slice(0, 5));
    try {
      setResult(await api.ask({ q: text }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't search the log. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title={`Ask about ${resident?.family_name_for_her ?? "Mum"}'s day`} intro="A little reassurance, from what the helper has logged." />
      <Card>
        <form noValidate onSubmit={(event) => { event.preventDefault(); void ask(q); }}>
          <label htmlFor="ask-question" className="block font-semibold">Your question</label>
          <textarea ref={input} id="ask-question" rows={2} maxLength={300} value={q} onChange={(e) => setQ(e.target.value)} aria-invalid={error ? true : undefined} aria-describedby={error ? "ask-error" : undefined} className="mt-2 block w-full resize-y rounded-2xl border border-line-strong bg-surface px-4 py-3 text-lg" placeholder="What would you like to know?" />
          {error ? <p id="ask-error" role="alert" className="mt-2 text-coral-ink">{error}</p> : null}
          <Button type="submit" className="mt-3 w-full sm:w-auto" disabled={busy}><MagnifyingGlass aria-hidden className="size-5" />{busy ? "Looking through the log…" : "Ask"}</Button>
        </form>
      </Card>
      {!result ? <Suggestions busy={busy} onAsk={(text) => void ask(text)} /> : null}
      <div className="mt-8" aria-live="polite" aria-atomic="true" aria-busy={busy}>
        {busy ? <div role="status"><span className="sr-only">Looking through the log.</span><Skeleton className="h-24" /></div> : result ? (
          <section aria-labelledby="answer-title">
            <Card tone="info">
              <Eyebrow id="answer-title">From the log</Eyebrow>
              <p className="mt-2 font-serif text-2xl leading-snug sm:text-3xl">{result.answer}</p>
            </Card>
            <h2 className="mt-6 mb-4 font-serif text-2xl">Matching moments</h2>
            {result.hits.length === 0 ? <p className="text-muted">No matching moments. Try another question or a different day.</p> : (
              <ol className="space-y-4 border-l border-line pl-4" data-testid="ask-hits">
                {result.hits.map(({ event, snippet }, i) => {
                  const style = EVENT_STYLE[event.kind];
                  const Icon = style.icon;
                  return <li key={`${event.id}-${i}`} className="rounded-2xl bg-surface p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm font-semibold ${style.badge}`}><Icon aria-hidden className="size-4" />{EVENT_LABEL[event.kind]}</span>
                      <time dateTime={event.ts} className="text-sm text-muted">{formatDay(event.ts, resident?.timezone)} · {formatTime(event.ts, resident?.timezone)}</time>
                    </div>
                    <p className="mt-2 font-medium">{event.summary}</p>
                    {snippet ? <p className="mt-1 text-sm text-muted">{snippet}</p> : null}
                  </li>;
                })}
              </ol>
            )}
            <p className="mt-4 text-xs text-muted">{result.engine === "typesense" ? "Search by Typesense" : "Simple search"}</p>
          </section>
        ) : !error ? <div className="rounded-3xl border border-dashed border-line-strong/60 px-6 py-10 text-center text-muted"><ChatCircleText aria-hidden className="mx-auto mb-3 size-9 text-sage-ink" weight="duotone" /><p>Ask anything about what the helper has logged</p></div> : null}
      </div>
      {result ? <Suggestions busy={busy} onAsk={(text) => void ask(text)} /> : null}
      {history.length ? <section className="mt-8" aria-labelledby="history-title"><h2 id="history-title" className="font-serif text-2xl">Recent questions</h2><ul className="mt-2 divide-y divide-line">{history.map((text) => <li key={text}><button type="button" disabled={busy} onClick={() => void ask(text)} className="min-h-11 w-full rounded-lg py-3 text-left text-sm text-sky-ink underline decoration-sky/40 underline-offset-4 disabled:opacity-60">{text}</button></li>)}</ul></section> : null}
    </>
  );
}

function Suggestions({ busy, onAsk }: { busy: boolean; onAsk: (text: string) => void }) {
  return <section className="mt-6" aria-labelledby="suggestions-title">
    <h2 id="suggestions-title" className="text-sm font-semibold text-muted">You could ask</h2>
    <div className="mt-2 flex flex-wrap gap-2">
      {SUGGESTIONS.map((text) => <Button key={text} variant="quiet" size="sm" className="h-auto py-2 text-left" disabled={busy} onClick={() => onAsk(text)}>{text}</Button>)}
    </div>
  </section>;
}
