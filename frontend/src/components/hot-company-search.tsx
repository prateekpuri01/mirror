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
import { useHotSearch, type ImportStatus } from "@/hooks/use-hot-search";
import { useJobs, useLocations } from "@/hooks/use-jobs";
import { useImportCompany } from "@/hooks/use-companies";
import { useJobProcessing } from "@/hooks/use-job-processing";
import { ReviewStep } from "@/components/add-company-flow";
import { HotSearchHit, DiscoverResponse } from "@/lib/types";
import { Badge } from "@/components/ui/badge";

interface HotCompanySearchProps {
  onClose: () => void;
}

// All sources are always on. The backend's discovery phase always runs the
// full set of query flavors (web + site:boards.greenhouse.io + site:jobs.lever.co
// + site:jobs.ashbyhq.com). The per-source toggle that used to live here only
// controlled query generation, not which ATS APIs we actually called — so
// exposing it created the illusion of control and invited confusion (esp. the
// "Eightfold" option, which was a no-op). Hardcoded here, and preserved as a
// constant so the existing request schema keeps working.
const DEFAULT_SOURCES = ["web", "greenhouse", "lever", "ashby"];

const ATS_COLORS: Record<string, string> = {
  greenhouse: "bg-emerald-100 text-emerald-700 border-emerald-200",
  lever: "bg-blue-100 text-blue-700 border-blue-200",
  ashby: "bg-amber-100 text-amber-700 border-amber-200",
  eightfold: "bg-teal-100 text-teal-700 border-teal-200",
};

const SOURCE_COLORS: Record<string, string> = {
  web: "bg-violet-100 text-violet-700 border-violet-200",
  // Legacy alias — older hits may still carry source="tavily"
  tavily: "bg-violet-100 text-violet-700 border-violet-200",
  greenhouse: "bg-emerald-100 text-emerald-700 border-emerald-200",
  lever: "bg-blue-100 text-blue-700 border-blue-200",
  ashby: "bg-amber-100 text-amber-700 border-amber-200",
  eightfold: "bg-teal-100 text-teal-700 border-teal-200",
};

export function HotCompanySearch({ onClose }: HotCompanySearchProps) {
  // sources is no longer user-controlled — always use the full set. Kept
  // as a local constant so the API call signature stays the same.
  const sources = DEFAULT_SOURCES;

  // Form, filter, and selection state — sourced from HotSearchContext so
  // every input survives tab navigation. Only transient input UI state
  // (typeahead query, dropdown open/closed, accordion toggles) stays local.
  const {
    guidance, setGuidance,
    selectedLocations, setSelectedLocations,
    minSalary, setMinSalary,
    selectedRefIds, setSelectedRefIds,
    expandedHit, setExpandedHit,
    selectedSlugs, setSelectedSlugs,
    hitSelections, setHitSelections,
    importStatuses, setImportStatuses,
  } = useHotSearch();

  const [locationQuery, setLocationQuery] = useState("");
  const [showLocationDropdown, setShowLocationDropdown] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [showReferences, setShowReferences] = useState(false);

  const { data: likedJobs } = useJobs({
    thumbs: 1,
    per_page: 20,
    sort_by: "updated_at",
    sort_dir: "desc",
  });

  const { data: allLocations } = useLocations();

  function toggleRef(id: string) {
    setSelectedRefIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const filteredLocations = (allLocations || []).filter(
    (loc) =>
      !selectedLocations.includes(loc.display_name) &&
      loc.display_name.toLowerCase().includes(locationQuery.toLowerCase())
  );

  function addLocation(name: string) {
    if (!selectedLocations.includes(name)) {
      setSelectedLocations((prev) => [...prev, name]);
    }
    setLocationQuery("");
    setShowLocationDropdown(false);
  }

  function removeLocation(name: string) {
    setSelectedLocations((prev) => prev.filter((l) => l !== name));
  }

  function handleLocationKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && locationQuery.trim()) {
      e.preventDefault();
      addLocation(locationQuery.trim());
    }
    if (e.key === "Escape") {
      setShowLocationDropdown(false);
    }
  }

  const {
    hits, candidateLog, status, errorMessage, phase, isSearching, candidateName,
    startSearch, stopSearch, clearSearch,
  } = useHotSearch();
  const [showLog, setShowLog] = useState(false);

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
    // Only importable (ATS + direct) hits can be selected. Lead hits have
    // no usable ATS, and tracked hits are already in the user's DB.
    const importable = hits.filter((h) => {
      const kind = h.kind || "ats";
      return (importStatuses[h.slug] || "idle") === "idle" && kind === "ats";
    });
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

  function handleSearch() {
    setImportStatuses({});
    setSelectedSlugs(new Set());
    setHitSelections({});
    startSearch(sources, guidance, {
      locations: selectedLocations.length > 0 ? selectedLocations : undefined,
      minSalary: minSalary ? parseInt(minSalary) * 1000 : undefined,
      referenceJobIds: selectedRefIds.size > 0 ? Array.from(selectedRefIds) : undefined,
    });
  }

  const isAnyImporting = Object.values(importStatuses).includes("importing");
  // Importable = idle status AND not a pre-imported (direct-drill) hit
  const importableHits = hits.filter(
    (h) => (importStatuses[h.slug] || "idle") === "idle" && h.ats !== "direct",
  );

  return (
    <div className="rounded-lg border bg-background p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Flame className="h-5 w-5 text-orange-500" />
          <h2 className="text-lg font-semibold">Find Hot Jobs</h2>
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
            AI-powered search finds companies with relevant jobs across the
            web and major ATS platforms (Greenhouse, Lever, Ashby). Add
            optional filters and guidance below to steer the search.
          </p>

          {/* Filters toggle */}
          <div className="space-y-2">
            <button
              onClick={() => setShowFilters(!showFilters)}
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {showFilters ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              <span>Filters</span>
              {(selectedLocations.length > 0 || minSalary) && (
                <Badge variant="outline" className="text-xs ml-1">active</Badge>
              )}
            </button>
            {showFilters && (
              <div className="grid grid-cols-2 gap-3 pl-5">
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">Location</label>
                  {/* Selected location tags */}
                  {selectedLocations.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-1">
                      {selectedLocations.map((loc) => (
                        <span
                          key={loc}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-muted text-xs font-medium"
                        >
                          {loc}
                          <button
                            onClick={() => removeLocation(loc)}
                            className="hover:text-foreground text-muted-foreground"
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                  {/* Combobox input */}
                  <div className="relative">
                    <input
                      value={locationQuery}
                      onChange={(e) => {
                        setLocationQuery(e.target.value);
                        setShowLocationDropdown(true);
                      }}
                      onFocus={() => setShowLocationDropdown(true)}
                      onBlur={() => {
                        // Delay to allow click on dropdown item
                        setTimeout(() => setShowLocationDropdown(false), 200);
                      }}
                      onKeyDown={handleLocationKeyDown}
                      placeholder="e.g. San Francisco, Remote"
                      className="w-full rounded-md border px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    {showLocationDropdown && locationQuery && filteredLocations.length > 0 && (
                      <div className="absolute z-10 mt-1 w-full max-h-[160px] overflow-y-auto rounded-md border bg-popover shadow-md">
                        {filteredLocations.slice(0, 8).map((loc) => (
                          <button
                            key={loc.id}
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => addLocation(loc.display_name)}
                            className="w-full px-3 py-1.5 text-left text-sm hover:bg-muted/50 truncate"
                          >
                            {loc.display_name}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">Min salary</label>
                  <div className="flex items-center gap-1">
                    <span className="text-sm text-muted-foreground">$</span>
                    <input
                      value={minSalary}
                      onChange={(e) => setMinSalary(e.target.value.replace(/\D/g, ""))}
                      placeholder="150"
                      className="w-20 rounded-md border px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    <span className="text-sm text-muted-foreground">K</span>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Find jobs like... toggle */}
          {likedJobs && likedJobs.items.length > 0 && (
            <div className="space-y-2">
              <button
                onClick={() => setShowReferences(!showReferences)}
                className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
              >
                {showReferences ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
                <span>Find jobs like...</span>
                <span className="text-xs text-muted-foreground/60">
                  ({likedJobs.items.length} liked)
                </span>
                {selectedRefIds.size > 0 && (
                  <Badge variant="outline" className="text-xs ml-1">
                    {selectedRefIds.size} selected
                  </Badge>
                )}
              </button>
              {showReferences && (
                <div className="pl-5 space-y-1 max-h-[200px] overflow-y-auto">
                  {likedJobs.items.map((job) => (
                    <label
                      key={job.id}
                      className="flex items-center gap-2 text-sm py-1 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedRefIds.has(job.id)}
                        onChange={() => toggleRef(job.id)}
                        className="rounded"
                      />
                      <span className="font-medium truncate">{job.display_title}</span>
                      <span className="text-muted-foreground truncate">
                        — {job.display_company}
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

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
            disabled={isSearching}
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

          {/* Collapsible activity log */}
          {candidateLog.length > 0 && (
            <div>
              <button
                onClick={() => setShowLog(!showLog)}
                className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                {showLog ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ChevronRight className="h-3 w-3" />
                )}
                <span>Activity log</span>
                <span className="text-muted-foreground/60">
                  ({candidateLog.filter((e) => e.status === "accepted").length} accepted, {candidateLog.filter((e) => e.status === "rejected").length} rejected)
                </span>
              </button>
              {showLog && (
                <div className="mt-2 max-h-[200px] overflow-y-auto rounded-md border bg-muted/20 px-3 py-2 space-y-1">
                  {candidateLog.map((entry, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs py-0.5">
                      {entry.status === "checking" && (
                        <Loader2 className="h-3 w-3 animate-spin text-amber-500 flex-shrink-0" />
                      )}
                      {entry.status === "accepted" && (
                        <Check className="h-3 w-3 text-emerald-500 flex-shrink-0" />
                      )}
                      {entry.status === "rejected" && (
                        <X className="h-3 w-3 text-red-400 flex-shrink-0" />
                      )}
                      <span className={entry.status === "rejected" ? "text-muted-foreground/60" : "font-medium"}>
                        {entry.name}
                      </span>
                      {entry.reason && (
                        <span className="text-muted-foreground/50">— {entry.reason}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

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
            </div>
          )}

          {/* Hit list */}
          {hits.length > 0 && (
            <div className="space-y-2 max-h-[600px] overflow-auto">
              {hits.map((hit) => {
                const hitKey = hit.slug || `${hit.kind || "ats"}:${hit.name}`;
                const hitKind = hit.kind || "ats";
                const isAts = hitKind === "ats";
                const isLead = hitKind === "lead";
                const isTracked = hitKind === "tracked";
                const isExpanded = expandedHit === hitKey;
                const hitStatus = importStatuses[hitKey] || "idle";
                const isSelected = selectedSlugs.has(hitKey);
                const jobSel = isAts ? getJobSelections(hit) : new Set<string>();

                const bgClass = isLead
                  ? "bg-amber-50/30 border-amber-200"
                  : isTracked
                    ? "bg-blue-50/30 border-blue-200"
                    : "";

                return (
                  <div
                    key={hitKey}
                    className={`rounded-md border transition-all duration-300 ${
                      hitStatus === "importing"
                        ? "opacity-50 bg-muted/20"
                        : hitStatus === "imported"
                          ? "bg-emerald-50/50 border-emerald-200"
                          : hitStatus === "error"
                            ? "bg-red-50/50 border-red-200"
                            : bgClass
                    }`}
                  >
                    {/* Hit card header */}
                    <div className="flex items-center gap-3 px-4 py-3">
                      {/* Checkbox: all ATS/direct hits are importable. */}
                      {isAts && !isSearching && hitStatus === "idle" && (
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleCompanySelect(hitKey)}
                          onClick={(e) => e.stopPropagation()}
                          className="rounded flex-shrink-0"
                        />
                      )}

                      {hitStatus === "importing" && (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground flex-shrink-0" />
                      )}
                      {hitStatus === "imported" && (
                        <Check className="h-4 w-4 text-emerald-600 flex-shrink-0" />
                      )}

                      <button
                        onClick={() => setExpandedHit(isExpanded ? null : hitKey)}
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
                            {isLead && (
                              <Badge
                                variant="outline"
                                className="bg-amber-100 text-amber-800 border-amber-300 text-xs flex-shrink-0"
                              >
                                Lead
                              </Badge>
                            )}
                            {isTracked && (
                              <Badge
                                variant="outline"
                                className="bg-blue-100 text-blue-800 border-blue-300 text-xs flex-shrink-0"
                              >
                                Tracked
                              </Badge>
                            )}
                            {isAts && hit.ats && (
                              <Badge
                                variant="outline"
                                className={`text-xs capitalize flex-shrink-0 ${ATS_COLORS[hit.ats] || ""}`}
                              >
                                {hit.ats}
                              </Badge>
                            )}
                            {hit.source && (
                              <Badge
                                variant="outline"
                                className={`text-xs flex-shrink-0 ${SOURCE_COLORS[hit.source] || "bg-muted text-muted-foreground"}`}
                              >
                                {hit.source}
                              </Badge>
                            )}
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
                          {isLead ? (
                            <>careers page</>
                          ) : (
                            <>
                              {hit.total_jobs} jobs · {hit.relevant_jobs} relevant
                              {jobSel.size > 0 && hitStatus === "idle" && (
                                <span className="text-foreground font-medium"> · {jobSel.size} selected</span>
                              )}
                            </>
                          )}
                        </span>
                      </button>
                    </div>

                    {/* Expanded: match reason + body */}
                    {isExpanded && (
                      <div className="border-t px-4 py-4 space-y-3">
                        {hit.match_reason && (
                          <p className="text-sm text-muted-foreground bg-muted/30 rounded-md px-3 py-2">
                            <span className="font-medium text-foreground">Why this company: </span>
                            {hit.match_reason}
                          </p>
                        )}

                        {isLead && hit.careers_url && (
                          <div>
                            <a
                              href={hit.careers_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-amber-100 text-amber-800 hover:bg-amber-200 text-sm font-medium border border-amber-300 transition-colors"
                            >
                              Open careers page →
                            </a>
                            <p className="text-xs text-muted-foreground mt-2">
                              We can't import jobs from this company automatically — browse their careers page to find roles.
                            </p>
                          </div>
                        )}

                        {isTracked && hit.top_jobs.length > 0 && (
                          <div className="space-y-2">
                            <p className="text-xs font-medium text-blue-800">
                              Matching jobs already in your tracker:
                            </p>
                            <ul className="space-y-1.5">
                              {hit.top_jobs.map((j) => (
                                <li
                                  key={j.url}
                                  className="text-sm flex items-start gap-2 px-3 py-2 rounded-md bg-white border border-blue-100"
                                >
                                  <span className="flex-1 min-w-0">
                                    <span className="font-medium">{j.title}</span>
                                    {j.location && (
                                      <span className="text-muted-foreground"> · {j.location}</span>
                                    )}
                                  </span>
                                  {j.url && (
                                    <a
                                      href={j.url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="text-xs text-blue-700 hover:underline whitespace-nowrap"
                                    >
                                      View →
                                    </a>
                                  )}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {isAts && (
                          <HitReview
                            hit={hit}
                            selectedUrls={jobSel}
                            onSelectionChange={(urls) => updateJobSelections(hit.slug, urls)}
                            onImport={() => importSingle(hit)}
                            importStatus={hitStatus}
                          />
                        )}
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
              No companies found matching your criteria. Try different guidance or looser filters.
            </div>
          )}

          {/* Footer */}
          {phase !== "searching" && (
            <div className="flex items-center justify-between pt-2 border-t">
              <button
                onClick={() => {
                  clearSearch();
                  onClose();
                }}
                className="h-9 px-4 rounded-md border text-sm hover:bg-muted/50 transition-colors"
              >
                Close
              </button>
              <div className="flex items-center gap-3">
                {phase === "error" && (
                  <button
                    onClick={handleSearch}
                    disabled={isSearching}
                    className="h-9 px-4 rounded-md bg-foreground text-background text-sm font-medium hover:bg-foreground/90 disabled:opacity-50 transition-colors"
                  >
                    Retry
                  </button>
                )}
                {selectedSlugs.size > 0 && (
                  <button
                    onClick={importAllSelected}
                    disabled={isAnyImporting || countSelectedJobs() === 0}
                    className={`h-9 px-5 rounded-md text-sm font-medium transition-all flex items-center gap-2 ${
                      isAnyImporting || countSelectedJobs() === 0
                        ? "bg-foreground/50 text-background/70 cursor-not-allowed"
                        : "bg-foreground text-background hover:bg-foreground/90 shadow-[0_0_12px_rgba(59,130,246,0.5)] hover:shadow-[0_0_18px_rgba(59,130,246,0.6)]"
                    }`}
                  >
                    {isAnyImporting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                    Import {selectedSlugs.size} {selectedSlugs.size === 1 ? "Company" : "Companies"} ({countSelectedJobs()} jobs)
                  </button>
                )}
              </div>
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
