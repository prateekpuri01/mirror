"use client";

import { useMemo, useState } from "react";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, X, ExternalLink, RefreshCw } from "lucide-react";
import { CompanyListItem } from "@/lib/types";
import { SOURCE_CONFIG } from "@/lib/constants";
import { useDeleteCompany } from "@/hooks/use-companies";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const columnHelper = createColumnHelper<CompanyListItem>();

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

function DeleteCompanyButton({ company }: { company: CompanyListItem }) {
  const [open, setOpen] = useState(false);
  const deleteMutation = useDeleteCompany();

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        onClick={(e) => e.stopPropagation()}
        className="h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-red-600 hover:bg-red-50 transition-colors"
      >
        <X className="h-4 w-4" />
      </PopoverTrigger>
      <PopoverContent
        side="left"
        align="center"
        className="w-72"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-col gap-2">
          <p className="font-medium text-sm">Delete {company.name}?</p>
          {company.job_count > 0 && (
            <p className="text-sm text-muted-foreground">
              This will also delete {company.job_count}{" "}
              {company.job_count === 1 ? "job" : "jobs"}.
            </p>
          )}
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => setOpen(false)}
              className="h-8 px-3 rounded-md border text-sm hover:bg-muted/50"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                deleteMutation.mutate(company.id, {
                  onSuccess: () => setOpen(false),
                });
              }}
              disabled={deleteMutation.isPending}
              className="h-8 px-3 rounded-md bg-red-600 text-white text-sm hover:bg-red-700 disabled:opacity-50"
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

interface CompaniesTableProps {
  companies: CompanyListItem[];
  total: number;
  page: number;
  perPage: number;
  sorting: SortingState;
  onSortingChange: (sorting: SortingState) => void;
  onPageChange: (page: number) => void;
  onPerPageChange: (perPage: number) => void;
  onRefresh?: (company: CompanyListItem) => void;
  disableRefresh?: boolean;
}

export function CompaniesTable({
  companies,
  total,
  page,
  perPage,
  sorting,
  onSortingChange,
  onPageChange,
  onPerPageChange,
  onRefresh,
  disableRefresh,
}: CompaniesTableProps) {
  const totalPages = Math.ceil(total / perPage);
  const startItem = total > 0 ? (page - 1) * perPage + 1 : 0;
  const endItem = Math.min(page * perPage, total);

  const columns = useMemo(
    () => [
      columnHelper.accessor("name", {
        header: "Name",
        size: 220,
        enableSorting: true,
        cell: ({ row }) => {
          const company = row.original;
          const url = company.website;
          return (
            <div className="flex items-center gap-1.5">
              <span className="font-medium">{company.name}</span>
              {url && (
                <a
                  href={url.startsWith("http") ? url : `https://${url}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-muted-foreground hover:text-blue-600 transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
              )}
            </div>
          );
        },
      }),
      columnHelper.accessor("sources", {
        header: "Sources",
        size: 160,
        enableSorting: false,
        cell: ({ getValue }) => {
          const sources = getValue();
          if (!sources.length)
            return <span className="text-muted-foreground">—</span>;
          return (
            <div className="flex flex-wrap gap-1">
              {sources.map((src) => {
                const config = SOURCE_CONFIG[src as keyof typeof SOURCE_CONFIG];
                if (!config) return null;
                return (
                  <Badge
                    key={src}
                    variant="outline"
                    className={`${config.bg} ${config.color} border-0 text-xs`}
                  >
                    {config.label}
                  </Badge>
                );
              })}
            </div>
          );
        },
      }),
      columnHelper.accessor("job_count", {
        header: "Jobs",
        size: 70,
        enableSorting: true,
        cell: ({ getValue }) => (
          <span className="text-sm font-medium">{getValue()}</span>
        ),
      }),
      columnHelper.accessor("last_scraped_at", {
        header: "Last Scraped",
        size: 110,
        enableSorting: true,
        cell: ({ getValue }) => (
          <span className="text-sm text-muted-foreground">
            {relativeDate(getValue())}
          </span>
        ),
      }),
      columnHelper.accessor("monitoring_active", {
        header: "Monitoring",
        size: 100,
        enableSorting: false,
        cell: ({ getValue }) => {
          const active = getValue();
          return (
            <Badge
              variant="outline"
              className={
                active
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200 text-xs"
                  : "bg-gray-50 text-gray-500 border-gray-200 text-xs"
              }
            >
              {active ? "Active" : "Paused"}
            </Badge>
          );
        },
      }),
      columnHelper.display({
        id: "actions",
        header: "",
        size: 80,
        cell: ({ row }) => {
          const c = row.original;
          const canRefresh = c.job_count > 0;
          return (
            <div className="flex items-center gap-0.5">
              {onRefresh && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (canRefresh) onRefresh(c);
                  }}
                  disabled={!canRefresh || disableRefresh}
                  className="h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-blue-600 hover:bg-blue-50 transition-colors disabled:opacity-30 disabled:hover:text-muted-foreground disabled:hover:bg-transparent disabled:cursor-not-allowed"
                  title={disableRefresh ? "Another scrape is in progress" : canRefresh ? "Refresh jobs" : "No jobs to discover ATS from"}
                >
                  <RefreshCw className="h-4 w-4" />
                </button>
              )}
              <DeleteCompanyButton company={c} />
            </div>
          );
        },
      }),
    ],
    [onRefresh, disableRefresh]
  );

  const table = useReactTable({
    data: companies,
    columns,
    state: { sorting },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      onSortingChange(next);
    },
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
    pageCount: totalPages,
  });

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
                    style={{
                      width:
                        header.getSize() !== 150 ? header.getSize() : undefined,
                    }}
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
            {companies.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="text-center py-12 text-muted-foreground"
                >
                  No companies found.
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  className="hover:bg-muted/50 transition-colors"
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
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
