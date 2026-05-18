"use client";

import { Suspense, useState, useEffect, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { type SortingState } from "@tanstack/react-table";
import { useCompanies } from "@/hooks/use-companies";
import { CompaniesTable } from "@/components/companies-table";
import { AddCompanyFlow } from "@/components/add-company-flow";
import { RefreshCompanyFlow } from "@/components/refresh-company-flow";
import { HotCompanySearch } from "@/components/hot-company-search";
import { useDiscoverFlow } from "@/hooks/use-discover-flow";
import { useHotSearch } from "@/hooks/use-hot-search";
import { useRefreshFlow } from "@/hooks/use-refresh-flow";
import { CompaniesParams } from "@/lib/types";

function CompaniesPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isDiscovering, result: discoverResult } = useDiscoverFlow();
  const { phase: hotSearchPhase } = useHotSearch();
  const hotSearchActive = hotSearchPhase !== "idle";
  const { step: refreshStep, company: refreshCompany, startRefresh, clearRefresh } = useRefreshFlow();
  const refreshActive = refreshStep !== "idle";

  // Read state from URL
  const params: CompaniesParams = {
    page: Number(searchParams.get("page")) || 1,
    per_page: Number(searchParams.get("per_page")) || 50,
    q: searchParams.get("q") || undefined,
    sort_by: searchParams.get("sort_by") || "job_count",
    sort_dir: (searchParams.get("sort_dir") as "asc" | "desc") || "desc",
  };

  const { data, isLoading, error } = useCompanies(params);
  const [showAddFlow, setShowAddFlow] = useState(false);
  const [showHotSearch, setShowHotSearch] = useState(false);

  // Auto-show add flow if a discover is in progress or has results (survives navigation)
  const addFlowVisible = showAddFlow || isDiscovering || !!discoverResult;
  // Auto-show hot search if it's running/has results (survives navigation)
  const hotSearchVisible = showHotSearch || hotSearchActive;

  // Debounced search
  const [searchInput, setSearchInput] = useState(params.q || "");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Sync search input when URL changes externally
  useEffect(() => {
    setSearchInput(searchParams.get("q") || "");
  }, [searchParams]);

  function updateParams(updates: Record<string, string | undefined>) {
    const p = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value !== undefined && value !== "") {
        p.set(key, value);
      } else {
        p.delete(key);
      }
    }
    router.push(`/companies?${p.toString()}`);
  }

  function handleSearchChange(value: string) {
    setSearchInput(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      updateParams({ q: value || undefined, page: undefined });
    }, 300);
  }

  // Sorting state derived from URL
  const sorting: SortingState = params.sort_by
    ? [{ id: params.sort_by, desc: params.sort_dir === "desc" }]
    : [{ id: "name", desc: false }];

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
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold mb-1">Companies</h1>
          {data && (
            <p className="text-sm text-muted-foreground">
              {data.total} {data.total === 1 ? "company" : "companies"}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!hotSearchVisible && (
            <button
              onClick={() => { setShowHotSearch(true); setShowAddFlow(false); }}
              className="h-9 px-4 rounded-md border text-sm font-medium hover:bg-orange-50 hover:text-orange-700 hover:border-orange-200 transition-colors flex items-center gap-2"
              title="Find Hot Jobs"
            >
              <span className="text-base leading-none">🔥</span>
              Find Hot Jobs
            </button>
          )}
          {!addFlowVisible && !hotSearchVisible && (
            <button
              onClick={() => setShowAddFlow(true)}
              className="h-9 px-4 rounded-md bg-foreground text-background text-sm font-medium hover:bg-foreground/90 transition-colors"
            >
              Add Company
            </button>
          )}
        </div>
      </div>

      {hotSearchVisible && (
        <div className="mb-6">
          <HotCompanySearch onClose={() => setShowHotSearch(false)} />
        </div>
      )}

      {addFlowVisible && (
        <div className="mb-6">
          <AddCompanyFlow onClose={() => setShowAddFlow(false)} />
        </div>
      )}

      {refreshActive && refreshCompany && (
        <div className="mb-6">
          <RefreshCompanyFlow company={refreshCompany} onClose={() => clearRefresh()} />
        </div>
      )}

      <div className="mb-4">
        <input
          type="text"
          placeholder="Search companies..."
          value={searchInput}
          onChange={(e) => handleSearchChange(e.target.value)}
          className="h-9 w-full max-w-sm rounded-md border px-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-muted-foreground">
          Loading companies...
        </div>
      )}

      {error && (
        <div className="flex items-center justify-center py-20 text-red-600">
          Failed to load companies: {(error as Error).message}
        </div>
      )}

      {data && (
        <CompaniesTable
          companies={data.items}
          total={data.total}
          page={data.page}
          perPage={data.per_page}
          sorting={sorting}
          onSortingChange={handleSortingChange}
          onPageChange={handlePageChange}
          onPerPageChange={handlePerPageChange}
          onRefresh={(company) => {
            setShowAddFlow(false);
            startRefresh(company);
          }}
          disableRefresh={refreshActive || addFlowVisible || hotSearchActive}
        />
      )}
    </main>
  );
}

export default function CompaniesPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-20 text-muted-foreground">
          Loading...
        </div>
      }
    >
      <CompaniesPageInner />
    </Suspense>
  );
}
