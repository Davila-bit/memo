"use client";

import { Drop, ForkKnife, Pill } from "@phosphor-icons/react";
import { useEffect, useId, useState } from "react";
import { api } from "@/lib/api";
import type { Cue } from "@/lib/contracts";
import { Button } from "../ui/button";
import { Card, Eyebrow } from "../ui/card";
import { Sheet } from "../ui/sheet";
import { FIELD } from "./schedule-editor";

export function CueCard({ cue, onChange, notify: toast }: { cue: Cue; onChange: (cue: Cue) => void; notify: (text: string, tone: "ok" | "warn") => void }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(cue.title);
  const [line, setLine] = useState(cue.line);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const id = useId();
  const Icon = cue.kind === "drink" ? Drop : cue.kind === "meal" ? ForkKnife : Pill;
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !line.trim()) { setError("Please add a title and a line for the call."); return; }
    setError(null);
    setBusy(true);
    const before = cue;
    const next = { ...cue, title: title.trim(), line: line.trim() };
    onChange(next);
    try {
      onChange(await api.updateCue(cue.id, next));
      setEditing(false);
      toast("Saved.", "ok");
    } catch (err) {
      onChange(before);
      const message = err instanceof Error ? err.message : "Couldn't save this cue. Please try again.";
      setError(message);
      toast(message, "warn");
    } finally { setBusy(false); }
  }

  async function upload(file: File | undefined) {
    if (!file || busy) return;
    setPhotoError(null);
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) { setPhotoError("Choose a JPEG, PNG or WebP photo."); return; }
    if (file.size > 5 * 1024 * 1024) { setPhotoError("That photo is too large. Choose one under 5 MB."); return; }
    const before = { ...cue };
    const url = URL.createObjectURL(file);
    setPreview(url);
    setBusy(true);
    try {
      onChange(await api.uploadCuePhoto(cue.id, file));
      toast("Photo saved.", "ok");
    } catch (err) {
      onChange(before);
      const message = err instanceof Error ? err.message : "Couldn't upload that photo. Please try again.";
      setPhotoError(message);
      toast(message, "warn");
    } finally { setPreview(null); setBusy(false); }
  }

  return <>
    <Card as="article" className="flex min-w-0 flex-col" aria-label={cue.title}>
      <div className="mb-4 flex h-36 items-center justify-center overflow-hidden rounded-xl bg-sage-tint">
        {preview || cue.image_url ? (
          // Family uploads and local object URLs do not use the image optimiser.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={preview ?? cue.image_url!} alt={cue.title} className="h-full w-full object-contain" />
        ) : <div className="text-center text-sage-ink"><Icon aria-hidden className="mx-auto size-10" weight="duotone" /><p className="mt-2 text-sm">Her own {cue.kind === "drink" ? "mug" : cue.kind === "meal" ? "plate" : "pill box"}, when you add a photo</p></div>}
      </div>
      <h3 className="font-serif text-2xl">{cue.title}</h3>
      <Eyebrow className="mt-3">On the call</Eyebrow>
      <p className="mt-1 flex-1">{cue.line}</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="quiet" size="sm" disabled={busy} onClick={() => { setTitle(cue.title); setLine(cue.line); setError(null); setEditing(true); }} aria-label={`Edit cue: ${cue.title}`}>Edit cue</Button>
        <label htmlFor={`${id}-photo`} className={`ring-on-focus inline-flex min-h-11 cursor-pointer items-center rounded-full border border-line-strong/60 px-4 text-sm font-medium ${busy ? "opacity-60" : "hover:bg-paper"}`}>
          <span>{busy && preview ? "Uploading…" : "Upload a photo"}</span>
          <input id={`${id}-photo`} type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" disabled={busy} aria-describedby={`${id}-photo-hint${photoError ? ` ${id}-photo-error` : ""}`} aria-invalid={photoError ? true : undefined} onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
      </div>
      <p id={`${id}-photo-hint`} className="mt-2 text-xs text-muted">JPEG, PNG or WebP, up to 5 MB.</p>
      {photoError ? <p id={`${id}-photo-error`} role="alert" className="mt-2 text-sm text-coral-ink">{photoError}</p> : null}
      <span className="sr-only" role="status">{busy && preview ? "Uploading photo." : ""}</span>
    </Card>
    <Sheet open={editing} onOpenChange={(open) => { if (!busy) setEditing(open); }} title="Edit cue">
      <form noValidate onSubmit={save} className="space-y-4">
        <div><label htmlFor={`${id}-title`} className="font-semibold">Title</label><input id={`${id}-title`} className={FIELD} maxLength={100} value={title} onChange={(e) => setTitle(e.target.value)} aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-error` : undefined} /></div>
        <div><label htmlFor={`${id}-line`} className="font-semibold">The line shown on the call</label><textarea id={`${id}-line`} rows={3} maxLength={200} className={FIELD} value={line} onChange={(e) => setLine(e.target.value)} aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-error` : undefined} /></div>
        {error ? <p id={`${id}-error`} role="alert" className="text-sm text-coral-ink">{error}</p> : null}
        <Button type="submit" className="w-full" disabled={busy}>{busy ? "Saving…" : "Save cue"}</Button>
      </form>
    </Sheet>
  </>;
}
