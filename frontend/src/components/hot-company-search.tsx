"use client";

import { useState, useCallback } from "react";
import {
  Loader2,
  X,
  ChevronDown,
  ChevronRight,
  Flame,
  Square,
  Check,
} from "lucide-react";
import { useHotSearch } from "@/hooks/use-hot-search";
import { useImportCompany } from "@/hooks/use-companies";
import { useJobProcessing } from "@/hooks/use-job-processing";
import { ReviewStep } from "@/components/add-company-flow";
import { HotSearchHit, DiscoverResponse, JobPreview } from "@/lib/types";
import { Badge } from "@/components/ui/badge";

interface HotCompanySearchProps {
  onClose: () => void;
}

const SOURCE_OPTIONS = [
  { id: "tavily", label: "Web Search", color: "bg-violet-100 text-violet-700 border-violet-200" },
  { id: "greenhouse", label: "Greenhouse", color: "bg-emerald-100 text-emerald-700 border-emerald-200" },
  { id: "lever", label: "Lever", color: "bg-blue-100 text-blue-700 border-blue-200" },
  { id: "ashby", label: "Ashby", color: "bg-amber-100 text-amber-700 border-amber-200" },
] as const;

const ATS_COLORS: Record<string, string> = {
  greenhouse: "bg-emerald-100 text-emerald-700 border-emerald-200",
  lever: "bg-blue-100 text-blue-700 border-blue-200",
  ashby: "bg-amber-100 text-amber-700 border-amber-200",
};

const SOURCE_COLORS: Record<string, string> = {
  tavily: "bg-violet-100 text-violet-700 border-violet-200",
  greenhouse: "bg-emerald-100 text-emerald-700 border-emerald-200",
  lever: "bg-blue-100 text-blue-700 border-blue-200",
  ashby: "bg-amber-100 text-amber-700 border-amber-200",
};

// Per-hit job selections: slug → Set<url>
type HitSelections = Record<string, Set<string>>;

// Import status per slug
type ImportStatus = "idle" | "importing" | "imported" | "error";

export function HotCompanySearch({ onClose }: HotCompanySearchProps) {
  const [sources, setSources] = useState<string[]>(["tavily", "greenhouse", "lever", "ashby"]);
  const [guidance, setGuidance] = useState("");
  const [expandedHit, setExpandedHit] = useState<string | null>(null);

  // Multi-select state
  const [selectedSlugs, setSelectedSlugs] = useState<Set<string>>(new Set());
  const [hitSelections, setHitSelections] = useState<HitSelections>({});
  const [importStatuses, setImportStatuses] = useState<Record<string, ImportStatus>>({});

  const {
    hits, status, errorMessage, phase, isSearching, candidateName,
    startSearch, stopSearch, clearSearch,
  } = useHotSearch();

  const importMutation = useImportCompany();
  const { startProcessing } = useJobProcessing();

  // Initialize job selections for a hit (auto-select jobs >= 75)
  function getJobSelections(hit: HotSearchHit): Set<string> {
    if (hitSelections[hit.slug]) return hitSelections[hit.slug];
    const initial = new Set(
      hit.top_jobs.filter((j) => j.relevance >= 75).map((j) => j.url)
    );
    setHitSelections((prev) => ({ ...prev, [hit.slug]: initial }));
    return initial;
  }

  function updateJobSelections(slug: string, urls: Set<string>) {
    setHitSelections((prev) => ({ ...prev, [slug]: urls }));
  }

  // Company-level selection
  function toggleCompanySelect(slug: string) {
    setSelectedSlugs((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function toggleSelectAllCompanies() {
    const importable = hits.filter((h) => (importStatuses[h.slug] || "idle") === "idle");
    if (selectedSlugs.size === importable.length) {
      setSelectedSlugs(new Set());
    } else {
      setSelectedSlugs(new Set(importable.map((h) => h.slug)));
    }
  }

  // Count total selected jobs across selected companies
  function countSelectedJobs(): number {
    let count = 0;
    for (const slug of selectedSlugs) {
      const sel = hitSelections[slug];
      count += sel ? sel.size : 0;
    }
    return count;
  }

  // Import all selected companies sequentially
  const importAllSelected = useCallback(async () => {
    const toImport = hits.filter(
      (h) => selectedSlugs.has(h.slug) && (importStatuses[h.slug] || "idle") === "idle"
    );

    for (const hit of toImport) {
      const urls = hitSelections[hit.slug];
      if (!urls || urls.size === 0) continue;

      setImportStatuses((prev) => ({ ...prev, [hit.slug]: "importing" }));

      try {
        const response = await importMutation.mutateAsync({
          name: hit.name,
          website: hit.website,
          ats: hit.ats,
          slug: hit.slug,
          selected_urls: Array.from(urls),
          monitoring_active: true,
        });
        if (response.job_ids?.length > 0) {
          startProcessing(response.job_ids);
        }
        setImportStatuses((prev) => ({ ...prev, [hit.slug]: "imported" }));
      } catch {
        setImportStatuses((prev) => ({ ...prev, [hit.slug]: "error" }));
      }
    }
    setSelectedSlugs(new Set());
  }, [hits, selectedSlugs, hitSelections, importStatuses, importMutation, startProcessing]);

  // Single company import (from expanded review)
  function importSingle(hit: HotSearchHit) {
    const urls = hitSelections[hit.slug] || new Set<string>();
    if (urls.size === 0) return;
    setImportStatuses((prev) => ({ ...prev, [hit.slug]: "importing" }));
    importMutation.mutate(
      {
        name: hit.name,
        website: hit.website,
        ats: hit.ats,
        slug: hit.slug,
        selected_urls: Array.from(urls),
        monitoring_active: true,
      },
      {
        onSuccess: (response) => {
          if (response.job_ids?.length > 0) startProcessing(response.job_ids);
          setImportStatuses((prev) => ({ ...prev, [hit.slug]: "imported" }));
        },
        onError: () => {
          setImportStatuses((prev) => ({ ...prev, [hit.slug]: "error" }));
        },
      }
    );
  }

  function toggleSource(id: string) {
    setSources((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
  }

  function handleSearch() {
    if (sources.length === 0) return;
    setImportStatuses({});
    setSelectedSlugs(new Set());
    setHitSelections({});
    startSearch(sources, guidance);
  }

  const isAnyImporting = Object.values(importStatuses).includes("importing");
  const importableHits = hits.filter((h) => (importStatuses[h.slug] || "idle") === "idle");

  return (
    <div className="rounded-lg border bg-background p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Flame className="h-5 w-5 text-orange-500" />
          <h2 className="text-lg font-semibold">Find Hot Companies</h2>
        </div>
        <button
          onClick={() => {
            if (!isSearching) clearSearch();
            onClose();
          }}
          className="p-1 rounded-md hover:bg-muted/50 text-muted-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Phase 1: Configure */}
      {phase === "idle" && (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            AI-powered search finds companies with relevant jobs across ATS boards.
            Select sources and optionally provide guidance to steer the search.
          </p>

          <div className="space-y-2">
            <label className="text-sm font-medium">Sources</label>
            <div className="flex flex-wrap gap-2">
              {SOURCE_OPTIONS.map((opt) => {
                const active = sources.includes(opt.id);
                return (
                  <button
                    key={opt.id}
                    onClick={() => toggleSource(opt.id)}
                    className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-all ${
                      active
                        ? opt.color
                        : "bg-muted/30 text-muted-foreground border-transparent opacity-50"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">Guidance (optional)</label>
            <textarea
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
              placeholder="e.g. AI startups in healthcare, Series A-C, remote-friendly"
              rows={2}
              className="w-full rounded-md border px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-none"
            />
          </div>

          <button
            onClick={handleSearch}
            disabled={sources.length === 0}
            className="h-9 px-4 rounded-md bg-foreground text-background text-sm font-medium hover:bg-foreground/90 disabled:opacity-50 transition-colors flex items-center gap-2"
          >
            <Flame className="h-3.5 w-3.5" />
            Search
          </button>
        </div>
      )}

      {/* Phase 2: Results */}
      {(phase === "searching" || phase === "done" || phase === "error") && (
        <div className="space-y-4">
          {/* Status bar */}
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm min-w-0">
              {isSearching && <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-500 flex-shrink-0" />}
              {phase === "done" && <Check className="h-3.5 w-3.5 text-emerald-500 flex-shrink-0" />}
              <span className="text-muted-foreground truncate">{status}</span>
              {isSearching && candidateName && (
                <span className="text-xs text-muted-foreground/60 truncate">
                  — checking {candidateName}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              {hits.length > 0 && (
                <span className="text-sm font-medium">
                  {hits.length} {hits.length === 1 ? "hit" : "hits"}
                </span>
              )}
              {isSearching && (
                <button
                  onClick={stopSearch}
                  className="h-7 px-3 rounded-md border text-xs font-medium hover:bg-muted/50 transition-colors flex items-center gap-1.5"
                >
                  <Square className="h-3 w-3" />
                  Stop
                </button>
              )}
            </div>
          </div>

          {errorMessage && (
            <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {errorMessage}
            </div>
          )}

          {/* Multi-select toolbar */}
          {hits.length > 0 && !isSearching && (
            <div className="flex items-center gap-3 text-sm">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={selectedSlugs.size > 0 && selectedSlugs.size === importableHits.length}
                  onChange={toggleSelectAllCompanies}
                  className="rounded"
                  disabled={importableHits.length === 0}
                />
                <span className="text-muted-foreground">
                  {selectedSlugs.size > 0
                    ? `${selectedSlugs.size} companies selected (${countSelectedJobs()} jobs)`
                    : "Select all"}
                </span>
              </label>
              {selectedSlugs.size > 0 && (
                <button
                  onClick={importAllSelected}
                  disabled={isAnyImporting || countSelectedJobs() === 0}
                  className="h-8 px-4 rounded-md bg-foreground text-background text-xs font-medium hover:bg-foreground/90 disabled:opacity-50 transition-colors flex items-center gap-2"
                >
                  {isAnyImporting && <Loader2 className="h-3 w-3 animate-spin" />}
                  Import {selectedSlugs.size} {selectedSlugs.size === 1 ? "Company" : "Companies"}
                </button>
              )}
            </div>
          )}

          {/* Hit list */}
          {hits.length > 0 && (
            <div className="space-y-2 max-h-[600px] overflow-auto">
              {hits.map((hit) => {
                const isExpanded = expandedHit === hit.slug;
                const hitStatus = importStatuses[hit.slug] || "idle";
                const isSelected = selectedSlugs.has(hit.slug);
                const jobSel = getJobSelections(hit);

                return (
                  <div
                    key={hit.slug}
                    className={`rounded-md border transition-all duration-300 ${
                      hitStatus === "importing"
                        ? "opacity-50 bg-muted/20"
                        : hitStatus === "imported"
                          ? "bg-emerald-50/50 border-emerald-200"
                          : hitStatus === "error"
                            ? "bg-red-50/50 border-red-200"
                            : ""
                    }`}
                  >
                    {/* Hit card header */}
                    <div className="flex items-center gap-3 px-4 py-3">
                      {/* Checkbox (only when not searching) */}
                      {!isSearching && hitStatus === "idle" && (
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleCompanySelect(hit.slug)}
                          onClick={(e) => e.stopPropagation()}
                          className="rounded flex-shrink-0"
                        />
                      )}

                      {/* Status indicator for importing/imported */}
                      {hitStatus === "importing" && (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground flex-shrink-0" />
                      )}
                      {hitStatus === "imported" && (
                        <Check className="h-4 w-4 text-emerald-600 flex-shrink-0" />
                      )}

                      <button
                        onClick={() => setExpandedHit(isExpanded ? null : hit.slug)}
                        className="flex items-center gap-3 flex-1 min-w-0 text-left"
                      >
                        {isExpanded ? (
                          <ChevronDown className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                        ) : (
                          <ChevronRight className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                        )}

                        <div className="flex flex-col min-w-0 flex-1 gap-0.5">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium truncate">{hit.name}</span>
                            <Badge
                              variant="outline"
                              className={`text-xs capitalize flex-shrink-0 ${ATS_COLORS[hit.ats] || ""}`}
                            >
                              {hit.ats}
                            </Badge>
                            <Badge
                              variant="outline"
                              className={`text-xs flex-shrink-0 ${SOURCE_COLORS[hit.source] || "bg-muted text-muted-foreground"}`}
                            >
                              {hit.source}
                            </Badge>
                            {hitStatus === "importing" && (
                              <span className="text-xs text-muted-foreground">Importing...</span>
                            )}
                            {hitStatus === "imported" && (
                              <Badge
                                variant="outline"
                                className="bg-emerald-50 text-emerald-700 border-emerald-200 text-xs"
                              >
                                Imported
                              </Badge>
                            )}
                          </div>
                          {hit.description && (
                            <p className="text-xs text-muted-foreground truncate">
                              {hit.description}
                            </p>
                          )}
                        </div>

                        <span className="text-sm text-muted-foreground flex-shrink-0 whitespace-nowrap">
                          {hit.total_jobs} jobs · {hit.relevant_jobs} relevant
                          {jobSel.size > 0 && hitStatus === "idle" && (
                            <span className="text-foreground font-medium"> · {jobSel.size} selected</span>
                          )}
                        </span>
                      </button>
                    </div>

                    {/* Expanded: match reason + job review */}
                    {isExpanded && (
                      <div className="border-t px-4 py-4 space-y-3">
                        {hit.match_reason && (
                          <p className="text-sm text-muted-foreground bg-muted/30 rounded-md px-3 py-2">
                            <span className="font-medium text-foreground">Why this company: </span>
                            {hit.match_reason}
                          </p>
                        )}
                        <HitReview
                          hit={hit}
                          selectedUrls={jobSel}
                          onSelectionChange={(urls) => updateJobSelections(hit.slug, urls)}
                          onImport={() => importSingle(hit)}
                          importStatus={hitStatus}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Empty states */}
          {hits.length === 0 && isSearching && (
            <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
              Searching for companies... results will appear here
            </div>
          )}

          {hits.length === 0 && phase === "done" && !errorMessage && (
            <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
              No companies found matching your criteria. Try different guidance or sources.
            </div>
          )}

          {/* Footer */}
          {phase !== "searching" && (
            <div className="flex items-center justify-end gap-3 pt-2 border-t">
              <button
                onClick={() => {
                  clearSearch();
                  onClose();
                }}
                className="h-9 px-4 rounded-md border text-sm hover:bg-muted/50 transition-colors"
              >
                Close
              </button>
              {phase === "error" && (
                <button
                  onClick={handleSearch}
                  disabled={sources.length === 0}
                  className="h-9 px-4 rounded-md bg-foreground text-background text-sm font-medium hover:bg-foreground/90 disabled:opacity-50 transition-colors"
                >
                  Retry
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-hit job review
// ---------------------------------------------------------------------------

interface HitReviewProps {
  hit: HotSearchHit;
  selectedUrls: Set<string>;
  onSelectionChange: (urls: Set<string>) => void;
  onImport: () => void;
  importStatus: ImportStatus;
}

function HitReview({ hit, selectedUrls, onSelectionChange, onImport, importStatus }: HitReviewProps) {
  const [threshold, setThreshold] = useState(75);

  const discovery: DiscoverResponse = {
    ats: hit.ats,
    slug: hit.slug,
    company_name: hit.name,
    total_jobs: hit.top_jobs.length,
    jobs: hit.top_jobs,
  };

  function handleThresholdChange(newThreshold: number) {
    setThreshold(newThreshold);
    onSelectionChange(
      new Set(
        hit.top_jobs
          .filter((j) => j.relevance >= newThreshold)
          .map((j) => j.url)
      )
    );
  }

  function toggleJob(jobUrl: string) {
    const next = new Set(selectedUrls);
    if (next.has(jobUrl)) next.delete(jobUrl);
    else next.add(jobUrl);
    onSelectionChange(next);
  }

  function toggleSelectAll() {
    if (selectedUrls.size === hit.top_jobs.length) {
      onSelectionChange(new Set());
    } else {
      onSelectionChange(new Set(hit.top_jobs.map((j) => j.url)));
    }
  }

  if (importStatus === "imported") {
    return (
      <div className="flex items-center justify-center py-4 text-sm text-emerald-600 gap-2">
        <Check className="h-4 w-4" />
        Company imported successfully
      </div>
    );
  }

  if (importStatus === "importing") {
    return (
      <div className="flex items-center justify-center py-4 gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Importing {selectedUrls.size} jobs...
      </div>
    );
  }

  return (
    <ReviewStep
      discovery={discovery}
      selectedUrls={selectedUrls}
      threshold={threshold}
      onThresholdChange={handleThresholdChange}
      onToggleJob={toggleJob}
      onToggleSelectAll={toggleSelectAll}
      onImport={onImport}
      onCancel={() => {}}
    />
  );
}
