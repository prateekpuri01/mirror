"use client";

import { ThumbsUp, ThumbsDown } from "lucide-react";
import { Job } from "@/lib/types";
import { useUpdateJob } from "@/hooks/use-jobs";
import { Button } from "@/components/ui/button";

interface JobThumbsProps {
  job: Job;
  size?: "sm" | "default";
}

export function JobThumbs({ job, size = "sm" }: JobThumbsProps) {
  const updateJob = useUpdateJob();

  function handleThumb(value: 1 | -1) {
    const newValue = job.thumbs === value ? null : value;
    updateJob.mutate({ id: job.id, data: { thumbs: newValue } });
  }

  const iconSize = size === "sm" ? 14 : 16;

  return (
    <div
      className="flex items-center gap-0.5"
      onClick={(e) => e.stopPropagation()}
    >
      <Button
        variant="ghost"
        size="icon-sm"
        className={
          job.thumbs === 1
            ? "bg-emerald-100 text-emerald-700 hover:bg-emerald-200"
            : "text-muted-foreground/40 hover:text-emerald-600 hover:bg-emerald-50"
        }
        onClick={() => handleThumb(1)}
        title={job.thumbs === 1 ? "Remove thumbs up" : "Thumbs up"}
      >
        <ThumbsUp
          size={iconSize}
          strokeWidth={job.thumbs === 1 ? 2.5 : 1.5}
          fill={job.thumbs === 1 ? "currentColor" : "none"}
        />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        className={
          job.thumbs === -1
            ? "bg-red-100 text-red-700 hover:bg-red-200"
            : "text-muted-foreground/40 hover:text-red-600 hover:bg-red-50"
        }
        onClick={() => handleThumb(-1)}
        title={job.thumbs === -1 ? "Remove thumbs down" : "Thumbs down"}
      >
        <ThumbsDown
          size={iconSize}
          strokeWidth={job.thumbs === -1 ? 2.5 : 1.5}
          fill={job.thumbs === -1 ? "currentColor" : "none"}
        />
      </Button>
    </div>
  );
}
