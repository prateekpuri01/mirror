"use client";

import { createContext, useContext, useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getResumeStatus } from "@/lib/api";

interface ResumeGenerationState {
  /** Currently generating/revising */
  running: boolean;
  /** Job ID being worked on */
  jobId: string | null;
  /** Error from last run */
  error: string | null;
  /** Current phase: planning | generating | critiquing | refining */
  phase: string | null;
  /** Current sub-step detail (human-readable) */
  stepDetail: string | null;
  /** Progress fraction (0-1) */
  progress: number;
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
  phase: null,
  stepDetail: null,
  progress: 0,
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
  const queryClient = useQueryClient();
  const [running, setRunning] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  const [stepDetail, setStepDetail] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
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
      setPhase("generating");
      jobIdRef.current = forJobId;

      intervalRef.current = setInterval(async () => {
        try {
          const status = await getResumeStatus();
          setPhase(status.phase ?? null);
          setStepDetail(status.step_detail ?? null);
          const stepNum = status.step_number ?? 0;
          const totalSteps = status.total_steps ?? 14;
          setProgress(totalSteps > 0 ? stepNum / totalSteps : 0);
          if (!status.running) {
            stopPolling();
            setRunning(false);
            setPhase(null);
            setStepDetail(null);
            setProgress(0);
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
              // Invalidate the jobs query so the new document attached to this
              // job is picked up regardless of whether the resume tab is
              // currently mounted. Without this, navigating away during
              // generation and coming back leaves the editor showing the
              // pre-generation doc — the new one sits in the DB unfetched.
              queryClient.invalidateQueries({ queryKey: ["jobs"] });
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
    phase,
    stepDetail,
    progress,
    completedJobIds,
    startPolling,
    clearCompleted,
  };
}
