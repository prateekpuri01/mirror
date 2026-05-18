"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Loader2,
  Plus,
  SlidersHorizontal,
  Tag as TagIcon,
  X,
  Check,
  Eye,
  EyeOff,
  ChevronDown,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ALL_STATUSES, STATUS_CONFIG, ALL_SOURCES, SOURCE_CONFIG } from "@/lib/constants";
import { useTags, useLocations } from "@/hooks/use-jobs";
import { useJobProcessing } from "@/hooks/use-job-processing";
import { useJobSelection } from "@/hooks/use-job-selection";
import { useExtractionTracking } from "@/hooks/use-extraction-tracking";
import {
  extractApply,
  extractPreview,
  importJobFromUrl,
  rescoreBatch,
  runGarbageCollect,
  scoreJob,
  triggerRequirementsExtraction,
} from "@/lib/api";

type ReprocessOp = "score" | "extract_salary_location" | "extract_requirements";

const WORK_MODELS = [
  { value: "remote", label: "Remote", bg: "bg-sky-100 text-sky-700 border-sky-300", activeBg: "bg-sky-500 text-white border-sky-500" },
  { value: "hybrid", label: "Hybrid", bg: "bg-violet-100 text-violet-700 border-violet-300", activeBg: "bg-violet-500 text-white border-violet-500" },
  { value: "onsite", label: "On-site", bg: "bg-slate-100 text-slate-700 border-slate-300", activeBg: "bg-slate-600 text-white border-slate-600" },
] as const;

const SALARY_STEPS = [0, 50000, 75000, 100000, 125000, 150000, 175000, 200000, 250000, 300000];

function formatSalaryLabel(value: number): string {
  if (value === 0) return "Any";
  return `$${value / 1000}K+`;
}

export function JobsToolbar() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { data: tags } = useTags();
  const { data: locations } = useLocations();
  const { hasProcessingJobs, startProcessing } = useJobProcessing();
  const { selectedIds, count: selectionCount, clearSelection } = useJobSelection();
  const extractionTracking = useExtractionTracking();

  const [rescoring, setRescoring] = useState(false);
  const [rescoreCount, setRescoreCount] = useState(0);
  const [gcLoading, setGcLoading] = useState(false);
  const [gcResult, setGcResult] = useState<string | null>(null);
  const [locationOpen, setLocationOpen] = useState(false);

  const currentQ = searchParams.get("q") || "";
  const currentStatus = searchParams.get("status") || "";
  const currentSource = searchParams.get("source") || "";
  const currentTag = searchParams.get("tag") || "";
  const currentLocation = searchParams.get("location") || "";
  const currentWorkModel = searchParams.get("work_model") || "";
  const currentMinSalary = Number(searchParams.get("min_salary")) || 0;
  const hideExpired = searchParams.get("hide_expired") !== "false"; // default true

  const [addJobOpen, setAddJobOpen] = useState(false);
  const [addJobUrl, setAddJobUrl] = useState("");
  const [addJobImporting, setAddJobImporting] = useState(false);
  const [addJobError, setAddJobError] = useState<string | null>(null);

  // Reprocess dropdown — selected operations + running state
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const [reprocessOps, setReprocessOps] = useState<Set<ReprocessOp>>(
    new Set(["score"]),
  );
  const [reprocessRunning, setReprocessRunning] = useState(false);
  const [reprocessError, setReprocessError] = useState<string | null>(null);

  // Tags popover open state
  const [tagsOpen, setTagsOpen] = useState(false);
  const [newTagName, setNewTagName] = useState("");
  const [createTagPending, setCreateTagPending] = useState(false);
  const [tagCreateError, setTagCreateError] = useState<string | null>(null);

  const toggleReprocessOp = useCallback((op: ReprocessOp) => {
    setReprocessOps((prev) => {
      const next = new Set(prev);
      if (next.has(op)) next.delete(op);
      else next.add(op);
      return next;
    });
  }, []);

  const handleReprocess = useCallback(async () => {
    if (selectionCount === 0 || reprocessOps.size === 0) return;
    setReprocessRunning(true);
    setReprocessError(null);
    const ids = Array.from(selectedIds);
    const ops = Array.from(reprocessOps);
    try {
      // Register all jobs as processing so the table shows the spinner glow
      startProcessing(ids);

      // Run each operation over the selected jobs. The browser handles ~10
      // parallel fetches comfortably; we don't cap concurrency for now since
      // the backend's per-endpoint locks already protect against thundering
      // herd, and per-job calls are bounded by the selection count.
      const runOpForId = async (op: ReprocessOp, id: string) => {
        if (op === "score") {
          await scoreJob(id);
        } else if (op === "extract_salary_location") {
          const preview = await extractPreview(id);
          await extractApply(id, {
            salary_min: preview.salary_min,
            salary_max: preview.salary_max,
            work_model: preview.work_model,
            locations: preview.locations,
          });
        } else if (op === "extract_requirements") {
          await triggerRequirementsExtraction(id);
          extractionTracking.startTracking(id);
        }
      };

      const tasks: Promise<void>[] = [];
      for (const id of ids) {
        for (const op of ops) {
          tasks.push(runOpForId(op, id).catch(() => undefined));
        }
      }
      await Promise.all(tasks);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setReprocessOpen(false);
    } catch (err) {
      setReprocessError(
        err instanceof Error ? err.message : "Reprocess failed",
      );
    } finally {
      setReprocessRunning(false);
    }
  }, [
    selectedIds,
    selectionCount,
    reprocessOps,
    startProcessing,
    extractionTracking,
    queryClient,
  ]);

  const handleCreateTag = useCallback(async () => {
    const name = newTagName.trim();
    if (!name) return;
    setCreateTagPending(true);
    setTagCreateError(null);
    try {
      const apiUrl =
        process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiUrl}/api/tags`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed: ${res.status}`);
      }
      setNewTagName("");
      await queryClient.invalidateQueries({ queryKey: ["tags"] });
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (err) {
      setTagCreateError(
        err instanceof Error ? err.message : "Failed to create tag",
      );
    } finally {
      setCreateTagPending(false);
    }
  }, [newTagName, queryClient]);

  const handleDeleteTag = useCallback(
    async (tagId: string) => {
      const apiUrl =
        process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      try {
        await fetch(`${apiUrl}/api/tags/${tagId}`, { method: "DELETE" });
        await queryClient.invalidateQueries({ queryKey: ["tags"] });
        await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      } catch {
        // silent — list will refresh on next load
      }
    },
    [queryClient],
  );

  const handleAddJob = useCallback(async () => {
    const url = addJobUrl.trim();
    if (!url) return;
    setAddJobImporting(true);
    setAddJobError(null);
    try {
      const job = await importJobFromUrl(url);
      // Register as processing BEFORE invalidating queries so the row renders
      // with the amber processing background + spinner the moment it appears
      // in the refetch. If we invalidated first, the row would briefly render
      // in its normal "scraped" state before flipping to processing.
      startProcessing([job.id]);
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setAddJobUrl("");
      setAddJobOpen(false);
    } catch (err) {
      setAddJobError(
        err instanceof Error ? err.message : "Failed to import job"
      );
    } finally {
      setAddJobImporting(false);
    }
  }, [addJobUrl, queryClient, startProcessing]);

  const [searchValue, setSearchValue] = useState(currentQ);
  const [salaryValue, setSalaryValue] = useState(currentMinSalary);
  const salaryCommitRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Sync search input with URL params when navigating back/forward
  useEffect(() => {
    setSearchValue(currentQ);
  }, [currentQ]);

  useEffect(() => {
    setSalaryValue(currentMinSalary);
  }, [currentMinSalary]);

  const updateParams = useCallback(
    (updates: Record<string, string>) => {
      const params = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(updates)) {
        if (value) {
          params.set(key, value);
        } else {
          params.delete(key);
        }
      }
      // Reset to page 1 when filters change
      params.delete("page");
      router.push(`?${params.toString()}`);
    },
    [router, searchParams]
  );

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => {
      if (searchValue !== currentQ) {
        updateParams({ q: searchValue });
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [searchValue, currentQ, updateParams]);

  // --- Work model multi-select ---
  const activeWorkModels = new Set(
    currentWorkModel ? currentWorkModel.split(",").filter(Boolean) : []
  );

  function toggleWorkModel(model: string) {
    const next = new Set(activeWorkModels);
    if (next.has(model)) {
      next.delete(model);
    } else {
      next.add(model);
    }
    updateParams({ work_model: [...next].join(",") });
  }

  // --- Location multi-select ---
  const activeLocations = new Set(
    currentLocation ? currentLocation.split(",").filter(Boolean) : []
  );

  function toggleLocation(locName: string) {
    const next = new Set(activeLocations);
    if (next.has(locName)) {
      next.delete(locName);
    } else {
      next.add(locName);
    }
    updateParams({ location: [...next].join(",") });
  }

  // --- Salary slider ---
  function handleSalaryChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = SALARY_STEPS[Number(e.target.value)] ?? 0;
    setSalaryValue(val);
    // Debounce the URL update
    if (salaryCommitRef.current) clearTimeout(salaryCommitRef.current);
    salaryCommitRef.current = setTimeout(() => {
      updateParams({ min_salary: val > 0 ? String(val) : "" });
    }, 300);
  }

  const salaryStepIndex = SALARY_STEPS.indexOf(salaryValue) >= 0
    ? SALARY_STEPS.indexOf(salaryValue)
    : 0;

  // --- Actions ---
  const handleRescore = useCallback(async () => {
    setRescoring(true);
    try {
      const result = await rescoreBatch();
      if (result.job_ids.length > 0) {
        startProcessing(result.job_ids);
        setRescoreCount(result.job_ids.length);
      }
    } catch {
      // ignore
    } finally {
      setRescoring(false);
    }
  }, [startProcessing]);

  const handleGarbageCollect = useCallback(async () => {
    setGcLoading(true);
    setGcResult(null);
    try {
      const result = await runGarbageCollect();
      setGcResult(`Archived ${result.archived} jobs`);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setTimeout(() => setGcResult(null), 3000);
    } catch {
      setGcResult("Failed to archive");
      setTimeout(() => setGcResult(null), 3000);
    } finally {
      setGcLoading(false);
    }
  }, [queryClient]);

  const hasFilters = currentQ || currentStatus || currentSource || currentTag || currentLocation || currentWorkModel || currentMinSalary > 0;
  const hasAdvancedFilters = currentSource || currentTag;

  function clearFilters() {
    setSearchValue("");
    setSalaryValue(0);
    router.push("/");
  }

  return (
    <div className="space-y-2">
      {/* Row 1: Main filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <Input
          placeholder="Search jobs..."
          value={searchValue}
          onChange={(e) => setSearchValue(e.target.value)}
          className="w-56"
        />

        <Select
          value={currentStatus || undefined}
          onValueChange={(v) => updateParams({ status: v === "all" ? "" : (v ?? "") })}
        >
          <SelectTrigger className="w-[130px]">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {ALL_STATUSES.map((s) => (
              <SelectItem key={s} value={s}>
                {STATUS_CONFIG[s].label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Work model toggles */}
        <div className="flex items-center gap-1">
          {WORK_MODELS.map((wm) => {
            const active = activeWorkModels.has(wm.value);
            return (
              <button
                key={wm.value}
                onClick={() => toggleWorkModel(wm.value)}
                className={`px-2.5 py-1 text-xs font-medium rounded-full border transition-colors ${
                  active ? wm.activeBg : wm.bg
                }`}
              >
                {wm.label}
              </button>
            );
          })}
        </div>

        {/* Salary slider */}
        <div className="flex items-center gap-2 px-2">
          <span className="text-xs text-muted-foreground whitespace-nowrap">Salary</span>
          <input
            type="range"
            min={0}
            max={SALARY_STEPS.length - 1}
            step={1}
            value={salaryStepIndex}
            onChange={handleSalaryChange}
            className="w-24 h-1.5 accent-emerald-600 cursor-pointer"
          />
          <span className="text-xs font-medium tabular-nums w-12">
            {formatSalaryLabel(salaryValue)}
          </span>
        </div>

        {/* Location multi-select — top-level button matching production */}
        {locations && locations.length > 0 && (
          <Popover open={locationOpen} onOpenChange={setLocationOpen}>
            <PopoverTrigger
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border rounded-md hover:bg-muted/50 cursor-pointer ${
                activeLocations.size > 0 ? "border-foreground/30 bg-muted/30" : ""
              }`}
            >
              Location
              {activeLocations.size > 0 && (
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4">
                  {activeLocations.size}
                </Badge>
              )}
            </PopoverTrigger>
            <PopoverContent className="w-56 p-1.5" align="start">
              <div className="max-h-[240px] overflow-y-auto">
                {locations.map((l) => {
                  const active = activeLocations.has(l.display_name);
                  return (
                    <button
                      key={l.id}
                      onClick={() => toggleLocation(l.display_name)}
                      className="flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-md hover:bg-muted/50 text-left"
                    >
                      <span
                        className={`flex items-center justify-center w-4 h-4 rounded border text-[10px] ${
                          active
                            ? "bg-foreground text-background border-foreground"
                            : "border-muted-foreground/30"
                        }`}
                      >
                        {active && <Check className="w-3 h-3" />}
                      </span>
                      <span className="truncate">{l.display_name}</span>
                    </button>
                  );
                })}
              </div>
              {activeLocations.size > 0 && (
                <div className="border-t mt-1 pt-1">
                  <button
                    onClick={() => updateParams({ location: "" })}
                    className="w-full px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground rounded-md hover:bg-muted/50"
                  >
                    Clear locations
                  </button>
                </div>
              )}
            </PopoverContent>
          </Popover>
        )}

        {/* Tags filter — top-level popover with full tag list + create-tag inline */}
        <Popover open={tagsOpen} onOpenChange={setTagsOpen}>
          <PopoverTrigger
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border rounded-md hover:bg-muted/50 cursor-pointer ${
              currentTag ? "border-foreground/30 bg-muted/30" : ""
            }`}
          >
            <TagIcon className="w-3.5 h-3.5" />
            Tags
            {currentTag && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4">
                1
              </Badge>
            )}
          </PopoverTrigger>
          <PopoverContent className="w-72 p-3" align="start">
            <div className="space-y-3">
              <p className="text-xs font-medium text-muted-foreground">Filter by tag</p>
              {tags && tags.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {tags.map((t) => {
                    const active = currentTag === t.name;
                    return (
                      <div
                        key={t.id}
                        onClick={() =>
                          updateParams({ tag: active ? "" : t.name })
                        }
                        className={`inline-flex items-center gap-1 pl-2 pr-1 py-0.5 text-xs rounded-full border cursor-pointer transition-colors ${
                          active
                            ? "bg-foreground text-background border-foreground"
                            : "border-muted-foreground/30 hover:bg-muted/50"
                        }`}
                        style={
                          t.color && !active
                            ? {
                                backgroundColor: `${t.color}20`,
                                color: t.color,
                                borderColor: t.color,
                              }
                            : undefined
                        }
                      >
                        <span>{t.name}</span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteTag(t.id);
                          }}
                          aria-label="Delete tag"
                          className="rounded-full p-0.5 hover:bg-black/10"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground italic">
                  No tags yet
                </p>
              )}
              <div className="border-t pt-3 space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">
                  Create tag
                </p>
                <p className="text-xs text-muted-foreground">
                  New tags auto-apply to jobs containing the tag name.
                </p>
                <div className="flex gap-1.5">
                  <Input
                    value={newTagName}
                    onChange={(e) => setNewTagName(e.target.value)}
                    placeholder="e.g. AI Safety"
                    className="h-8 text-sm"
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !createTagPending) handleCreateTag();
                    }}
                  />
                  <Button
                    size="sm"
                    onClick={handleCreateTag}
                    disabled={!newTagName.trim() || createTagPending}
                    className="h-8 px-2"
                  >
                    {createTagPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Plus className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </div>
                {tagCreateError && (
                  <p className="text-xs text-red-600">{tagCreateError}</p>
                )}
                {currentTag && (
                  <button
                    onClick={() => updateParams({ tag: "" })}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    Clear tag filter
                  </button>
                )}
              </div>
            </div>
          </PopoverContent>
        </Popover>

        {/* Add Job (URL import) — inline between Tags and More to match production */}
        <Popover open={addJobOpen} onOpenChange={setAddJobOpen}>
          <PopoverTrigger className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border rounded-md hover:bg-muted/50 cursor-pointer">
            <Plus className="w-3.5 h-3.5" />
            Add Job
          </PopoverTrigger>
          <PopoverContent className="w-[420px]" align="end">
            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
                  Job URL
                </label>
                <Input
                  value={addJobUrl}
                  onChange={(e) => setAddJobUrl(e.target.value)}
                  placeholder="https://example.com/jobs/12345"
                  disabled={addJobImporting}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !addJobImporting) handleAddJob();
                  }}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                We&apos;ll fetch the page, parse it with the LLM, save the
                job, and score it automatically.
              </p>
              {addJobError && (
                <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5">
                  {addJobError}
                </div>
              )}
              <div className="flex justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setAddJobUrl("");
                    setAddJobError(null);
                    setAddJobOpen(false);
                  }}
                  disabled={addJobImporting}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={handleAddJob}
                  disabled={!addJobUrl.trim() || addJobImporting}
                >
                  {addJobImporting ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                      Importing...
                    </>
                  ) : (
                    "Import"
                  )}
                </Button>
              </div>
            </div>
          </PopoverContent>
        </Popover>

        {/* Advanced filters popover — Source, Location, bulk actions all live here */}
        <Popover>
          <PopoverTrigger
            className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 text-sm border rounded-md hover:bg-muted/50 cursor-pointer ${
              hasAdvancedFilters || activeLocations.size > 0
                ? "border-foreground/30 bg-muted/30"
                : ""
            }`}
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            More
            {(hasAdvancedFilters || activeLocations.size > 0) && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4">
                {(currentSource ? 1 : 0) +
                  (currentTag ? 1 : 0) +
                  (activeLocations.size > 0 ? 1 : 0)}
              </Badge>
            )}
          </PopoverTrigger>
          <PopoverContent className="w-72 p-3" align="start">
            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1 block">Source</label>
                <Select
                  value={currentSource || undefined}
                  onValueChange={(v) => updateParams({ source: v === "all" ? "" : (v ?? "") })}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="All sources" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All sources</SelectItem>
                    {ALL_SOURCES.map((s) => (
                      <SelectItem key={s} value={s}>
                        {SOURCE_CONFIG[s].label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {currentSource && (
                <button
                  onClick={() => updateParams({ source: "" })}
                  className="text-xs text-muted-foreground hover:text-foreground"
                >
                  Clear advanced filters
                </button>
              )}

              <div className="border-t pt-3 space-y-2">
                <p className="text-xs font-medium text-muted-foreground">
                  Bulk actions
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={gcLoading}
                  onClick={handleGarbageCollect}
                  className="w-full justify-start"
                >
                  {gcLoading ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                      Archiving...
                    </>
                  ) : gcResult ? (
                    gcResult
                  ) : (
                    "Archive Expired"
                  )}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={rescoring || hasProcessingJobs}
                  onClick={handleRescore}
                  className="w-full justify-start"
                >
                  {rescoring ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                      Starting...
                    </>
                  ) : hasProcessingJobs ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                      Rescoring {rescoreCount} jobs...
                    </>
                  ) : (
                    "Rescore All"
                  )}
                </Button>
              </div>
            </div>
          </PopoverContent>
        </Popover>

        {/* Right-grouped actions: Clear filters, Reprocess (conditional), Hide Expired */}
        <div className="ml-auto flex items-center gap-2">
          {hasFilters && (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              <X className="w-3.5 h-3.5 mr-1" />
              Clear
            </Button>
          )}

          {/* Reprocess dropdown — only when there's a selection */}
          {selectionCount > 0 && (
            <Popover open={reprocessOpen} onOpenChange={setReprocessOpen}>
              <PopoverTrigger
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border rounded-md hover:bg-muted/50 cursor-pointer bg-amber-50 border-amber-200 text-amber-800"
                title={`${selectionCount} job${selectionCount === 1 ? "" : "s"} selected`}
              >
                <span className="font-medium">Reprocess</span>
                <Badge
                  variant="secondary"
                  className="text-[10px] px-1.5 py-0 h-4 bg-amber-200 text-amber-900"
                >
                  {selectionCount}
                </Badge>
                <ChevronDown className="w-3.5 h-3.5" />
              </PopoverTrigger>
              <PopoverContent className="w-64 p-2" align="end">
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground px-1">
                    Operations
                  </p>
                  {(
                    [
                      { op: "score" as const, label: "Score (AI scoring)" },
                      {
                        op: "extract_salary_location" as const,
                        label: "Extract Salary/Location",
                      },
                      {
                        op: "extract_requirements" as const,
                        label: "Extract Requirements",
                      },
                    ]
                  ).map(({ op, label }) => {
                    const active = reprocessOps.has(op);
                    return (
                      <button
                        key={op}
                        onClick={() => toggleReprocessOp(op)}
                        className={`flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-md text-left transition-colors ${
                          active
                            ? "bg-muted/50 font-medium"
                            : "hover:bg-muted/30"
                        }`}
                      >
                        <span
                          className={`flex items-center justify-center w-4 h-4 rounded border text-[10px] ${
                            active
                              ? "bg-foreground text-background border-foreground"
                              : "border-muted-foreground/30"
                          }`}
                        >
                          {active && <Check className="w-3 h-3" />}
                        </span>
                        <span>{label}</span>
                      </button>
                    );
                  })}
                  {reprocessError && (
                    <p className="text-xs text-red-600 px-1">{reprocessError}</p>
                  )}
                  <div className="border-t pt-2 flex items-center justify-between gap-2">
                    <button
                      onClick={() => {
                        clearSelection();
                        setReprocessOpen(false);
                      }}
                      className="text-xs text-muted-foreground hover:text-foreground"
                    >
                      Clear selection
                    </button>
                    <Button
                      size="sm"
                      onClick={handleReprocess}
                      disabled={reprocessRunning || reprocessOps.size === 0}
                    >
                      {reprocessRunning ? (
                        <>
                          <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                          Reprocessing...
                        </>
                      ) : (
                        `Reprocess ${selectionCount} Job${selectionCount === 1 ? "" : "s"}`
                      )}
                    </Button>
                  </div>
                </div>
              </PopoverContent>
            </Popover>
          )}

          {/* Hide Expired toggle — last button in the row to match production */}
          <Button
            variant={hideExpired ? "default" : "outline"}
            size="sm"
            onClick={() =>
              updateParams({ hide_expired: hideExpired ? "false" : "" })
            }
            title={hideExpired ? "Hiding expired jobs" : "Showing expired jobs"}
          >
            {hideExpired ? (
              <>
                <EyeOff className="w-3.5 h-3.5 mr-1.5" />
                Hide Expired
              </>
            ) : (
              <>
                <Eye className="w-3.5 h-3.5 mr-1.5" />
                Show Expired
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Active filter badges */}
      {(activeLocations.size > 0 || activeWorkModels.size > 0 || currentMinSalary > 0) && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {[...activeLocations].map((loc) => (
            <Badge
              key={loc}
              variant="secondary"
              className="text-xs gap-1 cursor-pointer hover:bg-muted"
              onClick={() => toggleLocation(loc)}
            >
              {loc}
              <X className="w-3 h-3" />
            </Badge>
          ))}
          {currentMinSalary > 0 && (
            <Badge
              variant="secondary"
              className="text-xs gap-1 cursor-pointer hover:bg-muted"
              onClick={() => {
                setSalaryValue(0);
                updateParams({ min_salary: "" });
              }}
            >
              Salary {formatSalaryLabel(currentMinSalary)}
              <X className="w-3 h-3" />
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}
