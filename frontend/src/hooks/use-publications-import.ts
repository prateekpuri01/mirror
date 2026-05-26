"use client";

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";
import { getApiUrl } from "@/lib/api";
import type { ProfileData, ProfilePublication } from "@/lib/types";

export type PublicationsImportPhase =
  | "idle"
  | "fetching"
  | "enriching"
  | "done"
  | "error";

interface PublicationsImportState {
  phase: PublicationsImportPhase;
  status: string;
  errorMessage: string;
  publications: ProfilePublication[];
  total: number;
  // Records titles we attempted but failed to enrich, so the UI can show
  // a "N skipped" footnote without losing the streamed successes.
  skipped: { title: string; reason: string }[];
  // Captured at start so the consumer (onboarding review screen) can tell
  // which scholar URL this batch belongs to and avoid double-starting.
  scholarUrl: string | null;
  start: (profile: ProfileData, scholarUrl?: string) => void;
  stop: () => void;
  reset: () => void;
}

const PublicationsImportContext = createContext<PublicationsImportState>({
  phase: "idle",
  status: "",
  errorMessage: "",
  publications: [],
  total: 0,
  skipped: [],
  scholarUrl: null,
  start: () => {},
  stop: () => {},
  reset: () => {},
});

export function usePublicationsImport() {
  return useContext(PublicationsImportContext);
}

export { PublicationsImportContext };

export function usePublicationsImportProvider(): PublicationsImportState {
  const [phase, setPhase] = useState<PublicationsImportPhase>("idle");
  const [status, setStatus] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [publications, setPublications] = useState<ProfilePublication[]>([]);
  const [total, setTotal] = useState(0);
  const [skipped, setSkipped] = useState<{ title: string; reason: string }[]>([]);
  const [scholarUrl, setScholarUrl] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase((p) => (p === "fetching" || p === "enriching" ? "done" : p));
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase("idle");
    setStatus("");
    setErrorMessage("");
    setPublications([]);
    setTotal(0);
    setSkipped([]);
    setScholarUrl(null);
  }, []);

  const start = useCallback(
    (profile: ProfileData, urlOverride?: string) => {
      // Bail if a stream is already running — caller should reset first.
      if (phase === "fetching" || phase === "enriching") return;

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const targetUrl =
        urlOverride
        || (profile.personal?.google_scholar as string | undefined)
        || null;

      setPhase("fetching");
      setStatus("Starting Scholar import...");
      setErrorMessage("");
      setPublications([]);
      setTotal(0);
      setSkipped([]);
      setScholarUrl(targetUrl);

      (async () => {
        try {
          const apiUrl = getApiUrl();
          const response = await fetch(
            `${apiUrl}/api/onboarding/import-publications-stream`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                profile,
                scholar_url: targetUrl,
              }),
              signal: controller.signal,
            },
          );

          if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw new Error(body.detail || `HTTP ${response.status}`);
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
                  } else if (eventType === "total") {
                    setTotal(parsed.total || 0);
                    setPhase("enriching");
                    setStatus(
                      `Enriching ${parsed.total} publications...`,
                    );
                  } else if (eventType === "publication") {
                    setPublications((prev) => [
                      ...prev,
                      parsed.publication as ProfilePublication,
                    ]);
                  } else if (eventType === "skip") {
                    setSkipped((prev) => [
                      ...prev,
                      {
                        title: parsed.title || "unknown",
                        reason: parsed.reason || "Skipped",
                      },
                    ]);
                  } else if (eventType === "error") {
                    setErrorMessage(parsed.message || "Error");
                    setPhase("error");
                  } else if (eventType === "done") {
                    const imp = parsed.imported || 0;
                    const tot = parsed.total || 0;
                    setStatus(
                      `Done — imported ${imp} of ${tot} publications.`,
                    );
                    setPhase((p) => (p === "error" ? "error" : "done"));
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
            // User cancelled — leave whatever's already streamed
            setStatus("Stopped");
            setPhase("done");
            return;
          }
          setErrorMessage((err as Error).message || "Import failed");
          setPhase("error");
        }
      })();
    },
    [phase],
  );

  return {
    phase,
    status,
    errorMessage,
    publications,
    total,
    skipped,
    scholarUrl,
    start,
    stop,
    reset,
  };
}
