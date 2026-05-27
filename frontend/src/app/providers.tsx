"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useSetupStatus } from "@/hooks/use-setup";
import {
  ResumeGenerationContext,
  useResumeGenerationProvider,
} from "@/hooks/use-resume-generation";
import {
  JobProcessingContext,
  useJobProcessingProvider,
} from "@/hooks/use-job-processing";
import {
  DiscoverFlowContext,
  useDiscoverFlowProvider,
} from "@/hooks/use-discover-flow";
import {
  HotSearchContext,
  useHotSearchProvider,
} from "@/hooks/use-hot-search";
import {
  RefreshFlowContext,
  useRefreshFlowProvider,
} from "@/hooks/use-refresh-flow";
import {
  ExtractionTrackingContext,
  useExtractionTrackingProvider,
} from "@/hooks/use-extraction-tracking";
import {
  JobSelectionContext,
  useJobSelectionProvider,
} from "@/hooks/use-job-selection";
import {
  PublicationsImportContext,
  usePublicationsImportProvider,
} from "@/hooks/use-publications-import";

function ResumeGenerationProvider({ children }: { children: React.ReactNode }) {
  const state = useResumeGenerationProvider();
  return (
    <ResumeGenerationContext.Provider value={state}>
      {children}
    </ResumeGenerationContext.Provider>
  );
}

function JobProcessingProvider({ children }: { children: React.ReactNode }) {
  const state = useJobProcessingProvider();
  return (
    <JobProcessingContext.Provider value={state}>
      {children}
    </JobProcessingContext.Provider>
  );
}

function DiscoverFlowProvider({ children }: { children: React.ReactNode }) {
  const state = useDiscoverFlowProvider();
  return (
    <DiscoverFlowContext.Provider value={state}>
      {children}
    </DiscoverFlowContext.Provider>
  );
}

function HotSearchProvider({ children }: { children: React.ReactNode }) {
  const state = useHotSearchProvider();
  return (
    <HotSearchContext.Provider value={state}>
      {children}
    </HotSearchContext.Provider>
  );
}

function RefreshFlowProvider({ children }: { children: React.ReactNode }) {
  const state = useRefreshFlowProvider();
  return (
    <RefreshFlowContext.Provider value={state}>
      {children}
    </RefreshFlowContext.Provider>
  );
}

function ExtractionTrackingProvider({ children }: { children: React.ReactNode }) {
  const state = useExtractionTrackingProvider();
  return (
    <ExtractionTrackingContext.Provider value={state}>
      {children}
    </ExtractionTrackingContext.Provider>
  );
}

function JobSelectionProvider({ children }: { children: React.ReactNode }) {
  const state = useJobSelectionProvider();
  return (
    <JobSelectionContext.Provider value={state}>
      {children}
    </JobSelectionContext.Provider>
  );
}

function PublicationsImportProvider({ children }: { children: React.ReactNode }) {
  const state = usePublicationsImportProvider();
  return (
    <PublicationsImportContext.Provider value={state}>
      {children}
    </PublicationsImportContext.Provider>
  );
}

/**
 * Redirects the user to /setup if no LLM provider key is configured.
 * Skips the redirect when the user is already on /setup so the page can
 * load. All other pages route here until setup is complete.
 */
function SetupGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data, isLoading } = useSetupStatus();

  useEffect(() => {
    if (isLoading || !data) return;
    if (data.needs_setup && pathname !== "/setup") {
      router.replace("/setup");
    }
  }, [data, isLoading, pathname, router]);

  return <>{children}</>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <SetupGate>
          <ResumeGenerationProvider>
            <JobProcessingProvider>
              <DiscoverFlowProvider>
                <HotSearchProvider>
                  <RefreshFlowProvider>
                    <ExtractionTrackingProvider>
                      <JobSelectionProvider>
                        <PublicationsImportProvider>
                          {children}
                        </PublicationsImportProvider>
                      </JobSelectionProvider>
                    </ExtractionTrackingProvider>
                  </RefreshFlowProvider>
                </HotSearchProvider>
              </DiscoverFlowProvider>
            </JobProcessingProvider>
          </ResumeGenerationProvider>
        </SetupGate>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
