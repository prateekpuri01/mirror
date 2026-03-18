// TypeScript types mirroring backend Pydantic schemas

export type JobStatus =
  | "new"
  | "interested"
  | "applied"
  | "interviewing"
  | "rejected"
  | "offer"
  | "archived";

export type JobSource =
  | "greenhouse"
  | "linkedin"
  | "ai_discovered"
  | "manual"
  | "ashby"
  | "lever"
  | "hn_who_is_hiring"
  | "company_website";

export type DocType = "resume" | "cover_letter" | "short_answer" | "other";

export interface TagRead {
  id: string;
  name: string;
  color: string | null;
}

export interface SearchProfileBrief {
  id: string;
  name: string;
}

export interface AppReqBrief {
  id: string;
  needs_resume: boolean;
  needs_cover_letter: boolean;
  needs_short_answers: boolean;
}

export interface DocumentBrief {
  id: string;
  doc_type: DocType;
  name: string;
  version: number;
  content_docx_path: string | null;
}

export interface DocumentFull {
  id: string;
  job_id: string | null;
  doc_type: DocType;
  name: string;
  content_markdown: string | null;
  content_json: ResumeJson | null;
  content_docx_path: string | null;
  is_base_template: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface LocationRead {
  id: string;
  display_name: string;
  city: string;
  state: string | null;
  country: string;
  is_remote: boolean;
}

export interface Job {
  id: string;
  title: string;
  company: string;
  company_id: string | null;
  location: string | null;
  remote: boolean;
  work_model: "remote" | "hybrid" | "onsite" | null;
  salary_min: number | null;
  salary_max: number | null;
  description: string;
  description_html: string | null;
  url: string;
  application_url: string | null;
  source: JobSource;
  posted_at: string | null;
  scraped_at: string;
  status: JobStatus;
  relevance_score: number | null;
  role_fit_score: number | null;
  interest_fit_score: number | null;
  score_rationale: Record<string, unknown> | null;
  thumbs: number | null;
  user_notes: string | null;
  extra_metadata: Record<string, unknown> | null;
  clean_title: string | null;
  clean_company: string | null;
  pipeline_stage: string;
  cleaned_at: string | null;
  scored_at: string | null;
  score_prompt_version: string | null;
  last_seen_at: string | null;
  expired_at: string | null;
  prefilter_pass: boolean | null;
  display_title: string;
  display_company: string;
  created_at: string;
  updated_at: string;
  tags: TagRead[];
  search_profiles: SearchProfileBrief[];
  application_requirements: AppReqBrief | null;
  documents: DocumentBrief[];
  normalized_locations: LocationRead[];
}

export interface JobListResponse {
  items: Job[];
  total: number;
  page: number;
  per_page: number;
}

// ---------------------------------------------------------------------------
// Resume JSON structure (mirrors backend resume schema)
// ---------------------------------------------------------------------------

export interface ResearchEntry {
  category_label: string;
  title: string;
  description: string;
  accomplishment_id?: string;
}

export interface ExperienceBlock {
  bullets: string[];
  accomplishment_ids?: string[];
}

export interface ResumeJson {
  tagline: string;
  summary: string;
  selected_research: ResearchEntry[];
  experience: Record<string, ExperienceBlock>;
  publications: { citation: string; publication_id?: string }[];
  technical_skills: {
    ai_systems: string;
    data_science: string;
    engineering: string;
    communication: string;
  };
  awards: string;
  tailoring_rationale?: string;
}

// ---------------------------------------------------------------------------
// Chat messages
// ---------------------------------------------------------------------------

export interface ChatMessageRead {
  id: string;
  job_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  section_context: string | null;
  created_at: string;
}

export interface JobUpdate {
  status?: JobStatus;
  thumbs?: number | null;
  user_notes?: string | null;
  relevance_score?: number | null;
  location?: string | null;
  remote?: boolean;
  salary_min?: number | null;
  salary_max?: number | null;
}

export interface JobsParams {
  page?: number;
  per_page?: number;
  status?: JobStatus;
  source?: JobSource;
  tag?: string;
  min_relevance?: number;
  thumbs?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  q?: string;
  remote?: boolean;
  company?: string;
  location?: string;
  work_model?: string;
  min_salary?: number;
}

// ---------------------------------------------------------------------------
// Companies
// ---------------------------------------------------------------------------

export interface CompanyListResponse {
  items: CompanyListItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface CompaniesParams {
  page?: number;
  per_page?: number;
  q?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export interface CompanyListItem {
  id: string;
  name: string;
  website: string | null;
  greenhouse_slug: string | null;
  ashby_slug: string | null;
  lever_slug: string | null;
  careers_url: string | null;
  notes: string | null;
  monitoring_active: boolean;
  include_patterns: string[] | null;
  exclude_patterns: string[] | null;
  aliases: string[] | null;
  last_scraped_at: string | null;
  enriched_at: string | null;
  created_at: string;
  updated_at: string;
  job_count: number;
  sources: string[];
}

export interface JobPreview {
  title: string;
  location: string | null;
  department: string | string[] | null;
  url: string;
  posted_at: string | null;
  relevance: number;
  description_html: string | null;
  remote: boolean;
}

export interface DiscoverResponse {
  ats: string | null;
  slug: string | null;
  company_name: string | null;
  total_jobs: number;
  jobs: JobPreview[];
  error?: string | null;
}

export interface RefreshResponse {
  ats: string | null;
  slug: string | null;
  company_name: string | null;
  total_jobs: number;
  jobs: JobPreview[];
  expired_count: number;
  error?: string | null;
}

export interface ImportRequest {
  name: string;
  website: string | null;
  ats: string;
  slug: string;
  selected_urls: string[] | null;
  monitoring_active: boolean;
}

export interface ImportResponse {
  company_id: string;
  company_name: string | null;
  jobs_imported: number;
  job_ids: string[];
}
