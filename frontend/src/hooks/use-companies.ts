"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchCompanies, discoverCompany, importCompany, deleteCompany, refreshCompany } from "@/lib/api";
import { CompaniesParams, CompanyListResponse, DiscoverResponse, ImportRequest, ImportResponse, RefreshResponse } from "@/lib/types";

export function useCompanies(params: CompaniesParams = {}) {
  return useQuery<CompanyListResponse>({
    queryKey: ["companies", params],
    queryFn: () => fetchCompanies(params),
  });
}

export function useDeleteCompany() {
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: (companyId: string) => deleteCompany(companyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useDiscoverCompany() {
  return useMutation<DiscoverResponse, Error, string>({
    mutationFn: (jobUrl: string) => discoverCompany(jobUrl),
  });
}

export function useRefreshCompany() {
  const queryClient = useQueryClient();

  return useMutation<RefreshResponse, Error, string>({
    mutationFn: (companyId: string) => refreshCompany(companyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useImportCompany() {
  const queryClient = useQueryClient();

  return useMutation<ImportResponse, Error, ImportRequest>({
    mutationFn: (data: ImportRequest) => importCompany(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
