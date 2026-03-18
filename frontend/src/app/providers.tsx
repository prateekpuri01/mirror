"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
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
        <ResumeGenerationProvider>
          <JobProcessingProvider>
            <DiscoverFlowProvider>
              <HotSearchProvider>{children}</HotSearchProvider>
            </DiscoverFlowProvider>
          </JobProcessingProvider>
        </ResumeGenerationProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
