import { JobSource, JobStatus } from "./types";

export const STATUS_CONFIG: Record<
  JobStatus,
  { label: string; color: string; bg: string }
> = {
  new: { label: "New", color: "text-blue-700", bg: "bg-blue-100" },
  interested: { label: "Interested", color: "text-emerald-700", bg: "bg-emerald-100" },
  applied: { label: "Applied", color: "text-violet-700", bg: "bg-violet-100" },
  interviewing: { label: "Interviewing", color: "text-amber-700", bg: "bg-amber-100" },
  rejected: { label: "Rejected", color: "text-red-700", bg: "bg-red-100" },
  offer: { label: "Offer", color: "text-green-700", bg: "bg-green-100" },
  archived: { label: "Archived", color: "text-gray-700", bg: "bg-gray-100" },
};

export const ALL_STATUSES: JobStatus[] = [
  "new",
  "interested",
  "applied",
  "interviewing",
  "rejected",
  "offer",
  "archived",
];

export const SOURCE_CONFIG: Record<
  JobSource,
  { label: string; color: string; bg: string }
> = {
  greenhouse: { label: "Greenhouse", color: "text-green-700", bg: "bg-green-50" },
  lever: { label: "Lever", color: "text-blue-700", bg: "bg-blue-50" },
  ashby: { label: "Ashby", color: "text-purple-700", bg: "bg-purple-50" },
  hn_who_is_hiring: { label: "HN", color: "text-orange-700", bg: "bg-orange-50" },
  linkedin: { label: "LinkedIn", color: "text-sky-700", bg: "bg-sky-50" },
  ai_discovered: { label: "AI", color: "text-pink-700", bg: "bg-pink-50" },
  manual: { label: "Manual", color: "text-gray-700", bg: "bg-gray-50" },
  company_website: { label: "Website", color: "text-teal-700", bg: "bg-teal-50" },
};

export const ALL_SOURCES: JobSource[] = [
  "greenhouse",
  "lever",
  "ashby",
  "hn_who_is_hiring",
  "linkedin",
  "ai_discovered",
  "manual",
  "company_website",
];
