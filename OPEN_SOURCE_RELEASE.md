# Open-Source Release Checklist

Tracking everything that needs to land before this repo is ready for public release.
Working through these top-to-bottom.

## Showstoppers — must land before any release

- [x] **1. Scrub personal data.** Move `docs/profile.yaml`, `docs/profile_complete.yaml`, `resume/`, and `output/` out of the tracked tree (or fully gitignored). Flesh out the `.example` files with realistic-but-fictional content. Audit alembic migrations for any baked-in identifying data.
  - Untracked `docs/profile.yaml`, `docs/profile_complete.yaml`, `.mcp.json`. `.gitignore` already covered `resume/`, `output/`, `docs/*.docx`, `docs/*.xlsx`, `docs/resume_style.yaml`.
  - Fleshed out `docs/profile.yaml.example` (full Sam Rivera fictional profile, 100+ lines) and created `docs/profile_complete.yaml.example` (5 accomplishments + 1 publication + 3 awards) so a fresh user has a runnable starting point.
  - Generalized `backend/scripts/ingest_past_resumes.py` so the filename regex is parameterized on `profile_data["personal"]["name"]` and multi-word companies derive from `work_history` + the optional `INGEST_MULTIWORD_COMPANIES` env var. Tested across 5 name/filename shapes.
  - Audited alembic migrations — clean, schema-only, no personal references.
  - **⚠ Action required**: the prior `.mcp.json` had a Brave Search API key in plaintext. Removing it from the working tree doesn't remove it from git history. Before going public: rotate the Brave key at brave.com, then either rewrite history with `git-filter-repo --replace-text` or accept the leak and rely on rotation. Likewise audit any other secrets that may have been in `.env` or `.mcp.json` in earlier commits.
- [x] **2. License + README + CONTRIBUTING.** Add LICENSE (MIT or Apache-2.0), a README that pitches the architecture (not just feature list), and a CONTRIBUTING that explains the dev loop.
  - LICENSE: already MIT, attributed to Prateek Puri (2026). No change needed.
  - README rewritten to lead with the architecture differentiators: two-tier memory (`content_memory` + `writing_memory`), staged pipeline with structural cross-section dedup, semantic eval. Project name standardized to **Mirror** (matches `CLAUDE.md`). Old feature-list pitch moved below the architecture story.
  - CONTRIBUTING rewritten with: dev-with-fictional-sample setup, eval-suite-must-run-on-PR rule, architecture map (where to land things), updated scope (LLM provider abstraction in roadmap), trace-dir hint for bug reports.
- [x] **3. One-command setup.** Boots and is usable from a fresh clone with literally `docker compose up --build`.
  - **DB-backed runtime config**: new `app_settings` table + `app_settings_service` + alembic migration. The FastAPI lifespan hook reads every row at startup and overrides matching fields on the global `Settings` object before serving requests, then resets the LLM client. DB values take precedence over env-var defaults. Persists across container rebuilds via the `pgdata` volume.
  - **Extended `/setup` wizard**: provider radio (OpenAI / Anthropic / Ollama), conditional key field per provider, optional disclosure for Perplexity + Brave keys, hot-reload after save. OpenAI keys still get live-tested with rate-limit detection; Anthropic/Ollama save without round-trip validation. Verified end-to-end: save → status reflects change → restart api → status STILL shows the saved provider (DB persistence proven).
  - **Compose has inline defaults for everything**: `${POSTGRES_USER:-jobboard}` style. The `./.env:/app/.env` mount is gone — settings live in the DB now. Power users can still pre-fill via env vars or compose's auto-loaded `.env`.
  - **Dockerfile no longer requires `ca-certificates.crt`**: BuildKit-wildcard `corporate-ca*.crt* ca-certificates*.crt*` silently no-ops when no cert is present, so fresh clones build clean. Users behind SSL-inspecting corporate proxies can still drop a cert at the old or new name.
  - **Profile YAML auto-fallback to `.example`**: lifespan hook checks for `docs/profile.yaml`, falls back to `docs/profile.yaml.example` if absent, same for the complete profile. Means the app boots into a usable demo state on first run instead of an empty-profile blank slate.
  - **README quick-start cut to 3 lines** (`git clone` → `docker compose up --build -d` → `open localhost:3050`). No `.env` edit, no `cp` step, no migration command.
- [x] **4. Pluggable LLM provider.** New `LLMClient` abstraction with OpenAI + Anthropic adapters, used everywhere LLM calls happen. Ollama as a stretch goal. Provider configurable via env var.
  - `backend/app/config.py` gained `llm_provider`, `anthropic_api_key`, `ollama_base_url`, plus per-role model overrides (`llm_resume_model`, `llm_scoring_model`, `llm_extraction_model`).
  - `backend/app/ai/client.py` rewritten as a provider-aware factory. Same `get_openai_client()` API; the underlying `AsyncOpenAI` is now pointed at OpenAI / Anthropic compat / Ollama based on `LLM_PROVIDER`. All 20 existing call sites (resume_pipeline, agent, scoring, writing_memory, etc.) work unchanged for the common chat-completion case.
  - Module-level `RESUME_MODEL` / `SCORING_MODEL` / `EXTRACTION_MODEL` constants now resolve from a per-provider table baked into `client.py`. Env overrides (`LLM_RESUME_MODEL` etc.) let you pin specific models without code changes.
  - Anthropic uses `claude-opus-4-7 / claude-sonnet-4-6 / claude-haiku-4-5-20251001` via their [OpenAI-SDK compatibility shim](https://docs.anthropic.com/en/api/openai-sdk). Ollama uses `llama3.1:70b/8b` via the local `/v1` endpoint.
  - Caveats documented in `.env.example`: a few callsites use OpenAI-only kwargs (e.g. `reasoning_effort` in `writing_memory.py`); Anthropic/Ollama silently ignore them.
  - Local sanity test: imports clean for all three provider configs; model-name resolution + endpoint resolution verified per provider.

## The 30% that turns it from "side project" to "this is real"

- [x] **5. Design doc.** `docs/MEMORY_DESIGN.md` walking through the two-tier memory: why style-rule extraction wasn't enough, why per-entity content reuse + job-context grounding works, what the staged pipeline buys vs. single-shot, what the eval suite proves.
  - Written as `docs/MEMORY_DESIGN.md` (~2,500 words). Structure: problem statement (the user's "I keep re-fixing the same bullets" complaint), the v3 extraction-based approach + concrete failure modes, the v4 two-tier memory + staged pipeline, key design choices (per-entity grounding keyed on stable IDs, source_doc_id in unique constraint, soft grounding vs. verbatim cache, structural cross-section dedup, targeted refinement), the chat agent's focused-context redesign, validation via the eval harness, what this is *not* (vector memory, verbatim cache, fully deterministic), open questions, and a file map pointing at every implementation file.
  - This is the "tell me about a hard system you designed" artifact — written for engineers reading code, not a marketing post. README and CONTRIBUTING both link to it.
- [ ] **6. Demo GIF / video.** 60–90s screencast in the README: generate a resume → click a research description → see "Past versions" → swap voice → critic catches a passive opening → refiner fixes it.
- [x] **7. CI with semantic eval.** GitHub Actions running unit tests + `eval_focused_edit.py` (LLM-as-judge) on PRs, with a pass-rate gate (≥80%).
  - `eval_focused_edit.py` now writes `output/eval_results.json` with per-turn judge output and exits non-zero when the aggregate pass rate dips below `EVAL_PASS_THRESHOLD` (default `0.80`). Local sanity test confirmed gate math at four threshold boundaries.
  - New workflow `.github/workflows/semantic-eval.yml`: PostgreSQL service container, runs migrations, seeds the DB from the fictional `docs/profile.yaml.example` + `profile_complete.yaml.example` (so PRs from forks that never had access to the maintainer's profile still exercise the eval against a runnable corpus), runs the eval, uploads the summary JSON as an artifact, and posts a PR comment with the pass rate + any failed checks.
  - Path-filtered to keep cost down: only fires on PRs touching `backend/app/ai/**`, the chat/documents routers, the memory services, eval scripts, or alembic migrations. Existing CI workflow (lint + unit tests + frontend type check) untouched and runs on every PR.
  - Required repo secrets: `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY` if `LLM_PROVIDER=anthropic`). Optional repo variables: `EVAL_PASS_THRESHOLD`, `LLM_PROVIDER`. Documented in `CONTRIBUTING.md`.
- [x] **8. Real unit tests on deterministic code.** `path_to_entity`, `employer_key`, the docx parser in `ingest_past_resumes.py`, the Ashby pivot URL detector, the trace-dump format. Aim for ~60% coverage on services + ai modules.
  - New `backend/tests/test_deterministic.py` — 114 tests across the load-bearing pure helpers: `employer_key`, `path_to_entity` (every entity type, edge cases for missing IDs and out-of-range indices), source-text hashing + `is_stale` (the staleness-detection contract that protects against `docs/profile_complete.yaml` drift), `_get_nested` / `_set_nested` / `migrate_resume_json`, `_domain_from_url` + `_disambiguator_line` (the Surge fix), `_ashby_jid_from_url` (the Ditto pivot), `parse_filename` parameterized on owner name (the multi-name generalization shipped in item 1), and the chat agent's context-construction helpers (`_format_edit_response`, `_previous_attempts_block`, `_trimmed_chat_history`, `_other_sections_excerpt`, `_focused_profile_for_edit`).
  - **Two real bugs surfaced and fixed.** First: `host.lstrip("www.")` in `_domain_from_url` was stripping any leading `w` or `.` (so `wellfound.com` → `ellfound.com` and the ATS filter silently leaked). Switched to `removeprefix("www.")`. Second: a test scaffold issue (`__file__` not defined inside an `exec()` namespace) — fixed and the parser-loading shim now stubs `__file__` properly. The fact that brand-new tests caught a real production bug on first run is the canonical pitch for why this matters.
  - All 114 pass in 0.4s. Suite is pure: no DB, no LLM, no network.

## Polish that signals quality

- [x] **9. Architecture diagram.** One image: scraper → job → strategic plan → selection → parallel(research, skills, publications) → bullets → critic → refiner → summary → docx. README hero image.
  - Three Mermaid diagrams shipped (GitHub renders natively — no image hosting, version-controllable):
    1. **System overview** in `README.md` — User → Frontend → API + (DB, Redis, SearXNG, LLM provider) + ATS scrapers + URL importer.
    2. **Staged generation pipeline** in `docs/MEMORY_DESIGN.md` — full flow with critic/refiner branches and parallel-stage subgraph styling.
    3. **Memory capture/retrieval** in `docs/MEMORY_DESIGN.md` — write path (PATCH → `_learn_from_inline_edit` → content_memory + selectively writing_memory) and read path (leaf prompts → fetch_grounding + format_writing_memory → LLM call).
  - Replaced the inline ASCII pipeline diagram in `MEMORY_DESIGN.md` with the Mermaid version (cleaner, conveys parallelism via subgraphs).
- [x] **10. Blog post.** "Why I rebuilt my resume generator's memory layer from scratch" — the v3→v4 story (style rules underfit; content memory + grounding + critic). Cross-post HN + personal site.
  - Drafted at `docs/BLOG_POST_DRAFT.md` (~2,200 words). Structure: hook with the user-frustration ("I was rewriting the same paragraphs every time"), v3 architecture + four concrete failure modes, the mental-model shift to two-tier memory, the new design with the real grounding-block snippet, the staged pipeline with the cross-section-dedup-via-data-flow story, the unexpected voice-mirroring fight (showing the actual passive output the LLM produced before the fix), the semantic eval as the safety net, what it isn't (vector memory, verbatim cache, provider-locked), and what I'd do differently next time.
  - Publishing notes inline: GitHub URL placeholders to update, Mermaid → PNG hero image instructions, HN title suggestion, tweet-length pitch.
  - Status: **draft for the user to review and publish.** Tone, real-vs-anonymous employer names, and GitHub URLs need a final pass before posting.
- [x] **11. Examples folder.** `examples/sample_profile.yaml` with three fictional accomplishments + three pre-baked `content_memory` rows so first-time users see grounding fire on their first generation.
  - New `examples/` directory with `seed_demo_memory.py` (idempotent, supports `--reset`) and a README explaining the demo flow + tag-and-prune SQL for cleanup.
  - Inserts three fictional past resumes (Anthropic Research Engineer, Cohere Lead DS, OpenAI Forward Deployed Engineer) into `content_memory`: **33 rows total** — 9 research_description + 9 experience_bullets_set + 9 skill_bucket + 3 summary + 3 tagline. Each resume has its own voice flavor while staying consistent with Sam Rivera's accomplishment data so the grounding examples are believable.
  - Also seeds 5 starter rules into `writing_memory` (banned "leveraged", participial-tail rule, active-verb opening, jargon-metric translation, no-cross-bucket-skills) so the Writing Style tab is non-empty on first boot.
  - Path resolution handles both host execution (`backend/app/...`) and api-container execution (`/app/app/...`) so the same script works in either context.
  - Verified end-to-end inside the running container: 33 rows landed, idempotency check works ("Demo rows already present — nothing to do"), `--reset` correctly cascades through the FK on documents.
  - README "Quick start" now points at the demo seed as the fastest path to seeing the memory layer in action.
- [x] **12. Drop NocoDB or commit to it.** It's in `docker-compose.yml` but the README story doesn't mention it. Either explain its role or remove it.
  - Dropped. NocoDB had tendrils across the codebase: a service in `docker-compose.yml`, a `NC_DB` env var, a stub webhook router (`backend/app/routers/webhook.py`), a webhook service that just logged events, a `NocDBWebhookPayload` schema, an alembic exclusion list for tables NocoDB would have created, and a `nocodb` exporter in `app/schemas/__init__.py`. All removed.
  - 5-service stack now: `api`, `db`, `redis`, `frontend`, `searxng`. ~150MB lighter image pull, one fewer port (8080) competing with whatever else might want it, one fewer service to explain in the README, no more "what is this for?" question from contributors.
  - Verified the api boots cleanly with no missing-import errors after the schema/__init__.py edit; alembic env.py simplified (no more `nc_*` / `xc_knex_` prefix exclusions); README services table updated; README roadmap item ticked.

---

## Working order

Top-to-bottom in priority. Each item lands as its own commit (or short series). Mark checked when shipped.
