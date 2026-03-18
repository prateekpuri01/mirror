"use client";

import { Job, JobStatus } from "@/lib/types";
import { ALL_STATUSES, STATUS_CONFIG } from "@/lib/constants";
import { useUpdateJob } from "@/hooks/use-jobs";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";

interface JobStatusSelectProps {
  job: Job;
  size?: "sm" | "default";
}

export function JobStatusSelect({ job, size = "sm" }: JobStatusSelectProps) {
  const updateJob = useUpdateJob();
  const config = STATUS_CONFIG[job.status];

  function handleSelect(status: JobStatus) {
    if (status !== job.status) {
      updateJob.mutate({ id: job.id, data: { status } });
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="focus:outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <Badge
          variant="outline"
          className={`${config.bg} ${config.color} border-0 cursor-pointer hover:opacity-80 ${
            size === "default" ? "text-sm px-3 py-1" : "text-xs"
          }`}
        >
          {config.label}
        </Badge>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      >
        {ALL_STATUSES.map((status) => {
          const sc = STATUS_CONFIG[status];
          return (
            <DropdownMenuItem
              key={status}
              onClick={() => handleSelect(status)}
              className="cursor-pointer"
            >
              <span
                className={`inline-block w-2 h-2 rounded-full mr-2 ${sc.bg}`}
              />
              {sc.label}
              {status === job.status && (
                <span className="ml-auto text-muted-foreground">&#10003;</span>
              )}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
