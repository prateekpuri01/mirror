"use client";

import { useState, useEffect } from "react";
import { Loader2, X } from "lucide-react";
import { useRefreshCompany, useImportCompany } from "@/hooks/use-companies";
import { useJobProcessing } from "@/hooks/use-job-processing";
import { CompanyListItem, RefreshResponse } from "@/lib/types";
import { ReviewStep } from "@/components/add-company-flow";

interface RefreshCompanyFlowProps {
  company: CompanyListItem;
  onClose: () => void;
}

export function RefreshCompanyFlow({ company, onClose }: RefreshCompanyFlowProps) {
  const [step, setStep] = useState<"loading" | "review" | "importing">("loading");
  const [refreshData, setRefreshData] = useState<RefreshResponse | null>(null);
  const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set());
  const [threshold, setThreshold] = useState(50);

  const refreshMutation = useRefreshCompany();
  const importMutation = useImportCompany();
  const { startProcessing } = useJobProcessing();

  // Auto-trigger refresh on mount
  useEffect(() => {
    refreshMutation.mutate(company.id, {
      onSuccess: (data) => {
        setRefreshData(data);
        // Auto-select jobs above threshold
        const above = new Set(
          data.jobs.filter((j) => j.relevance >= threshold).map((j) => j.url)
        );
        setSelectedUrls(above);
        setStep("review");
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company.id]);

  function handleThresholdChange(newThreshold: number) {
    setThreshold(newThreshold);
    if (refreshData) {
      const above = new Set(
        refreshData.jobs
          .filter((j) => j.relevance >= newThreshold)
          .map((j) => j.url)
      );
      setSelectedUrls(above);
    }
  }

  function toggleJob(jobUrl: string) {
    setSelectedUrls((prev) => {
      const next = new Set(prev);
      if (next.has(jobUrl)) next.delete(jobUrl);
      else next.add(jobUrl);
      return next;
    });
  }

  function toggleSelectAll() {
    if (!refreshData) return;
    if (selectedUrls.size === refreshData.jobs.length) {
      setSelectedUrls(new Set());
    } else {
      setSelectedUrls(new Set(refreshData.jobs.map((j) => j.url)));
    }
  }

  function handleImport() {
    if (!refreshData || !refreshData.ats || !refreshData.slug) return;
    setStep("importing");
    importMutation.mutate(
      {
        name: company.name,
        website: company.website,
        ats: refreshData.ats,
        slug: refreshData.slug,
        selected_urls: selectedUrls.size > 0 ? Array.from(selectedUrls) : null,
        monitoring_active: company.monitoring_active,
      },
      {
        onSuccess: (response) => {
          if (response.job_ids?.length > 0) {
            startProcessing(response.job_ids);
          }
          onClose();
        },
        onError: () => setStep("review"),
      }
    );
  }

  return (
    <div className="rounded-lg border bg-background p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Refresh Jobs — {company.name}</h2>
        <button
          onClick={onClose}
          className="p-1 rounded-md hover:bg-muted/50 text-muted-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Loading */}
      {step === "loading" && (
        <div className="flex items-center justify-center py-8 gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Scanning for new jobs...</span>
        </div>
      )}

      {refreshMutation.isError && step === "loading" && (
        <p className="text-sm text-red-600 mt-2">
          Error: {refreshMutation.error.message}
        </p>
      )}

      {/* Review */}
      {step === "review" && refreshData && (
        <div className="space-y-3">
          {/* Expired jobs notice */}
          {refreshData.expired_count > 0 && (
            <p className="text-sm text-amber-700 bg-amber-50 rounded-md px-3 py-2">
              {refreshData.expired_count}{" "}
              {refreshData.expired_count === 1
                ? "previously imported job is"
                : "previously imported jobs are"}{" "}
              no longer listed and {refreshData.expired_count === 1 ? "has" : "have"} been marked expired.
            </p>
          )}

          {refreshData.jobs.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <p>No new jobs found. All {refreshData.total_jobs === 0 ? "" : "current "}jobs are already imported.</p>
              <button
                onClick={onClose}
                className="mt-4 h-9 px-4 rounded-md border text-sm hover:bg-muted/50 transition-colors"
              >
                Close
              </button>
            </div>
          ) : (
            <ReviewStep
              discovery={refreshData}
              selectedUrls={selectedUrls}
              threshold={threshold}
              onThresholdChange={handleThresholdChange}
              onToggleJob={toggleJob}
              onToggleSelectAll={toggleSelectAll}
              onImport={handleImport}
              onCancel={onClose}
            />
          )}
        </div>
      )}

      {/* Importing */}
      {step === "importing" && (
        <div className="flex items-center justify-center py-8 gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Importing {selectedUrls.size} jobs...</span>
        </div>
      )}

      {importMutation.isError && step === "review" && (
        <p className="text-sm text-red-600 mt-2">
          Import failed: {importMutation.error.message}
        </p>
      )}
    </div>
  );
}
