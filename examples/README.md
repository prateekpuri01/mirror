# Examples

This directory contains **fictional, runnable example data** so a
first-time user can see Mirror's memory layer fire on their very first
resume generation — instead of needing to hand-edit several resumes
before the grounding mechanism has anything to ground on.

## What's here

| File | What it is |
|---|---|
| `seed_demo_memory.py` | Inserts 3 fictional "past resumes" into `content_memory` (research descriptions, employer bullet sets, skill buckets, summary, tagline) and 5 starter rules into `writing_memory` |

## The fictional persona

Both `docs/profile.yaml.example` and `docs/profile_complete.yaml.example`
ship with **Sam Rivera** — a senior applied AI scientist with a fictional
work history at three made-up companies (Helio Labs, Brightline Health,
Marlin Systems) and six accomplishments tagged with stable IDs the seed
script grounds on.

The three "past resumes" the seed script writes mimic three distinct
target-role flavors:

1. **Anthropic — Research Engineer** (research-leaning voice)
2. **Cohere — Lead Data Scientist** (production-rigor voice)
3. **OpenAI — Forward Deployed Engineer** (customer-facing voice)

Each resume contains its own hand-tuned phrasings of the same
accomplishments — exactly the corpus the agent grounds on at generation
time.

## How to use it

```bash
# 1. Boot the app with the fictional profile
cp docs/profile.yaml.example docs/profile.yaml
cp docs/profile_complete.yaml.example docs/profile_complete.yaml
docker compose up --build -d
docker compose exec api alembic upgrade head

# 2. Seed the demo memory rows
docker compose exec api python examples/seed_demo_memory.py

# 3. Open the app, import any job URL, generate a resume.
#    The "Past versions ▼" dropdown will already have entries from
#    Sam's three pre-baked past resumes, and the agent will mirror the
#    voice from those past versions in the new generation.
open http://localhost:3050
```

## Idempotency

The seed script checks for any existing demo rows (Document name starts
with `"Demo past resume — "`) before inserting. Re-running it without
clearing those rows is a no-op.

To re-seed from scratch:

```bash
docker compose exec api python examples/seed_demo_memory.py --reset
```

## What you'll see

After running the seed:

- **`/profile` → Writing Style tab** — 5 starter style rules ("Lead
  bullets with active verbs", "Avoid 'leveraged'", etc.) editable like
  the user's own rules.
- **Resume editor on any job** — every research entry, employer's
  bullet section, and skill bucket will have a "Past versions (3) ▼"
  dropdown pre-populated.
- **Generation traces** — `output/traces/{trace_id}/03_research_*.txt`
  will show the grounding block populated with Sam's prior versions.

## Replacing with your own data

Once you've onboarded your own profile and edited a few resumes, the
demo rows are no longer needed. They're tagged so they're easy to
identify and prune:

```sql
-- inside the db container
DELETE FROM content_memory
 WHERE source_doc_id IN (
   SELECT id FROM documents WHERE name LIKE 'Demo past resume — %'
 );
DELETE FROM documents WHERE name LIKE 'Demo past resume — %';
DELETE FROM writing_memory WHERE source_type = 'demo_seed';
```
