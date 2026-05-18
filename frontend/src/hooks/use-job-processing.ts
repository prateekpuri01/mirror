"use client";

import { createContext, useContext, useCallback, useEffect, useRef, useState } from "react";

const STORAGE_KEY = "job-processing-ids";

// Anything still flagged "processing" past this threshold is presumed stuck —
// either the job's pipeline_stage never reached "scored" (backend issue) or
// the user is on a filtered jobs view that excludes the new job, so the
// list-based clearing path never fires. We drop these on rehydrate so a
// page reload always cleans up; the in-memory 2-min timeout still handles
// same-session clearing.
const STALE_AFTER_MS = 3 * 60 * 1000;

interface PersistedEntry {
  id: string;
  startedAt: number;
}

/** Load persisted entries, dropping anything older than STALE_AFTER_MS. */
function loadPersistedEntries(): PersistedEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    // Backward compat: the previous format was string[] with no timestamps.
    // Without timestamps we can't tell if entries are stale, so drop them
    // — anything from before this version would otherwise survive forever.
    if (Array.isArray(parsed) && (parsed.length === 0 || typeof parsed[0] === "string")) {
      localStorage.removeItem(STORAGE_KEY);
      return [];
    }
    const entries = parsed as PersistedEntry[];
    const now = Date.now();
    return entries.filter(
      (e) => e && typeof e.id === "string" && now - e.startedAt < STALE_AFTER_MS,
    );
  } catch {
    return [];
  }
}

function persistEntries(ids: Set<string>, startedAt: Map<string, number>) {
  try {
    if (ids.size === 0) {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      const now = Date.now();
      const entries: PersistedEntry[] = [...ids].map((id) => ({
        id,
        startedAt: startedAt.get(id) ?? now,
      }));
      localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
    }
  } catch {
    // ignore quota errors
  }
}

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
  // When each job started processing — used to expire stale IDs on rehydrate
  // so a stuck Processing badge can't survive a page reload.
  const startedAtRef = useRef<Map<string, number>>(new Map());

  const hasProcessingJobs = processingJobIds.size > 0;

  const startProcessing = useCallback((jobIds: string[]) => {
    if (jobIds.length === 0) return;
    const now = Date.now();
    setProcessingJobIds((prev) => {
      const next = new Set(prev);
      for (const id of jobIds) {
        next.add(id);
        // Preserve original timestamp on rehydrate (caller will have
        // pre-populated startedAtRef); otherwise stamp now.
        if (!startedAtRef.current.has(id)) {
          startedAtRef.current.set(id, now);
        }
      }
      return next;
    });

    // Per-job auto-remove timeout. For rehydrated entries we shorten the
    // remaining time by how long the job has already been processing —
    // otherwise a 1m59s-old job gets a fresh 2m clock and survives ~4m
    // total across the reload.
    for (const id of jobIds) {
      const existing = timeoutsRef.current.get(id);
      if (existing) clearTimeout(existing);

      const startedAt = startedAtRef.current.get(id) ?? now;
      const remaining = Math.max(0, TIMEOUT_MS - (now - startedAt));

      const timeout = setTimeout(() => {
        setProcessingJobIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        timeoutsRef.current.delete(id);
        startedAtRef.current.delete(id);
      }, remaining);

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
            startedAtRef.current.delete(job.id);
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

          // Keep newly-added jobs pinned to the top of the table (and the
          // emerald glow visible) for 2 minutes after scoring finishes,
          // up from the original 5s. Was: glow faded before the user
          // could even tab back to the jobs view, so jobs added during a
          // hot-search batch slid back into their natural relevance
          // ranking and got "lost" mid-list. 2 min is long enough to
          // catch context-switching but short enough to clear naturally.
          for (const id of newlyCompleted) {
            setTimeout(() => {
              setCompletedJobIds((prevCompleted) => {
                const nextCompleted = new Set(prevCompleted);
                nextCompleted.delete(id);
                return nextCompleted;
              });
            }, 120000);
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

  // Rehydrate from localStorage on mount. We pre-populate startedAtRef with
  // the persisted timestamps so startProcessing preserves them instead of
  // stamping "now" — that's what makes the 3-minute staleness check actually
  // work across reloads.
  useEffect(() => {
    const entries = loadPersistedEntries();
    if (entries.length > 0) {
      for (const e of entries) {
        startedAtRef.current.set(e.id, e.startedAt);
      }
      startProcessing(entries.map((e) => e.id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist to localStorage whenever processing IDs change
  useEffect(() => {
    persistEntries(processingJobIds, startedAtRef.current);
  }, [processingJobIds]);

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
