"use client";

import { useState } from "react";
import { Loader2, Plus, Wand2, X } from "lucide-react";
import { suggestProfileSection } from "@/lib/api";

interface DomainsSectionProps {
  data: string[];
  onChange: (data: string[]) => void;
}

export function DomainsSection({ data, onChange }: DomainsSectionProps) {
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const add = () => {
    const trimmed = input.trim();
    if (trimmed && !data.includes(trimmed)) {
      onChange([...data, trimmed]);
      setInput("");
    }
  };

  const remove = (index: number) => {
    onChange(data.filter((_, i) => i !== index));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      add();
    }
  };

  const populate = async () => {
    setLoading(true);
    try {
      const result = await suggestProfileSection("domains");
      const suggested = result.domains;
      if (Array.isArray(suggested) && suggested.length > 0) {
        onChange(suggested as string[]);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {data.map((domain, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 bg-blue-50 text-blue-700 text-xs px-2 py-1 rounded-full"
          >
            {domain}
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
          onKeyDown={handleKeyDown}
          className="flex-1 rounded border px-2.5 py-1.5 text-sm"
          placeholder="Add domain..."
        />
        <button
          type="button"
          onClick={add}
          disabled={!input.trim()}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 px-2 disabled:opacity-50"
        >
          <Plus className="h-3 w-3" /> Add
        </button>
        <button
          type="button"
          onClick={populate}
          disabled={loading}
          className="flex items-center gap-1 text-xs text-purple-600 hover:text-purple-800 px-2 disabled:opacity-50"
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
