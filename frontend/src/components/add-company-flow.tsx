"use client";

import { useState, useMemo, Fragment } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getExpandedRowModel,
  getPaginationRowModel,
  flexRender,
  createColumnHelper,
} from "@tanstack/react-table";
import { Loader2, X, ChevronDown, ChevronRight } from "lucide-react";
import { useImportCompany } from "@/hooks/use-companies";
import { useJobProcessing } from "@/hooks/use-job-processing";
import { useDiscoverFlow } from "@/hooks/use-discover-flow";
import { useScrapeProgress } from "@/hooks/use-scrape-progress";
import { DiscoverResponse, JobPreview } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const columnHelper = createColumnHelper<JobPreview>();

interface AddCompanyFlowProps {
  onClose: () => void;
}

export function AddCompanyFlow({ onClose }: AddCompanyFlowProps) {
  const [url, setUrl] = useState("");
  const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set());
  const [threshold, setThreshold] = useState(50);
  const [importing, setImporting] = useState(false);

  const {
    isDiscovering,
    result: discovery,
    error: discoverError,
    startDiscover,
    clearDiscover,
  } = useDiscoverFlow();
  const importMutation = useImportCompany();
  const { startProcessing } = useJobProcessing();
  const progressMessage = useScrapeProgress(isDiscovering);

  // Derive step from persistent context state
  const step = importing
    ? "importing"
    : discovery
      ? "review"
      : "url";

  // Auto-select jobs above threshold when discovery completes
  const prevDiscoveryRef = useState<DiscoverResponse | null>(null);
  if (discovery && discovery !== prevDiscoveryRef[0]) {
    prevDiscoveryRef[1](discovery);
    const above = new Set(
      discovery.jobs.filter((j) => j.relevance >= threshold).map((j) => j.url)
    );
    setSelectedUrls(above);
  }

  // Step 1: Discover
  function handleDiscover() {
    if (!url.trim()) return;
    startDiscover(url.trim());
  }

  // Step 2: Threshold change
  function handleThresholdChange(newThreshold: number) {
    setThreshold(newThreshold);
    if (discovery) {
      const above = new Set(
        discovery.jobs
          .filter((j) => j.relevance >= newThreshold)
          .map((j) => j.url)
      );
      setSelectedUrls(above);
    }
  }

  // Step 2: Toggle individual job
  function toggleJob(jobUrl: string) {
    setSelectedUrls((prev) => {
      const next = new Set(prev);
      if (next.has(jobUrl)) next.delete(jobUrl);
      else next.add(jobUrl);
      return next;
    });
  }

  // Step 2: Select all / deselect all
  function toggleSelectAll() {
    if (!discovery) return;
    if (selectedUrls.size === discovery.jobs.length) {
      setSelectedUrls(new Set());
    } else {
      setSelectedUrls(new Set(discovery.jobs.map((j) => j.url)));
    }
  }

  function handleClose() {
    clearDiscover();
    onClose();
  }

  // Step 3: Import
  function handleImport() {
    if (!discovery || !discovery.ats || !discovery.slug) return;
    setImporting(true);
    importMutation.mutate(
      {
        name: discovery.company_name || discovery.slug,
        website: null,
        ats: discovery.ats,
        slug: discovery.slug,
        selected_urls: selectedUrls.size > 0 ? Array.from(selectedUrls) : null,
        monitoring_active: true,
      },
      {
        onSuccess: (response) => {
          if (response.job_ids?.length > 0) {
            startProcessing(response.job_ids);
          }
          handleClose();
        },
        onError: () => setImporting(false),
      }
    );
  }

  return (
    <div className="rounded-lg border bg-background p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Add Company</h2>
        <button
          onClick={handleClose}
          className="p-1 rounded-md hover:bg-muted/50 text-muted-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Step 1: URL input */}
      {step === "url" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Paste a job posting URL from Greenhouse, Lever, or Ashby to discover
            the company and all its open positions.
          </p>
          <div className="flex gap-2">
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleDiscover()}
              placeholder="https://boards.greenhouse.io/company/jobs/12345"
              className="flex-1 h-9 rounded-md border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              autoFocus
            />
            <button
              onClick={handleDiscover}
              disabled={!url.trim() || isDiscovering}
              className="h-9 px-4 rounded-md bg-foreground text-background text-sm font-medium hover:bg-foreground/90 disabled:opacity-50 transition-colors flex items-center gap-2"
            >
              {isDiscovering && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              )}
              Search
            </button>
          </div>
          {isDiscovering && progressMessage && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground/70 animate-in fade-in duration-300">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>{progressMessage}</span>
            </div>
          )}
          {discoverError && (
            <p className="text-sm text-red-600">
              {discoverError}
            </p>
          )}
        </div>
      )}

      {/* Step 2: Review jobs */}
      {step === "review" && discovery && (
        <ReviewStep
          discovery={discovery}
          selectedUrls={selectedUrls}
          threshold={threshold}
          onThresholdChange={handleThresholdChange}
          onToggleJob={toggleJob}
          onToggleSelectAll={toggleSelectAll}
          onImport={handleImport}
          onCancel={handleClose}
        />
      )}

      {/* Step 3: Importing */}
      {step === "importing" && (
        <div className="flex items-center justify-center py-8 gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Importing {selectedUrls.size} jobs...</span>
        </div>
      )}

      {importMutation.isError && !importing && discovery && (
        <p className="text-sm text-red-600 mt-2">
          Import failed: {importMutation.error.message}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Review step (separated for readability)
// ---------------------------------------------------------------------------

export interface ReviewStepProps {
  discovery: DiscoverResponse;
  selectedUrls: Set<string>;
  threshold: number;
  onThresholdChange: (t: number) => void;
  onToggleJob: (url: string) => void;
  onToggleSelectAll: () => void;
  onImport: () => void;
  onCancel: () => void;
}

export function ReviewStep({
  discovery,
  selectedUrls,
  threshold,
  onThresholdChange,
  onToggleJob,
  onToggleSelectAll,
  onImport,
  onCancel,
}: ReviewStepProps) {
  const columns = useMemo(
    () => [
      columnHelper.display({
        id: "select",
        size: 40,
        header: () => (
          <input
            type="checkbox"
            checked={selectedUrls.size === discovery.jobs.length && discovery.jobs.length > 0}
            onChange={onToggleSelectAll}
            className="rounded"
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={selectedUrls.has(row.original.url)}
            onChange={() => onToggleJob(row.original.url)}
            onClick={(e) => e.stopPropagation()}
            className="rounded"
          />
        ),
      }),
      columnHelper.display({
        id: "expand",
        size: 30,
        cell: ({ row }) =>
          row.getIsExpanded() ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          ),
      }),
      columnHelper.accessor("title", {
        header: "Title",
        size: 999,
        cell: ({ getValue }) => (
          <span className="font-medium truncate block max-w-[350px]">
            {getValue()}
          </span>
        ),
      }),
      columnHelper.accessor("location", {
        header: "Location",
        size: 160,
        cell: ({ row }) => {
          const loc = row.original.location;
          const remote = row.original.remote;
          return (
            <div className="flex flex-wrap gap-1">
              {loc && (
                <span className="text-sm truncate max-w-[120px]">{loc}</span>
              )}
              {remote && (
                <Badge
                  variant="outline"
                  className="bg-sky-50 text-sky-700 border-sky-200 text-xs"
                >
                  Remote
                </Badge>
              )}
              {!loc && !remote && (
                <span className="text-muted-foreground">—</span>
              )}
            </div>
          );
        },
      }),
      columnHelper.accessor("department", {
        header: "Department",
        size: 140,
        cell: ({ getValue }) => {
          const dept = getValue();
          if (!dept) return <span className="text-muted-foreground">—</span>;
          const display = Array.isArray(dept) ? dept.join(", ") : dept;
          return (
            <span className="text-sm truncate block max-w-[140px]">
              {display}
            </span>
          );
        },
      }),
      columnHelper.accessor("relevance", {
        header: "Relevance",
        size: 90,
        cell: ({ getValue }) => {
          const v = getValue();
          const color =
            v >= 70
              ? "text-emerald-600"
              : v >= 40
                ? "text-amber-600"
                : "text-red-500";
          return <span className={`text-sm font-medium ${color}`}>{v}</span>;
        },
      }),
    ],
    [selectedUrls, discovery.jobs.length, onToggleSelectAll, onToggleJob]
  );

  const table = useReactTable({
    data: discovery.jobs,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getRowCanExpand: () => true,
    initialState: {
      pagination: { pageSize: 20 },
    },
  });

  const aboveThreshold = discovery.jobs.filter(
    (j) => j.relevance >= threshold
  ).length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-medium">{discovery.company_name}</span>
        {discovery.ats && (
          <Badge variant="outline" className="text-xs capitalize">
            {discovery.ats}
          </Badge>
        )}
        <span className="text-sm text-muted-foreground">
          {discovery.total_jobs} jobs found
        </span>
      </div>

      {/* Threshold control */}
      <div className="flex items-center gap-4 text-sm">
        <label className="flex items-center gap-3">
          <span className="text-muted-foreground whitespace-nowrap">
            Relevance &ge;
          </span>
          <div className="relative flex items-center gap-2.5">
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={threshold}
              onChange={(e) => onThresholdChange(Number(e.target.value))}
              className="w-32 h-1.5 appearance-none rounded-full bg-muted cursor-pointer accent-foreground
                [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-foreground [&::-webkit-slider-thumb]:shadow-sm [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-background
                [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-foreground [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-background [&::-moz-range-thumb]:shadow-sm"
            />
            <span className="text-sm font-medium tabular-nums w-8 text-right">{threshold}</span>
          </div>
        </label>
        <span className="text-muted-foreground">
          {selectedUrls.size} of {discovery.total_jobs} selected
          {aboveThreshold !== selectedUrls.size &&
            ` (${aboveThreshold} above threshold)`}
        </span>
      </div>

      {/* Job table */}
      <div className="rounded-md border max-h-[500px] overflow-auto">
        <Table>
          <TableHeader className="sticky top-0 bg-background z-10">
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead
                    key={header.id}
                    style={{
                      width:
                        header.getSize() !== 150
                          ? header.getSize()
                          : undefined,
                    }}
                  >
                    {flexRender(
                      header.column.columnDef.header,
                      header.getContext()
                    )}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="text-center py-8 text-muted-foreground"
                >
                  No jobs found
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <Fragment key={row.id}>
                  <TableRow
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => row.toggleExpanded()}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id}>
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext()
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                  {row.getIsExpanded() && (
                    <TableRow>
                      <TableCell colSpan={columns.length} className="p-0">
                        <div className="px-6 py-4 bg-muted/30 border-t space-y-3">
                          {row.original.url && (
                            <a
                              href={row.original.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 text-sm text-blue-700 hover:underline"
                            >
                              View original posting →
                            </a>
                          )}
                          {row.original.description_html ? (
                            <div
                              className="prose prose-sm max-w-none"
                              dangerouslySetInnerHTML={{
                                __html: row.original.description_html,
                              }}
                            />
                          ) : (
                            <p className="text-sm text-muted-foreground">
                              No description available
                            </p>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {table.getPageCount() > 1 && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            Page {table.getState().pagination.pageIndex + 1} of{" "}
            {table.getPageCount()}
          </span>
          <div className="flex gap-2">
            <button
              className="h-8 px-3 rounded-md border text-sm disabled:opacity-50 hover:bg-muted/50"
              disabled={!table.getCanPreviousPage()}
              onClick={() => table.previousPage()}
            >
              Previous
            </button>
            <button
              className="h-8 px-3 rounded-md border text-sm disabled:opacity-50 hover:bg-muted/50"
              disabled={!table.getCanNextPage()}
              onClick={() => table.nextPage()}
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Footer actions */}
      <div className="flex items-center justify-end gap-3 pt-2 border-t">
        <button
          onClick={onCancel}
          className="h-9 px-4 rounded-md border text-sm hover:bg-muted/50 transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={onImport}
          disabled={selectedUrls.size === 0}
          className="h-9 px-4 rounded-md bg-foreground text-background text-sm font-medium hover:bg-foreground/90 disabled:opacity-50 transition-colors"
        >
          Import {selectedUrls.size} selected{" "}
          {selectedUrls.size === 1 ? "job" : "jobs"}
        </button>
      </div>
    </div>
  );
}
