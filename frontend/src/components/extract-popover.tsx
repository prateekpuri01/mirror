"use client";

import { useState } from "react";
import { Sparkles, Loader2, X, Plus } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Job } from "@/lib/types";
import { extractPreview, extractApply, type ExtractionPreview } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

interface ExtractPopoverProps {
  job: Job;
}

interface EditableLocation {
  city: string;
  state: string | null;
  country: string;
  display_name: string;
}

export function ExtractPopover({ job }: ExtractPopoverProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Editable state
  const [salaryMin, setSalaryMin] = useState<string>("");
  const [salaryMax, setSalaryMax] = useState<string>("");
  const [workModel, setWorkModel] = useState<string>("onsite");
  const [locations, setLocations] = useState<EditableLocation[]>([]);
  const [newLocation, setNewLocation] = useState("");
  const [confidence, setConfidence] = useState<string | null>(null);

  async function handleExtract() {
    setLoading(true);
    setError(null);
    try {
      const preview = await extractPreview(job.id);
      setSalaryMin(preview.salary_min?.toString() ?? "");
      setSalaryMax(preview.salary_max?.toString() ?? "");
      setWorkModel(preview.work_model ?? "onsite");
      setLocations(preview.locations);
      setConfidence(preview.salary_confidence);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Extraction failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await extractApply(job.id, {
        salary_min: salaryMin ? parseInt(salaryMin) : null,
        salary_max: salaryMax ? parseInt(salaryMax) : null,
        work_model: workModel,
        locations,
      });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["locations"] });
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  function removeLocation(index: number) {
    setLocations((prev) => prev.filter((_, i) => i !== index));
  }

  function addLocation() {
    const trimmed = newLocation.trim();
    if (!trimmed) return;
    // Parse "City, ST" or "City, XX" format
    const parts = trimmed.split(",").map((s) => s.trim());
    const city = parts[0];
    const stateOrCountry = parts[1] || "";
    const isUSState = /^[A-Z]{2}$/.test(stateOrCountry) && stateOrCountry.length === 2;
    setLocations((prev) => [
      ...prev,
      {
        city,
        state: isUSState ? stateOrCountry : null,
        country: isUSState ? "US" : stateOrCountry || "US",
        display_name: trimmed,
      },
    ]);
    setNewLocation("");
  }

  function handleOpen(nextOpen: boolean) {
    setOpen(nextOpen);
    if (nextOpen) {
      // Pre-fill with current job data
      setSalaryMin(job.salary_min && job.salary_min > 0 ? job.salary_min.toString() : "");
      setSalaryMax(job.salary_max && job.salary_max > 0 ? job.salary_max.toString() : "");
      setWorkModel(job.work_model ?? "onsite");
      setLocations(
        (job.normalized_locations ?? []).map((l) => ({
          city: l.city,
          state: l.state,
          country: l.country,
          display_name: l.display_name,
        }))
      );
      setConfidence(null);
      setError(null);
    }
  }

  const fmtSalary = (v: string) => {
    const n = parseInt(v);
    if (isNaN(n)) return "";
    return `$${Math.round(n / 1000)}K`;
  };

  return (
    <Popover open={open} onOpenChange={handleOpen}>
      <PopoverTrigger
        onClick={(e) => e.stopPropagation()}
        className="inline-flex items-center justify-center rounded-md p-1 text-muted-foreground/40 hover:text-amber-600 hover:bg-amber-50 transition-colors"
        title="Re-extract job fields with AI"
      >
        <Sparkles size={14} />
      </PopoverTrigger>
      <PopoverContent
        className="w-[380px] p-0"
        side="bottom"
        align="start"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 pt-3 pb-2 border-b">
          <div className="flex items-center gap-2">
            <Sparkles size={14} className="text-amber-600" />
            <span className="text-sm font-semibold">Extract Fields</span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={handleExtract}
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              "Re-parse with AI"
            )}
          </Button>
        </div>

        <div className="p-4 space-y-4">
          {/* Error */}
          {error && (
            <p className="text-xs text-red-600 bg-red-50 rounded px-2 py-1">{error}</p>
          )}

          {/* Salary */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1.5 flex items-center gap-2">
              Salary (annual USD)
              {confidence && (
                <Badge
                  variant="outline"
                  className={`text-[10px] px-1.5 py-0 ${
                    confidence === "high"
                      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                      : confidence === "medium"
                        ? "bg-amber-50 text-amber-700 border-amber-200"
                        : "bg-slate-50 text-slate-500 border-slate-200"
                  }`}
                >
                  {confidence}
                </Badge>
              )}
            </label>
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Input
                  type="number"
                  placeholder="Min"
                  value={salaryMin}
                  onChange={(e) => setSalaryMin(e.target.value)}
                  className="h-8 text-sm pr-14"
                />
                {salaryMin && (
                  <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                    {fmtSalary(salaryMin)}
                  </span>
                )}
              </div>
              <span className="text-muted-foreground text-sm">–</span>
              <div className="relative flex-1">
                <Input
                  type="number"
                  placeholder="Max"
                  value={salaryMax}
                  onChange={(e) => setSalaryMax(e.target.value)}
                  className="h-8 text-sm pr-14"
                />
                {salaryMax && (
                  <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                    {fmtSalary(salaryMax)}
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Work Model */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
              Work Model
            </label>
            <div className="flex gap-1.5">
              {(["remote", "hybrid", "onsite"] as const).map((wm) => (
                <button
                  key={wm}
                  onClick={() => setWorkModel(wm)}
                  className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                    workModel === wm
                      ? wm === "remote"
                        ? "bg-sky-100 text-sky-700 ring-1 ring-sky-300"
                        : wm === "hybrid"
                          ? "bg-violet-100 text-violet-700 ring-1 ring-violet-300"
                          : "bg-slate-100 text-slate-700 ring-1 ring-slate-300"
                      : "bg-muted text-muted-foreground hover:bg-muted/80"
                  }`}
                >
                  {wm === "onsite" ? "On-site" : wm.charAt(0).toUpperCase() + wm.slice(1)}
                </button>
              ))}
            </div>
          </div>

          {/* Locations */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1.5 block">
              Locations
            </label>
            <div className="space-y-1.5">
              {locations.length === 0 && (
                <p className="text-xs text-muted-foreground italic">No locations</p>
              )}
              {locations.map((loc, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between rounded-md bg-slate-50 border border-slate-200 px-2.5 py-1.5"
                >
                  <span className="text-sm">{loc.display_name}</span>
                  <button
                    onClick={() => removeLocation(i)}
                    className="text-muted-foreground hover:text-red-500 p-0.5"
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
              <div className="flex gap-1.5">
                <Input
                  placeholder="Add location (e.g. Austin, TX)"
                  value={newLocation}
                  onChange={(e) => setNewLocation(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addLocation();
                    }
                  }}
                  className="h-7 text-xs flex-1"
                />
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 px-2"
                  onClick={addLocation}
                  disabled={!newLocation.trim()}
                >
                  <Plus size={12} />
                </Button>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t bg-muted/30">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            className="h-7 text-xs"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
            Save
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
