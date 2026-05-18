"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { getScrapeProgress, ScrapeProgress } from "@/lib/api";

const POLL_INTERVAL = 1500;

const CHEEKY_MESSAGES = [
  "Rummaging through their careers page...",
  "Poking around for openings...",
  "Snooping on their careers page...",
  "Shaking the job tree...",
  "Mining for opportunities...",
  "Interrogating their ATS...",
];

function pickMessage(seed: number): string {
  return CHEEKY_MESSAGES[seed % CHEEKY_MESSAGES.length];
}

function formatProgress(p: ScrapeProgress): string {
  const name = p.company_name || "their careers page";

  if (p.phase === "resolving") {
    return `Figuring out where ${name} hides their jobs...`;
  }

  if (p.phase === "listing") {
    return `Knocking on ${name}'s door...`;
  }

  if (p.phase === "fetching" && p.total && p.total > 0) {
    const pct = Math.round((p.found / p.total) * 100);
    if (p.found === 0) {
      return `Found ${p.total} postings at ${name}. ${pickMessage(p.total)}`;
    }
    if (pct >= 90) {
      return `Almost there... ${p.found} of ${p.total} jobs from ${name}`;
    }
    return `${p.found} of ${p.total} jobs fetched from ${name}`;
  }

  if (p.phase === "fetching" && p.found > 0) {
    return `${p.found} jobs found so far. Still looking...`;
  }

  return null as unknown as string; // no update yet
}

export function useScrapeProgress(enabled: boolean) {
  const [message, setMessage] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    setMessage(null);
  }, []);

  useEffect(() => {
    if (!enabled) {
      stop();
      return;
    }

    // Show an immediate message while we wait for the first poll
    setMessage("Scanning for jobs...");

    const poll = async () => {
      try {
        const p = await getScrapeProgress();
        if (p.active) {
          const msg = formatProgress(p);
          if (msg) setMessage(msg);
        }
        // If not active yet, keep showing the default message
      } catch {
        // Ignore polling errors
      }
    };

    // First poll after 800ms (give backend time to start), then every 1.5s
    const initialTimeout = setTimeout(poll, 800);
    intervalRef.current = setInterval(poll, POLL_INTERVAL);

    return () => {
      clearTimeout(initialTimeout);
      stop();
    };
  }, [enabled, stop]);

  return message;
}
