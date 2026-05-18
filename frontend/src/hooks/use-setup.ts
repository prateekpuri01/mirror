"use client";

import { useQuery, useMutation } from "@tanstack/react-query";
import {
  checkSetupStatus,
  testApiKey,
  saveApiKeys,
  type SaveKeysPayload,
} from "@/lib/api";

export function useSetupStatus() {
  return useQuery({
    queryKey: ["setup-status"],
    queryFn: checkSetupStatus,
    staleTime: 30_000,
    retry: false,
  });
}

export function useTestApiKey() {
  return useMutation({
    mutationFn: (openaiApiKey: string) => testApiKey(openaiApiKey),
  });
}

export function useSaveApiKeys() {
  return useMutation({
    mutationFn: (data: SaveKeysPayload) => saveApiKeys(data),
  });
}
