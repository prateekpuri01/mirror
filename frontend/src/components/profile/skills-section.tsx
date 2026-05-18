"use client";

import { useState } from "react";
import { Loader2, Plus, Wand2, X, Pencil, Trash2 } from "lucide-react";
import type { ProfileSkills } from "@/lib/types";
import { suggestProfileSection } from "@/lib/api";

const DEFAULT_CATEGORIES = ["technical", "communication", "tools"];

interface SkillsSectionProps {
  data: ProfileSkills;
  onChange: (data: ProfileSkills) => void;
}

function SkillGroup({
  label,
  skills,
  onChange,
  onRename,
  onDelete,
  canDelete,
}: {
  label: string;
  skills: string[];
  onChange: (skills: string[]) => void;
  onRename: (newName: string) => void;
  onDelete: () => void;
  canDelete: boolean;
}) {
  const [input, setInput] = useState("");
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(label);

  const add = () => {
    const trimmed = input.trim();
    if (trimmed && !skills.includes(trimmed)) {
      onChange([...skills, trimmed]);
      setInput("");
    }
  };

  const remove = (index: number) => {
    onChange(skills.filter((_, i) => i !== index));
  };

  const handleRename = () => {
    const trimmed = editName.trim();
    if (trimmed && trimmed !== label) {
      onRename(trimmed);
    }
    setEditing(false);
  };

  const displayLabel = label.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1">
        {editing ? (
          <input
            type="text"
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            onBlur={handleRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleRename();
              if (e.key === "Escape") { setEditing(false); setEditName(label); }
            }}
            className="text-xs font-medium text-gray-600 border-b border-blue-400 outline-none bg-transparent px-0 py-0"
            autoFocus
          />
        ) : (
          <label className="text-xs font-medium text-gray-600">{displayLabel}</label>
        )}
        <button
          type="button"
          onClick={() => { setEditName(label); setEditing(true); }}
          className="text-gray-300 hover:text-gray-500 p-0.5"
          title="Rename category"
        >
          <Pencil className="h-2.5 w-2.5" />
        </button>
        {canDelete && (
          <button
            type="button"
            onClick={onDelete}
            className="text-gray-300 hover:text-red-500 p-0.5"
            title="Delete category"
          >
            <Trash2 className="h-2.5 w-2.5" />
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 mb-1.5">
        {skills.map((skill, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 bg-gray-100 text-gray-700 text-xs px-2 py-0.5 rounded-full"
          >
            {skill}
            <button
              type="button"
              onClick={() => remove(i)}
              className="hover:text-red-500"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          className="flex-1 rounded border px-2.5 py-1.5 text-sm"
          placeholder={`Add ${displayLabel.toLowerCase()} skill...`}
        />
        <button
          type="button"
          onClick={add}
          disabled={!input.trim()}
          className="text-xs text-blue-600 hover:text-blue-800 px-2 disabled:opacity-50"
        >
          <Plus className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
}

export function SkillsSection({ data, onChange }: SkillsSectionProps) {
  const [loading, setLoading] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState("");

  // Ensure data has at least the default categories
  const categories = Object.keys(data).length > 0
    ? Object.keys(data)
    : DEFAULT_CATEGORIES;

  const populate = async () => {
    setLoading(true);
    try {
      const result = await suggestProfileSection("skills");
      // Merge AI suggestions into existing categories
      const updated = { ...data };
      for (const [key, value] of Object.entries(result)) {
        if (Array.isArray(value)) {
          updated[key] = value as string[];
        }
      }
      onChange(updated);
    } finally {
      setLoading(false);
    }
  };

  const handleRename = (oldKey: string, newKey: string) => {
    const normalized = newKey.toLowerCase().replace(/\s+/g, "_");
    if (normalized === oldKey || normalized in data) return;
    const updated: ProfileSkills = {};
    for (const [key, value] of Object.entries(data)) {
      if (key === oldKey) {
        updated[normalized] = value;
      } else {
        updated[key] = value;
      }
    }
    onChange(updated);
  };

  const handleDelete = (key: string) => {
    const updated = { ...data };
    delete updated[key];
    onChange(updated);
  };

  const handleAddCategory = () => {
    const name = newCategoryName.trim();
    if (!name) return;
    const key = name.toLowerCase().replace(/\s+/g, "_");
    if (key in data) return;
    onChange({ ...data, [key]: [] });
    setNewCategoryName("");
  };

  return (
    <div className="space-y-4">
      {categories.map((key) => (
        <SkillGroup
          key={key}
          label={key}
          skills={data[key] || []}
          onChange={(skills) => onChange({ ...data, [key]: skills })}
          onRename={(newName) => handleRename(key, newName)}
          onDelete={() => handleDelete(key)}
          canDelete={categories.length > 1}
        />
      ))}

      {/* Add new category */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={newCategoryName}
          onChange={(e) => setNewCategoryName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleAddCategory();
            }
          }}
          className="w-40 rounded border px-2 py-1 text-xs"
          placeholder="New category name..."
        />
        <button
          type="button"
          onClick={handleAddCategory}
          disabled={!newCategoryName.trim()}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 disabled:opacity-50"
        >
          <Plus className="h-3 w-3" /> Add category
        </button>
      </div>

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
  );
}
