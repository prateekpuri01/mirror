"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import { HotSearchHit } from "@/lib/types";
import { getApiUrl } from "@/lib/api";

export type HotSearchPhase = "idle" | "searching" | "done" | "error";

interface HotSearchState {
  hits: HotSearchHit[];
  status: string;
  errorMessage: string;
  phase: HotSearchPhase;
  isSearching: boolean;
  candidateName: string;
  startSearch: (sources: string[], guidance: string) => void;
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
  }, []);

  const startSearch = useCallback(
    (sources: string[], guidance: string) => {
      abortRef.current?.abort();

      const controller = new AbortController();
      abortRef.current = controller;

      setHits([]);
      setStatus("Starting search...");
      setErrorMessage("");
      setPhase("searching");
      setCandidateName("");

      const apiUrl = getApiUrl();

      (async () => {
        try {
          const response = await fetch(`${apiUrl}/api/companies/hot-search`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sources, guidance }),
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
                    setCandidateName(parsed.name || "");
                  } else if (eventType === "hit") {
                    setHits((prev) => [...prev, parsed as HotSearchHit]);
                  } else if (eventType === "error") {
                    setErrorMessage(parsed.message || "Error");
                    setPhase("error");
                  } else if (eventType === "done") {
                    setStatus(
                      `Done — found ${parsed.total_hits} companies (checked ${parsed.total_candidates_checked})`
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
    startSearch,
    stopSearch,
    clearSearch,
  };
}
