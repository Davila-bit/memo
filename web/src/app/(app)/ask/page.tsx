import { ChatCircleText } from "@phosphor-icons/react/dist/ssr";
import type { Metadata } from "next";
import { PageHeader, Placeholder } from "@/components/page-header";

export const metadata: Metadata = { title: "Ask" };

// Placeholder: replace this page body (keep PageHeader for a consistent layout).
export default function AskPage() {
  return (
    <>
      <PageHeader title="Ask" intro="Ask about her day in plain words, like ‘Did she take her evening tablets on Tuesday?’" />
      <Placeholder icon={<ChatCircleText aria-hidden className="size-10" weight="duotone" />}>
        Asking about her day is on its way.
      </Placeholder>
    </>
  );
}
