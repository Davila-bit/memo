import Link from "next/link";
import { HearthMark } from "@/components/hearth-mark";
import { buttonClasses } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main id="main" className="flex min-h-dvh flex-col items-center justify-center px-5 py-10 text-center">
      <HearthMark className="size-12" />
      <h1 className="mt-6 font-serif text-[2.25rem] leading-tight font-medium">We couldn&apos;t find that page</h1>
      <p className="mt-3 max-w-sm text-[1.0625rem] text-muted">It may have moved. Today has everything that happened so far.</p>
      <Link href="/" className={buttonClasses("primary", "lg", "mt-8")}>
        Go to Today
      </Link>
    </main>
  );
}
