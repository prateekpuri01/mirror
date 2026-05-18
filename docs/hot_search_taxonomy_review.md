# Hot Search Taxonomy Review

Classification decisions made by the Hot Company Search pipeline. Each company that enters the pipeline lands in exactly one of these nodes. Examples are drawn from live test searches: **"AI safety research engineer"** and **"machine learning startups Series A"**.

---

## Taxonomy Overview

```
Candidate enters pipeline
  |
  +-- Already tracked in DB? --------> TRACKED (show existing matching jobs)
  |
  +-- Has supported ATS (GH/Lever/Ashby)?
  |     +-- Yes: scrape jobs
  |     |     +-- Any jobs pass relevance filter (score >= 75)? --> score + LLM picker
  |     |     |     +-- LLM grounding accepts? --------> ACCEPTED (ATS)
  |     |     |     +-- LLM grounding rejects? --------> DROPPED
  |     |     +-- No jobs pass relevance filter --------> SKIPPED (low score)
  |     |
  |     +-- No supported ATS
  |           +-- Lead drill (SearXNG) finds job URL? --> extract + preview
  |           +-- Perplexity drill finds job URL? -------> extract + preview
  |           +-- Browser agent finds job URL? ----------> extract + preview
  |           |     +-- LLM grounding accepts? --------> ACCEPTED (Direct)
  |           |     +-- LLM grounding rejects? --------> DROPPED
  |           +-- Nothing found, but careers page exists -> LEAD
  |           +-- Nothing found at all ------------------> SKIPPED
  |
  +-- Extraction/technical failure ---> SKIPPED (error)
  +-- Duplicate of known company -----> SKIPPED (dedup)
```

---

## Node 1: ACCEPTED (ATS)

**What it means:** Company uses Greenhouse, Lever, or Ashby. We scraped their full job board, scored all roles against the user's profile, and the LLM grounding model confirmed the company is relevant to the search.

**User experience:** Checkbox to select, expandable job list with relevance scores, Import button.

| Company | ATS | Jobs Found | Search | Match Reason |
|---------|-----|-----------|--------|-------------|
| Perplexity | Ashby | 10 | ML startups | AI startup building an LLM-powered search engine. Data Scientist roles involve working with AI systems. |
| Axle Health | Greenhouse | 10 | ML startups | Biomedical informatics company. Data Scientist II role develops research tools and techniques. |
| Mark43 | Greenhouse | 10 | AI safety | Public safety domain, mission-driven. Data Scientist role available. |
| Center for AI Safety | Lever | 2 | AI safety | Directly aligned: AI safety research organization with open research roles. |
| Ethyca | Greenhouse | 12 | AI safety | Privacy engineering company. Best match scored 50/100 (below threshold) -- borderline. |
| Top Hat | Ashby | 5 | ML startups | EdTech startup. 5 roles scraped from Ashby board. |
| HubSpot | Greenhouse | 0 (stale cache) | profile-driven | Marketing/CRM platform. Stale cache returned 0 jobs; would normally have many. |

**Why these passed:** The company operates in a domain related to the search query, and at least one open role scored well enough against the user's profile to surface.

---

## Node 2: ACCEPTED (Direct URL)

**What it means:** Company doesn't use a supported ATS, but we found a specific job posting URL via Perplexity search, lead drill (SearXNG), or the browser agent. The posting was extracted via LLM and returned as a preview.

**User experience:** Same as ATS hits -- checkbox, job details with description, Import button. Jobs are NOT auto-saved to the DB; the user confirms first.

| Company | Source | Jobs | Search | Match Reason |
|---------|--------|------|--------|-------------|
| Meta | Perplexity drill | 1 | AI safety | Research Engineer - AI Trust at Meta Superintelligence Labs. Involves evaluating safety techniques for AI systems. |
| Averlon | Perplexity drill | 1 | AI safety | Research Engineer, Trust & Safety. Aligns with AI governance background. |
| Biohub | Lead drill | 1 | profile-driven | Research Scientist, AI. Building RL environments and foundation models for biology. Remote, $150-350K. |
| Traverse | Lead drill | 1 | profile-driven | Research Scientist. Designing RL environments for non-deterministic tasks. |
| SEI AI Division | Lead drill | 1 | profile-driven | ML Research Scientist - Frontier Lab. Applied AI research for government missions. |
| Chainguard | Perplexity drill | 1 | AI safety | Product security research, adjacent to AI safety. Supply-chain security domain. |
| PwC | Lead drill | 1 | profile-driven | Strategic Commercial Director. Found via careers page domain search, later dropped by grounding. |
| Boston Consulting Group | Lead drill | 1 | profile-driven | Analyst role. Found via careers page, later dropped by grounding as off-topic. |

**Why these passed:** Perplexity or SearXNG found a real job posting URL, the LLM extractor successfully parsed it, and the grounding model confirmed it matches the search intent.

---

## Node 3: TRACKED

**What it means:** Company is already in the user's database (previously imported). The system checks their existing jobs against the current search filters and surfaces any matches.

**User experience:** "Tracked" badge, shows matching jobs already in the user's tracker with View links.

| Company | ATS | Matching Jobs | Search |
|---------|-----|--------------|--------|
| OpenAI | Ashby | 1-27 (varies by search) | AI safety, ML startups |
| Anthropic | Greenhouse | 1-11 (varies) | AI safety, ML startups |
| DeepMind | Greenhouse | 10-16 (varies) | profile-driven |
| Databricks | Greenhouse | 1-5 (varies) | ML startups |
| Mistral AI | Lever | 1 | ML startups |
| FAR.AI | Ashby | 1 | profile-driven |
| Center for AI Safety | Lever | 1 | AI safety |

**Why these appear:** The company was already imported. New search just filters their existing jobs for relevance to the current query.

---

## Node 4: LEAD

**What it means:** Company looks relevant to the search, but they don't use a supported ATS AND we couldn't find a specific job posting URL via any drill strategy (SearXNG, Perplexity, browser agent). We surface a link to their careers page so the user can browse manually.

**User experience:** "Lead" badge, amber styling, "Open careers page" button, no import capability.

| Company | Careers Page Found? | Search | Notes |
|---------|-------------------|--------|-------|
| Hugging Face | No (Google fallback) | ML startups | Major ML company. Uses custom careers page, no ATS detected. |
| DataRobot | No (Google fallback) | ML startups | AutoML platform. Enterprise careers portal, not scrapable. |
| QuEra Computing | No (Google fallback) | ML startups | Quantum computing startup. Likely too small for ATS. |
| Redwood Research | No | AI safety | AI safety research org. Small team, minimal web presence. |
| Partnership on AI | No | AI safety | AI policy consortium. Perplexity found URLs but extraction failed (JS-rendered). |
| DRUID AI | No (Google fallback) | ML startups | Conversational AI startup. Perplexity drill found URL but extraction failed. |
| Alaris Security | No (Google fallback) | AI safety | Security company. |
| Merida Biosciences | No (Google fallback) | ML startups | Biotech startup. |
| Tenvie Therapeutics | No (Google fallback) | ML startups | Drug discovery startup. |
| GovAI | Yes (govai.com/careers) | profile-driven | AI governance research org. Lead drill found no job URLs on their page. |
| Goldman Sachs | No | profile-driven | Uses Workday/internal portal. Lead drill failed. |
| Pfizer | Yes (pfizer.com/careers) | profile-driven | Pharma. Uses Workday. Lead drill found no parseable URLs. |
| Stanford HAI | Yes (careersearch.stanford.edu) | profile-driven | Stanford AI institute. Uses PeopleSoft/custom portal. |
| World Economic Forum | No | profile-driven | International org. Custom careers portal. |
| OECD | No | profile-driven | International org. Custom careers portal. |
| C3 AI | No (Google fallback) | profile-driven | Enterprise AI platform. Uses custom careers site. |

**Why these became leads:** Every automated strategy failed to find an importable job URL. Common reasons: custom JS-heavy careers portals (Workday, Taleo, PeopleSoft), very small orgs without structured job listings, or pages that require login.

---

## Node 5: DROPPED (Rejected by Grounding Model)

**What it means:** We found the company, possibly scraped their jobs, but the LLM grounding model judged the company or role to be off-topic for the user's search. The hit is suppressed from results.

**User experience:** Does not appear. Visible only in the activity log as a "skip" event with reason.

| Company | ATS | Role Found | Search | Rejection Reason |
|---------|-----|-----------|--------|-----------------|
| Coinbase | Greenhouse | (extraction failed) | ML startups | Crypto/blockchain space, not aligned with ML research startups. |
| Arena.im | Greenhouse | Cloud Engineer | ML startups | Infrastructure role, not ML research. Lacks research component. |
| Ataraxis AI | Web | (unknown) | ML startups | Precision medicine/oncology, doesn't fit "ML startups Series A" search. |
| Peregrine Technologies | Greenhouse | Technical Sourcer | AI safety | Recruitment position, not research or engineering. |
| Anduril Industries | Greenhouse | Sr. Threat & Attack Research Engineer | AI safety | Defense contractor (user exclusion). Cybersecurity, not AI safety research. |
| Abnormal Security | Greenhouse | (security role) | AI safety | Behavioral security/account takeover detection. Operational, not research. |
| Salt Security | Greenhouse | Technical Account Manager | AI safety | Customer-facing TAM role, not research. |
| AI Safety (intern) | Web | Research Engineer Intern | AI safety | Entry-level intern. Doesn't match seniority. |
| Palantir* | Lever | Forward Deployed AI Engineer | ML startups | Customer implementation focus, not research. *(borderline -- fixed in latest prompt)* |
| Wintermute Trading* | Lever | ML Researcher | ML startups | Financial markets/HFT domain. *(borderline -- accepted in latest prompt)* |
| Insider* | Lever | Senior ML Engineer | ML startups | Product personalization engineering. *(borderline -- accepted in latest prompt)* |
| Livefront* | Lever | AI Solutions Engineer | ML startups | Client-facing implementations. *(borderline -- accepted in latest prompt)* |
| Run:ai (Laminar)* | Lever | (optimization role) | ML startups | Commercial optimization. *(borderline -- accepted in latest prompt)* |

*\* Companies marked with asterisk were dropped under the old, more aggressive prompt. The updated prompt (current) accepts companies where the domain matches the search even if the specific role isn't a perfect profile fit.*

**Why these were dropped:** The grounding model checks two things: (1) does the company match the search intent? (2) is the role completely off-topic (recruiter, office manager, brand ambassador)? Only truly off-topic companies/roles are rejected.

---

## Node 6: SKIPPED (Low Relevance Score)

**What it means:** Company has a supported ATS and we scraped their jobs, but no role scored above the 75/100 relevance threshold against the user's profile keywords. The company is skipped without reaching the LLM grounding step.

**User experience:** Does not appear. Visible in activity log.

| Company | ATS | Jobs Scraped | Best Match | Score | Search |
|---------|-----|-------------|-----------|-------|--------|
| Apollo Research | Web/Ashby | 3 | Applied Sr. Fullstack Engineer | 50/100 | AI safety |
| NIST | Web | 3 | Director, Public Affairs | 40/100 | AI safety |
| Comet | Web | 6 | DevRel | 40/100 | ML startups |
| Abnormal Security | Web | 100 | Sr. Marketing Data Analyst | 70/100 | ML startups |
| Lightwheel | Web | 5 | Developer Advocate Intern | 52/100 | ML startups |
| Mind Robotics | Web | 7 | Data Architect, Robotics | 36/100 | ML startups |
| Mega | Web | 12 | Sr. Customer Experience Assoc. | 46/100 | ML startups |
| Array Labs | Web | 17 | Senior Data Analyst | 70/100 | ML startups |
| WitnessAI | Web | 4 | Principal Product Manager | 50/100 | ML startups |
| Aaru | Web | 11 | Researcher | 60/100 | ML startups |
| Anterior | Web | 3 | Member of Technical Staff (Backend) | 50/100 | ML startups |
| Etched.ai | Web | 25 | ML Research Engineer | 60/100 | ML startups |
| Apptronik | Web | 56 | Principal SW Eng - AI & Simulation | 58/100 | ML startups |
| Mark43 | Greenhouse | 48 | Data Scientist (LLM picker scored 2/5) | -- | AI safety |

**Why these were skipped:** The keyword-based relevance scorer compares job titles and descriptions against the user's target roles, domains, technical skills, and deal-breakers. These companies had jobs, but none matched well enough. The 75/100 threshold is intentionally high to avoid flooding the user with weak matches.

---

## Node 7: SKIPPED (Technical/Other)

**What it means:** The candidate was skipped for a non-relevance reason: extraction failure, duplicate detection, already in database, ATS board unreachable, etc.

| Company | Reason | Search |
|---------|--------|--------|
| SpaceX | Extraction failed: URL didn't look like a job posting | AI safety |
| Rubrik | Extraction failed: URL didn't look like a job posting | AI safety |
| Agility Robotics | Extraction failed: URL didn't look like a job posting | AI safety |
| Mashgin | Extraction failed: page requires login/JS rendering | ML startups |
| Parloa | Extraction failed: URL didn't look like a job posting | ML startups |
| Opendoor | Extraction failed: URL didn't look like a job posting | AI safety |
| Jobgether | Extraction failed: page requires login/JS rendering | AI safety |
| Redwood Materials | Extraction failed: URL didn't look like a job posting | ML startups |
| LMArena | ATS board not reachable | ML startups |
| READY Robotics | No open jobs found | ML startups |
| "AI Safety" | Duplicate of "Center for AI Safety" | AI safety |
| "Safe" | Duplicate of "Center for AI Safety" | ML startups |
| xAI | Duplicate (already evaluated) | ML startups |
| Augury | Job URL already in database | AI safety |
| "AI Research Scientist" | Job URL already in database (misparse: title as company) | AI safety |

**Common failure modes:**
- `site:boards.greenhouse.io` queries return job listing pages, not company board pages -- extraction correctly rejects these
- JS-heavy careers portals (Lever hosted pages with client-side rendering) return empty content
- LLM entity extraction sometimes parses a job title as a company name

---

## Summary Statistics (across 4 test searches)

| Node | Count | % of Candidates |
|------|-------|----------------|
| Accepted (ATS) | 7 | 8% |
| Accepted (Direct) | 8 | 9% |
| Tracked | 7 | 8% |
| Lead | 16 | 18% |
| Dropped | 13 | 14% |
| Skipped (score) | 14 | 15% |
| Skipped (other) | 16 | 17% |

**Conversion: 22 companies accepted out of ~91 candidates evaluated (24%).**
Of those, 15 have importable jobs; 7 are careers-page-only leads.
