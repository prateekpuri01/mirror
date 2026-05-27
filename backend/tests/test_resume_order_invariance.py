"""
Regression test: LAYOUT_DEFAULT_ORDER is complete and section_order
controls which sections render and in what sequence.
"""
import app.ai.docx_builder as db
from app.ai.docx_builder import LAYOUT_DEFAULT_ORDER, _render_ordered_sections


def test_all_layouts_have_default_order():
    assert set(LAYOUT_DEFAULT_ORDER.keys()) == {"banner", "compact", "timeline", "two_column"}


def test_no_duplicate_section_ids():
    for layout, order in LAYOUT_DEFAULT_ORDER.items():
        assert len(order) == len(set(order)), f"Duplicate IDs in {layout}: {order}"


def test_custom_order_is_respected():
    """section_order overrides the layout default."""
    rendered = []
    original_summary = db._render_summary
    original_awards = db._render_awards
    original_experience = db._render_experience

    db._render_summary = lambda *a, **kw: rendered.append("summary")
    db._render_awards = lambda *a, **kw: rendered.append("awards")
    db._render_experience = lambda *a, **kw: rendered.append("experience")

    try:
        resume_data = {"section_order": ["awards", "summary", "experience"]}
        _render_ordered_sections(object(), object(), resume_data, {}, "banner")
    finally:
        db._render_summary = original_summary
        db._render_awards = original_awards
        db._render_experience = original_experience

    assert rendered == ["awards", "summary", "experience"]


def test_fallback_to_layout_default_when_no_order():
    """Missing section_order falls back to layout default."""
    rendered = []
    patches = {
        "_render_summary": "summary",
        "_render_selected_research": "selected_research",
        "_render_experience": "experience",
        "_render_publications": "publications",
        "_render_skills": "technical_skills",
        "_render_education": "education",
        "_render_awards": "awards",
    }
    originals = {k: getattr(db, k) for k in patches}
    for fn_name, section_id in patches.items():
        sid = section_id
        setattr(db, fn_name, lambda *a, sid=sid, **kw: rendered.append(sid))

    try:
        _render_ordered_sections(object(), object(), {}, {}, "banner")
    finally:
        for fn_name, fn in originals.items():
            setattr(db, fn_name, fn)

    assert rendered == LAYOUT_DEFAULT_ORDER["banner"]


def test_unknown_section_ids_skipped():
    """Unknown section IDs in section_order are silently ignored."""
    rendered = []
    original_summary = db._render_summary
    db._render_summary = lambda *a, **kw: rendered.append("summary")

    try:
        resume_data = {"section_order": ["summary", "totally_unknown_section"]}
        _render_ordered_sections(object(), object(), resume_data, {}, "banner")
    finally:
        db._render_summary = original_summary

    assert rendered == ["summary"]
