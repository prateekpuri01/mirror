import {
  ActionCardRead,
  ChatMessageRead,
  CompaniesParams,
  CompanyListResponse,
  DiscoverResponse,
  DocumentFull,
  ImportRequest,
  ImportResponse,
  Job,
  JobListResponse,
  JobUpdate,
  JobsParams,
  LocationRead,
  ProfileData,
  ProfileCompleteData,
  ProfileAccomplishment,
  ProfilePublication,
  RefreshResponse,
  ResumeDesign,
  ResumeJson,
  TagRead,
} from "./types";

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
    let detail = "";
    try {
      const body = await res.json();
      detail = body?.detail ? ` — ${body.detail}` : "";
    } catch {
      // ignore non-JSON bodies
    }
    throw new Error(`API error: ${res.status} ${res.statusText}${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export function getApiUrl(): string {
  return API_URL;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export async function fetchJobs(params: JobsParams = {}): Promise<JobListResponse> {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    // Array values (e.g. pin_ids) need to be sent as repeated query
    // params (?pin_ids=a&pin_ids=b) so FastAPI's `list[str]` binding
    // picks them up. Calling URLSearchParams.set on an array would
    // stringify to "a,b" which the backend would treat as a single value.
    if (Array.isArray(value)) {
      for (const v of value) {
        if (v !== undefined && v !== null && v !== "") {
          searchParams.append(key, String(v));
        }
      }
    } else {
      searchParams.set(key, String(value));
    }
  }
  const qs = searchParams.toString();
  return apiFetch<JobListResponse>(`/api/jobs${qs ? `?${qs}` : ""}`);
}

export async function fetchJob(id: string): Promise<Job> {
  return apiFetch<Job>(`/api/jobs/${id}`);
}

export async function importJobFromUrl(url: string): Promise<Job> {
  return apiFetch<Job>("/api/jobs/import-from-url", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

/** Bulk-delete jobs by ID. Fans out to the per-job DELETE endpoint in
 *  parallel; resolves with the count of successful deletions and any
 *  errors so the UI can report partial failures. */
export async function deleteJobs(ids: string[]): Promise<{
  succeeded: string[];
  failed: { id: string; error: string }[];
}> {
  const results = await Promise.all(
    ids.map(async (id) => {
      try {
        await apiFetch<void>(`/api/jobs/${id}`, { method: "DELETE" });
        return { id, ok: true as const };
      } catch (err) {
        return {
          id,
          ok: false as const,
          error: err instanceof Error ? err.message : String(err),
        };
      }
    }),
  );
  return {
    succeeded: results.filter((r) => r.ok).map((r) => r.id),
    failed: results
      .filter((r): r is { id: string; ok: false; error: string } => !r.ok)
      .map((r) => ({ id: r.id, error: r.error })),
  };
}


export async function updateJob(id: string, data: JobUpdate): Promise<Job> {
  return apiFetch<Job>(`/api/jobs/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

// ---------------------------------------------------------------------------
// Tags & locations
// ---------------------------------------------------------------------------

export async function fetchTags(): Promise<TagRead[]> {
  return apiFetch<TagRead[]>("/api/tags");
}

export async function fetchLocations(): Promise<LocationRead[]> {
  return apiFetch<LocationRead[]>("/api/extraction/locations");
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

// ---------------------------------------------------------------------------
// Extraction
// ---------------------------------------------------------------------------

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
  data: {
    salary_min: number | null;
    salary_max: number | null;
    work_model: string | null;
    locations: { city: string; state: string | null; country: string; display_name: string }[];
  },
): Promise<{ status: string }> {
  return apiFetch(`/api/extraction/apply/${jobId}`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ---------------------------------------------------------------------------
// Resume generation, documents, section editing
// ---------------------------------------------------------------------------

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
  phase: string | null;
  step: string | null;
  step_detail: string | null;
  step_number: number;
  total_steps: number;
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
  instruction: string,
): Promise<{ status: string; doc_id: string }> {
  return apiFetch(`/api/documents/${docId}/revise`, {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });
}

export async function fetchResumeJson(docId: string): Promise<ResumeJson> {
  return apiFetch<ResumeJson>(`/api/documents/${docId}/json`);
}

export async function updateResumeSection(
  docId: string,
  path: string,
  value: unknown,
): Promise<DocumentFull> {
  return apiFetch<DocumentFull>(`/api/documents/${docId}/section`, {
    method: "PATCH",
    body: JSON.stringify({ path, value }),
  });
}

export async function generateResearchEntry(
  docId: string,
  accomplishmentId: string,
  index: number,
): Promise<DocumentFull> {
  // The backend wraps the updated document under a `document` key alongside
  // the freshly-generated `entry`. The API client unwraps so callers can
  // treat the return value as the document (mirroring how a plain PATCH
  // returns DocumentFull). Without this unwrap the UI was setting state
  // to the wrapper object, leaving content_json undefined and the swap
  // appearing not to apply.
  const res = await apiFetch<{ entry: unknown; document: DocumentFull }>(
    `/api/documents/${docId}/generate-research-entry`,
    {
      method: "POST",
      body: JSON.stringify({ accomplishment_id: accomplishmentId, index }),
    },
  );
  return res.document;
}

export async function addResearchEntry(
  docId: string,
  accomplishmentId: string,
): Promise<DocumentFull> {
  // Mirrors generateResearchEntry but appends instead of swapping. The
  // backend ignores `index` for this endpoint — passed as 0 to satisfy
  // the shared ResearchEntryRequest schema.
  const res = await apiFetch<{ entry: unknown; document: DocumentFull }>(
    `/api/documents/${docId}/add-research-entry`,
    {
      method: "POST",
      body: JSON.stringify({ accomplishment_id: accomplishmentId, index: 0 }),
    },
  );
  return res.document;
}

export async function generateBullet(
  docId: string,
  employerKey: string,
  accomplishmentId: string,
  insertAt?: number,
): Promise<DocumentFull> {
  // Same wrapping pattern as generate-research-entry — see comment above.
  const res = await apiFetch<{
    bullet: unknown;
    insert_at: number;
    document: DocumentFull;
  }>(`/api/documents/${docId}/generate-bullet`, {
    method: "POST",
    body: JSON.stringify({
      employer_key: employerKey,
      accomplishment_id: accomplishmentId,
      insert_at: insertAt ?? null,
    }),
  });
  return res.document;
}

// ---------------------------------------------------------------------------
// Content memory (past versions per entity)
// ---------------------------------------------------------------------------

export interface SectionHistoryItem {
  id: string;
  job_title: string | null;
  company_name: string | null;
  updated_at: string;
  preview: string;
  user_text: string | null;
  user_payload_json: unknown;
}

export interface SectionHistoryResponse {
  entity_type: string;
  entity_key: string;
  items: SectionHistoryItem[];
}

export async function fetchSectionHistory(
  docId: string,
  entityType: string,
  entityKey: string,
): Promise<SectionHistoryResponse> {
  const qs = new URLSearchParams({
    entity_type: entityType,
    entity_key: entityKey,
  }).toString();
  return apiFetch<SectionHistoryResponse>(
    `/api/documents/${docId}/section-history?${qs}`,
  );
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
// Action cards (brainstorm)
// ---------------------------------------------------------------------------

export async function fetchActionCards(jobId: string): Promise<ActionCardRead[]> {
  return apiFetch<ActionCardRead[]>(`/api/jobs/${jobId}/chat/action_cards`);
}

export interface ApplyActionCardResponse {
  id: string;
  status: "applied";
  section_path: string;
  new_value: unknown;
}

export async function applyActionCard(cardId: string): Promise<ApplyActionCardResponse> {
  return apiFetch<ApplyActionCardResponse>(
    `/api/chat/action_cards/${cardId}/apply`,
    { method: "POST" },
  );
}

export async function dismissActionCard(
  cardId: string,
): Promise<{ id: string; status: "dismissed" }> {
  return apiFetch<{ id: string; status: "dismissed" }>(
    `/api/chat/action_cards/${cardId}/dismiss`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// Application requirements
// ---------------------------------------------------------------------------

export async function triggerRequirementsExtraction(
  jobId: string,
): Promise<{ status: string; job_id: string }> {
  return apiFetch<{ status: string; job_id: string }>(
    `/api/jobs/${jobId}/requirements/extract`,
    { method: "POST" },
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
  short_answer_questions:
    | { question: string; max_length: number | null; required: boolean }[]
    | null;
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

export interface ScrapeProgress {
  active: boolean;
  company_name: string;
  phase: string;
  found: number;
  total: number | null;
  elapsed_seconds: number;
}

export async function getScrapeProgress(): Promise<ScrapeProgress> {
  return apiFetch<ScrapeProgress>("/api/companies/scrape-progress");
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

export async function fetchCompanyResearch(
  jobId: string,
): Promise<{
  available: boolean;
  research: import("./types").CompanyResearch | null;
}> {
  return apiFetch(`/api/jobs/${jobId}/company-research`);
}

export async function runCompanyResearch(
  jobId: string,
  force: boolean = false,
): Promise<{ research: import("./types").CompanyResearch }> {
  return apiFetch(
    `/api/jobs/${jobId}/company-research${force ? "?force=true" : ""}`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// Pipeline / scoring
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

// ---------------------------------------------------------------------------
// Setup wizard (provider + API keys, persisted to app_settings)
// ---------------------------------------------------------------------------

export type LLMProvider = "openai" | "anthropic" | "ollama";

export interface SetupStatusResponse {
  needs_setup: boolean;
  llm_provider: LLMProvider;
  has_openai_key: boolean;
  has_anthropic_key: boolean;
  ollama_base_url: string;
}

export interface TestKeyResponse {
  valid: boolean;
  error?: string | null;
  rate_limits?: {
    requests_per_minute?: number;
    tokens_per_minute?: number;
    remaining_requests?: number;
    remaining_tokens?: number;
  } | null;
}

export interface SaveKeysPayload {
  llm_provider?: LLMProvider;
  openai_api_key?: string;
  anthropic_api_key?: string;
  ollama_base_url?: string;
}

export async function checkSetupStatus(): Promise<SetupStatusResponse> {
  return apiFetch<SetupStatusResponse>("/api/setup/status");
}

export async function testApiKey(openaiApiKey: string): Promise<TestKeyResponse> {
  return apiFetch<TestKeyResponse>("/api/setup/test-key", {
    method: "POST",
    body: JSON.stringify({ openai_api_key: openaiApiKey }),
  });
}

export async function saveApiKeys(
  payload: SaveKeysPayload,
): Promise<{ success: boolean; saved: number; llm_provider: LLMProvider }> {
  return apiFetch("/api/setup/save-keys", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// Onboarding (resume upload + URL crawl + profile assembly)
// ---------------------------------------------------------------------------

export interface OnboardingStatusResponse {
  needs_onboarding: boolean;
  has_profile: boolean;
}

export async function checkOnboardingStatus(): Promise<OnboardingStatusResponse> {
  return apiFetch<OnboardingStatusResponse>("/api/onboarding/status");
}

export async function uploadResume(file: File): Promise<{
  success: boolean;
  extracted_profile: ProfileData;
  extracted_complete_profile: ProfileCompleteData;
  resume_text: string;
  error: string | null;
}> {
  const form = new FormData();
  form.append("file", file);
  // Don't set Content-Type — browser will set multipart/form-data with boundary
  const res = await fetch(`${API_URL}/api/onboarding/upload-resume`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body?.detail ? ` — ${body.detail}` : "";
    } catch {}
    throw new Error(`Upload failed: ${res.status}${detail}`);
  }
  return res.json();
}

export async function startUrlCrawl(
  urls: { type: string; url: string }[],
): Promise<{ task_id: string; status: string }> {
  return apiFetch("/api/onboarding/crawl-urls", {
    method: "POST",
    body: JSON.stringify({ urls }),
  });
}

export async function getCrawlStatus(taskId: string): Promise<{
  task_id: string;
  status: "idle" | "running" | "completed" | "failed";
  total_urls: number;
  completed_urls: number;
  results: Record<
    string,
    { url: string; text: string; success: boolean; error: string | null }
  >;
  error: string | null;
}> {
  return apiFetch(`/api/onboarding/crawl-status/${taskId}`);
}

export async function assembleProfile(data: {
  resume_text: string;
  resume_extracted: ProfileData;
  url_texts: Record<string, string>;
  resume_extracted_complete?: ProfileCompleteData;
}): Promise<{ profile: ProfileData; complete_profile: ProfileCompleteData }> {
  return apiFetch("/api/onboarding/assemble-profile", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function saveOnboardingProfile(data: {
  profile: ProfileData;
  complete_profile?: ProfileCompleteData;
}): Promise<{ success: boolean; profile: ProfileData }> {
  return apiFetch("/api/onboarding/save-profile", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ---------------------------------------------------------------------------
// Profile (read, patch, suggest, enrich)
// ---------------------------------------------------------------------------

export async function fetchProfile(): Promise<ProfileData> {
  return apiFetch<ProfileData>("/api/profile");
}

export async function fetchCompleteProfile(): Promise<ProfileCompleteData> {
  return apiFetch<ProfileCompleteData>("/api/profile/complete");
}

export async function updateProfile(patch: Partial<ProfileData>): Promise<ProfileData> {
  return apiFetch<ProfileData>("/api/profile", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function updateCompleteProfile(
  patch: Partial<ProfileCompleteData>,
): Promise<ProfileCompleteData> {
  return apiFetch<ProfileCompleteData>("/api/profile/complete", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

// Resume + minimal profile data the design tab's side preview renders.
// Backend selects the user's most recent resume content if one exists,
// otherwise returns the bundled sample fixture (is_sample=true).
export interface ResumeDesignPreviewContent {
  is_sample: boolean;
  resume: ResumeJson;
  profile: {
    personal: Record<string, unknown>;
    education: { degree: string; field: string; institution: string; year: string; honors?: string }[];
    work_history: { employer: string; title: string; start: string; end?: string; location?: string }[];
  };
}

export async function fetchResumeDesignPreviewContent(): Promise<ResumeDesignPreviewContent> {
  return apiFetch<ResumeDesignPreviewContent>("/api/profile/resume-design/preview-content");
}

// Download a sample .docx rendered with the given design overrides. The
// caller's overrides take precedence over what's saved on the profile —
// this is how the design tab previews unsaved selections.
export async function downloadResumeDesignSample(
  overrides?: Partial<ResumeDesign>,
): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/profile/resume-design/sample`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(overrides ?? {}),
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body?.detail ? ` — ${body.detail}` : "";
    } catch {}
    throw new Error(`Sample download failed: ${res.status}${detail}`);
  }
  return res.blob();
}

export async function generateKeywords(
  lookingFor: string,
  notLookingFor: string,
): Promise<{ positive_signals: string[]; exclusions: string[] }> {
  return apiFetch("/api/profile/generate-keywords", {
    method: "POST",
    body: JSON.stringify({ looking_for: lookingFor, not_looking_for: notLookingFor }),
  });
}

export async function suggestProfileSection(
  section: "domains" | "skills" | "target_roles" | "looking_for",
): Promise<Record<string, unknown>> {
  return apiFetch("/api/profile/suggest", {
    method: "POST",
    body: JSON.stringify({ section }),
  });
}

export async function enrichAccomplishment(
  accomplishment: ProfileAccomplishment,
): Promise<{
  success: boolean;
  error?: string;
  so_what?: string;
  skills_demonstrated?: string[];
  relevance_weight?: number;
  tags?: string[];
} & Partial<ProfileAccomplishment>> {
  return apiFetch("/api/profile/accomplishments/enrich", {
    method: "POST",
    body: JSON.stringify(accomplishment),
  });
}

export async function lookupPublication(
  title: string,
): Promise<{ found: boolean; publication?: ProfilePublication }> {
  return apiFetch("/api/profile/publications/lookup", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export async function importScholarPublications(
  url?: string,
): Promise<{
  success: boolean;
  publications: ProfilePublication[];
  error: string | null;
}> {
  return apiFetch("/api/profile/publications/import-scholar", {
    method: "POST",
    body: JSON.stringify({ url: url ?? null }),
  });
}

export async function scrapeLinkedIn(
  url?: string,
): Promise<{
  success: boolean;
  accomplishments: ProfileAccomplishment[];
  error: string | null;
}> {
  return apiFetch("/api/profile/linkedin/scrape", {
    method: "POST",
    body: JSON.stringify({ url: url ?? null }),
  });
}

export async function getLinkedInStatus(): Promise<{
  cached: boolean;
  scraped_at: string | null;
  url: string | null;
}> {
  return apiFetch("/api/profile/linkedin/status");
}

// ---------------------------------------------------------------------------
// Writing memory (rules learned from edits)
// ---------------------------------------------------------------------------

export type WritingMemoryCategory =
  | "word_choice"
  | "tone"
  | "structure"
  | "content"
  | "formatting";

export type WritingMemoryScope = "universal" | "job_specific";

export interface WritingMemoryRule {
  id: string;
  domain: string;
  rule_text: string;
  category: WritingMemoryCategory;
  scope: WritingMemoryScope;
  examples_json: { before?: string; after?: string }[] | null;
  source_type: string;
  source_job_id: string | null;
  confidence: number;
  occurrence_count: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface WritingMemoryConsolidateReport {
  before: number;
  after: number;
  merged_count: number;
  groups_merged: number;
  merges: { merged_rule_text: string; merged_from: string[]; reasoning: string }[];
}

export async function listWritingMemoryRules(opts?: {
  domain?: string;
  scope?: WritingMemoryScope;
  isActive?: boolean;
}): Promise<WritingMemoryRule[]> {
  const params = new URLSearchParams();
  if (opts?.domain) params.set("domain", opts.domain);
  if (opts?.scope) params.set("scope", opts.scope);
  if (opts?.isActive !== undefined) params.set("is_active", String(opts.isActive));
  const qs = params.toString();
  return apiFetch<WritingMemoryRule[]>(`/api/writing-memory${qs ? `?${qs}` : ""}`);
}

export async function createWritingMemoryRule(rule: {
  domain: string;
  rule_text: string;
  category: WritingMemoryCategory;
  scope?: WritingMemoryScope;
  examples_json?: { before: string; after: string }[];
}): Promise<WritingMemoryRule> {
  return apiFetch<WritingMemoryRule>("/api/writing-memory", {
    method: "POST",
    body: JSON.stringify(rule),
  });
}

export async function updateWritingMemoryRule(
  ruleId: string,
  patch: {
    rule_text?: string;
    category?: WritingMemoryCategory;
    scope?: WritingMemoryScope;
    is_active?: boolean;
  },
): Promise<WritingMemoryRule> {
  return apiFetch<WritingMemoryRule>(`/api/writing-memory/${ruleId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deleteWritingMemoryRule(ruleId: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/writing-memory/${ruleId}`, { method: "DELETE" });
}

export async function consolidateWritingMemory(
  domain: string = "resume",
): Promise<WritingMemoryConsolidateReport> {
  return apiFetch<WritingMemoryConsolidateReport>(
    `/api/writing-memory/consolidate?domain=${encodeURIComponent(domain)}`,
    { method: "POST" },
  );
}
