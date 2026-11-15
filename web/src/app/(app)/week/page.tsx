import { ChartBar } from "@phosphor-icons/react/dist/ssr";
import type { Metadata } from "next";
import { PageHeader, Placeholder } from "@/components/page-header";

export const metadata: Metadata = { title: "Week" };

// Placeholder: replace this page body (keep PageHeader for a consistent layout).
export default function WeekPage() {
  return (
    <>
      <PageHeader title="Week" intro="Seven days at a glance: how much she drank, meals, tablets and any missed check-ins." />
      <Placeholder icon={<ChartBar aria-hidden className="size-10" weight="duotone" />}>
        The week view is on its way. It will show a calm seven-day picture from the check-ins.
      </Placeholder>
    </>
  );
}
