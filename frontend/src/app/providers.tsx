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
          <JobProcessingProvider>{children}</JobProcessingProvider>
        </ResumeGenerationProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
