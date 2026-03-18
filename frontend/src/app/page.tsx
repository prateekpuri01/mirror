"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { type SortingState } from "@tanstack/react-table";
import { useJobs } from "@/hooks/use-jobs";
import { useJobProcessing } from "@/hooks/use-job-processing";
import { JobsTable } from "@/components/jobs-table";
import { JobsToolbar } from "@/components/jobs-toolbar";
import { JobsParams, JobStatus, JobSource } from "@/lib/types";

function JobsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read all filter/sort/pagination state from URL
  const params: JobsParams = {
    page: Number(searchParams.get("page")) || 1,
    per_page: Number(searchParams.get("per_page")) || 50,
    q: searchParams.get("q") || undefined,
    status: (searchParams.get("status") as JobStatus) || undefined,
    source: (searchParams.get("source") as JobSource) || undefined,
    tag: searchParams.get("tag") || undefined,
    sort_by: searchParams.get("sort_by") || "scraped_at",
    sort_dir: (searchParams.get("sort_dir") as "asc" | "desc") || "desc",
    location: searchParams.get("location") || undefined,
    work_model: searchParams.get("work_model") || undefined,
    min_salary: Number(searchParams.get("min_salary")) || undefined,
  };

  const { hasProcessingJobs, syncWithJobsData } = useJobProcessing();

  const { data, isLoading, error } = useJobs(params, {
    refetchInterval: hasProcessingJobs ? 3000 : false,
  });

  useEffect(() => {
    if (data?.items) {
      syncWithJobsData(data.items);
    }
  }, [data?.items, syncWithJobsData]);

  // Convert URL sort state to TanStack Table sorting state
  const sorting: SortingState = params.sort_by
    ? [{ id: params.sort_by, desc: params.sort_dir === "desc" }]
    : [];

  function updateParams(updates: Record<string, string | undefined>) {
    const p = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value !== undefined && value !== "") {
        p.set(key, value);
      } else {
        p.delete(key);
      }
    }
    router.push(`?${p.toString()}`);
  }

  function handleSortingChange(newSorting: SortingState) {
    if (newSorting.length > 0) {
      updateParams({
        sort_by: newSorting[0].id,
        sort_dir: newSorting[0].desc ? "desc" : "asc",
        page: undefined,
      });
    } else {
      updateParams({ sort_by: undefined, sort_dir: undefined, page: undefined });
    }
  }

  function handlePageChange(page: number) {
    updateParams({ page: String(page) });
  }

  function handlePerPageChange(perPage: number) {
    updateParams({ per_page: String(perPage), page: undefined });
  }

  return (
    <main className="max-w-[1400px] mx-auto px-6 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold mb-1">Jobs</h1>
        {data && (
          <p className="text-sm text-muted-foreground">
            {data.total.toLocaleString()} jobs found
          </p>
        )}
      </div>

      <div className="mb-4">
        <JobsToolbar />
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-muted-foreground">
          Loading jobs...
        </div>
      )}

      {error && (
        <div className="flex items-center justify-center py-20 text-red-600">
          Failed to load jobs: {(error as Error).message}
        </div>
      )}

      {data && (
        <JobsTable
          jobs={data.items}
          total={data.total}
          page={data.page}
          perPage={data.per_page}
          sorting={sorting}
          onSortingChange={handleSortingChange}
          onPageChange={handlePageChange}
          onPerPageChange={handlePerPageChange}
          params={params}
        />
      )}
    </main>
  );
}

export default function JobsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-20 text-muted-foreground">
          Loading...
        </div>
      }
    >
      <JobsPageInner />
    </Suspense>
  );
}
