import { AppShell } from "@/components/app-shell";
import { ServiceWorker } from "@/components/service-worker";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ServiceWorker />
      <AppShell>{children}</AppShell>
    </>
  );
}
