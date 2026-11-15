import { CalendarBlank } from "@phosphor-icons/react/dist/ssr";
import type { Metadata } from "next";
import { PageHeader, Placeholder } from "@/components/page-header";

export const metadata: Metadata = { title: "Schedule" };

// Placeholder: replace this page body (keep PageHeader for a consistent layout).
export default function SchedulePage() {
  return (
    <>
      <PageHeader title="Schedule" intro="When the helper checks in, and the photos of her own mug, plate and pill box it shows her." />
      <Placeholder icon={<CalendarBlank aria-hidden className="size-10" weight="duotone" />}>
        The schedule editor is on its way. For now, the helper follows the usual times.
      </Placeholder>
    </>
  );
}
