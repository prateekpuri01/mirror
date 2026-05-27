"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
import type {
  ProfileAccomplishment,
  ProfileData,
  ProfilePublication,
  ProfileWorkHistory,
} from "@/lib/types";
import { lookupPublication } from "@/lib/api";
import { usePublicationsImport } from "@/hooks/use-publications-import";

// ---------------------------------------------------------------------------
// Publication Lookup Modal (inline)
// ---------------------------------------------------------------------------

function PublicationLookup({
  onAdd,
}: {
  onAdd: (pub: ProfilePublication) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ProfilePublication | null>(null);
  const [notFound, setNotFound] = useState(false);

  const doLookup = async () => {
    if (!title.trim()) return;
    setLoading(true);
    setNotFound(false);
    setResult(null);
    try {
      const resp = await lookupPublication(title);
      if (resp.found && resp.publication) {
        setResult(resp.publication);
      } else {
        setNotFound(true);
      }
    } catch {
      setNotFound(true);
    } finally {
      setLoading(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
      >
        <Search className="h-3 w-3" /> Look Up Publication
      </button>
    );
  }

  return (
    <div className="border rounded p-3 bg-blue-50 space-y-2">
      <div className="text-xs font-semibold text-blue-800">
        Look Up Publication
      </div>
      <div className="flex gap-1.5">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              doLookup();
            }
          }}
          className="flex-1 rounded border px-2 py-1 text-sm"
          placeholder="Enter publication title..."
          autoFocus
        />
        <button
          type="button"
          onClick={doLookup}
          disabled={loading || !title.trim()}
          className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            "Search"
          )}
        </button>
      </div>
      {notFound && (
        <div className="text-xs text-gray-500">
          Publication not found. You can add it manually.
        </div>
      )}
      {result && (
        <div className="border border-blue-200 rounded p-2 bg-white space-y-1">
          <div className="text-xs font-medium">{result.title}</div>
          <div className="text-[10px] text-gray-500">
            {(result.authors || []).join(", ")} — {result.venue},{" "}
            {result.year}
          </div>
          {result.impact_summary && (
            <div className="text-[10px] text-gray-600">
              {result.impact_summary}
            </div>
          )}
          <span className="text-[9px] bg-amber-200 text-amber-800 px-1 py-0.5 rounded">
            AI-generated
          </span>
          <div className="flex gap-2 mt-1">
            <button
              type="button"
              onClick={() => {
                onAdd(result);
                setOpen(false);
                setTitle("");
                setResult(null);
              }}
              className="text-xs text-green-600 hover:text-green-800 font-medium"
            >
              Add to Profile
            </button>
            <button
              type="button"
              onClick={() => {
                setResult(null);
                setTitle("");
              }}
              className="text-xs text-gray-400 hover:text-gray-600"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}
      <button
        type="button"
        onClick={() => {
          setOpen(false);
          setTitle("");
          setResult(null);
          setNotFound(false);
        }}
        className="text-[10px] text-gray-400 hover:text-gray-600"
      >
        Cancel
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Google Scholar Import
// ---------------------------------------------------------------------------

function ScholarImportPanel({
  profile,
  hasScholarUrl,
}: {
  profile: ProfileData | null;
  hasScholarUrl: boolean;
}) {
  // Uses the streaming PublicationsImportContext (same one used during
  // onboarding) so the import runs as a background task — the user can
  // navigate to other tabs / pages and pubs continue streaming in. The
  // verify banner on /profile's Publications tab handles confirmation
  // after streaming completes. Auto-save into localPublications happens
  // in the Profile page's useEffect (see profile/page.tsx:185).
  const pubImport = usePublicationsImport();
  const isRunning =
    pubImport.phase === "fetching" || pubImport.phase === "enriching";

  const doImport = () => {
    if (!profile || isRunning) return;
    // Reset any previous stream state before kicking off a new one.
    pubImport.reset();
    pubImport.start(profile);
  };

  let label: string;
  if (isRunning) {
    label =
      pubImport.total > 0
        ? `Importing… ${pubImport.publications.length} / ${pubImport.total}`
        : "Starting import…";
  } else if (hasScholarUrl) {
    label = "Import from Google Scholar";
  } else {
    label = "Find publications by name";
  }

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={doImport}
        disabled={isRunning || !profile}
        className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 disabled:opacity-50 disabled:cursor-not-allowed"
        title={
          isRunning
            ? "Import is running in the background — feel free to navigate away"
            : ""
        }
      >
        {isRunning ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Plus className="h-3 w-3" />
        )}
        {label}
      </button>
      {pubImport.phase === "error" && pubImport.errorMessage && (
        <span className="text-[10px] text-red-500">
          {pubImport.errorMessage}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Publication Card
// ---------------------------------------------------------------------------

function PublicationCard({
  pub,
  index,
  accomplishments,
  workHistory,
  onChange,
  onRemove,
}: {
  pub: ProfilePublication;
  index: number;
  accomplishments: ProfileAccomplishment[];
  workHistory: ProfileWorkHistory[];
  onChange: (index: number, pub: ProfilePublication) => void;
  onRemove: (index: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [skillInput, setSkillInput] = useState("");
  const [quantInput, setQuantInput] = useState("");

  const update = (field: keyof ProfilePublication, value: unknown) => {
    onChange(index, { ...pub, [field]: value });
  };

  const addSkill = () => {
    const trimmed = skillInput.trim();
    if (trimmed) {
      update("skills_demonstrated", [
        ...(pub.skills_demonstrated || []),
        trimmed,
      ]);
      setSkillInput("");
    }
  };

  const removeSkill = (i: number) => {
    update(
      "skills_demonstrated",
      (pub.skills_demonstrated || []).filter((_, idx) => idx !== i)
    );
  };

  const addQuant = () => {
    const trimmed = quantInput.trim();
    if (trimmed) {
      update("quantitative_specifics", [
        ...(pub.quantitative_specifics || []),
        trimmed,
      ]);
      setQuantInput("");
    }
  };

  const removeQuant = (i: number) => {
    update(
      "quantitative_specifics",
      (pub.quantitative_specifics || []).filter((_, idx) => idx !== i)
    );
  };

  // Find accomplishments that reference this pub
  const linkedAccs = accomplishments.filter((a) =>
    (a.related_publication_ids || []).includes(pub.id || "")
  );

  const summary = `${pub.title || "Untitled"} (${pub.year || "?"})${pub.first_author ? " [1st author]" : ""}`;

  return (
    <div
      className={`border rounded ${pub.auto_populated ? "border-amber-300 bg-amber-50/50" : "bg-gray-50"}`}
    >
      <div className="flex items-center justify-between text-sm hover:bg-gray-100">
        <button
          type="button"
          className="flex-1 flex items-center gap-2 text-left min-w-0 px-3 py-2"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 text-gray-500 shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-gray-500 shrink-0" />
          )}
          <span className="text-xs truncate">{summary}</span>
          {pub.auto_populated && (
            <span className="text-[9px] bg-amber-200 text-amber-800 px-1 py-0.5 rounded shrink-0">
              AI
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={() => onRemove(index)}
          className="text-gray-400 hover:text-red-500 px-3 py-2 shrink-0"
          aria-label="Remove publication"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>

      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t pt-2">
          <div>
            <label className="block text-xs text-gray-500 mb-0.5">Title</label>
            <input
              type="text"
              value={pub.title || ""}
              onChange={(e) => update("title", e.target.value)}
              className="w-full rounded border px-2 py-1 text-sm"
            />
          </div>

          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              Authors (comma-separated)
            </label>
            <input
              type="text"
              value={(pub.authors || []).join(", ")}
              onChange={(e) =>
                update(
                  "authors",
                  e.target.value.split(",").map((a) => a.trim())
                )
              }
              className="w-full rounded border px-2 py-1 text-sm"
              placeholder="Author 1, Author 2, ..."
            />
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="block text-xs text-gray-500 mb-0.5">
                Venue
              </label>
              <input
                type="text"
                value={pub.venue || ""}
                onChange={(e) => update("venue", e.target.value)}
                className="w-full rounded border px-2 py-1 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-0.5">
                Year
              </label>
              <input
                type="text"
                value={pub.year || ""}
                onChange={(e) => update("year", e.target.value)}
                className="w-full rounded border px-2 py-1 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-0.5">
                Type
              </label>
              <select
                value={pub.type || ""}
                onChange={(e) => update("type", e.target.value)}
                className="w-full rounded border px-2 py-1 text-sm bg-white"
              >
                <option value="">—</option>
                <option value="journal">Journal</option>
                <option value="conference">Conference</option>
                <option value="report">Report</option>
                <option value="tool">Tool</option>
                <option value="commentary">Commentary</option>
                <option value="working_paper">Working Paper</option>
              </select>
            </div>
          </div>

          {/* URL & DOI */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-xs text-gray-500 mb-0.5">URL</label>
              <input
                type="text"
                value={pub.url || ""}
                onChange={(e) => update("url", e.target.value)}
                className="w-full rounded border px-2 py-1 text-sm"
                placeholder="https://..."
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-0.5">DOI</label>
              <input
                type="text"
                value={pub.doi || ""}
                onChange={(e) => update("doi", e.target.value)}
                className="w-full rounded border px-2 py-1 text-sm"
                placeholder="10.xxxx/..."
              />
            </div>
          </div>

          {/* Position */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-xs text-gray-500 mb-0.5">
                Position
              </label>
              <select
                value={pub.work_history_key || ""}
                onChange={(e) => update("work_history_key", e.target.value)}
                className="w-full rounded border px-2 py-1 text-sm bg-white"
              >
                <option value="">—</option>
                {workHistory.map((wh) => (
                  <option key={wh.key} value={wh.key || ""}>
                    {wh.employer} — {wh.title}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-1.5 text-xs text-gray-600">
                <input
                  type="checkbox"
                  checked={pub.first_author || false}
                  onChange={(e) => update("first_author", e.target.checked)}
                  className="rounded"
                />
                First author
              </label>
            </div>
          </div>

          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              Your Contribution
            </label>
            <textarea
              value={pub.abstract || ""}
              onChange={(e) => update("abstract", e.target.value)}
              className="w-full rounded border px-2 py-1 text-sm resize-none"
              rows={3}
              placeholder="What was your role and contribution to this work?"
            />
          </div>

          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              Impact Summary
            </label>
            <textarea
              value={pub.impact_summary || ""}
              onChange={(e) => update("impact_summary", e.target.value)}
              className="w-full rounded border px-2 py-1 text-sm resize-none"
              rows={2}
              placeholder="Job-search-relevant description of this publication's impact"
            />
          </div>

          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              So What?
            </label>
            <textarea
              value={pub.so_what || ""}
              onChange={(e) => update("so_what", e.target.value)}
              className="w-full rounded border px-2 py-1 text-sm resize-none"
              rows={2}
              placeholder="Why does this publication matter for your target roles?"
            />
          </div>

          {/* Quantitative Specifics */}
          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              Quantitative Specifics
            </label>
            <div className="space-y-1 mb-1">
              {(pub.quantitative_specifics || []).map((q, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <input
                    type="text"
                    value={q}
                    onChange={(e) => {
                      const updated = [...(pub.quantitative_specifics || [])];
                      updated[i] = e.target.value;
                      update("quantitative_specifics", updated);
                    }}
                    className="flex-1 rounded border px-2 py-0.5 text-xs"
                  />
                  <button
                    type="button"
                    onClick={() => removeQuant(i)}
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
                    addQuant();
                  }
                }}
                className="flex-1 rounded border px-2 py-0.5 text-xs"
                placeholder="Add metric..."
              />
              <button
                type="button"
                onClick={addQuant}
                disabled={!quantInput.trim()}
                className="text-xs text-blue-600 disabled:opacity-50"
              >
                <Plus className="h-3 w-3" />
              </button>
            </div>
          </div>

          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              Skills Demonstrated
            </label>
            <div className="flex flex-wrap gap-1 mb-1">
              {(pub.skills_demonstrated || []).map((s, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-0.5 bg-gray-200 text-gray-700 text-[10px] px-1.5 py-0.5 rounded-full"
                >
                  {s}
                  <button
                    type="button"
                    onClick={() => removeSkill(i)}
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
                    addSkill();
                  }
                }}
                className="flex-1 rounded border px-2 py-0.5 text-xs"
                placeholder="Add skill..."
              />
              <button
                type="button"
                onClick={addSkill}
                disabled={!skillInput.trim()}
                className="text-xs text-blue-600 disabled:opacity-50"
              >
                <Plus className="h-3 w-3" />
              </button>
            </div>
          </div>

          {/* Linked accomplishments (read-only) */}
          {linkedAccs.length > 0 && (
            <div>
              <label className="block text-xs text-gray-500 mb-0.5">
                Linked to Accomplishments
              </label>
              <div className="flex flex-wrap gap-1">
                {linkedAccs.map((a) => (
                  <span
                    key={a.id || a.title}
                    className="text-[10px] bg-green-50 text-green-700 px-1.5 py-0.5 rounded-full"
                  >
                    {a.title}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div>
            <label className="block text-xs text-gray-500 mb-0.5">
              Relevance Weight (0-1)
            </label>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={pub.relevance_weight ?? 0.5}
              onChange={(e) =>
                update("relevance_weight", parseFloat(e.target.value))
              }
              className="w-full"
            />
            <div className="text-xs text-gray-400 text-right">
              {(pub.relevance_weight ?? 0.5).toFixed(2)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Section
// ---------------------------------------------------------------------------

interface PublicationsSectionProps {
  data: ProfilePublication[];
  accomplishments: ProfileAccomplishment[];
  workHistory: ProfileWorkHistory[];
  hasScholarUrl: boolean;
  // Full profile (needed by the streaming import — passed straight through
  // to pubImport.start()).
  profile: ProfileData | null;
  onChange: (data: ProfilePublication[]) => void;
}

export function PublicationsSection({
  data,
  accomplishments,
  workHistory,
  profile,
  hasScholarUrl,
  onChange,
}: PublicationsSectionProps) {
  const updateOne = (index: number, pub: ProfilePublication) => {
    const updated = [...data];
    updated[index] = pub;
    onChange(updated);
  };

  const removeOne = (index: number) => {
    onChange(data.filter((_, i) => i !== index));
  };

  const addNew = () => {
    onChange([
      ...data,
      {
        title: "",
        authors: [],
        venue: "",
        year: "",
        abstract: "",
        first_author: false,
        relevance_weight: 0.5,
        skills_demonstrated: [],
      },
    ]);
  };

  const handleAddPubs = (pubs: ProfilePublication[]) => {
    onChange([...data, ...pubs]);
  };

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-gray-400 leading-relaxed">
        <strong>Publications</strong> are the formal scholarly record. You
        don&apos;t need to repeat the project impact here if the pub is linked to
        an accomplishment.
      </p>

      {/* Top actions */}
      <div className="flex items-center gap-3 flex-wrap">
        <PublicationLookup onAdd={(pub) => handleAddPubs([pub])} />
        <ScholarImportPanel
          profile={profile}
          hasScholarUrl={hasScholarUrl}
        />
      </div>

      <div className="space-y-1.5">
        {data.map((pub, i) => (
          <PublicationCard
            key={i}
            pub={pub}
            index={i}
            accomplishments={accomplishments}
            workHistory={workHistory}
            onChange={updateOne}
            onRemove={removeOne}
          />
        ))}
      </div>
      <button
        type="button"
        onClick={addNew}
        className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 mt-2"
      >
        <Plus className="h-3 w-3" /> Add publication
      </button>
    </div>
  );
}
