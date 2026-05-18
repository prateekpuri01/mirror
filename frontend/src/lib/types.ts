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
  | "company_website"
  | "eightfold";

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
  created_at: string;
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
// Company Research (from Perplexity)
// ---------------------------------------------------------------------------

export interface CompanyResearch {
  company_summary: string;
  company_stage: string;
  tech_signals: string[];
  culture_signals: string[];
  recent_news: string[];
  team_function: string;
  team_recent_work: string[];
  team_open_problems: string[];
  valued_skills: string[];
  interview_signals: string[];
  framing_angles: string[];
  citations: string[];
  researched_at: string;
  model_used: string;
  query_company: string;
  query_team: string | null;
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

export interface BulletItem {
  text: string;
  accomplishment_ids?: string[];
}

export interface ExperienceBlock {
  bullets: BulletItem[];
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
    communication?: string;
  };
  awards: string;
  tailoring_rationale?: string;
  _critique?: Record<string, unknown>;
  _research?: CompanyResearch;
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
  hide_expired?: boolean;
  pin_ids?: string[];
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
  eightfold_slug: string | null;
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

export interface ExtractedLocation {
  city: string;
  state: string | null;
  country: string;
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
  // Present when location/salary filters triggered LLM extraction
  extracted_work_model?: "remote" | "hybrid" | "onsite" | null;
  extracted_locations?: ExtractedLocation[];
  extracted_salary_min?: number | null;
  extracted_salary_max?: number | null;
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

// ---------------------------------------------------------------------------
// Hot Company Search
// ---------------------------------------------------------------------------

export interface HotSearchHit {
  name: string;
  ats: string;
  slug: string;
  website: string | null;
  total_jobs: number;
  relevant_jobs: number;
  top_jobs: JobPreview[];
  source: string;
  description: string;
  match_reason: string;
  // Hit kind:
  //   "ats"     — ATS-scraped result with relevant jobs (default)
  //   "lead"    — company we couldn't scrape, surfaced as a careers-page link
  //   "tracked" — company already in user's DB with matching jobs
  // Companies considered but rejected appear in the activity log as
  // "skip" events with a reason, not here.
  kind?: "ats" | "lead" | "tracked";
  careers_url?: string | null;
  company_id?: string | null;
}

export interface CandidateLogEntry {
  name: string;
  source: string;
  status: "checking" | "accepted" | "rejected";
  reason?: string;
}

export interface ImportResponse {
  company_id: string;
  company_name: string | null;
  jobs_imported: number;
  job_ids: string[];
}

// ---------------------------------------------------------------------------
// Profile types
// ---------------------------------------------------------------------------

export interface ProfileLink {
  url: string;
  label: string;
}

export interface ProfilePersonal {
  name?: string;
  email?: string;
  phone?: string;
  location?: string;
  remote_preference?: "remote" | "hybrid" | "onsite" | "flexible";
  willing_to_relocate?: boolean;
  linkedin?: string;
  google_scholar?: string;
  professional_links?: ProfileLink[];
}

export interface ProfileTargetRole {
  title: string;
  seniority?: string;
}

// Dynamic skill categories — users can add/rename/remove categories.
// Default categories: technical, communication, tools.
export type ProfileSkills = Record<string, string[]>;

export interface ProfileEducation {
  degree: string;
  field: string;
  institution: string;
  year: string;
  honors?: string;
}

export interface ProfileWorkHistory {
  employer: string;
  title: string;
  start: string;
  end?: string;
  location?: string;
  key?: string;
}

export interface ProfileSearchPreferences {
  looking_for?: string;
  not_looking_for?: string;
  positive_signals?: string[];
  exclusions?: string[];
  salary_minimum?: number | null;
  // Legacy (kept in type for migration read; never written by new UI)
  deal_breakers?: string[];
  nice_to_haves?: string[];
  industries_ranked?: string[];
  org_anti_patterns?: string[];
}

export interface ProfileData {
  personal?: ProfilePersonal;
  target_roles?: ProfileTargetRole[];
  domains?: string[];
  skills?: ProfileSkills;
  education?: ProfileEducation[];
  work_history?: ProfileWorkHistory[];
  awards?: string[];
  search_preferences?: ProfileSearchPreferences;
  experience_years?: string;
}

export interface ProfileAccomplishment {
  id?: string;
  category?: string;
  employer?: string;
  work_history_key?: string;
  title?: string;
  date_range?: string;
  impact_summary?: string;
  quantitative_specifics?: string[];
  so_what?: string;
  skills_demonstrated?: string[];
  relevance_weight?: number;
  tags?: string[];
  related_publication_ids?: string[];
  auto_populated?: boolean;
  source?: string;
}

export interface ProfilePublication {
  // Stable identifiers — at least one is usually present. The API returns
  // `id` as null for the seeded RAND/Scholar pubs, which use `rand_id` or
  // `doi` instead. Any of these will do as a unique key in the UI.
  id?: string | null;
  rand_id?: string | null;
  doi?: string | null;
  title?: string;
  authors?: string[];
  venue?: string;
  year?: string | number;
  type?: string;
  url?: string;
  abstract?: string;
  first_author?: boolean;
  impact_summary?: string;
  so_what?: string;
  quantitative_specifics?: string[];
  skills_demonstrated?: string[];
  relevance_weight?: number;
  work_history_key?: string;
  auto_populated?: boolean;
}

export interface ProfileCompleteData {
  accomplishments?: ProfileAccomplishment[];
  publications?: ProfilePublication[];
}
