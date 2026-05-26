# Contributing to Mirror

Mirror is a personal-tool project. Contributions are welcome but the scope
stays focused on what helps a single user run a smarter, more personal job
search. Read the [README](./README.md) for the architectural pitch before
opening anything non-trivial.

## Development setup

```bash
cp .env.example .env
# Add your OPENAI_API_KEY to .env (or use the in-app /setup wizard)
docker compose up --build -d
open http://localhost:3050
```

To boot with the fictional sample profile (recommended for development —
exercises the memory layer immediately):

```bash
cp docs/profile.yaml.example docs/profile.yaml
cp docs/profile_complete.yaml.example docs/profile_complete.yaml
docker compose restart api
```

## Common dev commands

```bash
docker compose logs -f api                       # Tail backend logs
docker compose exec api alembic upgrade head     # Apply migrations
docker compose exec api alembic revision --autogenerate -m "..."  # New migration
docker compose exec api pytest                   # Unit tests
cd frontend && npx tsc --noEmit                  # Frontend type check
cd frontend && npm run lint                      # Frontend lint
```

## Running the semantic eval

Mirror has an LLM-as-judge eval suite that exercises the multi-turn focused-
edit agent. **Run this before submitting any PR that touches prompts,
agent routing, or memory plumbing.**

```bash
docker compose exec api python scripts/eval/eval_focused_edit.py
```

The eval grades each turn on four axes (`respects_instruction`,
`no_fabrication`, `differs_from_prior`, `voice_matches_grounding`) and
prints per-scenario reports plus an aggregate pass rate. The script exits
non-zero (and CI fails) when pass rate drops below `EVAL_PASS_THRESHOLD`
(default `0.80`). A summary JSON lands at `output/eval_results.json`.

Add a new scenario by copying a `run_scenario(...)` block in the same
file and adapting. Aim for scenarios that exercise a specific failure
mode (passive openings, cross-section redundancy, multi-turn iteration,
fabricated metrics, etc.) rather than generic "rewrite this nicely."

### CI integration

The GitHub Actions workflow at `.github/workflows/semantic-eval.yml` runs
the eval on PRs that touch:

- `backend/app/ai/**`
- `backend/app/services/{content,writing}_memory_service.py`
- `backend/app/routers/{chat,documents}.py`
- `backend/scripts/eval/**`
- `backend/alembic/versions/**`

Required repo secrets:

- `OPENAI_API_KEY` — required when `LLM_PROVIDER=openai` (default)
- `ANTHROPIC_API_KEY` — required when `LLM_PROVIDER=anthropic`

Optional repo variables:

- `EVAL_PASS_THRESHOLD` — gate (default `"0.80"`)
- `LLM_PROVIDER` — `openai` / `anthropic` / `ollama` (default `openai`)

The workflow seeds the DB from `docs/profile.yaml.example` and
`docs/profile_complete.yaml.example`, runs migrations, fires the eval,
uploads `output/eval_results.json` as an artifact, and posts a comment on
the PR with the pass rate + any failed checks.

**Cost note:** each eval run makes ~20 LLM calls (~$0.50–$1 on GPT-5
class models). The path filter keeps it from firing on every PR. Fork
PRs without secret access will see the workflow skip (with a clear
`::error::` for missing keys); maintainers can re-run the eval from a
local branch before merging.

## Code style

### Backend (Python)

```bash
cd backend
ruff check .
ruff format .
```

- Line length: 100
- Target: Python 3.12
- Async all the way down (FastAPI + SQLAlchemy async)
- Double quotes for strings

### Frontend (TypeScript / React)

- ESLint config: `frontend/eslint.config.mjs`
- Tailwind for styles
- React Query for data fetching
- Functional components + hooks

```bash
cd frontend
npm run lint
```

## Architecture

Read [`docs/MEMORY_DESIGN.md`](./docs/MEMORY_DESIGN.md) before opening a
non-trivial PR — it explains the design choices behind the memory layer,
the staged generation pipeline, the chat agent's context strategy, and
what the eval suite proves. The code makes a lot more sense after that.

## Architecture map (where to land things)

| You're touching… | Look here |
|---|---|
| LLM prompts | `backend/app/ai/resume_prompts.py` (canonical source for all prompt text) |
| Generation pipeline | `backend/app/ai/resume_pipeline.py` |
| Chat editing agent | `backend/app/ai/resume_agent.py` (LangGraph nodes + routing) |
| Content memory (per-entity) | `backend/app/services/content_memory_service.py`, `backend/app/ai/content_memory_paths.py`, `backend/app/ai/content_memory_grounding.py` |
| Writing memory (style rules) | `backend/app/ai/writing_memory.py`, `backend/app/services/writing_memory_service.py` |
| LLM client | `backend/app/ai/client.py` (provider-agnostic — OpenAI / Anthropic / Ollama via OpenAI-compat shims) |
| Job scrapers | `backend/app/scrapers/` |
| URL job import (incl. Ashby pivot) | `backend/app/services/job_url_importer.py` + `backend/app/services/browser_pool.py` |
| Frontend resume editor | `frontend/src/components/resume-editor.tsx` |
| Profile UI sections | `frontend/src/components/profile/` |
| API client | `frontend/src/lib/api.ts` |

## Scope guidelines

**In scope:**

- New scraper integrations (standard ATS platforms with public APIs)
- Memory-layer improvements (better grounding, smarter consolidation)
- Pipeline stage additions (e.g. cover-letter pipeline)
- Semantic eval scenarios for new prompts
- Documentation, design docs, architecture diagrams
- New LLM provider adapters once the abstraction lands

**Out of scope:**

- Multi-user auth / team features (this is a single-user local tool)
- SaaS / hosted deployment
- Scrapers for sites with anti-bot protection (LinkedIn, Indeed, Glassdoor)
- Custom scrapers for corporate careers sites — use the URL import flow instead
- Any feature that sends user data to third parties beyond the configured
  AI / search providers

## Good first issues

If you're new to the codebase and want a well-scoped first
contribution, these are intentionally sized for a weekend and touch a
single area — no deep knowledge of the memory layer or pipeline
required.

### 🎯 Drag-and-drop resume section ordering

**The gap.** Section order in the resume (Summary, Experience,
Selected Research, Publications, Technical Skills, Education, Awards)
is hardcoded per layout in `backend/app/ai/docx_builder.py`:
`_build_layout_banner` (line 1113), `_build_layout_compact` (1124),
`_build_layout_timeline` (1135), `_build_layout_two_column` (1473).
An academic candidate may want Publications first; an industry
candidate may want Experience first. No way to override without
editing code today.

**The change.** Add a per-document `section_order: string[]` field to
`ResumeJson` that the editor mutates via drag-and-drop, with both the
docx builder and the in-app preview consuming it.

**Key things to know going in:**

- The LLM generation pipeline is **already order-agnostic**. Leaf
  prompts in `backend/app/ai/resume_pipeline.py` don't see section
  order, and sections are produced by independent parallel calls. You
  are adding a view-layer feature, not changing how resumes are
  generated.
- Two LLM call sites *do* take the full `content_json` as input and
  would leak `section_order` into prompts if you're not careful:
  `revise_resume` (`backend/app/ai/resume_builder.py:431`) and
  `broad_rewrite` (`backend/app/ai/resume_agent.py:911`). Strip
  `section_order` from the JSON passed to the LLM and re-attach the
  original value after the response returns — don't trust the prompt
  to preserve it.
- The `timeline` layout merges experience + education into a single
  block. Handle this with a synthetic `experience_education` section
  ID that only appears in the order array when that layout is active.
- Use `@dnd-kit/core` + `@dnd-kit/sortable` for the UI — modern,
  accessible (keyboard navigation built in), ~10 KB gzipped.
  `react-beautiful-dnd` is unmaintained; avoid.
- The existing `PATCH /api/documents/{doc_id}/section` endpoint
  (`backend/app/routers/documents.py:84`) already accepts arbitrary
  path + value pairs. Reuse it — no new endpoint needed.

**Suggested phases (small first, ship as you go):**

1. Add `section_order` to the `ResumeJson` schema (frontend type +
   default-order constants mirrored backend ↔ frontend).
2. Refactor `docx_builder.py` to read section order from
   `content_json` instead of hardcoding per layout. Default to the
   layout's historical order when the field is missing
   (backward-compatible).
3. Add drag handles + `<DndContext>` wrapping in
   `frontend/src/components/resume-editor.tsx`. Persist via the
   existing `updateResumeSection` API client.
4. Add the strip-and-restore around the two LLM revise sites.
5. Add a regression test —
   `backend/tests/test_resume_order_invariance.py` — that generates a
   resume twice with two different default section orders and asserts
   the content fields (everything *except* `section_order`) are
   identical. This is the test that locks the LLM pipeline's
   order-agnostic property in place forever.

**Out of scope for v1:** per-user "default section order" preferences
in the profile. Per-document customization is the high-value piece; a
profile-level default can be added later if demand surfaces.

**Why this is a good first issue.** Real product gap, clear contract
(one new field), exercises both backend and frontend, and the
invariance test is a satisfying way to lock the behavior down. Most of
the work is plumbing rather than ML reasoning, so it's a great way to
learn the codebase without needing to understand the prompt pipeline.

---

## Pull request guidelines

1. **Open an issue first** for non-trivial changes. Describe the use case
   and the failure mode you've seen before writing code.
2. **Keep PRs focused.** One feature or bug fix per PR.
3. **Run the eval suite** if you touched prompts, the agent, or memory.
   Paste the aggregate pass rate in your PR description.
4. **Add tests** for deterministic logic (path parsers, employer-key
   normalization, docx parser, etc.). Use pytest with `pytest-asyncio`.
5. **Update the README + roadmap** if you ship a new user-facing feature
   or remove something.
6. **Don't commit secrets.** `.env` is gitignored. Double-check `.mcp.json`
   too if you use Claude Code locally — it's also gitignored.

## Reporting issues

Please include:

- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (`docker compose logs api`, browser console)
- A trace dir if it's a generation-quality issue:
  `output/traces/<trace_id>/` — gives the full per-stage prompt + response
- Your OS and Docker version

## License

By contributing, you agree that your contributions will be licensed under
the [MIT License](./LICENSE).
