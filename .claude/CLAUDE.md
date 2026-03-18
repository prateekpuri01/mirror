# Job Board

AI-powered personal job board that finds relevant jobs, tracks applications, and generates tailored application materials using Claude API.

## Stack

- **Backend**: FastAPI (Python)
- **Frontend/UI**: NocoDB (Airtable-like interface)
- **Database**: PostgreSQL + Alembic migrations
- **AI**: Claude API (resume tailoring, job matching, cover letters)
- **Scheduling**: APScheduler (job scraping cron)
- **Deployment**: Docker Compose (local, single-user)

## Services (Docker)

| Service | Container | Port |
|---------|-----------|------|
| FastAPI backend | `api` | 8000 |
| PostgreSQL | `db` | 5433 (host) → 5432 (container) |
| NocoDB | `nocodb` | 8080 |
| Redis | `redis` | 6379 |

## Commands

```bash
docker compose up --build -d       # Start all services
docker compose logs -f api         # Tail API logs
docker compose exec api python -m pytest  # Run tests
docker compose exec api alembic upgrade head  # Run migrations
```

## Key Tables

- `jobs` — scraped/discovered job postings (UUID PK, enums for source/status)
- `search_profiles` — saved search criteria and preferences
- `job_search_profile` — many-to-many join (jobs ↔ search profiles)
- `application_requirements` — per-job checklist (one-to-one with jobs)
- `documents` — generated resumes, cover letters, tailored materials (markdown + docx path)
- `tags` / `job_tags` — job categorization and status labels
- `user_profile` — single row, JSONB data synced from `docs/profile.yaml` on startup

## Domain Terminology

| Term | Meaning |
|------|---------|
| Search profile | Saved set of job search criteria (role, location, skills) |
| Discovery | AI-powered job finding based on search profiles |
| Relevance score | AI rating of how well a job matches a profile |
| Thumbs up/down | Human feedback on AI job recommendations |
| Accomplishment | Bullet point from professional history, used in resume generation |

## Reference Documents

- `docs/profile.yaml` — User profile (skills, experience, search preferences). Source of truth for job matching and resume tailoring. Also displayed in the app UI.
- `resume/` — Resume files (base resume for AI document generation)

## Rules

- **Single repo**: Everything lives in this one repository.
- **Docker v2 CLI**: Always `docker compose`, never `docker-compose`.
- **Python style**: Use ruff for linting, black for formatting.
- **Never fabricate accomplishments**: AI-generated resumes must only use facts from `docs/accomplishments.md`.
- **Ask before destructive ops**: Migrations, bulk data changes, or anything that drops/replaces data.
