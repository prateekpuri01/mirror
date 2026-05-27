"use client";

import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { usePublicationsImport } from "@/hooks/use-publications-import";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  Upload,
  FileText,
  Globe,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertCircle,
  ArrowRight,
  ArrowLeft,
  SkipForward,
} from "lucide-react";
import {
  useUploadResume,
  useCrawlUrls,
  useCrawlStatus,
  useAssembleProfile,
  useSaveOnboardingProfile,
} from "@/hooks/use-onboarding";
import { ProfileSection } from "@/components/profile/profile-section";
import { PersonalSection } from "@/components/profile/personal-section";
import { WorkHistorySection } from "@/components/profile/work-history-section";
import { EducationSection } from "@/components/profile/education-section";
import { SkillsSection } from "@/components/profile/skills-section";
import { TargetRolesSection } from "@/components/profile/target-roles-section";
import { DomainsSection } from "@/components/profile/domains-section";
import { AwardsSection } from "@/components/profile/awards-section";
import type {
  ProfileData,
  ProfileCompleteData,
  ProfilePersonal,
  ProfileWorkHistory,
  ProfileEducation,
  ProfileSkills,
  ProfileTargetRole,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Step 1: Upload & Links
// ---------------------------------------------------------------------------

function StepUpload({
  onComplete,
}: {
  onComplete: (data: {
    resumeText: string;
    extractedProfile: ProfileData;
    extractedComplete: ProfileCompleteData;
    urls: { type: string; url: string }[];
  }) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadResume();

  // URL inputs
  const [linkedinUrl, setLinkedinUrl] = useState("");
  const [githubUrl, setGithubUrl] = useState("");
  const [scholarUrl, setScholarUrl] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");

  const handleFile = useCallback(
    (file: File) => {
      const name = file.name.toLowerCase();
      if (!name.endsWith(".pdf") && !name.endsWith(".docx")) {
        alert("Please upload a PDF or DOCX file.");
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        alert("File too large. Maximum size is 10 MB.");
        return;
      }
      setSelectedFile(file);
    },
    [],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const handleSubmit = async () => {
    if (!selectedFile) return;

    try {
      const result = await uploadMutation.mutateAsync(selectedFile);

      // Collect URLs
      const urls: { type: string; url: string }[] = [];

      // Use LinkedIn from resume extraction if not provided manually
      const effectiveLinkedin =
        linkedinUrl.trim() ||
        result.extracted_profile?.personal?.linkedin ||
        "";
      if (effectiveLinkedin) {
        const fullUrl = effectiveLinkedin.startsWith("http")
          ? effectiveLinkedin
          : `https://${effectiveLinkedin}`;
        urls.push({ type: "linkedin", url: fullUrl });
        if (!linkedinUrl.trim()) setLinkedinUrl(effectiveLinkedin);
      }
      if (githubUrl.trim())
        urls.push({ type: "github", url: githubUrl.trim() });
      if (scholarUrl.trim())
        urls.push({ type: "google_scholar", url: scholarUrl.trim() });
      if (websiteUrl.trim())
        urls.push({ type: "website", url: websiteUrl.trim() });

      // Merge user-entered URLs back into the extracted profile's personal section
      const mergedProfile = { ...result.extracted_profile };
      mergedProfile.personal = { ...mergedProfile.personal };
      if (effectiveLinkedin && !mergedProfile.personal.linkedin) {
        mergedProfile.personal.linkedin = effectiveLinkedin;
      }
      if (scholarUrl.trim()) {
        mergedProfile.personal.google_scholar = scholarUrl.trim();
      } else if (!mergedProfile.personal.google_scholar && result.extracted_profile?.personal?.google_scholar) {
        mergedProfile.personal.google_scholar = result.extracted_profile.personal.google_scholar;
      }

      onComplete({
        resumeText: result.resume_text,
        extractedProfile: mergedProfile,
        extractedComplete: result.extracted_complete_profile || { accomplishments: [], publications: [] },
        urls,
      });
    } catch {
      // Error is shown via mutation state
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-1">Upload Your Resume</h2>
        <p className="text-sm text-muted-foreground">
          Drop your resume below and we&apos;ll extract your profile
          automatically. You can edit everything before saving.
        </p>
      </div>

      {/* Drag-and-drop zone */}
      <div
        onDragOver={(e) => {
          if (uploadMutation.isPending) return;
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          if (uploadMutation.isPending) return;
          handleDrop(e);
        }}
        onClick={() => {
          if (uploadMutation.isPending) return;
          fileInputRef.current?.click();
        }}
        className={`
          border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors
          ${dragging ? "border-blue-500 bg-blue-50" : "border-gray-300 hover:border-gray-400 hover:bg-gray-50"}
          ${selectedFile ? "border-green-500 bg-green-50" : ""}
        `}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
          }}
        />
        {selectedFile ? (
          <div className="flex items-center justify-center gap-2 text-green-700">
            <FileText className="h-6 w-6" />
            <span className="font-medium">{selectedFile.name}</span>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setSelectedFile(null);
              }}
              className="text-xs text-gray-500 hover:text-red-500 ml-2 underline"
            >
              Remove
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            <Upload className="h-8 w-8 mx-auto text-gray-400" />
            <p className="text-sm text-gray-600">
              Drag and drop your resume here, or click to browse
            </p>
            <p className="text-xs text-gray-400">PDF or DOCX, up to 10 MB</p>
          </div>
        )}
      </div>

      {/* URL inputs */}
      <fieldset disabled={uploadMutation.isPending}>
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2 flex items-center gap-1.5">
            <Globe className="h-4 w-4" />
            Online Profiles
            <span className="text-xs text-gray-400 font-normal">(optional)</span>
          </h3>
          <p className="text-xs text-muted-foreground mb-3">
            We&apos;ll crawl these for additional context to enrich your profile.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                LinkedIn
              </label>
              <input
                type="url"
                value={linkedinUrl}
                onChange={(e) => setLinkedinUrl(e.target.value)}
                className="w-full rounded border px-2.5 py-1.5 text-sm disabled:opacity-50"
                placeholder="linkedin.com/in/..."
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                GitHub
              </label>
              <input
                type="url"
                value={githubUrl}
                onChange={(e) => setGithubUrl(e.target.value)}
                className="w-full rounded border px-2.5 py-1.5 text-sm disabled:opacity-50"
                placeholder="github.com/..."
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                Google Scholar
              </label>
              <input
                type="url"
                value={scholarUrl}
                onChange={(e) => setScholarUrl(e.target.value)}
                className="w-full rounded border px-2.5 py-1.5 text-sm disabled:opacity-50"
                placeholder="scholar.google.com/..."
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                Personal Website
              </label>
              <input
                type="url"
                value={websiteUrl}
                onChange={(e) => setWebsiteUrl(e.target.value)}
                className="w-full rounded border px-2.5 py-1.5 text-sm disabled:opacity-50"
                placeholder="https://..."
              />
            </div>
          </div>
        </div>
      </fieldset>

      {/* Error display */}
      {uploadMutation.isError && (
        <div className="flex items-start gap-2 text-sm text-red-600 bg-red-50 rounded-lg p-3">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{(uploadMutation.error as Error).message}</span>
        </div>
      )}

      {/* Submit */}
      <button
        onClick={handleSubmit}
        disabled={!selectedFile || uploadMutation.isPending}
        className="flex items-center gap-2 px-4 py-2 bg-foreground text-background rounded-md text-sm font-medium disabled:opacity-50 hover:opacity-90 transition-opacity"
      >
        {uploadMutation.isPending ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Parsing resume...
          </>
        ) : (
          <>
            Continue
            <ArrowRight className="h-4 w-4" />
          </>
        )}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2: Processing (URL crawl + assembly)
// ---------------------------------------------------------------------------

function StepProcessing({
  resumeText,
  extractedProfile,
  extractedComplete,
  urls,
  onComplete,
  onSkip,
}: {
  resumeText: string;
  extractedProfile: ProfileData;
  extractedComplete: ProfileCompleteData;
  urls: { type: string; url: string }[];
  onComplete: (profile: ProfileData, complete: ProfileCompleteData) => void;
  onSkip: () => void;
}) {
  const crawlMutation = useCrawlUrls();
  const assembleMutation = useAssembleProfile();
  const [taskId, setTaskId] = useState<string | null>(null);
  const { data: crawlStatus } = useCrawlStatus(taskId);
  const [assembling, setAssembling] = useState(false);

  // LinkedIn paste fallback
  const [linkedinPasteText, setLinkedinPasteText] = useState("");
  const [waitingForPaste, setWaitingForPaste] = useState(false);
  const linkedinUrl = urls.find((u) => u.type === "linkedin")?.url;

  // Start crawl on mount
  useEffect(() => {
    if (urls.length === 0) {
      onSkip();
      return;
    }
    crawlMutation.mutate(urls, {
      onSuccess: (data) => setTaskId(data.task_id),
      onError: () => onSkip(),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When crawl completes, check for LinkedIn failure
  useEffect(() => {
    if (crawlStatus?.status !== "completed" || assembling) return;

    // Check if LinkedIn specifically failed with auth
    const linkedinResult = linkedinUrl
      ? crawlStatus.results?.[linkedinUrl]
      : null;
    const linkedinFailed =
      linkedinResult && !linkedinResult.success && linkedinUrl;

    // Check if any other URL succeeded
    const hasOtherSuccess = Object.entries(crawlStatus.results).some(
      ([url, r]) => url !== linkedinUrl && r.success && r.text,
    );

    // If LinkedIn failed and no other data, offer paste fallback
    if (linkedinFailed && !hasOtherSuccess && !waitingForPaste) {
      setWaitingForPaste(true);
      return;
    }

    // Otherwise, proceed with whatever we have (or skip if nothing)
    if (!waitingForPaste) {
      doAssembly();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crawlStatus?.status]);

  // If crawl failed entirely
  useEffect(() => {
    if (crawlStatus?.status === "failed") {
      onSkip();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crawlStatus?.status]);

  function doAssembly(extraTexts?: Record<string, string>) {
    if (assembling) return;
    setAssembling(true);

    const urlTexts: Record<string, string> = { ...extraTexts };
    if (crawlStatus?.results) {
      for (const [url, result] of Object.entries(crawlStatus.results)) {
        if (result.success && result.text) {
          urlTexts[url] = result.text;
        }
      }
    }

    if (Object.keys(urlTexts).length === 0) {
      onSkip();
      return;
    }

    assembleMutation.mutate(
      {
        resume_text: resumeText,
        resume_extracted: extractedProfile,
        resume_extracted_complete: extractedComplete,
        url_texts: urlTexts,
      },
      {
        onSuccess: (data) => onComplete(data.profile, data.complete_profile),
        onError: () => onSkip(),
      },
    );
  }

  function handlePasteSubmit() {
    if (linkedinPasteText.trim()) {
      doAssembly({
        [linkedinUrl || "linkedin"]: linkedinPasteText.trim(),
      });
    } else {
      // User submitted empty paste — skip LinkedIn data
      doAssembly();
    }
    setWaitingForPaste(false);
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-1">Building Your Profile</h2>
        <p className="text-sm text-muted-foreground">
          Crawling your online profiles for additional context...
        </p>
      </div>

      {/* URL progress list */}
      <div className="space-y-2">
        {urls.map((entry) => {
          const result = crawlStatus?.results?.[entry.url];
          const isDone = !!result;
          const isSuccess = result?.success;

          return (
            <div
              key={entry.url}
              className="flex items-center gap-3 text-sm py-2 px-3 rounded-md bg-gray-50"
            >
              {!isDone ? (
                <Loader2 className="h-4 w-4 animate-spin text-blue-500 shrink-0" />
              ) : isSuccess ? (
                <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
              ) : (
                <XCircle className="h-4 w-4 text-red-400 shrink-0" />
              )}
              <span className="text-gray-600 capitalize text-xs font-medium min-w-[70px]">
                {entry.type.replace("_", " ")}
              </span>
              <span className="text-gray-500 truncate flex-1">
                {entry.url}
              </span>
              {isDone && !isSuccess && result?.error && (
                <span className="text-xs text-red-400 shrink-0 max-w-[200px] truncate">
                  {result.error.includes("authentication")
                    ? "Requires login"
                    : result.error.split("\n")[0]}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* LinkedIn paste fallback */}
      {waitingForPaste && (
        <div className="space-y-3 border border-amber-200 bg-amber-50 rounded-lg p-4">
          <div className="flex items-start gap-2">
            <AlertCircle className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-medium text-amber-900">
                LinkedIn requires authentication
              </p>
              <p className="text-xs text-amber-700 mt-0.5">
                Open your LinkedIn profile in a browser, select all the text
                (Cmd+A / Ctrl+A), copy it, and paste it below. Or skip to
                continue without LinkedIn data.
              </p>
            </div>
          </div>
          <textarea
            value={linkedinPasteText}
            onChange={(e) => setLinkedinPasteText(e.target.value)}
            className="w-full rounded border border-amber-300 px-3 py-2 text-sm h-32 resize-y"
            placeholder="Paste your LinkedIn profile text here..."
          />
          <div className="flex items-center gap-2">
            <button
              onClick={handlePasteSubmit}
              className="px-3 py-1.5 bg-foreground text-background rounded text-sm font-medium hover:opacity-90"
            >
              {linkedinPasteText.trim() ? "Use this text" : "Skip LinkedIn"}
            </button>
            <button
              onClick={() => {
                setWaitingForPaste(false);
                onSkip();
              }}
              className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700"
            >
              Skip all
            </button>
          </div>
        </div>
      )}

      {/* Assembly phase */}
      {assembling && !assembleMutation.isSuccess && (
        <div className="flex items-center gap-2 text-sm text-blue-600">
          <Loader2 className="h-4 w-4 animate-spin" />
          Assembling your profile from all sources...
        </div>
      )}

      {/* Skip button (shown when not waiting for paste) */}
      {!waitingForPaste && !assembling && (
        <button
          onClick={onSkip}
          className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700"
        >
          <SkipForward className="h-3.5 w-3.5" />
          Skip and use resume data only
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3: Review & Save
// ---------------------------------------------------------------------------

function StepReview({
  initialProfile,
  initialComplete,
  onSave,
  onBack,
}: {
  initialProfile: ProfileData;
  initialComplete: ProfileCompleteData | null;
  onSave: (profile: ProfileData, complete: ProfileCompleteData | null) => void;
  onBack: () => void;
}) {
  const [profile, setProfile] = useState<ProfileData>(initialProfile);
  const pubImport = usePublicationsImport();

  const updateSection = <K extends keyof ProfileData>(
    key: K,
    value: ProfileData[K],
  ) => {
    setProfile((prev) => ({ ...prev, [key]: value }));
  };

  // Auto-start the Scholar import once on review entry. Fires if we have
  // EITHER a Scholar URL or an author name (backend falls back to author
  // search on Semantic Scholar when no URL is present). Skips if pubs were
  // already extracted from the resume or if the importer is busy.
  useEffect(() => {
    const url = profile.personal?.google_scholar;
    const name = profile.personal?.name;
    const hasExistingPubs = (initialComplete?.publications?.length || 0) > 0;
    if ((url || name) && !hasExistingPubs && pubImport.phase === "idle") {
      pubImport.start(profile, url || undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Effective complete profile = whatever the upload step gave us, plus any
  // Scholar pubs that streamed in (or finished streaming) via context.
  // Streamed pubs are deduped against existing by title at save time on
  // the backend, so we just concat here.
  const effectiveComplete: ProfileCompleteData | null = useMemo(() => {
    const base = initialComplete || { accomplishments: [], publications: [] };
    if (pubImport.publications.length === 0) return base;
    const existingTitles = new Set(
      (base.publications || []).map((p) => (p.title || "").toLowerCase().trim()),
    );
    const incoming = pubImport.publications.filter(
      (p) => !existingTitles.has((p.title || "").toLowerCase().trim()),
    );
    return {
      ...base,
      publications: [...(base.publications || []), ...incoming],
    };
  }, [initialComplete, pubImport.publications]);

  const accCount = effectiveComplete?.accomplishments?.length || 0;
  const pubCount = effectiveComplete?.publications?.length || 0;
  const importRunning =
    pubImport.phase === "fetching" || pubImport.phase === "enriching";

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold mb-1">Review Your Profile</h2>
        <p className="text-sm text-muted-foreground">
          We extracted the following from your resume. Edit anything that
          needs fixing, then save.
        </p>
      </div>

      {(accCount > 0 || pubCount > 0 || importRunning) && (
        <div className="flex items-start gap-2 text-sm bg-green-50 border border-green-200 rounded-lg p-3">
          {importRunning ? (
            <Loader2 className="h-4 w-4 text-green-600 mt-0.5 shrink-0 animate-spin" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-green-600 mt-0.5 shrink-0" />
          )}
          <div className="text-green-800 flex-1">
            {(accCount > 0 || pubCount > 0) && (
              <div>
                Extracted{" "}
                {accCount > 0 && (
                  <strong>{accCount} accomplishment{accCount !== 1 ? "s" : ""}</strong>
                )}
                {accCount > 0 && pubCount > 0 && " and "}
                {pubCount > 0 && (
                  <strong>{pubCount} publication{pubCount !== 1 ? "s" : ""}</strong>
                )}
                .
              </div>
            )}
            {importRunning && (
              <div className="text-xs text-green-700 mt-1">
                {pubImport.status}
                {pubImport.total > 0 && (
                  <>
                    {" "}
                    ({pubImport.publications.length} / {pubImport.total} streamed)
                  </>
                )}
                {" — "}
                you can switch tabs; the import keeps running in the background.
              </div>
            )}
            {pubImport.phase === "error" && pubImport.errorMessage && (
              <div className="text-xs text-red-700 mt-1">
                Scholar import error: {pubImport.errorMessage}
              </div>
            )}
            {pubImport.skipped.length > 0 && pubImport.phase !== "error" && (
              <div className="text-xs text-green-700/70 mt-1">
                ({pubImport.skipped.length} paper(s) couldn&apos;t be enriched and were skipped.)
              </div>
            )}
          </div>
        </div>
      )}

      <div className="space-y-2">
        <ProfileSection title="Personal Info" defaultOpen={true}>
          <PersonalSection
            data={profile.personal || {}}
            onChange={(d: ProfilePersonal) => updateSection("personal", d)}
          />
        </ProfileSection>

        <ProfileSection title="Work History" defaultOpen={true}>
          <WorkHistorySection
            data={profile.work_history || []}
            onChange={(d: ProfileWorkHistory[]) =>
              updateSection("work_history", d)
            }
          />
        </ProfileSection>

        <ProfileSection title="Education" defaultOpen={true}>
          <EducationSection
            data={profile.education || []}
            onChange={(d: ProfileEducation[]) => updateSection("education", d)}
          />
        </ProfileSection>

        <ProfileSection title="Skills" defaultOpen={true}>
          <SkillsSection
            data={profile.skills || { technical: [], communication: [], tools: [] }}
            onChange={(d: ProfileSkills) => updateSection("skills", d)}
          />
        </ProfileSection>

        <ProfileSection title="Target Roles" defaultOpen={false}>
          <TargetRolesSection
            data={profile.target_roles || []}
            onChange={(d: ProfileTargetRole[]) =>
              updateSection("target_roles", d)
            }
          />
        </ProfileSection>

        <ProfileSection title="Domains" defaultOpen={false}>
          <DomainsSection
            data={profile.domains || []}
            onChange={(d: string[]) => updateSection("domains", d)}
          />
        </ProfileSection>

        <ProfileSection title="Awards" defaultOpen={false}>
          <AwardsSection
            data={profile.awards || []}
            onChange={(d: string[]) => updateSection("awards", d)}
          />
        </ProfileSection>
      </div>

      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 px-3 py-2 text-sm text-gray-600 hover:text-gray-800 border rounded-md"
        >
          <ArrowLeft className="h-4 w-4" />
          Back
        </button>
        <button
          onClick={() => onSave(profile, effectiveComplete)}
          className="flex items-center gap-2 px-4 py-2 bg-foreground text-background rounded-md text-sm font-medium hover:opacity-90 transition-opacity"
        >
          Save Profile
          <CheckCircle2 className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main wizard
// ---------------------------------------------------------------------------

type Step = "upload" | "processing" | "review";

export default function OnboardingPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const saveMutation = useSaveOnboardingProfile();
  const pubImport = usePublicationsImport();

  const [step, setStep] = useState<Step>("upload");
  const [resumeText, setResumeText] = useState("");
  const [extractedProfile, setExtractedProfile] = useState<ProfileData>({});
  const [extractedComplete, setExtractedComplete] = useState<ProfileCompleteData>({
    accomplishments: [],
    publications: [],
  });
  const [urls, setUrls] = useState<{ type: string; url: string }[]>([]);
  const [finalProfile, setFinalProfile] = useState<ProfileData>({});
  const [finalComplete, setFinalComplete] =
    useState<ProfileCompleteData | null>(null);

  // Save the profile to DB and navigate to /profile. Used as the terminal
  // step in every branch — replaces the old "land on a long review screen
  // and click Save" intermediate. The review-step component is kept around
  // as a fallback for the rare case where save errors out, so the user
  // isn't stranded on a blank screen.
  const saveAndContinue = async (
    profile: ProfileData,
    complete: ProfileCompleteData | null,
  ): Promise<boolean> => {
    try {
      await saveMutation.mutateAsync({
        profile,
        complete_profile: complete || undefined,
      });
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["profile-complete"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding-status"] });

      // Kick off the streaming Scholar import on a brand-new profile.
      // Triggers on EITHER a Scholar URL or an author name (backend
      // falls back to name-based Semantic Scholar lookup). Stream state
      // lives in PublicationsImportContext at app root, so navigation
      // away from /onboarding doesn't abort it — the /profile page
      // picks it up and shows the verify banner.
      const scholarUrl = profile.personal?.google_scholar;
      const authorName = profile.personal?.name;
      const hasPubs = (complete?.publications?.length || 0) > 0;
      if ((scholarUrl || authorName) && !hasPubs && pubImport.phase === "idle") {
        pubImport.start(profile, scholarUrl || undefined);
      }

      router.push("/profile");
      return true;
    } catch {
      // Save failed — leave the user on the wizard (or fall back to the
      // review screen) so they can retry without losing data.
      return false;
    }
  };

  // Step 1 complete
  const handleUploadComplete = async (data: {
    resumeText: string;
    extractedProfile: ProfileData;
    extractedComplete: ProfileCompleteData;
    urls: { type: string; url: string }[];
  }) => {
    setResumeText(data.resumeText);
    setExtractedProfile(data.extractedProfile);
    setExtractedComplete(data.extractedComplete);
    setFinalProfile(data.extractedProfile);
    setFinalComplete(data.extractedComplete);
    setUrls(data.urls);

    if (data.urls.length > 0) {
      setStep("processing");
    } else {
      // No URLs to crawl — save immediately and go to /profile.
      const ok = await saveAndContinue(data.extractedProfile, data.extractedComplete);
      if (!ok) setStep("review");
    }
  };

  // Step 2 complete (with assembled data)
  const handleProcessingComplete = async (
    profile: ProfileData,
    complete: ProfileCompleteData,
  ) => {
    setFinalProfile(profile);
    setFinalComplete(complete);
    const ok = await saveAndContinue(profile, complete);
    if (!ok) setStep("review");
  };

  // Step 2 skipped (use resume-only data) — same auto-save path
  const handleProcessingSkip = async () => {
    const ok = await saveAndContinue(extractedProfile, extractedComplete);
    if (!ok) setStep("review");
  };

  // Manual save from the review fallback screen (only reached if auto-save
  // failed). Same as saveAndContinue but no navigation gating.
  const handleSave = async (
    profile: ProfileData,
    complete: ProfileCompleteData | null,
  ) => {
    await saveAndContinue(profile, complete);
  };

  return (
    <div className="max-w-2xl mx-auto py-8 px-4">
      {/* Progress indicator */}
      <div className="flex items-center gap-2 mb-8">
        {(["upload", "processing", "review"] as Step[]).map((s, i) => (
          <div key={s} className="flex items-center gap-2">
            {i > 0 && (
              <div
                className={`h-px w-8 ${
                  step === s || (s === "review" && step === "review")
                    ? "bg-foreground"
                    : "bg-gray-200"
                }`}
              />
            )}
            <div
              className={`h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium ${
                step === s
                  ? "bg-foreground text-background"
                  : s === "upload" ||
                      (s === "processing" &&
                        (step === "review" || step === "processing")) ||
                      (s === "review" && step === "review")
                    ? "bg-foreground/20 text-foreground"
                    : "bg-gray-100 text-gray-400"
              }`}
            >
              {i + 1}
            </div>
          </div>
        ))}
        <span className="text-xs text-muted-foreground ml-2">
          {step === "upload" && "Upload Resume"}
          {step === "processing" && "Processing"}
          {step === "review" && "Review & Save"}
        </span>
      </div>

      {/* Save error */}
      {saveMutation.isError && (
        <div className="flex items-start gap-2 text-sm text-red-600 bg-red-50 rounded-lg p-3 mb-4">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>
            Failed to save profile. Please try again.
          </span>
        </div>
      )}

      {/* Steps */}
      {step === "upload" && (
        <StepUpload onComplete={handleUploadComplete} />
      )}

      {step === "processing" && (
        <StepProcessing
          resumeText={resumeText}
          extractedProfile={extractedProfile}
          extractedComplete={extractedComplete}
          urls={urls}
          onComplete={handleProcessingComplete}
          onSkip={handleProcessingSkip}
        />
      )}

      {step === "review" && (
        <StepReview
          initialProfile={finalProfile}
          initialComplete={finalComplete}
          onSave={handleSave}
          onBack={() => setStep("upload")}
        />
      )}
    </div>
  );
}
