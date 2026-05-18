"use client";

import { createContext, useContext, useCallback, useEffect, useRef, useState } from "react";
import { getExtractionStatus } from "@/lib/api";

interface ExtractionTrackingState {
  /** Job IDs currently being extracted */
  extractingJobIds: Set<string>;
  /** Job IDs that recently completed extraction */
  completedJobIds: Set<string>;
  /** Job IDs that failed extraction, with error messages */
  errors: Map<string, string>;
  /** Start tracking extraction for a job */
  startTracking: (jobId: string) => void;
  /** Check if a specific job is being extracted */
  isExtracting: (jobId: string) => boolean;
  /** Clear error for a job */
  clearError: (jobId: string) => void;
}

const ExtractionTrackingContext = createContext<ExtractionTrackingState>({
  extractingJobIds: new Set(),
  completedJobIds: new Set(),
  errors: new Map(),
  startTracking: () => {},
  isExtracting: () => false,
  clearError: () => {},
});

export function useExtractionTracking() {
  return useContext(ExtractionTrackingContext);
}

export { ExtractionTrackingContext };

/**
 * Hook that manages extraction polling for multiple jobs.
 * Use inside the provider component (providers.tsx).
 */
export function useExtractionTrackingProvider() {
  const [extractingJobIds, setExtractingJobIds] = useState<Set<string>>(new Set());
  const [completedJobIds, setCompletedJobIds] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Map<string, string>>(new Map());
  const intervalsRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  const stopPolling = useCallback((jobId: string) => {
    const interval = intervalsRef.current.get(jobId);
    if (interval) {
      clearInterval(interval);
      intervalsRef.current.delete(jobId);
    }
  }, []);

  const startTracking = useCallback(
    (jobId: string) => {
      // Don't double-track
      if (intervalsRef.current.has(jobId)) return;

      setExtractingJobIds((prev) => new Set(prev).add(jobId));

      const interval = setInterval(async () => {
        try {
          const status = await getExtractionStatus(jobId);

          if (!status.in_progress) {
            stopPolling(jobId);
            setExtractingJobIds((prev) => {
              const next = new Set(prev);
              next.delete(jobId);
              return next;
            });

            if (status.extraction_error) {
              setErrors((prev) => new Map(prev).set(jobId, status.extraction_error!));
            } else {
              // Mark as completed for UI feedback
              setCompletedJobIds((prev) => new Set(prev).add(jobId));
              setTimeout(() => {
                setCompletedJobIds((prev) => {
                  const next = new Set(prev);
                  next.delete(jobId);
                  return next;
                });
              }, 5000);
            }
          }
        } catch {
          // Silently retry on network error
        }
      }, 2000);

      intervalsRef.current.set(jobId, interval);
    },
    [stopPolling],
  );

  const isExtracting = useCallback(
    (jobId: string) => extractingJobIds.has(jobId),
    [extractingJobIds],
  );

  const clearError = useCallback((jobId: string) => {
    setErrors((prev) => {
      const next = new Map(prev);
      next.delete(jobId);
      return next;
    });
  }, []);

  // Cleanup all intervals on unmount
  useEffect(() => {
    return () => {
      for (const interval of intervalsRef.current.values()) {
        clearInterval(interval);
      }
    };
  }, []);

  return {
    extractingJobIds,
    completedJobIds,
    errors,
    startTracking,
    isExtracting,
    clearError,
  };
}
