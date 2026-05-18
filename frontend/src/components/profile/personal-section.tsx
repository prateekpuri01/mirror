"use client";

import { Plus, Trash2 } from "lucide-react";
import type { ProfilePersonal, ProfileLink } from "@/lib/types";

interface PersonalSectionProps {
  data: ProfilePersonal;
  onChange: (data: ProfilePersonal) => void;
}

export function PersonalSection({ data, onChange }: PersonalSectionProps) {
  const update = (field: keyof ProfilePersonal, value: string | boolean) => {
    onChange({ ...data, [field]: value });
  };

  const links = data.professional_links || [];

  const updateLink = (index: number, field: keyof ProfileLink, value: string) => {
    const updated = [...links];
    updated[index] = { ...updated[index], [field]: value };
    onChange({ ...data, professional_links: updated });
  };

  const addLink = () => {
    onChange({
      ...data,
      professional_links: [...links, { url: "", label: "" }],
    });
  };

  const removeLink = (index: number) => {
    onChange({
      ...data,
      professional_links: links.filter((_, i) => i !== index),
    });
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Name</label>
          <input
            type="text"
            value={data.name || ""}
            onChange={(e) => update("name", e.target.value)}
            className="w-full rounded border px-2.5 py-1.5 text-sm"
            placeholder="Full name"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Email</label>
          <input
            type="email"
            value={data.email || ""}
            onChange={(e) => update("email", e.target.value)}
            className="w-full rounded border px-2.5 py-1.5 text-sm"
            placeholder="email@example.com"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Phone</label>
          <input
            type="tel"
            value={data.phone || ""}
            onChange={(e) => update("phone", e.target.value)}
            className="w-full rounded border px-2.5 py-1.5 text-sm"
            placeholder="(555) 123-4567"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Location</label>
          <input
            type="text"
            value={data.location || ""}
            onChange={(e) => update("location", e.target.value)}
            className="w-full rounded border px-2.5 py-1.5 text-sm"
            placeholder="City, State"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Remote Preference</label>
          <select
            value={data.remote_preference || "flexible"}
            onChange={(e) => update("remote_preference", e.target.value)}
            className="w-full rounded border px-2.5 py-1.5 text-sm bg-white"
          >
            <option value="remote">Remote</option>
            <option value="hybrid">Hybrid</option>
            <option value="onsite">Onsite</option>
            <option value="flexible">Flexible</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">LinkedIn URL</label>
          <input
            type="url"
            value={data.linkedin || ""}
            onChange={(e) => update("linkedin", e.target.value)}
            className="w-full rounded border px-2.5 py-1.5 text-sm"
            placeholder="linkedin.com/in/..."
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Google Scholar URL</label>
          <input
            type="url"
            value={data.google_scholar || ""}
            onChange={(e) => update("google_scholar", e.target.value)}
            className="w-full rounded border px-2.5 py-1.5 text-sm"
            placeholder="scholar.google.com/..."
          />
        </div>
      </div>

      {/* Professional links for resume header */}
      <div>
        <label className="block text-xs font-medium text-gray-600 mb-1">
          Resume Header Links
        </label>
        <p className="text-[10px] text-gray-400 mb-2">
          These appear on the second line of your resume header as clickable
          hyperlinks. Google Scholar is included automatically if set above.
        </p>
        <div className="space-y-1.5">
          {links.map((link, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                type="text"
                value={link.label}
                onChange={(e) => updateLink(i, "label", e.target.value)}
                className="w-36 rounded border px-2 py-1 text-sm"
                placeholder="Display text"
              />
              <input
                type="url"
                value={link.url}
                onChange={(e) => updateLink(i, "url", e.target.value)}
                className="flex-1 rounded border px-2 py-1 text-sm"
                placeholder="https://..."
              />
              <button
                type="button"
                onClick={() => removeLink(i)}
                className="text-gray-400 hover:text-red-500 p-0.5"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={addLink}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 mt-1.5"
        >
          <Plus className="h-3 w-3" /> Add link
        </button>
      </div>
    </div>
  );
}
