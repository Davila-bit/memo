import { ShieldCheck } from "@phosphor-icons/react/dist/ssr";
import type { Metadata } from "next";
import { PageHeader, Placeholder } from "@/components/page-header";

export const metadata: Metadata = { title: "Privacy" };

// Placeholder: replace this page body (keep PageHeader for a consistent layout).
export default function PrivacyPage() {
  return (
    <>
      <PageHeader title="Privacy" intro="What Hearth keeps, what it never keeps, and who agreed to the check-ins." />
      <Placeholder icon={<ShieldCheck aria-hidden className="size-10" weight="duotone" />}>
        The privacy page is on its way. Hearth never records video or audio: the helper only watches live.
      </Placeholder>
    </>
  );
}
