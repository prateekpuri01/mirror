"use client";

import { createContext, useContext, useCallback, useRef, useState } from "react";
import { discoverCompany } from "@/lib/api";
import { DiscoverResponse } from "@/lib/types";

interface DiscoverFlowState {
  /** Whether a discover operation is in flight */
  isDiscovering: boolean;
  /** URL being discovered */
  discoverUrl: string | null;
  /** Result when done */
  result: DiscoverResponse | null;
  /** Error message */
  error: string | null;
  /** Start a discover operation (persists across navigation) */
  startDiscover: (url: string) => void;
  /** Clear everything (after import or cancel) */
  clearDiscover: () => void;
}

const DiscoverFlowContext = createContext<DiscoverFlowState>({
  isDiscovering: false,
  discoverUrl: null,
  result: null,
  error: null,
  startDiscover: () => {},
  clearDiscover: () => {},
});

export function useDiscoverFlow() {
  return useContext(DiscoverFlowContext);
}

export { DiscoverFlowContext };

export function useDiscoverFlowProvider() {
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [discoverUrl, setDiscoverUrl] = useState<string | null>(null);
  const [result, setResult] = useState<DiscoverResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const startDiscover = useCallback((url: string) => {
    // Cancel any previous in-flight request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsDiscovering(true);
    setDiscoverUrl(url);
    setResult(null);
    setError(null);

    discoverCompany(url)
      .then((data) => {
        if (controller.signal.aborted) return;
        if (data.error) {
          setError(data.error);
          setResult(null);
        } else {
          setResult(data);
        }
        setIsDiscovering(false);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Discovery failed");
        setIsDiscovering(false);
      });
  }, []);

  const clearDiscover = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsDiscovering(false);
    setDiscoverUrl(null);
    setResult(null);
    setError(null);
  }, []);

  return {
    isDiscovering,
    discoverUrl,
    result,
    error,
    startDiscover,
    clearDiscover,
  };
}
