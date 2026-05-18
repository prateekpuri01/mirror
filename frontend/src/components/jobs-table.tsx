"use client";

import { useCallback, useMemo, Fragment } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getExpandedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
  type ExpandedState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, Loader2 } from "lucide-react";
import { Job, JobsParams } from "@/lib/types";
import { STATUS_CONFIG, SOURCE_CONFIG } from "@/lib/constants";
import { useResumeGeneration } from "@/hooks/use-resume-generation";
import { useJobProcessing } from "@/hooks/use-job-processing";
import { useJobSelection } from "@/hooks/use-job-selection";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { JobStatusSelect } from "./job-status-select";
import { JobThumbs } from "./job-thumbs";
import { ExtractPopover } from "./extract-popover";
import { JobRowExpanded } from "./job-row-expanded";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const columnHelper = createColumnHelper<Job>();

// Small components that subscribe to selection context directly so cell
// renders reflect the latest selection state. Putting the subscription
// inside `useMemo`'s captured cell fn freezes it at first render.
function RowCheckbox({ jobId }: { jobId: string }) {
  const { isSelected, toggleOne } = useJobSelection();
  const checked = isSelected(jobId);
  return (
    <input
      type="checkbox"
      checked={checked}
      onChange={() => toggleOne(jobId)}
      onClick={(e) => e.stopPropagation()}
      className="h-4 w-4 rounded border-gray-300 cursor-pointer accent-foreground"
      aria-label="Select job"
    />
  );
}

function HeaderCheckbox({ visibleIds }: { visibleIds: string[] }) {
  const { selectedIds, setSelection, selectMany } = useJobSelection();
  const allVisibleSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
  const someVisibleSelected =
    !allVisibleSelected && visibleIds.some((id) => selectedIds.has(id));

  const toggleAllVisible = () => {
    if (allVisibleSelected) {
      const next = new Set(selectedIds);
      for (const id of visibleIds) next.delete(id);
      setSelection(next);
    } else {
      selectMany(visibleIds);
    }
  };

  return (
    <input
      type="checkbox"
      checked={allVisibleSelected}
      ref={(el) => {
        if (el) el.indeterminate = someVisibleSelected;
      }}
      onChange={toggleAllVisible}
      onClick={(e) => e.stopPropagation()}
      className="h-4 w-4 rounded border-gray-300 cursor-pointer accent-foreground"
      aria-label="Select all jobs on this page"
    />
  );
}

function relativeDate(dateStr: string | null): string {
  if (!dateStr) return "—";
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "1d ago";
  if (diffDays < 30) return `${diffDays}d ago`;
  if (diffDays < 365) return `${Math.floor(diffDays / 30)}mo ago`;
  return `${Math.floor(diffDays / 365)}y ago`;
}

function formatSalaryShort(min: number | null, max: number | null): string {
  if (min === null && max === null) return "—";        // never parsed
  if (min === -1) return "N/A";                         // parsed, not provided
  const fmt = (n: number) => `$${Math.round(n / 1000)}K`;
  if (min && max && max > 0) return `${fmt(min)}–${fmt(max)}`;
  if (min && min > 0) return `${fmt(min)}+`;
  if (max && max > 0) return `Up to ${fmt(max)}`;
  return "N/A";
}

interface JobsTableProps {
  jobs: Job[];
  total: number;
  page: number;
  perPage: number;
  sorting: SortingState;
  onSortingChange: (sorting: SortingState) => void;
  onPageChange: (page: number) => void;
  onPerPageChange: (perPage: number) => void;
  params: JobsParams;
}

export function JobsTable({
  jobs,
  total,
  page,
  perPage,
  sorting,
  onSortingChange,
  onPageChange,
  onPerPageChange,
}: JobsTableProps) {
  const visibleIds = useMemo(() => jobs.map((j) => j.id), [jobs]);

  const columns = useMemo(
    () => [
      columnHelper.display({
        id: "_select",
        header: () => <HeaderCheckbox visibleIds={visibleIds} />,
        size: 32,
        enableSorting: false,
        cell: ({ row }) => <RowCheckbox jobId={row.original.id} />,
      }),
      columnHelper.accessor("status", {
        header: "Status",
        size: 110,
        enableSorting: true,
        cell: ({ row }) => <JobStatusSelect job={row.original} />,
      }),
      columnHelper.accessor("title", {
        header: "Title",
        size: 350,
        enableSorting: true,
        cell: ({ row, getValue }) => (
          <span className="font-medium truncate block max-w-[340px]">
            {getValue()}
            {row.original.expired_at && (
              <Badge variant="outline" className="ml-2 bg-red-50 text-red-600 border-red-200 text-[10px] py-0">
                Expired
              </Badge>
            )}
          </span>
        ),
      }),
      columnHelper.accessor("company", {
        header: "Company",
        size: 160,
        enableSorting: true,
        cell: ({ getValue }) => (
          <span className="truncate block max-w-[160px]">{getValue()}</span>
        ),
      }),
      columnHelper.accessor("location", {
        header: "Location",
        size: 200,
        enableSorting: false,
        cell: ({ row }) => {
          const normalized = row.original.normalized_locations ?? [];
          const wm = row.original.work_model;
          const remote = row.original.remote;

          const workModelBadge = wm === "remote" ? (
            <Badge variant="outline" className="bg-sky-50 text-sky-700 border-sky-200 text-xs">
              Remote
            </Badge>
          ) : wm === "hybrid" ? (
            <Badge variant="outline" className="bg-violet-50 text-violet-700 border-violet-200 text-xs">
              Hybrid
            </Badge>
          ) : null;

          // Prefer normalized locations as individual badges
          if (normalized.length > 0) {
            return (
              <div className="flex flex-wrap gap-1 max-w-[200px]">
                {normalized.map((l) => (
                  <Badge key={l.id} variant="outline" className="bg-slate-50 text-slate-700 border-slate-200 text-xs font-normal">
                    {l.display_name}
                  </Badge>
                ))}
                {workModelBadge}
              </div>
            );
          }

          // Fall back to raw location + legacy remote flag
          const loc = row.original.location;
          const showRemoteBadge = workModelBadge ?? (remote ? (
            <Badge variant="outline" className="bg-sky-50 text-sky-700 border-sky-200 text-xs">
              Remote
            </Badge>
          ) : null);

          if (!loc && showRemoteBadge) {
            return <div className="flex flex-wrap gap-1">{showRemoteBadge}</div>;
          }
          if (loc) {
            return (
              <div className="flex flex-wrap gap-1 max-w-[200px]">
                <Badge variant="outline" className="bg-slate-50 text-slate-700 border-slate-200 text-xs font-normal">
                  {loc}
                </Badge>
                {showRemoteBadge}
              </div>
            );
          }
          return <span className="text-sm text-muted-foreground">—</span>;
        },
      }),
      columnHelper.accessor("salary_min", {
        id: "salary_avg",
        header: "Salary",
        size: 120,
        enableSorting: true,
        cell: ({ row }) => (
          <span className="text-sm">
            {formatSalaryShort(row.original.salary_min, row.original.salary_max)}
          </span>
        ),
      }),
      columnHelper.accessor("source", {
        header: "Source",
        size: 100,
        enableSorting: false,
        cell: ({ getValue }) => {
          const config = SOURCE_CONFIG[getValue()];
          return (
            <Badge
              variant="outline"
              className={`${config.bg} ${config.color} border-0 text-xs`}
            >
              {config.label}
            </Badge>
          );
        },
      }),
      columnHelper.accessor("role_fit_score", {
        header: "Role",
        size: 60,
        enableSorting: true,
        cell: ({ getValue }) => {
          const v = getValue();
          if (v == null) return <span className="text-muted-foreground">—</span>;
          const color =
            v >= 70
              ? "text-emerald-600"
              : v >= 40
                ? "text-amber-600"
                : "text-red-500";
          return <span className={`text-sm font-medium ${color}`}>{v}</span>;
        },
      }),
      columnHelper.accessor("interest_fit_score", {
        header: "Interest",
        size: 70,
        enableSorting: true,
        cell: ({ getValue }) => {
          const v = getValue();
          if (v == null) return <span className="text-muted-foreground">—</span>;
          const color =
            v >= 70
              ? "text-emerald-600"
              : v >= 40
                ? "text-amber-600"
                : "text-red-500";
          return <span className={`text-sm font-medium ${color}`}>{v}</span>;
        },
      }),
      columnHelper.accessor("relevance_score", {
        id: "relevance_score",
        header: () => (
          <span title="Relevance" className="inline-flex items-center">
            <ArrowUpDown className="w-3 h-3 text-muted-foreground" />
          </span>
        ),
        size: 80,
        enableSorting: true,
        cell: ({ row }) => (
          <div className="flex items-center gap-0.5">
            <JobThumbs job={row.original} />
            <ExtractPopover job={row.original} />
          </div>
        ),
      }),
      columnHelper.accessor("posted_at", {
        header: "Posted",
        size: 100,
        enableSorting: true,
        cell: ({ row, getValue }) => (
          <div className="flex flex-col gap-0.5">
            <span className="text-sm text-muted-foreground">
              {relativeDate(getValue())}
            </span>
            {row.original.expired_at && (
              <span className="text-[10px] font-medium uppercase tracking-wide text-red-500 border border-red-200 rounded px-1 py-0 w-fit leading-4">
                Expired
              </span>
            )}
          </div>
        ),
      }),
    ],
    []
  );

  const { running, jobId: activeJobId, completedJobIds } = useResumeGeneration();
  const {
    processingJobIds,
    completedJobIds: completedProcessingIds,
  } = useJobProcessing();

  // Pin newly-added jobs (in-flight processing + recently completed) to the
  // top of the visible list regardless of sort. Users add jobs and then
  // lose track of them when scoring lands them mid-list — pinning surfaces
  // every just-added job until the completion glow fades.
  const pinnedIds = useMemo(() => {
    const s = new Set<string>();
    for (const id of processingJobIds) s.add(id);
    for (const id of completedProcessingIds) s.add(id);
    return s;
  }, [processingJobIds, completedProcessingIds]);

  const orderedJobs = useMemo(() => {
    if (pinnedIds.size === 0) return jobs;
    const pinned: Job[] = [];
    const rest: Job[] = [];
    for (const j of jobs) {
      if (pinnedIds.has(j.id)) pinned.push(j);
      else rest.push(j);
    }
    return [...pinned, ...rest];
  }, [jobs, pinnedIds]);

  const table = useReactTable({
    data: orderedJobs,
    columns,
    state: {
      sorting,
    },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      onSortingChange(next);
    },
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getRowCanExpand: () => true,
    manualSorting: true,
    manualPagination: true,
  });

  const totalPages = Math.ceil(total / perPage);
  const startItem = (page - 1) * perPage + 1;
  const endItem = Math.min(page * perPage, total);

  return (
    <div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead
                    key={header.id}
                    style={{ width: header.getSize() !== 150 ? header.getSize() : undefined }}
                    className={
                      header.column.getCanSort()
                        ? "cursor-pointer select-none hover:bg-muted/50"
                        : ""
                    }
                    onClick={header.column.getToggleSortingHandler()}
                  >
                    <div className="flex items-center gap-1">
                      {flexRender(
                        header.column.columnDef.header,
                        header.getContext()
                      )}
                      {header.column.getCanSort() && (
                        header.column.getIsSorted() === "asc" ? (
                          <ArrowUp className="h-3.5 w-3.5 text-foreground" />
                        ) : header.column.getIsSorted() === "desc" ? (
                          <ArrowDown className="h-3.5 w-3.5 text-foreground" />
                        ) : (
                          <ArrowUpDown className="h-3.5 w-3.5 text-muted-foreground/50" />
                        )
                      )}
                    </div>
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {jobs.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="text-center py-12 text-muted-foreground"
                >
                  No jobs found
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => {
                const isGenerating = running && activeJobId === row.original.id;
                const isProcessing = processingJobIds.has(row.original.id);
                const justCompletedResume = completedJobIds.has(row.original.id);
                const justCompletedProcessing = completedProcessingIds.has(row.original.id);
                const isExpired = !!row.original.expired_at;
                const showSpinner = isGenerating || isProcessing;
                const showGlow = justCompletedResume || justCompletedProcessing;
                const spinnerTooltip = isGenerating ? "Generating resume" : "Processing job";
                return (
                <Fragment key={row.id}>
                  <TableRow
                    className={`cursor-pointer hover:bg-muted/50 transition-colors duration-700 ${
                      showGlow ? "bg-emerald-50 ring-1 ring-emerald-300 ring-inset" : ""
                    } ${showSpinner ? "bg-amber-50/50" : ""} ${isExpired ? "opacity-50" : ""}`}
                    data-state={row.getIsExpanded() ? "expanded" : undefined}
                    onClick={() => row.toggleExpanded()}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id}>
                        <div className="flex items-center gap-1.5">
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext()
                          )}
                          {showSpinner && cell.column.id === "title" && (
                            <Tooltip>
                              <TooltipTrigger render={<span />}>
                                <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-600 shrink-0" />
                              </TooltipTrigger>
                              <TooltipContent>{spinnerTooltip}</TooltipContent>
                            </Tooltip>
                          )}
                        </div>
                      </TableCell>
                    ))}
                  </TableRow>
                  {row.getIsExpanded() && (
                    <JobRowExpanded
                      job={row.original}
                      colSpan={columns.length}
                    />
                  )}
                </Fragment>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between py-4">
        <div className="text-sm text-muted-foreground">
          {total > 0
            ? `Showing ${startItem}–${endItem} of ${total.toLocaleString()}`
            : "No results"}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={perPage}
            onChange={(e) => onPerPageChange(Number(e.target.value))}
            className="h-8 rounded-md border px-2 text-sm"
          >
            <option value={20}>20 / page</option>
            <option value={50}>50 / page</option>
            <option value={100}>100 / page</option>
          </select>
          <button
            className="h-8 px-3 rounded-md border text-sm disabled:opacity-50 hover:bg-muted/50"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            Previous
          </button>
          <span className="text-sm px-2">
            {page} / {totalPages || 1}
          </span>
          <button
            className="h-8 px-3 rounded-md border text-sm disabled:opacity-50 hover:bg-muted/50"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
