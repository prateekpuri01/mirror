"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import { Plus, Trash2, GripVertical, Loader2, Sparkles } from "lucide-react";
import type { ProfileSearchPreferences } from "@/lib/types";
import { generateKeywords, suggestProfileSection } from "@/lib/api";

interface SearchPreferencesSectionProps {
  data: ProfileSearchPreferences;
  onChange: (data: ProfileSearchPreferences) => void;
}

function EditableList({
  label,
  description,
  items,
  placeholder,
  onChange,
}: {
  label: string;
  description?: string;
  items: string[];
  placeholder: string;
  onChange: (items: string[]) => void;
}) {
  const [input, setInput] = useState("");

  const add = () => {
    const trimmed = input.trim();
    if (trimmed) {
      onChange([...items, trimmed]);
      setInput("");
    }
  };

  const remove = (index: number) => {
    onChange(items.filter((_, i) => i !== index));
  };

  const update = (index: number, value: string) => {
    const updated = [...items];
    updated[index] = value;
    onChange(updated);
  };

  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-0.5">{label}</label>
      {description && (
        <p className="text-[11px] text-gray-400 mb-1.5">{description}</p>
      )}
      <div className="space-y-1">
        {items.map((item, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <GripVertical className="h-3 w-3 text-gray-300 shrink-0" />
            <input
              type="text"
              value={item}
              onChange={(e) => update(i, e.target.value)}
              className="flex-1 rounded border px-2 py-1 text-sm"
            />
            <button
              type="button"
              onClick={() => remove(i)}
              className="text-gray-400 hover:text-red-500 p-0.5"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        ))}
      </div>
      <div className="flex gap-2 mt-1.5">
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
          className="flex-1 rounded border px-2 py-1 text-sm"
          placeholder={placeholder}
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

export function SearchPreferencesSection({
  data,
  onChange,
}: SearchPreferencesSectionProps) {
  const [generating, setGenerating] = useState(false);
  const [autoSuggesting, setAutoSuggesting] = useState(false);
  // Tracks whether we've already attempted an auto-suggest in this mount —
  // we only run it once per session to avoid re-firing if the user manually
  // clears both fields. Cleared fields are a valid state we should respect.
  const autoTriedRef = useRef(false);

  // Migrate legacy fields on first render if new fields absent
  const migrated = useMemo(() => {
    if (data.looking_for !== undefined) return data;

    const hasLegacy =
      data.industries_ranked?.length ||
      data.nice_to_haves?.length ||
      data.deal_breakers?.length ||
      data.org_anti_patterns?.length;

    if (!hasLegacy) return data;

    const lookingParts = [
      ...(data.industries_ranked || []),
      ...(data.nice_to_haves || []),
    ];
    const notLookingParts = [
      ...(data.deal_breakers || []),
      ...(data.org_anti_patterns || []),
    ];

    return {
      ...data,
      looking_for: lookingParts.join("\n"),
      not_looking_for: notLookingParts.join("\n"),
    };
  }, [data]);

  // If migration produced new data, trigger save via onChange on first render
  if (migrated !== data && migrated.looking_for !== undefined) {
    // Schedule to avoid setState-during-render
    setTimeout(() => onChange(migrated), 0);
  }

  const current = migrated;

  // Auto-suggest "looking_for" / "not_looking_for" on first mount when both
  // fields are empty. Fires once per session — if the user clears both, we
  // don't re-suggest (the cleared state is intentional, not absence of data).
  useEffect(() => {
    if (autoTriedRef.current) return;
    if (current.looking_for?.trim() || current.not_looking_for?.trim()) return;
    autoTriedRef.current = true;

    let cancelled = false;
    (async () => {
      setAutoSuggesting(true);
      try {
        const result = (await suggestProfileSection("looking_for")) as {
          looking_for?: string;
          not_looking_for?: string;
          error?: string;
        };
        if (cancelled) return;
        if (result?.error) return;
        if (result?.looking_for || result?.not_looking_for) {
          onChange({
            ...current,
            looking_for: result.looking_for || "",
            not_looking_for: result.not_looking_for || "",
            looking_for_ai_generated: !!result.looking_for,
            not_looking_for_ai_generated: !!result.not_looking_for,
          });
        }
      } catch {
        // Suggestion failed silently — user can still write their own.
      } finally {
        if (!cancelled) setAutoSuggesting(false);
      }
    })();

    return () => {
      cancelled = true;
    };
    // Intentional: fire only once on mount; we gate on autoTriedRef above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleGenerateKeywords = async () => {
    setGenerating(true);
    try {
      const result = await generateKeywords(
        current.looking_for || "",
        current.not_looking_for || "",
      );
      onChange({
        ...current,
        positive_signals: result.positive_signals,
        exclusions: result.exclusions,
      });
    } catch (err) {
      // Keyword generation failed — error not surfaced to user (could add toast)
    } finally {
      setGenerating(false);
    }
  };

  const canGenerate = !!(current.looking_for?.trim() || current.not_looking_for?.trim());

  return (
    <div className="space-y-5">
      {autoSuggesting && (
        <div className="flex items-center gap-2 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700">
          <Loader2 className="h-3 w-3 animate-spin" />
          AI is drafting your &ldquo;what I&apos;m looking for&rdquo; based on your background — should take ~15 sec
        </div>
      )}
      <div>
        <label className="text-xs font-medium text-gray-600 mb-0.5 flex items-center gap-1.5">
          <span>What I&apos;m Looking For</span>
          {current.looking_for_ai_generated && (
            <span className="inline-flex items-center gap-0.5 text-[9px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded font-semibold">
              <Sparkles className="h-2.5 w-2.5" />
              AI-drafted
            </span>
          )}
        </label>
        <p className="text-[11px] text-gray-400 mb-1.5">
          Describe the kind of role, org, and work that excites you. This goes
          directly to the AI when scoring jobs and searching for companies.
        </p>
        <textarea
          value={current.looking_for || ""}
          onChange={(e) =>
            onChange({
              ...current,
              looking_for: e.target.value,
              // Any user edit clears the AI flag for this field.
              looking_for_ai_generated: false,
            })
          }
          disabled={autoSuggesting}
          className="w-full rounded border px-2 py-1.5 text-sm min-h-[140px] resize-y disabled:bg-gray-50 disabled:text-gray-400"
          placeholder={
            autoSuggesting
              ? "Drafting from your profile…"
              : "e.g., Research-heavy roles at mission-driven orgs working on AI safety, computational social science, or public interest tech..."
          }
        />
      </div>

      <div>
        <label className="text-xs font-medium text-gray-600 mb-0.5 flex items-center gap-1.5">
          <span>What I&apos;m NOT Looking For</span>
          {current.not_looking_for_ai_generated && (
            <span className="inline-flex items-center gap-0.5 text-[9px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded font-semibold">
              <Sparkles className="h-2.5 w-2.5" />
              AI-drafted
            </span>
          )}
        </label>
        <p className="text-[11px] text-gray-400 mb-1.5">
          Describe what to avoid. The AI uses this to filter out bad matches and
          penalize jobs that slip through.
        </p>
        <textarea
          value={current.not_looking_for || ""}
          onChange={(e) =>
            onChange({
              ...current,
              not_looking_for: e.target.value,
              not_looking_for_ai_generated: false,
            })
          }
          disabled={autoSuggesting}
          className="w-full rounded border px-2 py-1.5 text-sm min-h-[140px] resize-y disabled:bg-gray-50 disabled:text-gray-400"
          placeholder={
            autoSuggesting
              ? "Drafting from your profile…"
              : "e.g., Defense contractors, surveillance tech, companies with no remote flexibility, pure frontend roles..."
          }
        />
      </div>

      <div className="flex items-center gap-3 py-1">
        <button
          type="button"
          onClick={handleGenerateKeywords}
          disabled={!canGenerate || generating}
          className="inline-flex items-center gap-1.5 rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {generating ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Sparkles className="h-3 w-3" />
          )}
          Generate Keywords
        </button>
        <span className="text-[11px] text-gray-400">
          Auto-extract keywords from your descriptions for bulk filtering
        </span>
      </div>

      <EditableList
        label="Positive Signals"
        description="Auto-generated from your description. These boost keyword-matched jobs in bulk scoring."
        items={current.positive_signals || []}
        placeholder="Add keyword..."
        onChange={(items) => onChange({ ...current, positive_signals: items })}
      />

      <EditableList
        label="Exclusions"
        description="Auto-generated from your avoidances. Jobs matching these are filtered out or penalized."
        items={current.exclusions || []}
        placeholder="Add exclusion..."
        onChange={(items) => onChange({ ...current, exclusions: items })}
      />

      <div>
        <label className="block text-xs font-medium text-gray-600 mb-0.5">
          Minimum Salary
        </label>
        <p className="text-[11px] text-gray-400 mb-1.5">
          Your minimum acceptable annual salary (USD). Used by the AI to evaluate
          salary competitiveness when scoring jobs. Leave blank to skip.
        </p>
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-500">$</span>
          <input
            type="number"
            value={current.salary_minimum ?? ""}
            onChange={(e) => {
              const val = e.target.value;
              onChange({
                ...current,
                salary_minimum: val === "" ? null : Number(val),
              });
            }}
            className="w-32 rounded border px-2 py-1 text-sm"
            placeholder="150000"
            min={0}
            step={5000}
          />
          <span className="text-xs text-gray-400">per year</span>
        </div>
      </div>

    </div>
  );
}
