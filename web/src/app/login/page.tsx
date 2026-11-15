import type { Metadata } from "next";
import { LoginForm } from "./login-form";
import { HearthMark } from "@/components/hearth-mark";
import { IS_STATIC } from "@/lib/base";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <main id="main" className="flex min-h-dvh flex-col items-center justify-center px-5 py-10">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-3">
          <HearthMark className="size-11" />
          <span className="font-serif text-3xl font-medium tracking-tight">Memo</span>
        </div>
        <h1 className="mt-8 font-serif text-[2.25rem] leading-[1.1] font-medium tracking-tight">Family sign-in</h1>
        <p className="mt-3 text-[1.0625rem] text-muted">Gentle check-ins for someone you love, and a quiet word to you when it matters.</p>
        <LoginForm />
        {IS_STATIC ? <p className="mt-6 text-sm text-muted">This is the practice version: sample data, nothing leaves your browser. The real app calls a kitchen iPad over FaceTime.</p> : null}
        <p className="mt-10 text-sm text-muted">Memo never records video or audio. The helper only watches live, during each short check-in.</p>
      </div>
    </main>
  );
}
