"use client";

import { Plus, Trash2, ArrowUp, ArrowDown } from "lucide-react";
import type { ProfileWorkHistory } from "@/lib/types";

interface WorkHistorySectionProps {
  data: ProfileWorkHistory[];
  onChange: (data: ProfileWorkHistory[]) => void;
}

/** Slug-like key derived from an employer name. Used to link accomplishments
 * and bullets to work history entries without depending on hand-maintained
 * `key:` fields in profile YAML (which are often missing). */
export function employerKey(name: string): string {
  return (name || "")
    .toLowerCase()
    .replace(/^the\s+/, "")
    .replace(/[^\w\s]/g, " ")
    .trim()
    .replace(/\s+/g, "_");
}

export function WorkHistorySection({ data, onChange }: WorkHistorySectionProps) {
  const update = (index: number, field: keyof ProfileWorkHistory, value: string) => {
    const updated = [...data];
    updated[index] = { ...updated[index], [field]: value };
    // Auto-generate key on employer name change
    if (field === "employer") {
      updated[index].key = employerKey(value);
    }
    onChange(updated);
  };

  const add = () => {
    onChange([
      ...data,
      { employer: "", title: "", start: "", end: "", location: "", key: "" },
    ]);
  };

  const remove = (index: number) => {
    onChange(data.filter((_, i) => i !== index));
  };

  const move = (index: number, direction: -1 | 1) => {
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= data.length) return;
    const updated = [...data];
    [updated[index], updated[newIndex]] = [updated[newIndex], updated[index]];
    onChange(updated);
  };

  return (
    <div className="space-y-3">
      {data.map((wh, i) => (
        <div key={i} className="border rounded p-3 space-y-2 bg-gray-50">
          <div className="flex items-start justify-between">
            <div className="grid grid-cols-2 gap-2 flex-1">
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  Employer
                </label>
                <input
                  type="text"
                  value={wh.employer ?? ""}
                  onChange={(e) => update(i, "employer", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="Company Name"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  Title
                </label>
                <input
                  type="text"
                  value={wh.title ?? ""}
                  onChange={(e) => update(i, "title", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="Job Title"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  Start
                </label>
                <input
                  type="text"
                  value={wh.start ?? ""}
                  onChange={(e) => update(i, "start", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="2022"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  End
                </label>
                <input
                  type="text"
                  value={wh.end || ""}
                  onChange={(e) => update(i, "end", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="Present"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  Location
                </label>
                <input
                  type="text"
                  value={wh.location || ""}
                  onChange={(e) => update(i, "location", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="City, State"
                />
              </div>
            </div>
            <div className="flex flex-col items-center gap-1 ml-2">
              <button
                type="button"
                onClick={() => move(i, -1)}
                disabled={i === 0}
                className="text-gray-400 hover:text-gray-600 disabled:opacity-30 p-0.5"
              >
                <ArrowUp className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => remove(i)}
                className="text-gray-400 hover:text-red-500 p-0.5"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => move(i, 1)}
                disabled={i === data.length - 1}
                className="text-gray-400 hover:text-gray-600 disabled:opacity-30 p-0.5"
              >
                <ArrowDown className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          {wh.key && (
            <div className="text-[10px] text-gray-400">
              Key: <code>{wh.key}</code>
            </div>
          )}
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
      >
        <Plus className="h-3 w-3" /> Add employer
      </button>
    </div>
  );
}
