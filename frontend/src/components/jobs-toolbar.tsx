"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, SlidersHorizontal, X, Check } from "lucide-react";
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
import { rescoreBatch, runGarbageCollect } from "@/lib/api";

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

        {/* Location multi-select */}
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
                      <span className={`flex items-center justify-center w-4 h-4 rounded border text-[10px] ${
                        active ? "bg-foreground text-background border-foreground" : "border-muted-foreground/30"
                      }`}>
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

        {/* Advanced filters popover */}
        <Popover>
          <PopoverTrigger
            className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 text-sm border rounded-md hover:bg-muted/50 cursor-pointer ${
              hasAdvancedFilters ? "border-foreground/30 bg-muted/30" : ""
            }`}
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            More
            {hasAdvancedFilters && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4">
                {(currentSource ? 1 : 0) + (currentTag ? 1 : 0)}
              </Badge>
            )}
          </PopoverTrigger>
          <PopoverContent className="w-64 p-3" align="start">
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

              {tags && tags.length > 0 && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Tag</label>
                  <Select
                    value={currentTag || undefined}
                    onValueChange={(v) => updateParams({ tag: v === "all" ? "" : (v ?? "") })}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="All tags" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All tags</SelectItem>
                      {tags.map((t) => (
                        <SelectItem key={t.id} value={t.name}>
                          {t.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}

              {hasAdvancedFilters && (
                <button
                  onClick={() => updateParams({ source: "", tag: "" })}
                  className="text-xs text-muted-foreground hover:text-foreground"
                >
                  Clear advanced filters
                </button>
              )}
            </div>
          </PopoverContent>
        </Popover>

        {/* Right-aligned actions */}
        <div className="ml-auto flex items-center gap-2">
          {hasFilters && (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              <X className="w-3.5 h-3.5 mr-1" />
              Clear
            </Button>
          )}

          <Button
            variant="outline"
            size="sm"
            disabled={gcLoading}
            onClick={handleGarbageCollect}
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
