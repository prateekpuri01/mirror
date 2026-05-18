"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import { HotSearchHit } from "@/lib/types";
import { getApiUrl } from "@/lib/api";

export type HotSearchPhase = "idle" | "searching" | "done" | "error";

export type CandidateLogStatus = "checking" | "accepted" | "rejected";

export interface CandidateLogEntry {
  name: string;
  source?: string;
  status: CandidateLogStatus;
  reason?: string;
}

export interface HotSearchOptions {
  locations?: string[];
  minSalary?: number;
  referenceJobIds?: string[];
}

// Per-hit selections (slug → set of selected job URLs) — survives navigation
export type HitSelections = Record<string, Set<string>>;

export type ImportStatus = "idle" | "importing" | "imported" | "error";

interface HotSearchState {
  // SSE-streamed result state
  hits: HotSearchHit[];
  status: string;
  errorMessage: string;
  phase: HotSearchPhase;
  isSearching: boolean;
  candidateName: string;
  candidateLog: CandidateLogEntry[];
  // Form/filter inputs — hoisted into context so they survive navigation.
  // Setting them here means typing guidance, configuring filters, and
  // toggling hit selections all persist when the user moves tabs and
  // comes back.
  guidance: string;
  setGuidance: (v: string) => void;
  selectedLocations: string[];
  setSelectedLocations: (v: string[] | ((prev: string[]) => string[])) => void;
  minSalary: string;
  setMinSalary: (v: string) => void;
  selectedRefIds: Set<string>;
  setSelectedRefIds: (v: Set<string> | ((prev: Set<string>) => Set<string>)) => void;
  // Post-results selection state
  expandedHit: string | null;
  setExpandedHit: (v: string | null) => void;
  selectedSlugs: Set<string>;
  setSelectedSlugs: (v: Set<string> | ((prev: Set<string>) => Set<string>)) => void;
  hitSelections: HitSelections;
  setHitSelections: (v: HitSelections | ((prev: HitSelections) => HitSelections)) => void;
  importStatuses: Record<string, ImportStatus>;
  setImportStatuses: (
    v: Record<string, ImportStatus> | ((prev: Record<string, ImportStatus>) => Record<string, ImportStatus>),
  ) => void;
  // Actions
  startSearch: (
    sources: string[],
    guidance: string,
    options?: HotSearchOptions,
  ) => void;
  stopSearch: () => void;
  clearSearch: () => void;
}

const HotSearchContext = createContext<HotSearchState>({
  hits: [],
  status: "",
  errorMessage: "",
  phase: "idle",
  isSearching: false,
  candidateName: "",
  candidateLog: [],
  guidance: "",
  setGuidance: () => {},
  selectedLocations: [],
  setSelectedLocations: () => {},
  minSalary: "",
  setMinSalary: () => {},
  selectedRefIds: new Set(),
  setSelectedRefIds: () => {},
  expandedHit: null,
  setExpandedHit: () => {},
  selectedSlugs: new Set(),
  setSelectedSlugs: () => {},
  hitSelections: {},
  setHitSelections: () => {},
  importStatuses: {},
  setImportStatuses: () => {},
  startSearch: () => {},
  stopSearch: () => {},
  clearSearch: () => {},
});

export function useHotSearch() {
  return useContext(HotSearchContext);
}

export { HotSearchContext };

export function useHotSearchProvider(): HotSearchState {
  const [hits, setHits] = useState<HotSearchHit[]>([]);
  const [status, setStatus] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [phase, setPhase] = useState<HotSearchPhase>("idle");
  const [candidateName, setCandidateName] = useState("");
  const [candidateLog, setCandidateLog] = useState<CandidateLogEntry[]>([]);
  // Persisted form + selection state
  const [guidance, setGuidance] = useState("");
  const [selectedLocations, setSelectedLocations] = useState<string[]>([]);
  const [minSalary, setMinSalary] = useState("");
  const [selectedRefIds, setSelectedRefIds] = useState<Set<string>>(new Set());
  const [expandedHit, setExpandedHit] = useState<string | null>(null);
  const [selectedSlugs, setSelectedSlugs] = useState<Set<string>>(new Set());
  const [hitSelections, setHitSelections] = useState<HitSelections>({});
  const [importStatuses, setImportStatuses] = useState<Record<string, ImportStatus>>({});
  const abortRef = useRef<AbortController | null>(null);

  const stopSearch = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase((prev) => (prev === "searching" ? "done" : prev));
  }, []);

  const clearSearch = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setHits([]);
    setStatus("");
    setErrorMessage("");
    setPhase("idle");
    setCandidateName("");
    setCandidateLog([]);
    // Wipe form + selection state too — Clear is the only path that should
    // reset these. Navigation never resets, since the state lives in the
    // app-root provider.
    setGuidance("");
    setSelectedLocations([]);
    setMinSalary("");
    setSelectedRefIds(new Set());
    setExpandedHit(null);
    setSelectedSlugs(new Set());
    setHitSelections({});
    setImportStatuses({});
  }, []);

  const startSearch = useCallback(
    (sources: string[], guidance: string, options?: HotSearchOptions) => {
      abortRef.current?.abort();

      const controller = new AbortController();
      abortRef.current = controller;

      setHits([]);
      setStatus("Starting search...");
      setErrorMessage("");
      setPhase("searching");
      setCandidateName("");
      setCandidateLog([]);

      const apiUrl = getApiUrl();
      // Match the production WIP request shape exactly: always include all
      // optional fields with their default empty/null values. Backend
      // Pydantic handles absent fields fine, but keeping the wire format
      // identical removes one variable from result-divergence debugging.
      const body = {
        sources,
        guidance,
        locations: options?.locations || [],
        min_salary: options?.minSalary ?? null,
        reference_job_ids: options?.referenceJobIds || [],
      };

      (async () => {
        try {
          const response = await fetch(`${apiUrl}/api/companies/hot-search`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: controller.signal,
          });

          if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `Error: ${response.status}`);
          }

          const reader = response.body?.getReader();
          if (!reader) throw new Error("No response stream");

          const decoder = new TextDecoder();
          let buffer = "";
          let eventType = "";

          const processLines = (lines: string[]) => {
            for (const line of lines) {
              if (line.startsWith("event: ")) {
                eventType = line.slice(7).trim();
              } else if (line.startsWith("data: ")) {
                const data = line.slice(6);
                try {
                  const parsed = JSON.parse(data);

                  if (eventType === "status") {
                    setStatus(parsed.message || "");
                  } else if (eventType === "candidate") {
                    const name = parsed.name || "";
                    setCandidateName(name);
                    if (name) {
                      setCandidateLog((prev) => [
                        ...prev,
                        { name, source: parsed.source, status: "checking" },
                      ]);
                    }
                  } else if (eventType === "hit") {
                    setHits((prev) => [...prev, parsed as HotSearchHit]);
                    // Mark the matching checking-entry as accepted with the
                    // relevant_jobs count. Production fires this paired
                    // update so the Activity log shows "accepted — N
                    // relevant jobs" without waiting for a separate event.
                    setCandidateLog((prev) =>
                      prev.map((e) =>
                        e.name === parsed.name && e.status === "checking"
                          ? {
                              ...e,
                              status: "accepted",
                              reason: `${parsed.relevant_jobs} relevant jobs`,
                            }
                          : e,
                      ),
                    );
                  } else if (eventType === "skip") {
                    // Backend skip event — mark the existing checking entry
                    // as rejected with the backend-supplied reason, or
                    // append a new rejected entry if we never saw a
                    // candidate event for this name first.
                    setCandidateLog((prev) => {
                      const idx = prev.findIndex(
                        (e) => e.name === parsed.name && e.status === "checking",
                      );
                      if (idx >= 0) {
                        const next = [...prev];
                        next[idx] = {
                          ...next[idx],
                          status: "rejected",
                          reason: parsed.reason,
                        };
                        return next;
                      }
                      return [
                        ...prev,
                        {
                          name: parsed.name,
                          source: parsed.source,
                          status: "rejected",
                          reason: parsed.reason,
                        },
                      ];
                    });
                  } else if (eventType === "error") {
                    setErrorMessage(parsed.message || "Error");
                    setPhase("error");
                  } else if (eventType === "done") {
                    setStatus(
                      `Done — found ${parsed.total_hits} companies (checked ${parsed.total_candidates_checked})`,
                    );
                    setPhase((prev) => (prev === "error" ? "error" : "done"));
                  }
                } catch {
                  // skip unparseable
                }
                eventType = "";
              }
            }
          };

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";
            processLines(lines);
          }

          if (buffer.trim()) {
            processLines(buffer.split("\n"));
          }
        } catch (err) {
          if ((err as Error).name === "AbortError") {
            setStatus("Search stopped");
            setPhase("done");
          } else {
            setErrorMessage((err as Error).message || "Search failed");
            setPhase("error");
          }
        }
      })();
    },
    []
  );

  return {
    hits,
    status,
    errorMessage,
    phase,
    isSearching: phase === "searching",
    candidateName,
    candidateLog,
    guidance,
    setGuidance,
    selectedLocations,
    setSelectedLocations,
    minSalary,
    setMinSalary,
    selectedRefIds,
    setSelectedRefIds,
    expandedHit,
    setExpandedHit,
    selectedSlugs,
    setSelectedSlugs,
    hitSelections,
    setHitSelections,
    importStatuses,
    setImportStatuses,
    startSearch,
    stopSearch,
    clearSearch,
  };
}
