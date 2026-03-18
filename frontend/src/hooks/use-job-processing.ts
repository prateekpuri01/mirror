"use client";

import { createContext, useContext, useCallback, useEffect, useRef, useState } from "react";

interface JobProcessingState {
  /** IDs of jobs currently being processed by the pipeline */
  processingJobIds: Set<string>;
  /** IDs of jobs that recently completed processing (for glow effect) */
  completedJobIds: Set<string>;
  /** Whether any jobs are currently processing (controls refetch interval) */
  hasProcessingJobs: boolean;
  /** Start tracking processing for a batch of job IDs */
  startProcessing: (jobIds: string[]) => void;
  /** Sync processing state with current jobs data from the query */
  syncWithJobsData: (jobs: { id: string; pipeline_stage: string }[]) => void;
  /** Clear the completed glow for a job */
  clearCompleted: (id: string) => void;
}

const JobProcessingContext = createContext<JobProcessingState>({
  processingJobIds: new Set(),
  completedJobIds: new Set(),
  hasProcessingJobs: false,
  startProcessing: () => {},
  syncWithJobsData: () => {},
  clearCompleted: () => {},
});

export function useJobProcessing() {
  return useContext(JobProcessingContext);
}

export { JobProcessingContext };

const TERMINAL_STAGES = new Set(["scored", "skipped"]);
const TIMEOUT_MS = 2 * 60 * 1000; // 2 minutes

/**
 * Hook that manages job processing tracking. Use inside the provider component.
 */
export function useJobProcessingProvider() {
  const [processingJobIds, setProcessingJobIds] = useState<Set<string>>(new Set());
  const [completedJobIds, setCompletedJobIds] = useState<Set<string>>(new Set());
  const timeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const hasProcessingJobs = processingJobIds.size > 0;

  const startProcessing = useCallback((jobIds: string[]) => {
    if (jobIds.length === 0) return;
    setProcessingJobIds((prev) => {
      const next = new Set(prev);
      for (const id of jobIds) {
        next.add(id);
      }
      return next;
    });

    // Set 2-minute timeout for each job to auto-remove stale IDs
    for (const id of jobIds) {
      const existing = timeoutsRef.current.get(id);
      if (existing) clearTimeout(existing);

      const timeout = setTimeout(() => {
        setProcessingJobIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        timeoutsRef.current.delete(id);
      }, TIMEOUT_MS);

      timeoutsRef.current.set(id, timeout);
    }
  }, []);

  const syncWithJobsData = useCallback(
    (jobs: { id: string; pipeline_stage: string }[]) => {
      setProcessingJobIds((prev) => {
        if (prev.size === 0) return prev;

        const newlyCompleted: string[] = [];
        const next = new Set(prev);

        for (const job of jobs) {
          if (prev.has(job.id) && TERMINAL_STAGES.has(job.pipeline_stage)) {
            next.delete(job.id);
            newlyCompleted.push(job.id);

            // Clear the timeout since the job completed normally
            const timeout = timeoutsRef.current.get(job.id);
            if (timeout) {
              clearTimeout(timeout);
              timeoutsRef.current.delete(job.id);
            }
          }
        }

        if (newlyCompleted.length > 0) {
          // Move to completed set for glow effect
          setCompletedJobIds((prevCompleted) => {
            const nextCompleted = new Set(prevCompleted);
            for (const id of newlyCompleted) {
              nextCompleted.add(id);
            }
            return nextCompleted;
          });

          // Auto-clear glow after 5 seconds
          for (const id of newlyCompleted) {
            setTimeout(() => {
              setCompletedJobIds((prevCompleted) => {
                const nextCompleted = new Set(prevCompleted);
                nextCompleted.delete(id);
                return nextCompleted;
              });
            }, 5000);
          }
        }

        return next;
      });
    },
    []
  );

  const clearCompleted = useCallback((id: string) => {
    setCompletedJobIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      for (const timeout of timeoutsRef.current.values()) {
        clearTimeout(timeout);
      }
    };
  }, []);

  return {
    processingJobIds,
    completedJobIds,
    hasProcessingJobs,
    startProcessing,
    syncWithJobsData,
    clearCompleted,
  };
}
