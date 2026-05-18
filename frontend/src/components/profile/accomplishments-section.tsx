"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Link2,
  Loader2,
  Plus,
  Search,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import type {
  ProfileAccomplishment,
  ProfilePublication,
  ProfileWorkHistory,
} from "@/lib/types";
import { employerKey } from "@/components/profile/work-history-section";

/** Return a usable key for a work history entry — prefer the explicit `.key`
 * field, fall back to a deterministic slug of the employer name. Profile YAML
 * entries often ship without `key:` set, and raw `value=""` in the dropdown
 * collides all options onto the same empty string. */
function whKey(wh: ProfileWorkHistory): string {
  return wh.key || employerKey(wh.employer || "");
}
import { scrapeLinkedIn, getLinkedInStatus, enrichAccomplishment } from "@/lib/api";

// ---------------------------------------------------------------------------
// Publication Linker (inline picker)
// ---------------------------------------------------------------------------

function PublicationLinker({
  linkedIds,
  publications,
  onChange,
}: {
  linkedIds: string[];
  publications: ProfilePublication[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  const filtered = publications.filter(
    (p) =>
      p.title?.toLowerCase().includes(search.toLowerCase()) &&
      !linkedIds.includes(p.id || "")
  );

  return (
    <div>
      <label className="block text-xs text-gray-500 mb-0.5">
        Linked Publications
      </label>
      <div className="flex flex-wrap gap-1 mb-1">
        {linkedIds.map((id) => {
          const pub = publications.find((p) => p.id === id);
          return (
            <span
              key={id}
              className="inline-flex items-center gap-0.5 bg-blue-50 text-blue-700 text-[10px] px-1.5 py-0.5 rounded-full"
            >
              <FileText className="h-2.5 w-2.5" />
              {pub?.title?.slice(0, 30) || id}
              {(pub?.title?.length || 0) > 30 ? "..." : ""}
              <button
                type="button"
                onClick={() => onChange(linkedIds.filter((i) => i !== id))}
                className="hover:text-red-500"
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </span>
          );
        })}
      </div>
      {!open ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex items-center gap-1 text-[10px] text-blue-600 hover:text-blue-800"
        >
          <Link2 className="h-3 w-3" /> Link Publication
        </button>
      ) : (
        <div className="border rounded p-2 bg-white space-y-1">
          <div className="flex items-center gap-1 border rounded px-1.5 py-0.5">
            <Search className="h-3 w-3 text-gray-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="flex-1 text-xs outline-none"
              placeholder="Search publications..."
              autoFocus
            />
          </div>
          <div className="max-h-32 overflow-y-auto space-y-0.5">
            {filtered.length === 0 && (
              <div className="text-[10px] text-gray-400 py-1">No matching publications</div>
            )}
            {filtered.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  if (p.id) onChange([...linkedIds, p.id]);
                  setOpen(false);
                  setSearch("");
                }}
                className="w-full text-left text-[10px] px-1.5 py-1 hover:bg-blue-50 rounded truncate"
              >
                {p.title} ({p.year || "?"})
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              setSearch("");
            }}
            className="text-[10px] text-gray-400 hover:text-gray-600"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Accomplishment Card
// ---------------------------------------------------------------------------

function AccomplishmentCard({
  acc,
  index,
  publications,
  workHistory,
  onChange,
  onRemove,
}: {
  acc: ProfileAccomplishment;
  index: number;
  publications: ProfilePublication[];
  workHistory: ProfileWorkHistory[];
  onChange: (index: number, acc: ProfileAccomplishment) => void;
  onRemove: (index: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [quantInput, setQuantInput] = useState("");
  const [skillInput, setSkillInput] = useState("");
  const [tagInput, setTagInput] = useState("");

  const update = (field: keyof ProfileAccomplishment, value: unknown) => {
    onChange(index, { ...acc, [field]: value });
  };

  const updateEmployerFromDropdown = (selectedKey: string) => {
    const wh = workHistory.find((w) => whKey(w) === selectedKey);
    onChange(index, {
      ...acc,
      work_history_key: selectedKey || undefined,
      employer: wh?.employer || "",
    });
  };

  const addToArray = (
    field: "skills_demonstrated" | "quantitative_specifics" | "tags",
    input: string,
    setInput: (v: string) => void
  ) => {
    const trimmed = input.trim();
    if (trimmed) {
      update(field, [...((acc[field] as string[]) || []), trimmed]);
      setInput("");
    }
  };

  const removeFromArray = (
    field: "skills_demonstrated" | "quantitative_specifics" | "tags",
    i: number
  ) => {
    update(field, ((acc[field] as string[]) || []).filter((_, idx) => idx !== i));
  };

  const linkedPubCount = (acc.related_publication_ids || []).length;
  const summary = `${acc.title || "Untitled"}${acc.date_range ? ` (${acc.date_range})` : ""}`;

  // Count how many "more" fields have data
  const moreFieldCount = [
    acc.so_what,
    (acc.skills_demonstrated || []).length > 0,
    (acc.tags || []).length > 0,
    acc.category,
  ].filter(Boolean).length;

  return (
    <div
      className={`border rounded ${acc.auto_populated ? "border-amber-300 bg-amber-50/50" : "bg-gray-50"}`}
    >
      <button
        type="button"
        className="w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-gray-100"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2 text-left min-w-0">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 text-gray-500 shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-gray-500 shrink-0" />
          )}
          <span className="text-xs truncate">{summary}</span>
          {acc.auto_populated && (
            <span className="text-[9px] bg-amber-200 text-amber-800 px-1 py-0.5 rounded shrink-0">
              {acc.source === "linkedin" ? "LinkedIn" : "AI"}
            </span>
          )}
          {linkedPubCount > 0 && (
            <span className="text-[9px] text-blue-500 shrink-0">
              <FileText className="h-3 w-3 inline" /> {linkedPubCount}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove(index);
          }}
          className="text-gray-400 hover:text-red-500 p-0.5"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t">
          {/* ── Primary fields (user maintains these) ── */}
          <div className="grid grid-cols-2 gap-2 pt-2">
            <div className="col-span-2">
              <label className="block text-xs text-gray-500 mb-0.5">Title</label>
              <input
                type="text"
                value={acc.title || ""}
                onChange={(e) => update("title", e.target.value)}
                className="w-full rounded border px-2 py-1 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-0.5">Employer</label>
              <select
                value={acc.work_history_key || ""}
                onChange={(e) => updateEmployerFromDropdown(e.target.value)}
                className="w-full rounded border px-2 py-1 text-sm bg-white"
              >
                <option value="">None (outside project)</option>
                {workHistory.map((wh, i) => {
                  const key = whKey(wh);
                  return (
                    <option key={`${key}-${i}`} value={key}>
                      {wh.employer} — {wh.title}
                    </option>
                  );
                })}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-0.5">Date Range</label>
              <input
                type="text"
                value={acc.date_range || ""}
                onChange={(e) => update("date_range", e.target.value)}
                className="w-full rounded border px-2 py-1 text-sm"
                placeholder="2022-2024"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              Impact Summary
            </label>
            <textarea
              value={acc.impact_summary || ""}
              onChange={(e) => update("impact_summary", e.target.value)}
              className="w-full rounded border px-2 py-1 text-sm resize-none"
              rows={3}
            />
          </div>

          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              Quantitative Specifics
            </label>
            <div className="space-y-1 mb-1">
              {(acc.quantitative_specifics || []).map((q, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <input
                    type="text"
                    value={q}
                    onChange={(e) => {
                      const updated = [...(acc.quantitative_specifics || [])];
                      updated[i] = e.target.value;
                      update("quantitative_specifics", updated);
                    }}
                    className="flex-1 rounded border px-2 py-0.5 text-xs"
                  />
                  <button
                    type="button"
                    onClick={() => removeFromArray("quantitative_specifics", i)}
                    className="text-gray-400 hover:text-red-500"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
            <div className="flex gap-1.5">
              <input
                type="text"
                value={quantInput}
                onChange={(e) => setQuantInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addToArray("quantitative_specifics", quantInput, setQuantInput);
                  }
                }}
                className="flex-1 rounded border px-2 py-0.5 text-xs"
                placeholder="Add metric..."
              />
              <button
                type="button"
                onClick={() =>
                  addToArray("quantitative_specifics", quantInput, setQuantInput)
                }
                disabled={!quantInput.trim()}
                className="text-xs text-blue-600 disabled:opacity-50"
              >
                <Plus className="h-3 w-3" />
              </button>
            </div>
          </div>

          {/* Publication Linker */}
          <PublicationLinker
            linkedIds={acc.related_publication_ids || []}
            publications={publications}
            onChange={(ids) => update("related_publication_ids", ids)}
          />

          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              Relevance Weight (0-1)
            </label>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={acc.relevance_weight ?? 0.5}
              onChange={(e) =>
                update("relevance_weight", parseFloat(e.target.value))
              }
              className="w-full"
            />
            <div className="text-xs text-gray-400 text-right">
              {(acc.relevance_weight ?? 0.5).toFixed(2)}
            </div>
          </div>

          {/* ── "More" toggle for auto-populatable fields ── */}
          <button
            type="button"
            onClick={() => setShowMore(!showMore)}
            className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-gray-600"
          >
            {showMore ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            More{moreFieldCount > 0 ? ` (${moreFieldCount} filled)` : ""}
          </button>

          {showMore && (
            <div className="space-y-2 border-t pt-2">
              <div className="flex items-center justify-between">
                <p className="text-[9px] text-gray-400">
                  These fields help the AI but can be auto-populated from your
                  impact summary.
                </p>
                <button
                  type="button"
                  disabled={enriching || !acc.impact_summary?.trim()}
                  onClick={async () => {
                    setEnriching(true);
                    try {
                      const result = await enrichAccomplishment(acc);
                      if (result.success) {
                        onChange(index, {
                          ...acc,
                          so_what: result.so_what || acc.so_what,
                          skills_demonstrated: result.skills_demonstrated || acc.skills_demonstrated,
                          relevance_weight: result.relevance_weight ?? acc.relevance_weight,
                          tags: result.tags || acc.tags,
                        });
                      }
                    } finally {
                      setEnriching(false);
                    }
                  }}
                  className="flex items-center gap-1 text-[10px] text-blue-600 hover:text-blue-800 disabled:opacity-50 shrink-0"
                >
                  {enriching ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Wand2 className="h-3 w-3" />
                  )}
                  {enriching ? "Generating..." : "Auto-fill"}
                </button>
              </div>

              <div>
                <label className="block text-xs text-gray-500 mb-0.5">So What?</label>
                <textarea
                  value={acc.so_what || ""}
                  onChange={(e) => update("so_what", e.target.value)}
                  className="w-full rounded border px-2 py-1 text-sm resize-none"
                  rows={2}
                  placeholder="Why does this matter for your target roles?"
                />
              </div>

              <div>
                <label className="block text-xs text-gray-500 mb-0.5">
                  Skills Demonstrated
                </label>
                <div className="flex flex-wrap gap-1 mb-1">
                  {(acc.skills_demonstrated || []).map((s, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center gap-0.5 bg-gray-200 text-gray-700 text-[10px] px-1.5 py-0.5 rounded-full"
                    >
                      {s}
                      <button
                        type="button"
                        onClick={() => removeFromArray("skills_demonstrated", i)}
                        className="hover:text-red-500"
                      >
                        <X className="h-2.5 w-2.5" />
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-1.5">
                  <input
                    type="text"
                    value={skillInput}
                    onChange={(e) => setSkillInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addToArray("skills_demonstrated", skillInput, setSkillInput);
                      }
                    }}
                    className="flex-1 rounded border px-2 py-0.5 text-xs"
                    placeholder="Add skill..."
                  />
                  <button
                    type="button"
                    onClick={() =>
                      addToArray("skills_demonstrated", skillInput, setSkillInput)
                    }
                    disabled={!skillInput.trim()}
                    className="text-xs text-blue-600 disabled:opacity-50"
                  >
                    <Plus className="h-3 w-3" />
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-xs text-gray-500 mb-0.5">Category</label>
                  <select
                    value={acc.category || ""}
                    onChange={(e) => update("category", e.target.value)}
                    className="w-full rounded border px-2 py-1 text-sm bg-white"
                  >
                    <option value="">—</option>
                    <option value="project">Project</option>
                    <option value="award">Award</option>
                    <option value="education">Education</option>
                    <option value="leadership">Leadership</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-0.5">Tags</label>
                  <div className="flex flex-wrap gap-1 mb-1">
                    {(acc.tags || []).map((t, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-0.5 bg-purple-100 text-purple-700 text-[10px] px-1.5 py-0.5 rounded-full"
                      >
                        {t}
                        <button
                          type="button"
                          onClick={() => removeFromArray("tags", i)}
                          className="hover:text-red-500"
                        >
                          <X className="h-2.5 w-2.5" />
                        </button>
                      </span>
                    ))}
                  </div>
                  <div className="flex gap-1.5">
                    <input
                      type="text"
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addToArray("tags", tagInput, setTagInput);
                        }
                      }}
                      className="flex-1 rounded border px-2 py-0.5 text-xs"
                      placeholder="Add tag..."
                    />
                    <button
                      type="button"
                      onClick={() => addToArray("tags", tagInput, setTagInput)}
                      disabled={!tagInput.trim()}
                      className="text-xs text-blue-600 disabled:opacity-50"
                    >
                      <Plus className="h-3 w-3" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// LinkedIn Import Panel
// ---------------------------------------------------------------------------

function LinkedInImportPanel({
  onImport,
}: {
  onImport: (accs: ProfileAccomplishment[]) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [drafts, setDrafts] = useState<ProfileAccomplishment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusChecked, setStatusChecked] = useState(false);
  const [lastScraped, setLastScraped] = useState<string | null>(null);

  const checkStatus = async () => {
    try {
      const status = await getLinkedInStatus();
      setLastScraped(status.scraped_at || null);
      setStatusChecked(true);
    } catch {
      setStatusChecked(true);
    }
  };

  const doImport = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await scrapeLinkedIn();
      if (result.success) {
        setDrafts(result.accomplishments);
      } else {
        setError(result.error || "Import failed");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setLoading(false);
    }
  };

  if (!statusChecked) {
    checkStatus();
  }

  if (drafts && drafts.length > 0) {
    return (
      <div className="border rounded p-3 bg-amber-50 space-y-2">
        <div className="text-xs font-semibold text-amber-800">
          LinkedIn Import: {drafts.length} draft{drafts.length !== 1 ? "s" : ""}
        </div>
        {drafts.map((d, i) => (
          <div
            key={i}
            className="flex items-center justify-between bg-white border border-amber-200 rounded px-2 py-1.5"
          >
            <div className="text-xs min-w-0 truncate">
              <span className="font-medium">{d.title}</span>
              {d.employer && (
                <span className="text-gray-500"> — {d.employer}</span>
              )}
            </div>
            <div className="flex gap-1.5 shrink-0">
              <button
                type="button"
                onClick={() => {
                  onImport([d]);
                  setDrafts(drafts.filter((_, idx) => idx !== i));
                }}
                className="text-[10px] text-green-600 hover:text-green-800 font-medium"
              >
                Add
              </button>
              <button
                type="button"
                onClick={() => setDrafts(drafts.filter((_, idx) => idx !== i))}
                className="text-[10px] text-gray-400 hover:text-gray-600"
              >
                Skip
              </button>
            </div>
          </div>
        ))}
        <button
          type="button"
          onClick={() => {
            onImport(drafts);
            setDrafts(null);
          }}
          className="text-[10px] text-green-600 hover:text-green-800 font-medium"
        >
          Add All
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <button
        type="button"
        onClick={doImport}
        disabled={loading}
        className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 disabled:opacity-50"
      >
        {loading ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Plus className="h-3 w-3" />
        )}
        {loading ? "Importing from LinkedIn..." : "Import from LinkedIn"}
      </button>
      {lastScraped && (
        <span className="text-[10px] text-gray-400">
          Last synced: {new Date(lastScraped).toLocaleDateString()}
        </span>
      )}
      {lastScraped && (
        <button
          type="button"
          onClick={doImport}
          disabled={loading}
          className="text-[10px] text-gray-400 hover:text-gray-600"
        >
          Refresh
        </button>
      )}
      {error && <span className="text-[10px] text-red-500">{error}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Section
// ---------------------------------------------------------------------------

interface AccomplishmentsSectionProps {
  data: ProfileAccomplishment[];
  publications: ProfilePublication[];
  workHistory: ProfileWorkHistory[];
  onChange: (data: ProfileAccomplishment[]) => void;
}

export function AccomplishmentsSection({
  data,
  publications,
  workHistory,
  onChange,
}: AccomplishmentsSectionProps) {
  // Group accomplishments by work_history_key
  const groups: { key: string; label: string; accs: { acc: ProfileAccomplishment; index: number }[] }[] = [];

  // Build position groups from work history (preserve order)
  const keysSeen = new Set<string>();
  for (const wh of workHistory) {
    const key = whKey(wh);
    if (!key || keysSeen.has(key)) continue;
    keysSeen.add(key);
    groups.push({
      key,
      label: `${wh.employer} — ${wh.title}`,
      accs: [],
    });
  }
  // "Standalone" bucket
  groups.push({ key: "", label: "Standalone (no position)", accs: [] });

  // Assign accomplishments to groups
  data.forEach((acc, index) => {
    const accWhKey = acc.work_history_key || "";
    const group = groups.find((g) => g.key === accWhKey) || groups[groups.length - 1];
    group.accs.push({ acc, index });
  });

  const updateOne = (index: number, acc: ProfileAccomplishment) => {
    const updated = [...data];
    updated[index] = acc;
    onChange(updated);
  };

  const removeOne = (index: number) => {
    onChange(data.filter((_, i) => i !== index));
  };

  const addToPosition = (workHistoryKey: string) => {
    const wh = workHistory.find((w) => whKey(w) === workHistoryKey);
    onChange([
      ...data,
      {
        title: "",
        employer: wh?.employer || "",
        work_history_key: workHistoryKey || undefined,
        date_range: "",
        impact_summary: "",
        quantitative_specifics: [],
        so_what: "",
        skills_demonstrated: [],
        relevance_weight: 0.5,
      },
    ]);
  };

  const handleLinkedInImport = (newAccs: ProfileAccomplishment[]) => {
    onChange([...data, ...newAccs]);
  };

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-gray-400 leading-relaxed">
        <strong>Accomplishments</strong> describe what you did and why it
        mattered. If a publication resulted from this work, link it below rather
        than repeating the details.
      </p>

      {/* Top actions */}
      <div className="flex items-center gap-3 flex-wrap">
        <LinkedInImportPanel onImport={handleLinkedInImport} />
        <button
          type="button"
          onClick={() => addToPosition("")}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
        >
          <Plus className="h-3 w-3" /> Add Standalone
        </button>
      </div>

      {/* Position groups */}
      {groups.map((group) => {
        // Skip empty standalone group
        if (group.key === "" && group.accs.length === 0) return null;

        const wh = workHistory.find((w) => whKey(w) === group.key);
        const dateRange = wh
          ? `${wh.start}–${wh.end || "present"}`
          : undefined;

        return (
          <div key={group.key || "__standalone"}>
            <div className="flex items-baseline gap-2 mb-1.5">
              <div className="text-xs font-semibold text-gray-600">
                {group.label}
              </div>
              {dateRange && (
                <span className="text-[10px] text-gray-400">{dateRange}</span>
              )}
            </div>
            <div className="space-y-1.5 ml-2 border-l-2 border-gray-200 pl-3">
              {group.accs.map(({ acc, index }) => (
                <AccomplishmentCard
                  key={index}
                  acc={acc}
                  index={index}
                  publications={publications}
                  workHistory={workHistory}
                  onChange={updateOne}
                  onRemove={removeOne}
                />
              ))}
              {group.key && (
                <button
                  type="button"
                  onClick={() => addToPosition(group.key)}
                  className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-blue-600"
                >
                  <Plus className="h-3 w-3" /> Add accomplishment to this
                  position
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
