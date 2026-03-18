# Job Board — Tickets

## Phase 0: Project Foundation

### T0-1: Repo Init + Docker Compose
- [ ] Initialize git repo
- [ ] Create `docker-compose.yml` with: PostgreSQL 16, NocoDB (connected to Postgres), Redis (for scheduler/cache)
- [ ] Create `.env.example` with all config vars
- [ ] Create `.gitignore`
- [ ] Verify all services start and NocoDB can reach Postgres

### T0-2: FastAPI Backend Skeleton
- [ ] Create `backend/` directory structure:
  ```
  backend/
    app/
      __init__.py
      main.py          # FastAPI app + CORS
      config.py         # Settings via pydantic-settings
      database.py       # SQLAlchemy engine + session
      models/           # SQLAlchemy ORM models (empty init)
      routers/          # FastAPI routers (empty init)
      schemas/          # Pydantic models (empty init)
      services/         # Business logic (empty init)
      scrapers/         # Job scrapers (empty init)
      ai/               # Claude API integrations (empty init)
    alembic/
    alembic.ini
    requirements.txt
    Dockerfile
    tests/
  ```
- [ ] Add FastAPI container to docker-compose
- [ ] Verify `/health` endpoint returns 200

### T0-3: Database Schema + Alembic
- [ ] Configure Alembic with async SQLAlchemy
- [ ] Create initial migration with these tables:

**jobs**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| title | text | |
| company | text | |
| location | text | nullable |
| remote | boolean | default false |
| salary_min | integer | nullable |
| salary_max | integer | nullable |
| description | text | full posting text |
| url | text | unique, source link |
| source | enum | greenhouse/linkedin/ai_discovered/manual |
| posted_at | timestamp | nullable |
| scraped_at | timestamp | |
| status | enum | new/interested/applied/interviewing/rejected/offer/archived |
| relevance_score | float | 0-1, AI-computed |
| thumbs | smallint | null=unrated, 1=up, -1=down |
| user_notes | text | free-form feedback for AI |
| created_at | timestamp | |
| updated_at | timestamp | |

**search_profiles**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name | text | e.g. "ML Engineer - Remote" |
| keywords | text[] | array of search terms |
| locations | text[] | |
| remote_ok | boolean | |
| salary_min | integer | nullable |
| experience_level | text | nullable |
| is_active | boolean | controls scheduler |
| created_at | timestamp | |

**job_search_profile** (join table)
| Column | Type |
|--------|------|
| job_id | UUID FK |
| search_profile_id | UUID FK |

**application_requirements**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| job_id | UUID FK | unique (one req set per job) |
| needs_resume | boolean | default true |
| needs_cover_letter | boolean | default false |
| needs_short_answers | boolean | default false |
| short_answer_prompts | jsonb | array of prompt strings |
| needs_video_interview | boolean | default false |
| needs_other | boolean | default false |
| other_description | text | what "other" means |

**documents**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| job_id | UUID FK | nullable (null = base template) |
| doc_type | enum | resume/cover_letter/short_answer/other |
| name | text | |
| content_markdown | text | editable source |
| content_docx_path | text | path to generated .docx |
| is_base_template | boolean | |
| version | integer | auto-increment per job+type |
| created_at | timestamp | |
| updated_at | timestamp | |

**tags**
| Column | Type |
|--------|------|
| id | UUID PK |
| name | text unique |
| color | text |

**job_tags** (join table)
| Column | Type |
|--------|------|
| job_id | UUID FK |
| tag_id | UUID FK |

- [ ] Run migration, verify tables exist
- [ ] Verify NocoDB auto-discovers the tables

### T0-4: Reference Documents
- [ ] Create `docs/accomplishments.md` — your professional accomplishments (you fill in)
- [ ] Create `docs/base_resume.md` — your base resume (you fill in)
- [ ] These are the source of truth the AI uses for all generation

### T0-5: Clean Up .claude/ Config
- [ ] Rewrite `CLAUDE.md` for this project
- [ ] Rewrite `settings.json` (remove QDA-specific everything)
- [ ] Clean `settings.local.json` (remove QDA permissions, MCP servers)
- [ ] Remove QDA-specific skills (db-query, docker-logs, gitlab-mr, langgraph-debug, playwright-e2e)
- [ ] Remove QDA-specific agents (backend-test-writer, e2e-test-writer, full-stack-debugger)
- [ ] Remove QDA-specific commands (debug, refactor, test) or adapt them

---

## Phase 1: NocoDB Setup + Views

### T1-1: Configure NocoDB Base + Views
- [ ] Create a NocoDB "base" connected to the PostgreSQL database
- [ ] Configure the Jobs grid view:
  - Visible columns: title, company, location, remote, salary range, status, thumbs, tags, source, relevance_score
  - Hidden by default: description, url, user_notes, timestamps
  - Sort: relevance_score desc, then scraped_at desc
  - Filters preset: status != archived
- [ ] Configure a Kanban view grouped by `status`
- [ ] Configure column types in NocoDB:
  - status → single-select (new/interested/applied/interviewing/rejected/offer/archived)
  - thumbs → rating (or use a number field with conditional formatting)
  - remote → checkbox
  - tags → linked record to tags table
  - needs_* → checkboxes on the application_requirements table (linked)

### T1-2: NocoDB Webhooks → FastAPI
- [ ] Set up NocoDB webhook: on row update (thumbs/status change) → POST to FastAPI `/api/feedback/webhook`
- [ ] FastAPI endpoint receives the webhook, logs the feedback event
- [ ] This is the foundation for the AI feedback loop

---

## Phase 2: Job Scrapers

### T2-1: Greenhouse Scraper
- [ ] Implement `scrapers/greenhouse.py`
- [ ] Greenhouse has a public job board API: `https://boards-api.greenhouse.io/v1/boards/{company}/jobs`
- [ ] Accept a list of target companies (configurable)
- [ ] Parse job data → insert into `jobs` table (upsert on URL)
- [ ] Router: `POST /api/scrape/greenhouse` (manual trigger)
- [ ] Tests for parsing + dedup logic

### T2-2: LinkedIn Scraper (Best-Effort)
- [ ] Research viable approaches: LinkedIn API (limited), Google search `site:linkedin.com/jobs`, or browser automation
- [ ] Implement `scrapers/linkedin.py` with the most viable approach
- [ ] Likely approach: search via Google, extract LinkedIn job IDs, fetch public job page
- [ ] Handle rate limiting and failure gracefully
- [ ] Router: `POST /api/scrape/linkedin`

### T2-3: Deduplication + Relevance Scoring
- [ ] Deduplicate jobs by URL (exact) and by company+title similarity (fuzzy)
- [ ] When a new job is scraped, call Claude API to score relevance (0-1):
  - Input: job description + `accomplishments.md` + `base_resume.md` + past thumbs up/down examples
  - Output: relevance score + brief rationale (stored in a notes field or separate column)
- [ ] Batch scoring for efficiency

### T2-4: Scheduled Scraping
- [ ] Add APScheduler to FastAPI startup
- [ ] For each active `search_profile`, run configured scrapers on a cron (e.g. daily)
- [ ] Store last-run timestamp per profile
- [ ] Router: `GET /api/scrape/status`, `POST /api/scrape/run-now`

---

## Phase 3: AI Discovery Agent

### T3-1: Company Discovery Agent
- [ ] Implement `ai/discovery_agent.py`
- [ ] Agent flow:
  1. Read user's accomplishments + resume + feedback history (thumbs + comments)
  2. Identify themes: industries, company sizes, roles, tech stacks the user likes
  3. Search the web for companies matching those themes
  4. Check if those companies have open roles (via careers page / Greenhouse / Lever)
  5. Insert discovered jobs into `jobs` table with source=`ai_discovered`
- [ ] Use Claude API with tool use for web search
- [ ] Router: `POST /api/agent/discover` (manual trigger), also schedulable

### T3-2: Feedback Loop Integration
- [ ] When computing relevance or running discovery, include:
  - All thumbs-up jobs (positive examples)
  - All thumbs-down jobs (negative examples)
  - User comments/notes on rated jobs
- [ ] Agent refines its understanding of preferences over time
- [ ] Consider storing a generated "preference summary" that updates after each batch of feedback

---

## Phase 4: Document Generation

### T4-1: Resume Generator
- [ ] Implement `ai/resume_generator.py`
- [ ] Input: job description + `accomplishments.md` + `base_resume.md`
- [ ] Claude prompt: "Given this job posting and my full accomplishments, create a tailored resume that emphasizes the most relevant experience. Maintain truthfulness — only reframe and prioritize, never fabricate."
- [ ] Output: markdown content → stored in `documents` table
- [ ] Convert markdown → .docx using `python-docx` with a professional template
- [ ] Router: `POST /api/generate/resume/{job_id}` → returns document ID
- [ ] Store both markdown (editable) and generated .docx path

### T4-2: Cover Letter Generator
- [ ] Implement `ai/cover_letter_generator.py`
- [ ] Same inputs as resume + the generated resume for consistency
- [ ] Claude prompt focuses on narrative, motivation, and company-specific fit
- [ ] Output: markdown → documents table → .docx
- [ ] Router: `POST /api/generate/cover-letter/{job_id}`

### T4-3: Short Answer Generator
- [ ] Implement `ai/short_answer_generator.py`
- [ ] Input: prompts from `application_requirements.short_answer_prompts` + accomplishments + resume + job description
- [ ] Generate answer for each prompt individually
- [ ] Store each as a separate document (or one document with sections)
- [ ] Router: `POST /api/generate/short-answers/{job_id}`

---

## Phase 5: Companion UI (Document Management)

### T5-1: Companion UI Skeleton
- [ ] Decide framework: lightweight Next.js app or simple React SPA (Vite)
- [ ] Add to docker-compose on port 3000
- [ ] Pages: Job Detail, Document Viewer/Editor, Agent Dashboard
- [ ] Navigation: link from NocoDB row → companion UI job detail page (via URL with job_id)

### T5-2: Job Detail Page
- [ ] Display: job title, company, description, link to original posting
- [ ] Application requirements section: checkboxes for what's needed
- [ ] "Generate" buttons: Resume, Cover Letter, Short Answers
- [ ] Show generation status (loading/done/error)
- [ ] List of generated documents with version history

### T5-3: Document Viewer + Editor
- [ ] Display markdown content with rendered preview
- [ ] Edit mode: markdown editor (e.g. Monaco, CodeMirror, or Milkdown for WYSIWYG-ish)
- [ ] Save edits → updates `documents.content_markdown` via API
- [ ] "Re-generate .docx" button after editing
- [ ] "Download .docx" button
- [ ] Version selector dropdown to view/restore previous versions

### T5-4: Agent Dashboard
- [ ] Show recent AI discovery runs: when, how many jobs found
- [ ] Current preference summary (derived from feedback)
- [ ] Trigger manual discovery run
- [ ] View/edit search profiles

---

## Phase 6: Polish + Quality of Life

### T6-1: Notifications for New Matches
- [ ] After a scrape/discovery run, identify new high-relevance jobs
- [ ] Desktop notification, email, or simple in-app "new jobs" badge
- [ ] Configurable relevance threshold for notification

### T6-2: Bulk Operations
- [ ] Archive all rejected jobs
- [ ] Bulk tag assignments
- [ ] Export filtered job list to CSV

### T6-3: Analytics Dashboard
- [ ] Jobs scraped over time
- [ ] Application funnel (applied → interview → offer)
- [ ] Response rates by source/company
- [ ] Tags/skills most correlated with thumbs-up jobs
