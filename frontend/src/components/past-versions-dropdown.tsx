"use client";

import { useEffect, useState } from "react";
import { fetchSectionHistory, type SectionHistoryItem } from "@/lib/api";

interface PastVersionsDropdownProps {
  docId: string;
  entityType:
    | "research_description"
    | "experience_bullets_set"
    | "skill_bucket"
    | "summary"
    | "tagline";
  entityKey: string;
  /**
   * Called with the value to apply at ``applyPath``. The component does not
   * call PATCH itself — the parent decides how to apply (so updates flow
   * through the same handler that handles inline edits, and the existing
   * "recently updated" highlight + content_memory upsert kick in for free).
   */
  applyPath: string;
  onApply: (path: string, value: unknown) => void;
  className?: string;
}

function formatLabel(item: SectionHistoryItem): string {
  const company = item.company_name?.trim();
  const title = item.job_title?.trim();
  if (company && title) return `${company} — ${title}`;
  return company || title || "Past version";
}

/**
 * "Past versions ▼" picker — fetches content_memory rows for the given entity
 * and applies the chosen one back into the resume. Hidden entirely when the
 * server returns no past versions, so the editor stays uncluttered for
 * cold-start cases.
 */
export function PastVersionsDropdown({
  docId,
  entityType,
  entityKey,
  applyPath,
  onApply,
  className = "",
}: PastVersionsDropdownProps) {
  const [items, setItems] = useState<SectionHistoryItem[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchSectionHistory(docId, entityType, entityKey)
      .then((res) => {
        if (!cancelled) setItems(res.items);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [docId, entityType, entityKey]);

  if (loading || !items || items.length === 0) return null;

  return (
    <select
      className={`text-[10px] text-gray-400 bg-transparent border border-dashed border-gray-300 rounded px-1 py-0.5 cursor-pointer hover:border-gray-400 hover:text-gray-600 focus:outline-none focus:ring-1 focus:ring-amber-300 max-w-[260px] truncate ${className}`}
      value=""
      title="Apply a past hand-tuned version of this section"
      onChange={(e) => {
        const id = e.target.value;
        if (!id) return;
        const chosen = items.find((it) => it.id === id);
        if (!chosen) return;
        // Bullet sets land in user_payload_json; everything else in user_text.
        const payload =
          entityType === "experience_bullets_set"
            ? chosen.user_payload_json
            : chosen.user_text;
        if (payload === null || payload === undefined) return;
        onApply(applyPath, payload);
        e.target.value = "";
      }}
    >
      <option value="">Past versions ({items.length})…</option>
      {items.map((it) => (
        <option key={it.id} value={it.id} title={it.preview}>
          {formatLabel(it)}
        </option>
      ))}
    </select>
  );
}
