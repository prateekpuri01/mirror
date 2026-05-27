"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus, Trash2, Wand2 } from "lucide-react";
import {
  consolidateWritingMemory,
  createWritingMemoryRule,
  deleteWritingMemoryRule,
  listWritingMemoryRules,
  updateWritingMemoryRule,
  type WritingMemoryCategory,
  type WritingMemoryConsolidateReport,
  type WritingMemoryRule,
} from "@/lib/api";

const CATEGORIES: { value: WritingMemoryCategory; label: string }[] = [
  { value: "word_choice", label: "Word choice" },
  { value: "tone", label: "Tone" },
  { value: "structure", label: "Structure" },
  { value: "content", label: "Content" },
  { value: "formatting", label: "Formatting" },
];


/**
 * Writing Style — surfaces the abstract style rules learned from past edits
 * (the "writing_memory" layer) so the user can audit, edit, delete, and add.
 *
 * Only shows ACTIVE UNIVERSAL rules — those are the ones actually injected
 * into generation prompts. Job-specific rules and decayed/deactivated rules
 * are hidden because they're not load-bearing for new resumes.
 */
export function WritingStyleSection() {
  const [rules, setRules] = useState<WritingMemoryRule[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ rule_text: string; category: WritingMemoryCategory }>({
    rule_text: "",
    category: "word_choice",
  });
  const [consolidateReport, setConsolidateReport] = useState<WritingMemoryConsolidateReport | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listWritingMemoryRules({
        domain: "resume",
        scope: "universal",
        isActive: true,
      });
      setRules(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load rules");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleEditField = async (
    rule: WritingMemoryRule,
    field: "rule_text" | "category",
    value: string,
  ) => {
    if (rule[field] === value) return;
    setBusy(rule.id);
    setError(null);
    try {
      const updated = await updateWritingMemoryRule(rule.id, { [field]: value } as never);
      setRules((prev) => prev?.map((r) => (r.id === rule.id ? updated : r)) ?? prev);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setBusy(null);
    }
  };

  const handleDelete = async (rule: WritingMemoryRule) => {
    setBusy(rule.id);
    setError(null);
    try {
      await deleteWritingMemoryRule(rule.id);
      setRules((prev) => prev?.filter((r) => r.id !== rule.id) ?? prev);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete");
    } finally {
      setBusy(null);
    }
  };

  const handleAdd = async () => {
    const text = draft.rule_text.trim();
    if (text.length < 3) return;
    setBusy("__new__");
    setError(null);
    try {
      const created = await createWritingMemoryRule({
        domain: "resume",
        rule_text: text,
        category: draft.category,
        scope: "universal",
      });
      setRules((prev) => [created, ...(prev ?? [])]);
      setDraft({ rule_text: "", category: draft.category });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create rule");
    } finally {
      setBusy(null);
    }
  };

  const handleConsolidate = async () => {
    setBusy("__consolidate__");
    setError(null);
    setConsolidateReport(null);
    try {
      const report = await consolidateWritingMemory("resume");
      setConsolidateReport(report);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to consolidate");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4 text-sm">
      <div className="text-xs text-muted-foreground leading-relaxed">
        These are the writing-style rules learned from your past resume edits.
        They get injected into every resume generation as soft style preferences.
        Edit, delete, or add new ones — the agent will follow them on the next run.
      </div>

      {error && (
        <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          {error}
        </div>
      )}

      {/* Add new rule */}
      <div className="rounded border border-dashed border-gray-300 px-3 py-2.5 space-y-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          Add a new rule
        </div>
        <div className="flex gap-2 items-start">
          <textarea
            className="flex-1 border border-gray-300 rounded px-2 py-1.5 text-xs resize-none focus:outline-none focus:ring-2 focus:ring-blue-300"
            rows={2}
            placeholder='e.g. "Avoid the word \"leveraged\" — use \"used\" or \"applied\" instead."'
            value={draft.rule_text ?? ""}
            onChange={(e) => setDraft({ ...draft, rule_text: e.target.value })}
            disabled={busy === "__new__"}
          />
          <div className="flex flex-col gap-1.5 items-stretch">
            <select
              className="border border-gray-300 rounded px-2 py-1 text-xs bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
              value={draft.category ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, category: e.target.value as WritingMemoryCategory })
              }
              disabled={busy === "__new__"}
            >
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
            <button
              className="inline-flex items-center justify-center gap-1 rounded bg-blue-600 text-white text-xs px-2 py-1 hover:bg-blue-700 disabled:opacity-50"
              onClick={handleAdd}
              disabled={busy === "__new__" || draft.rule_text.trim().length < 3}
            >
              {busy === "__new__" ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Plus className="h-3 w-3" />
              )}
              Add
            </button>
          </div>
        </div>
      </div>

      {/* Existing rules */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {loading ? "Loading…" : `${rules?.length ?? 0} active rule${(rules?.length ?? 0) === 1 ? "" : "s"}`}
          </div>
          <button
            className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground border border-gray-200 rounded px-2 py-0.5 disabled:opacity-50"
            onClick={handleConsolidate}
            disabled={busy === "__consolidate__" || (rules?.length ?? 0) < 2}
            title="LLM-merge near-duplicate rules"
          >
            {busy === "__consolidate__" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Wand2 className="h-3 w-3" />
            )}
            Consolidate
          </button>
        </div>

        {consolidateReport && (
          <div className="text-[11px] bg-emerald-50 border border-emerald-200 rounded px-2 py-1.5 space-y-1.5">
            {consolidateReport.groups_merged === 0 ? (
              <div className="text-emerald-700">
                Nothing worth merging — your rules already look distinct.
              </div>
            ) : (
              <>
                <div className="text-emerald-800 font-medium">
                  Distilled {consolidateReport.before} rules into {consolidateReport.after}
                  {" "}({consolidateReport.merged_count} merged into {consolidateReport.groups_merged}{" "}
                  rule{consolidateReport.groups_merged === 1 ? "" : "s"}).
                </div>
                <button
                  className="text-[10px] text-emerald-700 hover:text-emerald-900 underline"
                  onClick={() => setConsolidateReport(null)}
                >
                  dismiss
                </button>
                <div className="space-y-1.5 pt-1">
                  {consolidateReport.merges.map((m, i) => (
                    <div key={i} className="bg-white border border-emerald-100 rounded px-2 py-1">
                      <div className="text-emerald-900 font-medium">→ {m.merged_rule_text}</div>
                      {m.reasoning && (
                        <div className="text-[10px] text-emerald-700/80 italic mt-0.5">
                          {m.reasoning}
                        </div>
                      )}
                      <div className="mt-1 space-y-0.5">
                        {m.merged_from.map((src, j) => (
                          <div key={j} className="text-[10px] text-muted-foreground line-through">
                            {src}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {!loading && rules && rules.length === 0 && (
          <div className="text-xs text-muted-foreground italic px-1">
            No active rules yet. Edit a few resumes and the agent will start
            extracting your style preferences automatically — or add one above.
          </div>
        )}

        {rules?.map((rule) => (
          <RuleRow
            key={rule.id}
            rule={rule}
            isBusy={busy === rule.id}
            onEditText={(value) => handleEditField(rule, "rule_text", value)}
            onEditCategory={(value) => handleEditField(rule, "category", value)}
            onDelete={() => handleDelete(rule)}
          />
        ))}
      </div>
    </div>
  );
}


function RuleRow({
  rule,
  isBusy,
  onEditText,
  onEditCategory,
  onDelete,
}: {
  rule: WritingMemoryRule;
  isBusy: boolean;
  onEditText: (value: string) => void;
  onEditCategory: (value: string) => void;
  onDelete: () => void;
}) {
  const [text, setText] = useState(rule.rule_text);
  useEffect(() => setText(rule.rule_text), [rule.rule_text]);

  const example = rule.examples_json?.find((e) => e.before && e.after);

  return (
    <div className="rounded border border-gray-200 px-3 py-2 group hover:border-gray-300 bg-white">
      <div className="flex items-start gap-2">
        <textarea
          className="flex-1 text-xs leading-relaxed border border-transparent hover:border-gray-200 focus:border-blue-300 rounded px-1.5 py-1 resize-none focus:outline-none focus:ring-1 focus:ring-blue-300"
          value={text}
          rows={Math.min(5, Math.max(1, Math.ceil(text.length / 80)))}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            if (text.trim() && text !== rule.rule_text) onEditText(text.trim());
            else if (!text.trim()) setText(rule.rule_text);
          }}
          disabled={isBusy}
        />
        <select
          className="text-[10px] border border-gray-200 rounded px-1.5 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-blue-300 shrink-0"
          value={rule.category ?? ""}
          onChange={(e) => onEditCategory(e.target.value)}
          disabled={isBusy}
        >
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <button
          className="text-gray-300 hover:text-red-500 p-1 leading-none opacity-0 group-hover:opacity-100 transition-opacity disabled:opacity-50"
          title="Delete rule"
          onClick={onDelete}
          disabled={isBusy}
        >
          {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
        </button>
      </div>
      {example && (
        <div className="mt-1 ml-1.5 text-[10px] text-muted-foreground">
          e.g. <span className="line-through">{example.before}</span>{" "}
          → <span className="text-emerald-700">{example.after}</span>
        </div>
      )}
      <div className="mt-1 ml-1.5 text-[10px] text-muted-foreground/60">
        seen {rule.occurrence_count}× · confidence {(rule.confidence * 100).toFixed(0)}%
        {rule.source_type !== "explicit_user" && rule.source_type !== "consolidation" && (
          <span> · auto-learned</span>
        )}
      </div>
    </div>
  );
}
