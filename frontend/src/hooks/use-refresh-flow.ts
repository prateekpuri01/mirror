"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import { refreshCompany } from "@/lib/api";
import { CompanyListItem, RefreshResponse } from "@/lib/types";

type RefreshStep = "idle" | "loading" | "review" | "importing" | "done";

interface RefreshFlowState {
  step: RefreshStep;
  company: CompanyListItem | null;
  result: RefreshResponse | null;
  error: string | null;
  progressMessage: string | null;
  startRefresh: (company: CompanyListItem) => void;
  setResult: (result: RefreshResponse) => void;
  setStep: (step: RefreshStep) => void;
  clearRefresh: () => void;
}

const RefreshFlowContext = createContext<RefreshFlowState>({
  step: "idle",
  company: null,
  result: null,
  error: null,
  progressMessage: null,
  startRefresh: () => {},
  setResult: () => {},
  setStep: () => {},
  clearRefresh: () => {},
});

export function useRefreshFlow() {
  return useContext(RefreshFlowContext);
}

export { RefreshFlowContext };

export function useRefreshFlowProvider(): RefreshFlowState {
  const [step, setStep] = useState<RefreshStep>("idle");
  const [company, setCompany] = useState<CompanyListItem | null>(null);
  const [result, setResult] = useState<RefreshResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);
  const abortRef = useRef(false);

  const startRefresh = useCallback((comp: CompanyListItem) => {
    abortRef.current = false;
    setCompany(comp);
    setResult(null);
    setError(null);
    setStep("loading");
    setProgressMessage(null);

    refreshCompany(comp.id)
      .then((data) => {
        if (abortRef.current) return;
        setResult(data);
        setStep("review");
      })
      .catch((err) => {
        if (abortRef.current) return;
        setError(err instanceof Error ? err.message : "Refresh failed");
        setStep("loading"); // stay on loading so error is visible
      });
  }, []);

  const clearRefresh = useCallback(() => {
    abortRef.current = true;
    setStep("idle");
    setCompany(null);
    setResult(null);
    setError(null);
    setProgressMessage(null);
  }, []);

  return {
    step,
    company,
    result,
    error,
    progressMessage,
    startRefresh,
    setResult,
    setStep,
    clearRefresh,
  };
}
