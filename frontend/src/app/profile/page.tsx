"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  useProfile,
  useCompleteProfile,
  useUpdateProfile,
  useUpdateCompleteProfile,
} from "@/hooks/use-profile";
import { ProfileSection } from "@/components/profile/profile-section";
import { PersonalSection } from "@/components/profile/personal-section";
import { TargetRolesSection } from "@/components/profile/target-roles-section";
import { DomainsSection } from "@/components/profile/domains-section";
import { SkillsSection } from "@/components/profile/skills-section";
import { EducationSection } from "@/components/profile/education-section";
import { WorkHistorySection } from "@/components/profile/work-history-section";
import { AwardsSection } from "@/components/profile/awards-section";
import { SearchPreferencesSection } from "@/components/profile/search-preferences-section";
import { AccomplishmentsSection } from "@/components/profile/accomplishments-section";
import { PublicationsSection } from "@/components/profile/publications-section";
import { WritingStyleSection } from "@/components/profile/writing-style-section";
import { ResumeDesignSection } from "@/components/profile/resume-design-section";
import { ResumePreview } from "@/components/profile/resume-preview";
import { DEFAULT_RESUME_DESIGN } from "@/lib/types";
import { fetchResumeDesignPreviewContent } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { usePublicationsImport } from "@/hooks/use-publications-import";
import type {
  ProfileData,
  ProfilePersonal,
  ProfileTargetRole,
  ProfileSkills,
  ProfileEducation,
  ProfileWorkHistory,
  ProfileSearchPreferences,
  ProfileAccomplishment,
  ProfilePublication,
  ResumeDesign,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Debounced auto-save hook
// ---------------------------------------------------------------------------

function useDebouncedSave<T>(
  saveFn: (data: T) => void,
  delay: number = 500,
): [(data: T) => void, "idle" | "saving" | "saved"] {
  const [status, setStatus] = useState<"idle" | "saving" | "saved">("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const debouncedSave = useCallback(
    (data: T) => {
      setStatus("saving");
      if (timerRef.current) clearTimeout(timerRef.current);
      if (savedTimerRef.current) clearTimeout(savedTimerRef.current);

      timerRef.current = setTimeout(() => {
        saveFn(data);
        setStatus("saved");
        savedTimerRef.current = setTimeout(() => setStatus("idle"), 2000);
      }, delay);
    },
    [saveFn, delay],
  );

  return [debouncedSave, status];
}

// ---------------------------------------------------------------------------
// Profile page content
// ---------------------------------------------------------------------------

type ProfileTab =
  | "preferences"
  | "about"
  | "experience"
  | "skills"
  | "publications"
  | "writing_style"
  | "resume_design";

const TABS: { id: ProfileTab; label: string }[] = [
  { id: "preferences", label: "Search Preferences" },
  { id: "about", label: "About" },
  { id: "experience", label: "Experience" },
  { id: "skills", label: "Skills & Goals" },
  { id: "publications", label: "Publications" },
  { id: "writing_style", label: "Writing Style" },
  { id: "resume_design", label: "Resume Design" },
];

function ProfileContent() {
  const { data: profile, isLoading, error } = useProfile();
  const { data: completeProfile, isLoading: completeLoading } =
    useCompleteProfile();
  const updateProfile = useUpdateProfile();
  const updateComplete = useUpdateCompleteProfile();

  // Tab state
  const [activeTab, setActiveTab] = useState<ProfileTab>("preferences");

  // Local state for optimistic editing
  const [localProfile, setLocalProfile] = useState<ProfileData | null>(null);
  const [localAccomplishments, setLocalAccomplishments] = useState<
    ProfileAccomplishment[] | null
  >(null);
  const [localPublications, setLocalPublications] = useState<
    ProfilePublication[] | null
  >(null);

  // Sync remote → local on first load
  useEffect(() => {
    if (profile && !localProfile) {
      setLocalProfile(profile);
    }
  }, [profile, localProfile]);

  useEffect(() => {
    if (completeProfile && !localAccomplishments) {
      setLocalAccomplishments(completeProfile.accomplishments || []);
      setLocalPublications(completeProfile.publications || []);
    }
  }, [completeProfile, localAccomplishments]);

  // Save callbacks
  const saveSection = useCallback(
    (sections: Partial<ProfileData>) => {
      updateProfile.mutate(sections);
    },
    [updateProfile],
  );

  const saveComplete = useCallback(
    (sections: { accomplishments?: ProfileAccomplishment[]; publications?: ProfilePublication[] }) => {
      updateComplete.mutate(sections);
    },
    [updateComplete],
  );

  // Debounced save hooks for each section
  const [savePersonal, personalStatus] = useDebouncedSave<ProfilePersonal>(
    (data) => saveSection({ personal: data }),
  );
  const [saveTargetRoles, targetRolesStatus] = useDebouncedSave<ProfileTargetRole[]>(
    (data) => saveSection({ target_roles: data }),
  );
  const [saveDomains, domainsStatus] = useDebouncedSave<string[]>(
    (data) => saveSection({ domains: data }),
  );
  const [saveSkills, skillsStatus] = useDebouncedSave<ProfileSkills>(
    (data) => saveSection({ skills: data }),
  );
  const [saveEducation, educationStatus] = useDebouncedSave<ProfileEducation[]>(
    (data) => saveSection({ education: data }),
  );
  const [saveWorkHistory, workHistoryStatus] = useDebouncedSave<ProfileWorkHistory[]>(
    (data) => saveSection({ work_history: data }),
  );
  const [saveAwards, awardsStatus] = useDebouncedSave<string[]>(
    (data) => saveSection({ awards: data }),
  );
  const [savePrefs, prefsStatus] = useDebouncedSave<ProfileSearchPreferences>(
    (data) => saveSection({ search_preferences: data }),
  );
  const [saveAccomplishments, accStatus] = useDebouncedSave<ProfileAccomplishment[]>(
    (data) => saveComplete({ accomplishments: data }),
  );
  const [savePublications, pubStatus] = useDebouncedSave<ProfilePublication[]>(
    (data) => saveComplete({ publications: data }),
  );
  const [saveResumeDesign, resumeDesignStatus] = useDebouncedSave<ResumeDesign>(
    (data) => saveSection({ resume_design: data }),
  );

  // Streaming Scholar import: when the onboarding flow kicked one off and
  // the user landed here mid-stream, fold each newly-arrived publication
  // into localPublications + trigger the debounced save. Dedup by title so
  // pubs the resume already extracted aren't doubled. Once the stream is
  // done, reset() so subsequent visits to /profile don't replay stale
  // state from context.
  const pubImport = usePublicationsImport();
  useEffect(() => {
    if (pubImport.publications.length === 0) return;
    if (localPublications === null) return; // still loading from DB
    setLocalPublications((prev) => {
      const prevList = prev || [];
      const prevTitles = new Set(
        prevList.map((p) => (p.title || "").toLowerCase().trim()),
      );
      const fresh = pubImport.publications.filter(
        (p) => !prevTitles.has((p.title || "").toLowerCase().trim()),
      );
      if (fresh.length === 0) return prevList;
      const merged = [...prevList, ...fresh];
      savePublications(merged);
      return merged;
    });
  }, [pubImport.publications, localPublications, savePublications]);

  useEffect(() => {
    if (pubImport.phase === "done" || pubImport.phase === "error") {
      // Give the debounced save a beat to flush, then drop the context
      // state. Without this, navigating back to /profile after a previous
      // import would re-merge old streamed entries.
      const t = setTimeout(() => pubImport.reset(), 1500);
      return () => clearTimeout(t);
    }
  }, [pubImport.phase, pubImport]);

  if (isLoading || completeLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-sm text-muted-foreground">
        Loading profile...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <p className="text-sm text-muted-foreground">
          No profile found.
        </p>
        <a
          href="/onboarding"
          className="text-sm text-blue-600 hover:text-blue-800 underline"
        >
          Upload your resume to build your profile
        </a>
      </div>
    );
  }

  const p = localProfile || profile || {};

  return (
    <div>
      {/* Tab bar */}
      <div className="flex border-b mb-4 overflow-x-auto">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === tab.id
                ? "border-foreground text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="space-y-3">
        {activeTab === "about" && (
          <>
            <ProfileSection title="Personal Information" saveStatus={personalStatus}>
              <PersonalSection
                data={p.personal || {}}
                onChange={(data) => {
                  setLocalProfile({ ...p, personal: data });
                  savePersonal(data);
                }}
              />
            </ProfileSection>

            <ProfileSection title="Education" saveStatus={educationStatus}>
              <EducationSection
                data={p.education || []}
                onChange={(data) => {
                  setLocalProfile({ ...p, education: data });
                  saveEducation(data);
                }}
              />
            </ProfileSection>

            <ProfileSection title="Awards & Honors" saveStatus={awardsStatus}>
              <AwardsSection
                data={p.awards || []}
                onChange={(data) => {
                  setLocalProfile({ ...p, awards: data });
                  saveAwards(data);
                }}
              />
            </ProfileSection>
          </>
        )}

        {activeTab === "experience" && (
          <>
            <ProfileSection title="Work History" saveStatus={workHistoryStatus}>
              <WorkHistorySection
                data={p.work_history || []}
                onChange={(data) => {
                  setLocalProfile({ ...p, work_history: data });
                  saveWorkHistory(data);
                }}
              />
            </ProfileSection>

            <ProfileSection title="Accomplishments" saveStatus={accStatus}>
              <AccomplishmentsSection
                data={localAccomplishments || []}
                publications={localPublications || []}
                workHistory={p.work_history || []}
                onChange={(data) => {
                  setLocalAccomplishments(data);
                  saveAccomplishments(data);
                }}
              />
            </ProfileSection>
          </>
        )}

        {activeTab === "skills" && (
          <>
            <ProfileSection title="Target Roles" saveStatus={targetRolesStatus}>
              <TargetRolesSection
                data={p.target_roles || []}
                onChange={(data) => {
                  setLocalProfile({ ...p, target_roles: data });
                  saveTargetRoles(data);
                }}
              />
            </ProfileSection>

            <ProfileSection title="Domains" saveStatus={domainsStatus}>
              <DomainsSection
                data={p.domains || []}
                onChange={(data) => {
                  setLocalProfile({ ...p, domains: data });
                  saveDomains(data);
                }}
              />
            </ProfileSection>

            <ProfileSection title="Skills" saveStatus={skillsStatus}>
              <SkillsSection
                data={p.skills || {}}
                onChange={(data) => {
                  setLocalProfile({ ...p, skills: data });
                  saveSkills(data);
                }}
              />
            </ProfileSection>

          </>
        )}

        {activeTab === "preferences" && (
          <ProfileSection title="Search Preferences" saveStatus={prefsStatus}>
            <SearchPreferencesSection
              data={p.search_preferences || {}}
              onChange={(data) => {
                setLocalProfile({ ...p, search_preferences: data });
                savePrefs(data);
              }}
            />
          </ProfileSection>
        )}

        {activeTab === "publications" && (
          <ProfileSection title="Publications" saveStatus={pubStatus}>
            {/* Streaming Scholar import indicator: shows when the
                onboarding flow kicked off an import and the user landed
                here while it's still in flight. Publications drop into
                the list below as they arrive. */}
            {(pubImport.phase === "fetching" || pubImport.phase === "enriching") && (
              <div className="flex items-start gap-2 text-xs bg-blue-50 border border-blue-200 rounded-md px-3 py-2 mb-2">
                <div className="h-3 w-3 mt-0.5 rounded-full border-2 border-blue-500 border-t-transparent animate-spin shrink-0" />
                <div className="text-blue-800 flex-1">
                  <div>{pubImport.status}</div>
                  {pubImport.total > 0 && (
                    <div className="text-blue-700/80 mt-0.5">
                      {pubImport.publications.length} / {pubImport.total}{" "}
                      streamed in — feel free to edit other sections in the
                      meantime.
                    </div>
                  )}
                </div>
              </div>
            )}
            {pubImport.phase === "error" && pubImport.errorMessage && (
              <div className="text-xs bg-red-50 border border-red-200 rounded-md px-3 py-2 mb-2 text-red-700">
                Scholar import failed: {pubImport.errorMessage}
              </div>
            )}
            <PublicationsSection
              data={localPublications || []}
              accomplishments={localAccomplishments || []}
              workHistory={p.work_history || []}
              hasScholarUrl={!!p.personal?.google_scholar}
              onChange={(data) => {
                setLocalPublications(data);
                savePublications(data);
              }}
            />
          </ProfileSection>
        )}

        {activeTab === "writing_style" && (
          <ProfileSection title="Writing Style">
            <WritingStyleSection />
          </ProfileSection>
        )}

        {activeTab === "resume_design" && (
          <ResumeDesignTab
            design={p.resume_design ?? DEFAULT_RESUME_DESIGN}
            saveStatus={resumeDesignStatus}
            onChange={(data) => {
              setLocalProfile({ ...p, resume_design: data });
              saveResumeDesign(data);
            }}
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Resume Design tab — split layout (form on left, live preview on right).
// ---------------------------------------------------------------------------

function ResumeDesignTab({
  design,
  saveStatus,
  onChange,
}: {
  design: import("@/lib/types").ResumeDesign;
  saveStatus: "idle" | "saving" | "saved";
  onChange: (data: import("@/lib/types").ResumeDesign) => void;
}) {
  const previewContent = useQuery({
    queryKey: ["resume-design-preview-content"],
    queryFn: fetchResumeDesignPreviewContent,
    staleTime: 60_000,
  });

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] gap-4">
      <ProfileSection title="Resume Design" saveStatus={saveStatus}>
        <ResumeDesignSection data={design} onChange={onChange} />
      </ProfileSection>

      <div className="xl:sticky xl:top-4 xl:self-start">
        <div className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wider">
          Live Preview
        </div>
        {previewContent.isLoading ? (
          <div className="border border-gray-200 rounded-lg bg-white p-6 text-xs text-muted-foreground">
            Loading preview…
          </div>
        ) : previewContent.data ? (
          <ResumePreview
            design={design}
            resume={previewContent.data.resume}
            profile={previewContent.data.profile}
            isSample={previewContent.data.is_sample}
          />
        ) : (
          <div className="border border-red-200 bg-red-50 rounded-lg p-3 text-xs text-red-700">
            Couldn&apos;t load preview content.{" "}
            {previewContent.error instanceof Error ? previewContent.error.message : ""}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page wrapper
// ---------------------------------------------------------------------------

export default function ProfilePage() {
  return (
    <div className="max-w-6xl mx-auto px-6 py-6">
      <h1 className="text-lg font-bold mb-4">Profile</h1>
      <Suspense
        fallback={
          <div className="text-sm text-muted-foreground py-20 text-center">
            Loading...
          </div>
        }
      >
        <ProfileContent />
      </Suspense>
    </div>
  );
}
