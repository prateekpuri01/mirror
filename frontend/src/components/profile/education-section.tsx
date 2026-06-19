"use client";

import { Plus, Trash2 } from "lucide-react";
import type { ProfileEducation } from "@/lib/types";

interface EducationSectionProps {
  data: ProfileEducation[];
  onChange: (data: ProfileEducation[]) => void;
}

export function EducationSection({ data, onChange }: EducationSectionProps) {
  const update = (index: number, field: keyof ProfileEducation, value: string) => {
    const updated = [...data];
    updated[index] = { ...updated[index], [field]: value };
    onChange(updated);
  };

  const add = () => {
    onChange([
      ...data,
      { degree: "", field: "", institution: "", year: "", honors: "" },
    ]);
  };

  const remove = (index: number) => {
    onChange(data.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-3">
      {data.map((edu, i) => (
        <div key={i} className="border rounded p-3 space-y-2 bg-gray-50">
          <div className="flex items-start justify-between">
            <div className="grid grid-cols-2 gap-2 flex-1">
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  Degree
                </label>
                <input
                  type="text"
                  value={edu.degree ?? ""}
                  onChange={(e) => update(i, "degree", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="Ph.D."
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  Field
                </label>
                <input
                  type="text"
                  value={edu.field ?? ""}
                  onChange={(e) => update(i, "field", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="Computer Science"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  Institution
                </label>
                <input
                  type="text"
                  value={edu.institution ?? ""}
                  onChange={(e) => update(i, "institution", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="MIT"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  Year
                </label>
                <input
                  type="text"
                  value={edu.year ?? ""}
                  onChange={(e) => update(i, "year", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="2020"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-0.5">
                  Honors
                </label>
                <input
                  type="text"
                  value={edu.honors || ""}
                  onChange={(e) => update(i, "honors", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm"
                  placeholder="Cum Laude (optional)"
                />
              </div>
            </div>
            <button
              type="button"
              onClick={() => remove(i)}
              className="text-gray-400 hover:text-red-500 p-1 ml-2"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
      >
        <Plus className="h-3 w-3" /> Add education
      </button>
    </div>
  );
}
