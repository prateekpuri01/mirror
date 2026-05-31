"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { Loader2 } from "lucide-react";
import { DocumentFull, Job, ResumeJson } from "@/lib/types";
import { SOURCE_CONFIG } from "@/lib/constants";
import { useQueryClient } from "@tanstack/react-query";
import { useUpdateJob, useScoreJob } from "@/hooks/use-jobs";
import { useResumeGeneration } from "@/hooks/use-resume-generation";
import { useChat } from "@/hooks/use-chat";
import { useProfile, useCompleteProfile } from "@/hooks/use-profile";
import { useExtractionTracking } from "@/hooks/use-extraction-tracking";
import {
  ApplicationField,
  draftAllAnswers,
  draftSingleAnswer,
  fetchDocument,
  fetchRequirements,
  addResearchEntry,
  generateBullet,
  generateResearchEntry,
  generateResume,
  getDocumentDownloadUrl,
  getExtractionStatus,
  triggerRequirementsExtraction,
  updateDraftResponse,
  updateResumeSection,
} from "@/lib/api";
import type { CompanyResearch } from "@/lib/types";
import { JobStatusSelect } from "./job-status-select";
import { JobThumbs } from "./job-thumbs";
import { ResumeEditor } from "./resume-editor";
import { ChatPanel } from "./chat-panel";
import { CompanyResearchPanel } from "./company-research-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";

interface JobRowExpandedProps {
  job: Job;
  colSpan: number;
}

function formatDate(dateStr: string | null) {
  if (!dateStr) return "—";
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function scoreColor(score: number | null): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 70) return "text-emerald-600";
  if (score >= 40) return "text-amber-600";
  return "text-red-500";
}

function scoreBg(score: number | null): string {
  if (score == null) return "bg-muted";
  if (score >= 70) return "bg-emerald-50";
  if (score >= 40) return "bg-amber-50";
  return "bg-red-50";
}

interface SubScore {
  score: number;
  notes?: string;
  matches?: string[];
  gaps?: string[];
  [key: string]: unknown;
}

interface RationaleSection {
  score: number;
  [key: string]: unknown;
}

function ScoreBar({ score, max }: { score: number; max: number }) {
  const pct = Math.min(100, (score / max) * 100);
  const color =
    pct >= 70 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-red-400";
  return (
    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function ScoreColumn({
  label,
  totalScore,
  section,
}: {
  label: string;
  totalScore: number | null;
  section: RationaleSection | undefined;
}) {
  function getSubScores(sec: RationaleSection): [string, SubScore][] {
    return Object.entries(sec).filter(
      ([k, v]) => k !== "score" && typeof v === "object" && v !== null && "score" in (v as Record<string, unknown>)
    ) as [string, SubScore][];
  }

  const subScores = section ? getSubScores(section) : [];
  const maxSub = subScores.length > 0
    ? Math.max(...subScores.map(([, s]) => s.score))
    : 0;
  // Estimate max per sub-score: total / count rounded up to nearest 5
  const estimatedMax = subScores.length > 0
    ? Math.ceil((100 / subScores.length) / 5) * 5
    : 30;

  return (
    <div>
      {/* Header */}
      <div className="flex items-baseline justify-between mb-3">
        <span className="text-sm font-semibold tracking-tight">{label}</span>
        <span className={`text-2xl font-bold tabular-nums ${scoreColor(totalScore)}`}>
          {totalScore ?? "—"}
          <span className="text-xs font-normal text-muted-foreground">/100</span>
        </span>
      </div>

      {/* Sub-scores */}
      {subScores.length > 0 && (
        <div className="space-y-3">
          {subScores.map(([key, sub]) => (
            <div key={key}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium capitalize text-foreground/80">
                  {key.replace(/_/g, " ")}
                </span>
                <span className="text-xs font-semibold tabular-nums">{sub.score}</span>
              </div>
              <ScoreBar score={sub.score} max={estimatedMax} />
              {sub.notes && (
                <p className="text-[11px] leading-relaxed text-muted-foreground mt-1.5">
                  {sub.notes}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ScoreRationale({
  rationale,
  job,
}: {
  rationale: Record<string, unknown>;
  job: Job;
}) {
  const roleFit = rationale.role_fit as RationaleSection | undefined;
  const interestFit = rationale.interest_fit as RationaleSection | undefined;
  const scoreJobMutation = useScoreJob();

  return (
    <div onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-semibold">Score Rationale</span>
        <div className="flex items-center gap-2">
          {job.scored_at && (
            <span className="text-xs text-muted-foreground">
              Scored {formatDate(job.scored_at)}
            </span>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={scoreJobMutation.isPending}
            onClick={() => scoreJobMutation.mutate(job.id)}
          >
            {scoreJobMutation.isPending ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                Scoring...
              </>
            ) : (
              "Rescore"
            )}
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-6">
        <div className="rounded-lg border p-4">
          <ScoreColumn label="Role Fit" totalScore={job.role_fit_score} section={roleFit} />
        </div>
        <div className="rounded-lg border p-4">
          <ScoreColumn label="Interest Fit" totalScore={job.interest_fit_score} section={interestFit} />
        </div>
      </div>
    </div>
  );
}

function formatSalary(min: number | null, max: number | null) {
  if (!min && !max) return null;
  const fmt = (n: number) => {
    if (n >= 1000) return `$${Math.round(n / 1000)}K`;
    return `$${n}`;
  };
  if (min && max) return `${fmt(min)}–${fmt(max)}`;
  if (min) return `${fmt(min)}+`;
  return `Up to ${fmt(max!)}`;
}

function UnscoredJobSection({ jobId }: { jobId: string }) {
  const scoreJobMutation = useScoreJob();
  return (
    <div className="flex items-center gap-3 rounded-lg border border-dashed p-4" onClick={(e) => e.stopPropagation()}>
      <span className="text-sm text-muted-foreground">No score yet</span>
      <Button
        size="sm"
        variant="outline"
        disabled={scoreJobMutation.isPending}
        onClick={() => scoreJobMutation.mutate(jobId)}
      >
        {scoreJobMutation.isPending ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
            Scoring...
          </>
        ) : (
          "Score Job"
        )}
      </Button>
    </div>
  );
}

function pipelineStageBadge(stage: string) {
  const styles: Record<string, string> = {
    scored: "bg-emerald-50 text-emerald-700 border-emerald-200",
    skipped: "bg-amber-50 text-amber-700 border-amber-200",
    scraped: "bg-slate-100 text-slate-600 border-slate-200",
    cleaned: "bg-slate-100 text-slate-600 border-slate-200",
  };
  return styles[stage] || "bg-slate-100 text-slate-600 border-slate-200";
}

// ---------------------------------------------------------------------------
// Details Tab
// ---------------------------------------------------------------------------

function DetailsTab({ job }: { job: Job }) {
  const updateJob = useUpdateJob();
  const [notes, setNotes] = useState(job.user_notes || "");
  const notesRef = useRef(job.user_notes || "");

  const handleNotesBlur = useCallback(() => {
    if (notes !== notesRef.current) {
      notesRef.current = notes;
      updateJob.mutate({
        id: job.id,
        data: { user_notes: notes || null },
      });
    }
  }, [notes, job.id, updateJob]);

  const salary = formatSalary(job.salary_min, job.salary_max);
  const sourceConfig = SOURCE_CONFIG[job.source];
  const meta = job.extra_metadata || {};

  return (
    <div className="space-y-5">
    <div className="flex gap-6">
      {/* Left column — description */}
      <div className="flex-[3] min-w-0 overflow-auto max-h-[500px] rounded-lg border p-4">
        <h3 className="text-lg font-semibold mb-1">{job.title}</h3>
        <p className="text-sm text-muted-foreground mb-4">
          {job.company}
          {job.location && ` · ${job.location}`}
          {job.work_model === "remote" ? (
            <Badge variant="outline" className="ml-2 text-xs bg-sky-50 text-sky-700 border-0">
              Remote
            </Badge>
          ) : job.work_model === "hybrid" ? (
            <Badge variant="outline" className="ml-2 text-xs bg-violet-50 text-violet-700 border-0">
              Hybrid
            </Badge>
          ) : job.remote ? (
            <Badge variant="outline" className="ml-2 text-xs bg-sky-50 text-sky-700 border-0">
              Remote
            </Badge>
          ) : null}
        </p>
        {job.description_html ? (
          <div
            className="prose prose-sm max-w-none"
            dangerouslySetInnerHTML={{
              __html: DOMPurify.sanitize(job.description_html),
            }}
          />
        ) : (
          <div className="whitespace-pre-wrap text-sm">{job.description}</div>
        )}
      </div>

      {/* Right column — actions & metadata */}
      <div className="flex-[2] min-w-[280px] space-y-5">
        {/* Links */}
        <div className="flex gap-2">
          <a href={job.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
            <Button size="sm">View Posting</Button>
          </a>
          {job.application_url && (
            <a href={job.application_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
              <Button size="sm" variant="outline">Apply</Button>
            </a>
          )}
        </div>

        {/* Status + Thumbs */}
        <div className="flex items-center gap-3">
          <JobStatusSelect job={job} size="default" />
          <JobThumbs job={job} size="default" />
        </div>

        {/* Notes */}
        <div>
          <label className="text-sm font-medium mb-1 block">Notes</label>
          <Textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={handleNotesBlur}
            placeholder="Add notes about this job..."
            className="resize-y min-h-[80px]"
            onClick={(e) => e.stopPropagation()}
          />
        </div>

        {/* Tags */}
        {job.tags.length > 0 && (
          <div>
            <label className="text-sm font-medium mb-1 block">Tags</label>
            <div className="flex flex-wrap gap-1">
              {job.tags.map((tag) => (
                <Badge
                  key={tag.id}
                  variant="outline"
                  style={
                    tag.color
                      ? { backgroundColor: `${tag.color}20`, color: tag.color, borderColor: tag.color }
                      : undefined
                  }
                >
                  {tag.name}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Metadata */}
        <div className="space-y-1.5 text-sm">
          <label className="font-medium block">Details</label>
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            <span className="text-muted-foreground">Source</span>
            <Badge variant="outline" className={`${sourceConfig.bg} ${sourceConfig.color} border-0 w-fit text-xs`}>
              {sourceConfig.label}
            </Badge>
            <span className="text-muted-foreground">Posted</span>
            <span>{formatDate(job.posted_at)}</span>
            <span className="text-muted-foreground">Scraped</span>
            <span>{formatDate(job.scraped_at)}</span>
            {salary && (
              <>
                <span className="text-muted-foreground">Salary</span>
                <span>{salary}</span>
              </>
            )}
            {typeof meta.department === "string" && (
              <>
                <span className="text-muted-foreground">Department</span>
                <span>{meta.department}</span>
              </>
            )}
            {typeof meta.team === "string" && (
              <>
                <span className="text-muted-foreground">Team</span>
                <span>{meta.team}</span>
              </>
            )}
            <span className="text-muted-foreground">Pipeline</span>
            <Badge variant="outline" className={`${pipelineStageBadge(job.pipeline_stage)} w-fit text-xs`}>
              {job.pipeline_stage}
            </Badge>
            {job.expired_at && (
              <>
                <span className="text-muted-foreground">Expired</span>
                <span className="text-red-600">{formatDate(job.expired_at)}</span>
              </>
            )}
          </div>
        </div>

        {/* Application Requirements */}
        {job.application_requirements && (
          <div>
            <label className="text-sm font-medium mb-1 block">Requirements</label>
            <div className="flex flex-wrap gap-1">
              {job.application_requirements.needs_resume && (
                <Badge variant="secondary" className="text-xs">Resume</Badge>
              )}
              {job.application_requirements.needs_cover_letter && (
                <Badge variant="secondary" className="text-xs">Cover Letter</Badge>
              )}
              {job.application_requirements.needs_short_answers && (
                <Badge variant="secondary" className="text-xs">Short Answers</Badge>
              )}
            </div>
          </div>
        )}

        {/* Non-resume documents */}
        {job.documents.filter((d) => d.doc_type !== "resume").length > 0 && (
          <div>
            <label className="text-sm font-medium mb-1 block">Documents</label>
            <div className="space-y-1">
              {job.documents
                .filter((d) => d.doc_type !== "resume")
                .map((doc) => (
                  <div key={doc.id} className="text-sm flex items-center gap-2">
                    <Badge variant="outline" className="text-xs">{doc.doc_type}</Badge>
                    <span>{doc.name}</span>
                    <span className="text-muted-foreground text-xs">v{doc.version}</span>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>
    </div>

    {/* Score Rationale — full width below */}
    {job.score_rationale ? (
      <ScoreRationale rationale={job.score_rationale} job={job} />
    ) : (
      <UnscoredJobSection jobId={job.id} />
    )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Diff utility — compares two resume JSON objects, returns changed leaf paths
// ---------------------------------------------------------------------------

function diffResumePaths(
  oldObj: Record<string, unknown>,
  newObj: Record<string, unknown>,
  prefix = ""
): string[] {
  const changed: string[] = [];
  const allKeys = new Set([...Object.keys(oldObj), ...Object.keys(newObj)]);
  for (const key of allKeys) {
    const path = prefix ? `${prefix}.${key}` : key;
    const oldVal = oldObj[key];
    const newVal = newObj[key];
    if (oldVal === newVal) continue;
    if (
      oldVal && newVal &&
      typeof oldVal === "object" && typeof newVal === "object" &&
      !Array.isArray(oldVal) && !Array.isArray(newVal)
    ) {
      changed.push(
        ...diffResumePaths(
          oldVal as Record<string, unknown>,
          newVal as Record<string, unknown>,
          path
        )
      );
    } else if (Array.isArray(oldVal) && Array.isArray(newVal)) {
      const maxLen = Math.max(oldVal.length, newVal.length);
      for (let i = 0; i < maxLen; i++) {
        const elemPath = `${path}.${i}`;
        if (i >= oldVal.length || i >= newVal.length) {
          changed.push(elemPath);
        } else if (
          typeof oldVal[i] === "object" && typeof newVal[i] === "object" &&
          oldVal[i] && newVal[i]
        ) {
          changed.push(
            ...diffResumePaths(
              oldVal[i] as Record<string, unknown>,
              newVal[i] as Record<string, unknown>,
              elemPath
            )
          );
        } else if (oldVal[i] !== newVal[i]) {
          changed.push(elemPath);
        }
      }
    } else {
      changed.push(path);
    }
  }
  return changed;
}

// ---------------------------------------------------------------------------
// Resume Tab — Split layout: Resume Editor (left) + Chat Panel (right)
// ---------------------------------------------------------------------------

function ResumeTab({ job }: { job: Job }) {
  const queryClient = useQueryClient();
  const resumeGen = useResumeGeneration();
  const { data: profile } = useProfile();
  const { data: completeProfile } = useCompleteProfile();
  const [doc, setDoc] = useState<DocumentFull | null>(null);
  const [resumeJson, setResumeJson] = useState<ResumeJson | null>(null);
  const resumeJsonRef = useRef<ResumeJson | null>(null);
  const [loadingDoc, setLoadingDoc] = useState(false);
  const [recentlyUpdatedPaths, setRecentlyUpdatedPaths] = useState<Set<string>>(new Set());
  const [selectedSection, setSelectedSection] = useState<string | null>(null);
  const [genError, setGenError] = useState<string | null>(null);
  const [swappingIndex, setSwappingIndex] = useState<number | null>(null);
  const [generatingBullet, setGeneratingBullet] = useState<{
    employer_key: string;
    accomplishment_id: string;
  } | null>(null);
  const [bulletError, setBulletError] = useState<string | null>(null);

  // Keep ref in sync with state for use in handleResumeUpdate diff
  useEffect(() => {
    resumeJsonRef.current = resumeJson;
  }, [resumeJson]);

  const isThisJob = resumeGen.jobId === job.id;
  const isWorking = resumeGen.running && isThisJob;

  const resumeDocs = job.documents.filter((d) => d.doc_type === "resume");
  const latestResumeDoc = resumeDocs.length > 0 ? resumeDocs[0] : null;
  const hasResume = !!doc?.content_json;

  // Load full document content
  const loadDoc = useCallback(async (docId: string) => {
    setLoadingDoc(true);
    try {
      const d = await fetchDocument(docId);
      setDoc(d);
      if (d.content_json) {
        setResumeJson(d.content_json as ResumeJson);
      }
    } catch {
      setDoc(null);
    } finally {
      setLoadingDoc(false);
    }
  }, []);

  // Auto-load the latest resume doc
  useEffect(() => {
    if (latestResumeDoc) {
      loadDoc(latestResumeDoc.id);
    }
  }, [latestResumeDoc?.id, latestResumeDoc?.version, loadDoc]);

  // Reload doc + refresh jobs list when generation completes
  const prevRunning = useRef(isWorking);
  useEffect(() => {
    if (prevRunning.current && !isWorking) {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      if (latestResumeDoc) {
        setTimeout(() => loadDoc(latestResumeDoc.id), 500);
      }
    }
    prevRunning.current = isWorking;
  }, [isWorking, queryClient, latestResumeDoc?.id, loadDoc]);

  const handleGenerate = async () => {
    setGenError(null);
    try {
      await generateResume(job.id);
      resumeGen.startPolling(job.id);
    } catch (err) {
      setGenError(err instanceof Error ? err.message : "Failed to start generation");
    }
  };

  // Handle inline section edits from the ResumeEditor
  const handleSectionEdit = useCallback(
    async (path: string, value: unknown) => {
      if (!doc) return;
      // Optimistic update
      setResumeJson((prev) => {
        if (!prev) return prev;
        const updated = JSON.parse(JSON.stringify(prev));
        const keys = path.split(".");
        let obj: Record<string, unknown> = updated;
        for (let i = 0; i < keys.length - 1; i++) {
          const key = keys[i];
          if (Array.isArray(obj)) {
            obj = (obj as unknown[])[parseInt(key)] as Record<string, unknown>;
          } else {
            obj = obj[key] as Record<string, unknown>;
          }
        }
        const finalKey = keys[keys.length - 1];
        if (Array.isArray(obj)) {
          (obj as unknown[])[parseInt(finalKey)] = value;
        } else {
          obj[finalKey] = value;
        }
        return updated;
      });

      // Persist to backend
      try {
        await updateResumeSection(doc.id, path, value);
      } catch {
        // Revert on error — reload from server
        loadDoc(doc.id);
      }
    },
    [doc, loadDoc]
  );

  // Mark a path as recently updated — amber glow fades after 4 seconds
  const flashUpdatedPath = useCallback((path: string) => {
    setRecentlyUpdatedPaths((prev) => new Set(prev).add(path));
    setTimeout(() => {
      setRecentlyUpdatedPaths((prev) => {
        const next = new Set(prev);
        next.delete(path);
        return next;
      });
    }, 10000);
  }, []);

  // Handle resume updates from the chat agent (SSE section_update events)
  const handleResumeUpdate = useCallback(
    (path: string | null, value: unknown) => {
      if (path === null) {
        // Broad rewrite — diff old vs new to find changed sections
        const newJson = value as ResumeJson;
        const prev = resumeJsonRef.current;
        setResumeJson(newJson);
        resumeJsonRef.current = newJson;
        if (prev) {
          const changedPaths = diffResumePaths(
            prev as unknown as Record<string, unknown>,
            newJson as unknown as Record<string, unknown>
          );
          setTimeout(() => {
            for (const p of changedPaths) {
              flashUpdatedPath(p);
            }
          }, 50);
        }
      } else {
        // Section update — apply the same way as inline edits
        setResumeJson((prev) => {
          if (!prev) return prev;
          const updated = JSON.parse(JSON.stringify(prev));
          const keys = path.split(".");
          let obj: Record<string, unknown> = updated;
          for (let i = 0; i < keys.length - 1; i++) {
            const key = keys[i];
            if (Array.isArray(obj)) {
              obj = (obj as unknown[])[parseInt(key)] as Record<string, unknown>;
            } else {
              obj = obj[key] as Record<string, unknown>;
            }
          }
          const finalKey = keys[keys.length - 1];
          if (Array.isArray(obj)) {
            (obj as unknown[])[parseInt(finalKey)] = value;
          } else {
            obj[finalKey] = value;
          }
          return updated;
        });
        // Flash the updated section
        flashUpdatedPath(path);
      }
      // Refresh doc from server after a brief delay
      if (doc) {
        setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: ["jobs"] });
          loadDoc(doc.id);
        }, 1000);
      }
    },
    [doc, loadDoc, queryClient, flashUpdatedPath]
  );

  // Chat hook
  const chat = useChat({
    jobId: job.id,
    onResumeUpdate: handleResumeUpdate,
  });

  // Swap research entry — replaces selected_research[index] with a freshly
  // tailored description for the given accomplishment_id.
  const handleSwapResearch = useCallback(
    async (index: number, accomplishmentId: string) => {
      if (!doc) return;
      setSwappingIndex(index);
      setGenError(null);
      try {
        const updated = await generateResearchEntry(doc.id, accomplishmentId, index);
        setDoc(updated);
        if (updated.content_json) {
          setResumeJson(updated.content_json as ResumeJson);
        }
        flashUpdatedPath(`selected_research.${index}`);
      } catch (err) {
        setGenError(err instanceof Error ? err.message : "Failed to swap research entry");
      } finally {
        setSwappingIndex(null);
      }
    },
    [doc, flashUpdatedPath]
  );

  // Append a new research entry generated from an accomplishment.
  const [addingResearch, setAddingResearch] = useState(false);
  const handleAddResearch = useCallback(
    async (accomplishmentId: string) => {
      if (!doc) return;
      setAddingResearch(true);
      setGenError(null);
      try {
        const updated = await addResearchEntry(doc.id, accomplishmentId);
        setDoc(updated);
        if (updated.content_json) {
          setResumeJson(updated.content_json as ResumeJson);
          const newIndex = (updated.content_json as ResumeJson).selected_research?.length;
          if (newIndex) flashUpdatedPath(`selected_research.${newIndex - 1}`);
        }
      } catch (err) {
        setGenError(err instanceof Error ? err.message : "Failed to add research entry");
      } finally {
        setAddingResearch(false);
      }
    },
    [doc, flashUpdatedPath]
  );


  // Generate a bullet from an accomplishment for a specific employer.
  const handleGenerateBullet = useCallback(
    async (employerKey: string, accomplishmentId: string) => {
      if (!doc) return;
      setGeneratingBullet({ employer_key: employerKey, accomplishment_id: accomplishmentId });
      setBulletError(null);
      try {
        const updated = await generateBullet(doc.id, employerKey, accomplishmentId);
        setDoc(updated);
        if (updated.content_json) {
          setResumeJson(updated.content_json as ResumeJson);
        }
        flashUpdatedPath(`experience.${employerKey}.bullets`);
      } catch (err) {
        setBulletError(
          err instanceof Error ? err.message : "Bullet generation failed"
        );
      } finally {
        setGeneratingBullet(null);
      }
    },
    [doc, flashUpdatedPath]
  );

  // Trigger a read-only proofread pass via the chat agent.
  const handleProofread = useCallback(() => {
    chat.sendMessage("/proofread", null, doc?.id ?? null, "proofread");
  }, [chat, doc]);

  // Cached company research from the document, if any.
  const documentResearch =
    ((resumeJson || doc?.content_json) as { _research?: CompanyResearch } | undefined)
      ?._research ?? null;

  return (
    <div className="space-y-3" onClick={(e) => e.stopPropagation()}>
      {/* Header bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold">
            {doc ? doc.name : `Resume for ${job.company}`}
          </h3>
          {doc && (
            <span className="text-xs text-muted-foreground">v{doc.version}</span>
          )}
          {isWorking && (
            <Loader2 className="h-4 w-4 animate-spin text-amber-600" />
          )}
        </div>
        <div className="flex items-center gap-2">
          {doc?.content_docx_path && (
            <a href={getDocumentDownloadUrl(doc.id)} className="text-xs text-blue-600 hover:underline">
              Download .docx
            </a>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={isWorking || (resumeGen.running && !isThisJob)}
            onClick={handleGenerate}
          >
            {isWorking ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                Generating...
              </>
            ) : doc ? "Regenerate" : "Generate Resume"}
          </Button>
        </div>
      </div>

      {/* Errors */}
      {(resumeGen.error && isThisJob) && (
        <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
          {resumeGen.error}
        </div>
      )}
      {genError && (
        <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
          {genError}
        </div>
      )}

      {/* Loading state — phase-aware messaging so the user can tell
          "5-min company research" apart from "actual resume LLM pipeline".
          The backend reports phase ∈ {researching, planning, generating,
          critiquing, refining, summary_tagline}; step_detail carries the
          fine-grained label when set. */}
      {isWorking && (
        <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-amber-500" />
          {(() => {
            const phase = resumeGen.phase || "";
            const detail = resumeGen.stepDetail || "";
            if (phase === "researching") {
              return (
                <>
                  <p className="font-medium text-foreground">
                    Researching company & team
                  </p>
                  <p className="text-xs text-muted-foreground max-w-md text-center">
                    {detail || "Fetching what the company does, recent news, valued skills. First-time research takes 1–3 min; subsequent generations for this job are instant (cached)."}
                  </p>
                </>
              );
            }
            // The backend reports phase ∈ {planning, generating, saving} and
            // a finer step_detail per substep — the whole "generating" phase
            // covers 5 sequential LLM calls (research/skills → bullets →
            // critic → refiner → summary+tagline). Use step_detail as the
            // primary label so the UI doesn't look frozen, with the broad
            // phase as a smaller subtitle for context.
            const phaseLabel: Record<string, string> = {
              planning: "Planning",
              generating: "Generating",
              saving: "Saving",
            };
            const phaseSubtitle = phaseLabel[phase] || "Working";
            const primary = detail || `${phaseSubtitle} tailored resume...`;
            return (
              <>
                <p className="font-medium text-foreground text-center max-w-md">
                  {primary}
                </p>
                <p className="text-xs text-muted-foreground/80">
                  {phaseSubtitle} · this typically takes 60–90 seconds total
                </p>
                {resumeGen.progress > 0 && (
                  <div className="w-48 h-1 rounded-full bg-muted overflow-hidden mt-1">
                    <div
                      className="h-full bg-amber-500 transition-all duration-500"
                      style={{ width: `${Math.round(resumeGen.progress * 100)}%` }}
                    />
                  </div>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* Empty state */}
      {!doc && !loadingDoc && !isWorking && (
        <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground">
          <p className="mb-3">No resume generated yet for this job.</p>
          <Button
            size="sm"
            onClick={handleGenerate}
            disabled={resumeGen.running}
          >
            Generate Resume
          </Button>
        </div>
      )}

      {/* Bullet-generation error toast (non-blocking) */}
      {bulletError && (
        <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 flex items-center justify-between">
          <span>{bulletError}</span>
          <button
            onClick={() => setBulletError(null)}
            className="ml-3 text-red-600 hover:text-red-800 font-medium"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Company research panel (above the editor) — hidden during
          regenerate so the progress UI gets full focus */}
      {(resumeJson || doc?.content_json) && !isWorking && (
        <CompanyResearchPanel jobId={job.id} documentResearch={documentResearch} />
      )}

      {/* Split layout: Resume Editor + Chat Panel — also hidden during
          regenerate; the progress UI above takes over the space */}
      {(resumeJson || doc?.content_json) && !isWorking && (
        <div className="flex gap-3">
          {/* Left: Resume Editor (60%) */}
          <div className="flex-[3] min-w-0 border rounded-lg p-4 bg-white max-h-[600px] overflow-y-auto">
            <ResumeEditor
              docId={doc!.id}
              resumeJson={resumeJson || (doc!.content_json as ResumeJson)}
              selectedSection={selectedSection}
              recentlyUpdatedPaths={recentlyUpdatedPaths}
              onSelectSection={setSelectedSection}
              onSectionEdit={handleSectionEdit}
              onSwapResearch={handleSwapResearch}
              swappingIndex={swappingIndex}
              onAddResearch={handleAddResearch}
              addingResearch={addingResearch}
              onGenerateBullet={handleGenerateBullet}
              generatingBullet={generatingBullet}
              workHistory={profile?.work_history}
              accomplishments={completeProfile?.accomplishments}
              education={profile?.education}
              profilePublications={completeProfile?.publications}
              resumeDesign={profile?.resume_design}
            />
          </div>

          {/* Right: Chat Panel (40%) */}
          <div className="flex-[2] min-w-[280px] border rounded-lg bg-white max-h-[600px] flex flex-col">
            <ChatPanel
              messages={chat.messages}
              isSending={chat.isSending}
              isLoading={chat.isLoading}
              error={chat.error}
              disabled={!hasResume}
              selectedSection={selectedSection}
              onSend={(content, sectionContext) =>
                chat.sendMessage(content, sectionContext)
              }
              onProofread={handleProofread}
              onClear={chat.clearMessages}
              onDeselectSection={() => setSelectedSection(null)}
              workHistory={profile?.work_history}
              selectedResearchTitle={resumeJson?.selected_research_section_title}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Application Requirements Tab
// ---------------------------------------------------------------------------

interface ExtractedRequirements {
  id: string;
  job_id: string;
  needs_resume: boolean;
  needs_cover_letter: boolean;
  needs_short_answers: boolean;
  needs_other: boolean;
  cover_letter_status: string | null;
  short_answer_questions: { question: string; max_length: number | null; required: boolean }[] | null;
  other_requirements: { type: string; description: string }[] | null;
  extraction_status: string | null;
  extraction_error: string | null;
  extracted_at: string | null;
  extraction_source_url: string | null;
  raw_extraction: Record<string, unknown> | null;
  application_fields: ApplicationField[] | null;
}

function DraftButton({
  jobId,
  field,
  onDrafted,
}: {
  jobId: string;
  field: ApplicationField;
  onDrafted: (label: string, response: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [drafting, setDrafting] = useState(false);

  const handleDraft = async () => {
    setDrafting(true);
    try {
      const result = await draftSingleAnswer(jobId, field.label, instructions || undefined);
      onDrafted(result.field_label, result.draft_response);
      setOpen(false);
      setInstructions("");
    } catch {
      // error is visible in the UI
    } finally {
      setDrafting(false);
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className="text-xs h-6 px-2 text-purple-600 hover:text-purple-700 hover:bg-purple-50 rounded-md font-medium cursor-pointer">
        {field.draft_response ? "Re-draft" : "Draft"}
      </PopoverTrigger>
      <PopoverContent className="w-80" onClick={(e) => e.stopPropagation()}>
        <div className="space-y-3">
          <p className="text-sm font-medium">Draft: {field.label}</p>
          <Textarea
            placeholder="Optional instructions (e.g. 'emphasize my NLP experience', 'keep it under 200 words')"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            className="text-sm min-h-[60px]"
          />
          <Button
            size="sm"
            onClick={handleDraft}
            disabled={drafting}
            className="w-full"
          >
            {drafting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                Drafting...
              </>
            ) : (
              "Generate Draft"
            )}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function DraftEditor({
  jobId,
  field,
  onUpdate,
}: {
  jobId: string;
  field: ApplicationField;
  onUpdate: (label: string, response: string) => void;
}) {
  const [value, setValue] = useState(field.draft_response || "");
  const savedRef = useRef(field.draft_response || "");
  const [saving, setSaving] = useState(false);

  // Sync if draft_response changes externally (e.g. after AI drafting)
  useEffect(() => {
    setValue(field.draft_response || "");
    savedRef.current = field.draft_response || "";
  }, [field.draft_response]);

  const handleBlur = async () => {
    if (value === savedRef.current) return;
    setSaving(true);
    try {
      await updateDraftResponse(jobId, field.label, value);
      savedRef.current = value;
      onUpdate(field.label, value);
    } catch {
      // revert on failure
      setValue(savedRef.current);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3 rounded-md border border-purple-300 bg-white p-3">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-medium text-purple-700">AI Draft</span>
        {saving && (
          <span className="text-xs text-muted-foreground flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" />
            Saving...
          </span>
        )}
      </div>
      <Textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={handleBlur}
        className="text-sm leading-relaxed min-h-[80px] resize-y border-0 p-0 shadow-none focus-visible:ring-0"
        onClick={(e) => e.stopPropagation()}
      />
      {field.max_length && (
        <div className={`text-xs mt-1 text-right ${value.length > field.max_length ? "text-red-500 font-medium" : "text-muted-foreground"}`}>
          {value.length} / {field.max_length}
        </div>
      )}
    </div>
  );
}

function AppRequirementsTab({ job }: { job: Job }) {
  const tracking = useExtractionTracking();
  const [requirements, setRequirements] = useState<ExtractedRequirements | null>(null);
  const [loading, setLoading] = useState(true);
  // Local poll flag is OR-ed with the global tracking state so the spinner
  // continues to show if the user navigates away and back.
  const [localExtracting, setLocalExtracting] = useState(false);
  const extracting = localExtracting || tracking.isExtracting(job.id);
  const [draftingAll, setDraftingAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadRequirements = useCallback(async () => {
    try {
      const data = await fetchRequirements(job.id);
      setRequirements(data);
      setError(null);
      return data;
    } catch {
      setRequirements(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, [job.id]);

  useEffect(() => {
    loadRequirements();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadRequirements]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setLocalExtracting(false);
  }, []);

  const startPolling = useCallback(() => {
    setLocalExtracting(true);
    pollRef.current = setInterval(async () => {
      try {
        const status = await getExtractionStatus(job.id);
        if (!status.in_progress) {
          stopPolling();
          await loadRequirements();
        }
      } catch {
        stopPolling();
        await loadRequirements();
      }
    }, 2000);
  }, [job.id, stopPolling, loadRequirements]);

  const handleExtract = async () => {
    setError(null);
    try {
      const result = await triggerRequirementsExtraction(job.id);
      // Register globally so other pages can show the in-progress indicator
      // even after this tab unmounts.
      tracking.startTracking(job.id);
      if (result.status === "already_running") {
        startPolling();
      } else {
        startPolling();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start extraction");
    }
  };

  const handleDraftAll = async () => {
    setDraftingAll(true);
    setError(null);
    try {
      await draftAllAnswers(job.id);
      await loadRequirements();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to draft answers");
    } finally {
      setDraftingAll(false);
    }
  };

  const handleFieldDrafted = useCallback(
    (label: string, response: string) => {
      setRequirements((prev) => {
        if (!prev || !prev.application_fields) return prev;
        return {
          ...prev,
          application_fields: prev.application_fields.map((f) =>
            f.label === label ? { ...f, draft_response: response } : f
          ),
        };
      });
    },
    []
  );

  const url = job.application_url || job.url;

  return (
    <div className="space-y-4" onClick={(e) => e.stopPropagation()}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Application Requirements</h3>
        <div className="flex items-center gap-2">
          {requirements?.extracted_at && (
            <span className="text-xs text-muted-foreground">
              Last extracted {formatDate(requirements.extracted_at)}
            </span>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={extracting}
            onClick={handleExtract}
          >
            {extracting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                Extracting...
              </>
            ) : requirements?.extraction_status === "completed" ? (
              "Re-extract"
            ) : (
              "Extract Requirements"
            )}
          </Button>
        </div>
      </div>

      {/* What this tab does */}
      <p className="text-xs text-muted-foreground leading-relaxed">
        Sends an AI agent to the application page, identifies every form
        field, and groups them by type — short-answer questions, personal
        info, and fixed-choice dropdowns. Draft tailored responses to the
        short answers here, then paste them into the real form when
        you&apos;re ready to apply.
      </p>

      {/* Source URL */}
      <p className="text-xs text-muted-foreground">
        Source:{" "}
        <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
          {url}
        </a>
      </p>

      {/* Error */}
      {(error || requirements?.extraction_error) && (
        <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
          {error || requirements?.extraction_error}
        </div>
      )}

      {/* Extracting state */}
      {extracting && (
        <div className="flex flex-col items-center justify-center py-12 text-sm text-muted-foreground gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-amber-500" />
          <p>Agent is visiting the application page and extracting requirements...</p>
        </div>
      )}

      {/* Loading */}
      {loading && !extracting && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Empty state */}
      {!loading && !extracting && !requirements?.extraction_status && (
        <div className="flex flex-col items-center justify-center py-12 text-sm text-muted-foreground">
          <p className="mb-3">No requirements extracted yet.</p>
          <p className="text-xs mb-4">
            Click &quot;Extract Requirements&quot; to send an AI agent to visit the application page
            and discover what&apos;s needed.
          </p>
        </div>
      )}

      {/* Results */}
      {!extracting && requirements?.extraction_status === "completed" && (() => {
        const fields = requirements.application_fields || [];
        // New 3-category split; legacy "free_text" falls into shortAnswer
        const shortAnswer = fields.filter((f) => f.response_type === "short_answer" || f.response_type === "free_text");
        const personalInfo = fields.filter((f) => f.response_type === "personal_info");
        const fixed = fields.filter((f) => f.response_type === "fixed_response");

        return (
          <div className="space-y-5">
            {/* Summary badges */}
            <div>
              <label className="text-sm font-medium mb-2 block">Summary</label>
              <div className="flex flex-wrap gap-2">
                {requirements.needs_resume && (
                  <Badge variant="secondary">Resume</Badge>
                )}
                <Badge
                  variant={requirements.cover_letter_status === "required" ? "default" : "secondary"}
                  className={
                    requirements.cover_letter_status === "required"
                      ? "bg-amber-100 text-amber-800 border-0"
                      : requirements.cover_letter_status === "optional"
                        ? "bg-blue-50 text-blue-700 border-0"
                        : ""
                  }
                >
                  Cover Letter: {requirements.cover_letter_status || "not needed"}
                </Badge>
                {shortAnswer.length > 0 && (
                  <Badge variant="secondary" className="bg-purple-50 text-purple-700 border-0">
                    Short Answers ({shortAnswer.length})
                  </Badge>
                )}
                {personalInfo.length > 0 && (
                  <Badge variant="secondary" className="bg-blue-50 text-blue-700 border-0">
                    Personal Info ({personalInfo.length})
                  </Badge>
                )}
                {fixed.length > 0 && (
                  <Badge variant="secondary" className="bg-slate-100 text-slate-700 border-0">
                    Fixed Response ({fixed.length})
                  </Badge>
                )}
              </div>
            </div>

            {/* Short Answers — full width, prominent */}
            {shortAnswer.length > 0 && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium flex items-center gap-2">
                    <span className="inline-block w-2 h-2 rounded-full bg-purple-500" />
                    Short Answers
                    <span className="text-xs font-normal text-muted-foreground">
                      ({shortAnswer.length} field{shortAnswer.length !== 1 ? "s" : ""})
                    </span>
                  </label>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={draftingAll}
                    onClick={handleDraftAll}
                    className="text-purple-600 border-purple-200 hover:bg-purple-50"
                  >
                    {draftingAll ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                        Drafting All...
                      </>
                    ) : (
                      "Draft All Answers"
                    )}
                  </Button>
                </div>
                <div className="space-y-2">
                  {shortAnswer.map((f, i) => (
                    <div key={i} className="rounded-md border border-purple-200 bg-purple-50/30 p-3 text-sm">
                      <div className="flex items-start justify-between gap-2">
                        <p className="font-medium">{f.label}</p>
                        <DraftButton
                          jobId={job.id}
                          field={f}
                          onDrafted={handleFieldDrafted}
                        />
                      </div>
                      <div className="flex gap-3 mt-1 text-xs text-muted-foreground">
                        {f.field_type && <span>{f.field_type}</span>}
                        {f.max_length && <span>Max: {f.max_length} chars</span>}
                        <span>{f.required ? "Required" : "Optional"}</span>
                      </div>
                      {f.description && (
                        <p className="text-xs text-muted-foreground mt-1">{f.description}</p>
                      )}
                      {f.draft_response != null && (
                        <DraftEditor
                          jobId={job.id}
                          field={f}
                          onUpdate={handleFieldDrafted}
                        />
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              {/* Left: Personal Info fields */}
              <div className="space-y-3">
                <label className="text-sm font-medium mb-1 block flex items-center gap-2">
                  <span className="inline-block w-2 h-2 rounded-full bg-blue-500" />
                  Personal Info
                  <span className="text-xs font-normal text-muted-foreground">
                    ({personalInfo.length} field{personalInfo.length !== 1 ? "s" : ""})
                  </span>
                </label>
                {personalInfo.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">None found</p>
                ) : (
                  <div className="space-y-1">
                    {personalInfo.map((f, i) => (
                      <div key={i} className="rounded-md border border-blue-200 bg-blue-50/30 px-3 py-1.5 text-sm flex items-center justify-between">
                        <span className="font-medium">{f.label}</span>
                        <div className="flex gap-2 text-xs text-muted-foreground">
                          {f.field_type && <span>{f.field_type}</span>}
                          <span>{f.required ? "Required" : "Optional"}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Right: Fixed Response fields */}
              <div className="space-y-3">
                <label className="text-sm font-medium mb-1 block flex items-center gap-2">
                  <span className="inline-block w-2 h-2 rounded-full bg-slate-500" />
                  Fixed Responses
                  <span className="text-xs font-normal text-muted-foreground">
                    ({fixed.length} field{fixed.length !== 1 ? "s" : ""})
                  </span>
                </label>
                {fixed.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">None found</p>
                ) : (
                  <div className="space-y-2">
                    {fixed.map((f, i) => (
                      <div key={i} className="rounded-md border p-3 text-sm">
                        <div className="flex items-start justify-between gap-2">
                          <p className="font-medium">{f.label}</p>
                          {f.field_type && (
                            <Badge variant="outline" className="text-xs shrink-0">{f.field_type}</Badge>
                          )}
                        </div>
                        {f.options && f.options.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            {f.options.map((opt, j) => (
                              <span key={j} className="text-xs bg-muted px-1.5 py-0.5 rounded">{opt}</span>
                            ))}
                          </div>
                        )}
                        <div className="flex gap-3 mt-1 text-xs text-muted-foreground">
                          <span>{f.required ? "Required" : "Optional"}</span>
                        </div>
                        {f.description && (
                          <p className="text-xs text-muted-foreground mt-1">{f.description}</p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Raw extraction (collapsed) */}
            <details className="text-xs">
              <summary className="text-sm font-medium cursor-pointer text-muted-foreground hover:text-foreground">
                Raw Extraction JSON
              </summary>
              <pre className="rounded-md border bg-muted/50 p-3 mt-2 overflow-auto max-h-[300px] whitespace-pre-wrap">
                {JSON.stringify(requirements.raw_extraction, null, 2)}
              </pre>
            </details>
          </div>
        );
      })()}

      {/* Failed state */}
      {!extracting && requirements?.extraction_status === "failed" && (
        <div className="text-center py-8">
          <p className="text-sm text-red-600 mb-2">Extraction failed</p>
          <p className="text-xs text-muted-foreground mb-3">
            {requirements.extraction_error}
          </p>
          <Button size="sm" variant="outline" onClick={handleExtract}>
            Retry
          </Button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main expanded row with tabs
// ---------------------------------------------------------------------------

type TabId = "details" | "resume" | "requirements";

export function JobRowExpanded({ job, colSpan }: JobRowExpandedProps) {
  const [activeTab, setActiveTab] = useState<TabId>("details");
  const resumeGen = useResumeGeneration();
  const hasResume = job.documents.some((d) => d.doc_type === "resume");
  const isThisJobWorking = resumeGen.running && resumeGen.jobId === job.id;

  return (
    <tr>
      <td colSpan={colSpan} className="p-0">
        <div className="border-t border-b bg-muted/30">
          {/* Tab bar */}
          <div className="flex border-b px-6 pt-2" onClick={(e) => e.stopPropagation()}>
            <button
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === "details"
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setActiveTab("details")}
            >
              Details
            </button>
            <button
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
                activeTab === "resume"
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setActiveTab("resume")}
            >
              Resume
              {isThisJobWorking && (
                <Loader2 className="h-3 w-3 animate-spin text-amber-600" />
              )}
              {!isThisJobWorking && hasResume && (
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500" />
              )}
            </button>
            <button
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === "requirements"
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setActiveTab("requirements")}
            >
              Requirements
            </button>
          </div>

          {/* Tab content */}
          <div className="p-6">
            {activeTab === "details" && <DetailsTab job={job} />}
            {activeTab === "resume" && <ResumeTab job={job} />}
            {activeTab === "requirements" && <AppRequirementsTab job={job} />}
          </div>
        </div>
      </td>
    </tr>
  );
}
