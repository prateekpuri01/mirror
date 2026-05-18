"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";

interface AwardsSectionProps {
  data: string[];
  onChange: (data: string[]) => void;
}

export function AwardsSection({ data, onChange }: AwardsSectionProps) {
  const [input, setInput] = useState("");

  const add = () => {
    const trimmed = input.trim();
    if (trimmed) {
      onChange([...data, trimmed]);
      setInput("");
    }
  };

  const remove = (index: number) => {
    onChange(data.filter((_, i) => i !== index));
  };

  const update = (index: number, value: string) => {
    const updated = [...data];
    updated[index] = value;
    onChange(updated);
  };

  return (
    <div className="space-y-1.5">
      {data.map((award, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type="text"
            value={award}
            onChange={(e) => update(i, e.target.value)}
            className="flex-1 rounded border px-2.5 py-1.5 text-sm"
          />
          <button
            type="button"
            onClick={() => remove(i)}
            className="text-gray-400 hover:text-red-500 p-1"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
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
          placeholder="Add award..."
        />
        <button
          type="button"
          onClick={add}
          disabled={!input.trim()}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 px-2 disabled:opacity-50"
        >
          <Plus className="h-3 w-3" /> Add
        </button>
      </div>
    </div>
  );
}
