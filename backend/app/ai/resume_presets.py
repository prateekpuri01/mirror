"""Resume design presets — layouts, color schemes, and fonts.

Each preset is a partial-style dict that gets deep-merged over the
docx_builder default style. Selection IDs (e.g. ``"navy"``, ``"two_column"``)
are stored as strings in ``user_profile.data["resume_design"]`` — renaming
one is a breaking change for existing users.

The full resolution chain (highest precedence first) is implemented in
``docx_builder._load_style_for_request``:
  1. explicit selection passed to ``build_docx``
  2. ``user_profile.data["resume_design"]``
  3. ``docs/resume_style.yaml`` power-user override
  4. ``docx_builder._DEFAULT_STYLE``
"""

from __future__ import annotations

import copy
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layout presets
# ---------------------------------------------------------------------------

# Layout selects WHICH sections render in WHAT order, the header shape,
# and the section-header style. ``section_style`` is consumed by
# ``docx_builder._render_section_header`` — values are "rule" (default,
# thin underline), "spaced" (letter-spaced caps, no rule), or "accent_bar"
# (colored block prefix, no rule). ``header_kind`` selects the header
# renderer: "banner" (filled colored band), "split" (name left / contact
# right), or "centered" (default).
LAYOUTS: dict[str, dict] = {
    "banner": {
        "layout": {
            "kind": "banner",
            "columns": 1,
            "header_kind": "banner",
            "section_style": "spaced",
            "section_underline": False,
        },
    },
    "compact": {
        "layout": {
            "kind": "compact",
            "columns": 1,
            "header_kind": "split",
            "section_style": "accent_bar",
            "section_underline": False,
        },
    },
    "two_column": {
        "layout": {
            "kind": "two_column",
            "columns": 2,
            "sidebar_width_in": 2.3,
        },
    },
    "timeline": {
        "layout": {
            "kind": "timeline",
            "columns": 1,
            "header_kind": "centered",
            "section_style": "rule",
            "section_underline": True,
            # Width of the left "dates" gutter in the experience table.
            "date_gutter_in": 0.95,
        },
    },
}


# ---------------------------------------------------------------------------
# Color schemes
# ---------------------------------------------------------------------------

# Each scheme provides a full colors block. Schemes are monochromatic (one
# accent + neutral grays); the maintainer's "Mustard" default remains
# available via the YAML fallback for power users who want dual-color play.
COLOR_SCHEMES: dict[str, dict] = {
    "navy": {
        "colors": {
            "navy": "#1F3A5F",       # section headers + name bold
            "orange": "#1F3A5F",     # tagline + accents (same as navy here)
            "contact": "#555555",
            "separator": "#B8B8B8",
            "dark": "#1A1A1A",
            "name_first": "#5C7A99",
            "link": "#1F3A5F",
            "border": "#1F3A5F",
        },
    },
    "slate": {
        "colors": {
            "navy": "#334155",
            "orange": "#334155",
            "contact": "#555555",
            "separator": "#B8B8B8",
            "dark": "#0F172A",
            "name_first": "#64748B",
            "link": "#334155",
            "border": "#334155",
        },
    },
    "charcoal": {
        "colors": {
            "navy": "#3D3D3D",
            "orange": "#3D3D3D",
            "contact": "#555555",
            "separator": "#B8B8B8",
            "dark": "#1A1A1A",
            "name_first": "#7A7A7A",
            "link": "#3D3D3D",
            "border": "#3D3D3D",
        },
    },
    "forest": {
        "colors": {
            "navy": "#2D5044",
            "orange": "#2D5044",
            "contact": "#555555",
            "separator": "#B8B8B8",
            "dark": "#1A1A1A",
            "name_first": "#6B8779",
            "link": "#2D5044",
            "border": "#2D5044",
        },
    },
    "burgundy": {
        "colors": {
            "navy": "#6B1F2E",
            "orange": "#6B1F2E",
            "contact": "#555555",
            "separator": "#B8B8B8",
            "dark": "#1A1A1A",
            "name_first": "#A05462",
            "link": "#6B1F2E",
            "border": "#6B1F2E",
        },
    },
}


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

# All fonts here ship natively with Word/Office365 so .docx export doesn't
# need font embedding. Helvetica falls back to Arial on Windows; Word
# handles the substitution.
FONTS: dict[str, dict] = {
    "aptos": {
        "fonts": {
            "name_light": "Aptos",
            "name_bold": "Aptos",
            "body": "Aptos",
            "skill_label": "Aptos",
        },
    },
    "calibri": {
        "fonts": {
            "name_light": "Calibri Light",
            "name_bold": "Calibri",
            "body": "Calibri",
            "skill_label": "Calibri",
        },
    },
    "helvetica": {
        "fonts": {
            "name_light": "Helvetica",
            "name_bold": "Helvetica",
            "body": "Helvetica",
            "skill_label": "Helvetica",
        },
    },
    "garamond": {
        "fonts": {
            "name_light": "Garamond",
            "name_bold": "Garamond",
            "body": "Garamond",
            "skill_label": "Garamond",
        },
    },
    "georgia": {
        "fonts": {
            "name_light": "Georgia",
            "name_bold": "Georgia",
            "body": "Georgia",
            "skill_label": "Georgia",
        },
    },
}


DEFAULT_DESIGN = {
    "layout": "banner",
    "color_scheme": "navy",
    "font": "aptos",
    "version": 1,
}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge ``override`` into ``base`` (returns a new dict)."""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve_style(selection: dict | None, base_style: dict) -> dict:
    """Merge layout/color/font preset partials over ``base_style``.

    ``selection`` keys: ``layout``, ``color_scheme``, ``font``. Unknown IDs
    fall back to the default for that knob with a logged warning — the
    function never raises, so a download can't 500 on a hand-edited DB row.
    """
    out = copy.deepcopy(base_style)
    selection = selection or {}

    layout_id = selection.get("layout") or DEFAULT_DESIGN["layout"]
    color_id = selection.get("color_scheme") or DEFAULT_DESIGN["color_scheme"]
    font_id = selection.get("font") or DEFAULT_DESIGN["font"]

    # Apply color first, then font (so font wins on its own block), then
    # layout last (so layout-specific sizing/spacing wins). Layouts no
    # longer override fonts — the user's font choice always wins now.
    if color_id in COLOR_SCHEMES:
        out = _deep_merge(out, COLOR_SCHEMES[color_id])
    else:
        logger.warning("Unknown color_scheme %r — falling back to default", color_id)
        out = _deep_merge(out, COLOR_SCHEMES[DEFAULT_DESIGN["color_scheme"]])

    if font_id in FONTS:
        out = _deep_merge(out, FONTS[font_id])
    else:
        logger.warning("Unknown font %r — falling back to default", font_id)
        out = _deep_merge(out, FONTS[DEFAULT_DESIGN["font"]])

    if layout_id in LAYOUTS:
        out = _deep_merge(out, LAYOUTS[layout_id])
    else:
        logger.warning("Unknown layout %r — falling back to default", layout_id)
        out = _deep_merge(out, LAYOUTS[DEFAULT_DESIGN["layout"]])

    return out


# ---------------------------------------------------------------------------
# Sample resume content (for the "Download Sample" button when the user
# hasn't generated a real resume yet).
# ---------------------------------------------------------------------------

SAMPLE_PROFILE_DATA = {
    "personal": {
        "name": "Alex Sample",
        "email": "alex@example.com",
        "phone": "5555550199",
        "linkedin": "linkedin.com/in/alex-sample",
        "professional_links": [],
    },
    "education": [
        {
            "degree": "M.S.",
            "field": "Computer Science",
            "institution": "State University",
            "year": "2018",
            "honors": "with distinction",
        },
        {
            "degree": "B.S.",
            "field": "Mathematics",
            "institution": "State College",
            "year": "2016",
        },
    ],
    "work_history": [
        {
            "employer": "Acme Robotics",
            "title": "Senior Software Engineer",
            "start": "Jan 2022",
            "end": "Present",
            "location": "San Francisco, CA",
        },
        {
            "employer": "Beta Analytics",
            "title": "Software Engineer",
            "start": "Jul 2018",
            "end": "Dec 2021",
            "location": "Austin, TX",
        },
    ],
}


SAMPLE_RESUME_CONTENT = {
    "tagline": "Software engineer with an eye for distributed systems and developer experience",
    "summary": (
        "Engineer with seven years building data and ML platforms used by hundreds "
        "of internal teams. Equally comfortable owning a backend service, shaping "
        "an API, or unblocking a stuck dataset."
    ),
    "selected_research": [
        {
            "category_label": "Platform",
            "title": "Reduced cross-region replication latency by 4x",
            "description": (
                "Designed and rolled out a streaming replication pipeline that "
                "replaced batch dumps, cutting p95 catch-up time from 40m to 9m "
                "and recovering ~$80k/yr in egress."
            ),
        },
    ],
    "experience": {
        "acme_robotics": {
            "bullets": [
                {"text": "Led migration of the metrics pipeline from Airflow to Dagster, eliminating ~30% of weekly on-call pages."},
                {"text": "Built a Python SDK adopted by 12 internal teams to publish features into the real-time inference store."},
                {"text": "Mentored two junior engineers through their first production launches; both promoted within 18 months."},
            ],
        },
        "beta_analytics": {
            "bullets": [
                {"text": "Shipped the first version of the customer-facing analytics API, growing to 2M requests/day in year one."},
                {"text": "Reduced ETL runtimes 6x by rewriting the join layer in Polars."},
                {"text": "Owned cost reduction for the data lake — cut S3 spend 38% via lifecycle policies and columnar repack."},
            ],
        },
    },
    "publications": [
        {
            "citation": (
                "Sample, A. and Coauthor, B. (2024). Towards better feature stores: "
                "Lessons from a high-throughput recommender. Proceedings of MLSys 2024."
            ),
        },
    ],
    "technical_skills": {
        "ai_systems": "PyTorch, vLLM, LangChain, agentic workflows",
        "data_science": "Polars, pandas, scikit-learn, causal inference",
        "engineering": "Python, Rust, FastAPI, Postgres, Kafka, Dagster, Docker",
    },
    "awards": "ACME Robotics Engineering Excellence Award (2024) — for the replication pipeline rebuild.",
}
