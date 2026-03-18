"use client";

import { createContext, useContext, useCallback, useEffect, useRef, useState } from "react";
import { getResumeStatus } from "@/lib/api";

interface ResumeGenerationState {
  /** Currently generating/revising */
  running: boolean;
  /** Job ID being worked on */
  jobId: string | null;
  /** Error from last run */
  error: string | null;
  /** Job IDs that recently completed (for glow effect) */
  completedJobIds: Set<string>;
  /** Start polling after kicking off a generation/revision */
  startPolling: (jobId: string) => void;
  /** Clear the completed glow for a job */
  clearCompleted: (jobId: string) => void;
}

const ResumeGenerationContext = createContext<ResumeGenerationState>({
  running: false,
  jobId: null,
  error: null,
  completedJobIds: new Set(),
  startPolling: () => {},
  clearCompleted: () => {},
});

export function useResumeGeneration() {
  return useContext(ResumeGenerationContext);
}

export { ResumeGenerationContext };

/**
 * Hook that manages polling logic. Use inside the provider component.
 */
export function useResumeGenerationProvider() {
  const [running, setRunning] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [completedJobIds, setCompletedJobIds] = useState<Set<string>>(new Set());
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const jobIdRef = useRef<string | null>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (forJobId: string) => {
      stopPolling();
      setRunning(true);
      setJobId(forJobId);
      setError(null);
      jobIdRef.current = forJobId;

      intervalRef.current = setInterval(async () => {
        try {
          const status = await getResumeStatus();
          if (!status.running) {
            stopPolling();
            setRunning(false);
            if (status.error) {
              setError(status.error);
            } else {
              // Mark this job as completed for glow
              const completedId = jobIdRef.current;
              if (completedId) {
                setCompletedJobIds((prev) => new Set(prev).add(completedId));
                // Auto-clear glow after 5 seconds
                setTimeout(() => {
                  setCompletedJobIds((prev) => {
                    const next = new Set(prev);
                    next.delete(completedId);
                    return next;
                  });
                }, 5000);
              }
            }
          }
        } catch {
          stopPolling();
          setRunning(false);
        }
      }, 2000);
    },
    [stopPolling]
  );

  const clearCompleted = useCallback((id: string) => {
    setCompletedJobIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  return {
    running,
    jobId,
    error,
    completedJobIds,
    startPolling,
    clearCompleted,
  };
}
