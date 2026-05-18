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

type ProfileTab = "preferences" | "about" | "experience" | "skills" | "publications" | "writing_style";

const TABS: { id: ProfileTab; label: string }[] = [
  { id: "preferences", label: "Search Preferences" },
  { id: "about", label: "About" },
  { id: "experience", label: "Experience" },
  { id: "skills", label: "Skills & Goals" },
  { id: "publications", label: "Publications" },
  { id: "writing_style", label: "Writing Style" },
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
      <div className="flex border-b mb-4">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
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
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page wrapper
// ---------------------------------------------------------------------------

export default function ProfilePage() {
  return (
    <div className="max-w-3xl mx-auto px-6 py-6">
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
