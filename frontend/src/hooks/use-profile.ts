"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchProfile,
  updateProfile,
  fetchCompleteProfile,
  updateCompleteProfile,
} from "@/lib/api";
import type { ProfileData, ProfileCompleteData } from "@/lib/types";

export function useProfile() {
  return useQuery({
    queryKey: ["profile"],
    queryFn: fetchProfile,
    staleTime: 60_000,
    retry: false,
  });
}

export function useCompleteProfile() {
  return useQuery({
    queryKey: ["profile-complete"],
    queryFn: fetchCompleteProfile,
    staleTime: 60_000,
    retry: false,
  });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sections: Partial<ProfileData>) => updateProfile(sections),
    onSuccess: (data) => {
      queryClient.setQueryData(["profile"], data);
    },
  });
}

export function useUpdateCompleteProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sections: Partial<ProfileCompleteData>) =>
      updateCompleteProfile(sections),
    onSuccess: (data) => {
      queryClient.setQueryData(["profile-complete"], data);
    },
  });
}
