"use client";

import { useQuery, useMutation } from "@tanstack/react-query";
import {
  checkOnboardingStatus,
  uploadResume,
  startUrlCrawl,
  getCrawlStatus,
  assembleProfile,
  saveOnboardingProfile,
} from "@/lib/api";
import type { ProfileData, ProfileCompleteData } from "@/lib/types";

export function useOnboardingStatus() {
  return useQuery({
    queryKey: ["onboarding-status"],
    queryFn: checkOnboardingStatus,
    staleTime: 30_000,
    retry: false,
  });
}

export function useUploadResume() {
  return useMutation({
    mutationFn: (file: File) => uploadResume(file),
  });
}

export function useCrawlUrls() {
  return useMutation({
    mutationFn: (urls: { type: string; url: string }[]) => startUrlCrawl(urls),
  });
}

export function useCrawlStatus(taskId: string | null) {
  return useQuery({
    queryKey: ["crawl-status", taskId],
    queryFn: () => getCrawlStatus(taskId!),
    enabled: !!taskId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "completed" || status === "failed") return false;
      return 2000;
    },
  });
}

export function useAssembleProfile() {
  return useMutation({
    mutationFn: (data: {
      resume_text: string;
      resume_extracted: ProfileData;
      url_texts: Record<string, string>;
      resume_extracted_complete?: ProfileCompleteData;
    }) => assembleProfile(data),
  });
}

export function useSaveOnboardingProfile() {
  return useMutation({
    mutationFn: (data: {
      profile: ProfileData;
      complete_profile?: ProfileCompleteData;
    }) => saveOnboardingProfile(data),
  });
}
