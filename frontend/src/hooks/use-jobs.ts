"use client";

import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { fetchJobs, updateJob, fetchTags, fetchLocations, scoreJob } from "@/lib/api";
import { Job, JobListResponse, JobUpdate, JobsParams, LocationRead } from "@/lib/types";

export function useJobs(
  params: JobsParams,
  options?: { refetchInterval?: number | false },
) {
  return useQuery<JobListResponse>({
    queryKey: ["jobs", params],
    queryFn: () => fetchJobs(params),
    refetchInterval: options?.refetchInterval,
  });
}

export function useTags() {
  return useQuery({
    queryKey: ["tags"],
    queryFn: fetchTags,
    staleTime: 60_000,
  });
}

export function useLocations() {
  return useQuery<LocationRead[]>({
    queryKey: ["locations"],
    queryFn: fetchLocations,
    staleTime: 60_000,
  });
}

export function useScoreJob() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (jobId: string) => scoreJob(jobId),
    onMutate: async () => {
      // Cancel in-flight refetches so they don't overwrite our update
      // (scoring takes ~5-8s — plenty of time for stale refetches to race)
      await queryClient.cancelQueries({ queryKey: ["jobs"] });
    },
    onSuccess: (data) => {
      // Patch the job in all query caches with returned scores
      queryClient.setQueriesData<JobListResponse>(
        { queryKey: ["jobs"] },
        (old) => {
          if (!old) return old;
          return {
            ...old,
            items: old.items.map((job) =>
              job.id === data.job_id
                ? {
                    ...job,
                    role_fit_score: data.role_fit_score,
                    interest_fit_score: data.interest_fit_score,
                    relevance_score: data.relevance_score,
                    score_rationale: data.score_rationale,
                    pipeline_stage: "scored",
                    scored_at: new Date().toISOString(),
                  }
                : job
            ),
          };
        }
      );
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useUpdateJob() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: JobUpdate }) =>
      updateJob(id, data),
    onMutate: async ({ id, data }) => {
      // Cancel ongoing fetches
      await queryClient.cancelQueries({ queryKey: ["jobs"] });

      // Snapshot all job list caches
      const previousQueries = queryClient.getQueriesData<JobListResponse>({
        queryKey: ["jobs"],
      });

      // Optimistically update all matching caches
      queryClient.setQueriesData<JobListResponse>(
        { queryKey: ["jobs"] },
        (old) => {
          if (!old) return old;
          return {
            ...old,
            items: old.items.map((job) =>
              job.id === id ? { ...job, ...data } : job
            ),
          };
        }
      );

      return { previousQueries };
    },
    onError: (_err, _vars, context) => {
      // Rollback on error
      if (context?.previousQueries) {
        for (const [key, data] of context.previousQueries) {
          queryClient.setQueryData(key, data);
        }
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
