import { AppSidebar } from "@/components/AppSidebar";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <SidebarProvider>
      <div className="flex w-full h-screen overflow-hidden bg-background text-foreground">
        <AppSidebar />
        <main className="flex-1 flex flex-col min-w-0 relative">
          <div className="absolute top-4 left-4 z-50 md:hidden">
             <SidebarTrigger />
          </div>
          {children}
        </main>
      </div>
    </SidebarProvider>
  );
}
