import { ChatMessageRead, CompaniesParams, CompanyListResponse, DiscoverResponse, DocumentFull, ImportRequest, ImportResponse, Job, JobListResponse, JobUpdate, JobsParams, LocationRead, RefreshResponse, ResumeJson, TagRead } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function fetchJobs(params: JobsParams = {}): Promise<JobListResponse> {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.set(key, String(value));
    }
  }
  const qs = searchParams.toString();
  return apiFetch<JobListResponse>(`/api/jobs${qs ? `?${qs}` : ""}`);
}

export async function fetchJob(id: string): Promise<Job> {
  return apiFetch<Job>(`/api/jobs/${id}`);
}

export async function updateJob(id: string, data: JobUpdate): Promise<Job> {
  return apiFetch<Job>(`/api/jobs/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function fetchTags(): Promise<TagRead[]> {
  return apiFetch<TagRead[]>("/api/tags");
}

export async function fetchLocations(): Promise<LocationRead[]> {
  return apiFetch<LocationRead[]>("/api/extraction/locations");
}

// Extraction preview / apply
export interface ExtractionPreview {
  salary_min: number | null;
  salary_max: number | null;
  salary_confidence: string | null;
  work_model: string | null;
  locations: { city: string; state: string | null; country: string; display_name: string }[];
}

export async function extractPreview(jobId: string): Promise<ExtractionPreview> {
  return apiFetch<ExtractionPreview>(`/api/extraction/preview/${jobId}`, { method: "POST" });
}

export async function extractApply(
  jobId: string,
  data: { salary_min: number | null; salary_max: number | null; work_model: string | null; locations: { city: string; state: string | null; country: string; display_name: string }[] },
): Promise<{ status: string }> {
  return apiFetch(`/api/extraction/apply/${jobId}`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function addTagsToJob(jobId: string, tagIds: string[]): Promise<Job> {
  return apiFetch<Job>(`/api/jobs/${jobId}/tags`, {
    method: "POST",
    body: JSON.stringify(tagIds),
  });
}

export async function removeTagFromJob(jobId: string, tagId: string): Promise<void> {
  return apiFetch<void>(`/api/jobs/${jobId}/tags/${tagId}`, {
    method: "DELETE",
  });
}

// Resume generation
export async function generateResume(jobId: string): Promise<{ status: string; job_id: string }> {
  return apiFetch<{ status: string; job_id: string }>(`/api/jobs/${jobId}/generate-resume`, {
    method: "POST",
  });
}

export async function getResumeStatus(): Promise<{
  running: boolean;
  job_id: string | null;
  started_at: string | null;
  error: string | null;
}> {
  return apiFetch(`/api/resume-status`);
}

export function getDocumentDownloadUrl(docId: string): string {
  return `${API_URL}/api/documents/${docId}/download`;
}

export async function fetchDocument(docId: string): Promise<DocumentFull> {
  return apiFetch<DocumentFull>(`/api/documents/${docId}`);
}

export async function reviseDocument(
  docId: string,
  instruction: string
): Promise<{ status: string; doc_id: string }> {
  return apiFetch(`/api/documents/${docId}/revise`, {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });
}

// ---------------------------------------------------------------------------
// Resume JSON + section editing
// ---------------------------------------------------------------------------

export async function fetchResumeJson(docId: string): Promise<ResumeJson> {
  return apiFetch<ResumeJson>(`/api/documents/${docId}/json`);
}

export async function updateResumeSection(
  docId: string,
  path: string,
  value: unknown
): Promise<DocumentFull> {
  return apiFetch<DocumentFull>(`/api/documents/${docId}/section`, {
    method: "PATCH",
    body: JSON.stringify({ path, value }),
  });
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export async function fetchChatMessages(jobId: string): Promise<ChatMessageRead[]> {
  return apiFetch<ChatMessageRead[]>(`/api/jobs/${jobId}/chat`);
}

export async function clearChat(jobId: string): Promise<{ deleted: number }> {
  return apiFetch<{ deleted: number }>(`/api/jobs/${jobId}/chat`, {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Application Requirements Extraction
// ---------------------------------------------------------------------------

export async function triggerRequirementsExtraction(
  jobId: string
): Promise<{ status: string; job_id: string }> {
  return apiFetch<{ status: string; job_id: string }>(
    `/api/jobs/${jobId}/requirements/extract`,
    { method: "POST" }
  );
}

export async function getExtractionStatus(jobId: string): Promise<{
  job_id: string;
  extraction_status: string | null;
  extraction_error: string | null;
  extracted_at: string | null;
  extraction_source_url: string | null;
  in_progress: boolean;
}> {
  return apiFetch(`/api/jobs/${jobId}/requirements/extract/status`);
}

export interface ApplicationField {
  label: string;
  response_type: "short_answer" | "personal_info" | "fixed_response" | "free_text";
  field_type: string | null;
  required: boolean;
  options: string[] | null;
  max_length: number | null;
  description: string | null;
  draft_response: string | null;
}

export async function draftAllAnswers(
  jobId: string,
): Promise<{ drafts: Record<string, string> }> {
  return apiFetch(`/api/jobs/${jobId}/requirements/draft-answers`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function draftSingleAnswer(
  jobId: string,
  fieldLabel: string,
  instructions?: string,
): Promise<{ field_label: string; draft_response: string }> {
  return apiFetch(`/api/jobs/${jobId}/requirements/draft-answers`, {
    method: "POST",
    body: JSON.stringify({ field_label: fieldLabel, instructions: instructions || null }),
  });
}

export async function updateDraftResponse(
  jobId: string,
  fieldLabel: string,
  draftResponse: string,
): Promise<{ field_label: string; draft_response: string }> {
  return apiFetch(`/api/jobs/${jobId}/requirements/draft`, {
    method: "PATCH",
    body: JSON.stringify({ field_label: fieldLabel, draft_response: draftResponse }),
  });
}

export async function fetchRequirements(jobId: string): Promise<{
  id: string;
  job_id: string;
  needs_resume: boolean;
  needs_cover_letter: boolean;
  needs_short_answers: boolean;
  needs_other: boolean;
  cover_letter_status: string | null;
  short_answer_questions: { question: string; max_length: number | null; required: boolean }[] | null;
  other_requirements: { type: string; description: string }[] | null;
  extraction_status: string | null;
  extraction_error: string | null;
  extracted_at: string | null;
  extraction_source_url: string | null;
  raw_extraction: Record<string, unknown> | null;
  application_fields: ApplicationField[] | null;
}> {
  return apiFetch(`/api/jobs/${jobId}/requirements`);
}

export function getApiUrl(): string {
  return API_URL;
}

// ---------------------------------------------------------------------------
// Companies
// ---------------------------------------------------------------------------

export async function fetchCompanies(params: CompaniesParams = {}): Promise<CompanyListResponse> {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.set(key, String(value));
    }
  }
  const qs = searchParams.toString();
  return apiFetch<CompanyListResponse>(`/api/companies${qs ? `?${qs}` : ""}`);
}

export async function deleteCompany(companyId: string): Promise<void> {
  return apiFetch<void>(`/api/companies/${companyId}`, { method: "DELETE" });
}

export async function discoverCompany(jobUrl: string): Promise<DiscoverResponse> {
  return apiFetch<DiscoverResponse>("/api/companies/discover", {
    method: "POST",
    body: JSON.stringify({ job_url: jobUrl }),
  });
}

export async function refreshCompany(companyId: string): Promise<RefreshResponse> {
  return apiFetch<RefreshResponse>(`/api/companies/${companyId}/refresh`, {
    method: "POST",
  });
}

export async function importCompany(data: ImportRequest): Promise<ImportResponse> {
  return apiFetch<ImportResponse>("/api/companies/import", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

export async function scoreJob(jobId: string): Promise<{
  job_id: string;
  role_fit_score: number | null;
  interest_fit_score: number | null;
  relevance_score: number | null;
  score_rationale: Record<string, unknown> | null;
}> {
  return apiFetch(`/api/scoring/score/${jobId}`, { method: "POST" });
}

export async function rescoreBatch(): Promise<{
  job_ids: string[];
  current_version: string;
}> {
  return apiFetch(`/api/pipeline/rescore`, { method: "POST" });
}

export async function fetchPipelineStatus(): Promise<{
  scraped: number;
  cleaned: number;
  scored: number;
  skipped: number;
  total: number;
  expired: number;
}> {
  return apiFetch(`/api/pipeline/status`);
}

export async function runGarbageCollect(expiredDays?: number): Promise<{
  archived: number;
}> {
  const qs = expiredDays !== undefined ? `?expired_days=${expiredDays}` : "";
  return apiFetch(`/api/pipeline/liveness/gc${qs}`, { method: "POST" });
}
