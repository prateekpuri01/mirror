"use client";

import { useState } from "react";
import { Loader2, Plus, Trash2, Wand2 } from "lucide-react";
import type { ProfileTargetRole } from "@/lib/types";
import { suggestProfileSection } from "@/lib/api";

interface TargetRolesSectionProps {
  data: ProfileTargetRole[];
  onChange: (data: ProfileTargetRole[]) => void;
}

export function TargetRolesSection({ data, onChange }: TargetRolesSectionProps) {
  const [loading, setLoading] = useState(false);

  const update = (index: number, field: keyof ProfileTargetRole, value: string) => {
    const updated = [...data];
    updated[index] = { ...updated[index], [field]: value };
    onChange(updated);
  };

  const add = () => {
    onChange([...data, { title: "", seniority: "senior" }]);
  };

  const remove = (index: number) => {
    onChange(data.filter((_, i) => i !== index));
  };

  const populate = async () => {
    setLoading(true);
    try {
      const result = await suggestProfileSection("target_roles");
      const roles = result.target_roles;
      if (Array.isArray(roles) && roles.length > 0) {
        onChange(roles as ProfileTargetRole[]);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-2">
      {data.map((role, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type="text"
            value={role.title}
            onChange={(e) => update(i, "title", e.target.value)}
            className="flex-1 rounded border px-2.5 py-1.5 text-sm"
            placeholder="Role title"
          />
          <select
            value={role.seniority || ""}
            onChange={(e) => update(i, "seniority", e.target.value)}
            className="rounded border px-2.5 py-1.5 text-sm bg-white w-32"
          >
            <option value="">Any level</option>
            <option value="junior">Junior</option>
            <option value="mid">Mid</option>
            <option value="senior">Senior</option>
            <option value="staff">Staff</option>
            <option value="principal">Principal</option>
            <option value="lead">Lead</option>
            <option value="director">Director</option>
          </select>
          <button
            type="button"
            onClick={() => remove(i)}
            className="text-gray-400 hover:text-red-500 p-1"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <div className="flex items-center gap-3 mt-1">
        <button
          type="button"
          onClick={add}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
        >
          <Plus className="h-3 w-3" /> Add role
        </button>
        <button
          type="button"
          onClick={populate}
          disabled={loading}
          className="flex items-center gap-1 text-xs text-purple-600 hover:text-purple-800 disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Wand2 className="h-3 w-3" />
          )}
          {loading ? "Generating..." : "Populate with AI"}
        </button>
      </div>
    </div>
  );
}
