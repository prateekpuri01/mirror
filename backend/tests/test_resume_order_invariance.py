"""
Regression tests for section ordering:

1. LAYOUT_DEFAULT_ORDER is complete and section_order controls which
   sections render and in what sequence (docx dispatcher).
2. The strip-and-restore invariance around the LLM revision flow — the
   user's custom section_order must survive a round-trip even when the LLM
   returns JSON without that field (this is the regression #1 asked us to
   lock down).
"""

import pytest

import app.ai.docx_builder as db
from app.ai.docx_builder import LAYOUT_DEFAULT_ORDER, _render_ordered_sections


def test_all_layouts_have_default_order():
    assert set(LAYOUT_DEFAULT_ORDER.keys()) == {
        "banner",
        "compact",
        "centered_clean",
        "timeline",
        "two_column",
    }


def test_no_duplicate_section_ids():
    for layout, order in LAYOUT_DEFAULT_ORDER.items():
        assert len(order) == len(set(order)), f"Duplicate IDs in {layout}: {order}"


def test_custom_order_is_respected(monkeypatch):
    """section_order overrides the layout default."""
    rendered = []
    monkeypatch.setattr(db, "_render_summary", lambda *a, **kw: rendered.append("summary"))
    monkeypatch.setattr(db, "_render_awards", lambda *a, **kw: rendered.append("awards"))
    monkeypatch.setattr(db, "_render_experience", lambda *a, **kw: rendered.append("experience"))

    resume_data = {"section_order": ["awards", "summary", "experience"]}
    _render_ordered_sections(object(), object(), resume_data, {}, "banner")

    assert rendered == ["awards", "summary", "experience"]


def test_fallback_to_layout_default_when_no_order(monkeypatch):
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
    for fn_name, section_id in patches.items():
        monkeypatch.setattr(db, fn_name, lambda *a, sid=section_id, **kw: rendered.append(sid))

    _render_ordered_sections(object(), object(), {}, {}, "banner")

    assert rendered == LAYOUT_DEFAULT_ORDER["banner"]


def test_empty_order_renders_nothing(monkeypatch):
    """An explicit empty section_order renders nothing — it must NOT fall
    back to the layout default (distinguishes [] from a missing field)."""
    rendered = []
    for fn_name in (
        "_render_summary",
        "_render_selected_research",
        "_render_experience",
        "_render_publications",
        "_render_skills",
        "_render_education",
        "_render_awards",
    ):
        monkeypatch.setattr(db, fn_name, lambda *a, name=fn_name, **kw: rendered.append(name))

    _render_ordered_sections(object(), object(), {"section_order": []}, {}, "banner")

    assert rendered == []


def test_unknown_section_ids_skipped(monkeypatch):
    """Unknown section IDs in section_order are silently ignored."""
    rendered = []
    monkeypatch.setattr(db, "_render_summary", lambda *a, **kw: rendered.append("summary"))

    resume_data = {"section_order": ["summary", "totally_unknown_section"]}
    _render_ordered_sections(object(), object(), resume_data, {}, "banner")

    assert rendered == ["summary"]


def _patch_all_renders(monkeypatch):
    """Patch every section render fn to record its id; return the list."""
    rendered = []
    patches = {
        "_render_summary": "summary",
        "_render_selected_research": "selected_research",
        "_render_experience": "experience",
        "_render_experience_education_timeline": "experience_education",
        "_render_publications": "publications",
        "_render_skills": "technical_skills",
        "_render_education": "education",
        "_render_awards": "awards",
    }
    for fn_name, sid in patches.items():
        monkeypatch.setattr(db, fn_name, lambda *a, sid=sid, **kw: rendered.append(sid))
    return rendered


def test_template_section_order_used_when_no_resume_order(monkeypatch):
    """A resume with no section_order falls back to the profile's design
    template (resume_design.section_order), not the raw layout default."""
    rendered = _patch_all_renders(monkeypatch)
    profile = {"resume_design": {"section_order": ["awards", "summary", "experience"]}}

    _render_ordered_sections(object(), object(), {}, profile, "banner")

    assert rendered == ["awards", "summary", "experience"]


def test_resume_order_wins_over_template(monkeypatch):
    """A document's own section_order takes precedence over the template."""
    rendered = _patch_all_renders(monkeypatch)
    profile = {"resume_design": {"section_order": ["awards", "summary"]}}

    _render_ordered_sections(object(), object(), {"section_order": ["summary"]}, profile, "banner")

    assert rendered == ["summary"]


def test_template_filtered_to_layout(monkeypatch):
    """Template ids not valid for the active layout are dropped (e.g. a
    banner template can't inject the timeline-only experience_education)."""
    rendered = _patch_all_renders(monkeypatch)
    profile = {"resume_design": {"section_order": ["summary", "experience_education", "awards"]}}

    _render_ordered_sections(object(), object(), {}, profile, "banner")

    assert rendered == ["summary", "awards"]


def test_empty_template_falls_back_to_layout_default(monkeypatch):
    """An empty/absent template uses the layout default (the Sections control
    never lets a user hide everything)."""
    rendered = _patch_all_renders(monkeypatch)
    profile = {"resume_design": {"section_order": []}}

    _render_ordered_sections(object(), object(), {}, profile, "banner")

    assert rendered == LAYOUT_DEFAULT_ORDER["banner"]


@pytest.mark.asyncio
async def test_broad_rewrite_preserves_section_order(monkeypatch):
    """The user's section_order survives the LLM round-trip even when the
    model returns JSON without it. broad_rewrite strips it before the call
    and restores it on the result — revise_resume mirrors this pattern."""
    import app.ai.resume_agent as agent

    custom_order = ["awards", "summary", "experience"]

    # LLM returns a rewritten resume that has NO section_order field.
    async def fake_call(system, user_content, max_tokens=4000):
        return {"summary": "rewritten", "experience": {}}

    monkeypatch.setattr(agent, "_call_openai_json", fake_call)

    state = {
        "resume_json": {"summary": "original", "section_order": custom_order},
        "chat_history": [],
        "user_message": "make it punchier",
        "profile_text": "",
        "job_context": "",
    }

    out = await agent.broad_rewrite(state)

    assert out["updated_json"]["section_order"] == custom_order


@pytest.mark.asyncio
async def test_broad_rewrite_survives_non_dict_llm_result(monkeypatch):
    """A malformed (non-dict) LLM response must not crash the restore step."""
    import app.ai.resume_agent as agent

    async def fake_call(system, user_content, max_tokens=4000):
        return ["unexpected", "list"]

    monkeypatch.setattr(agent, "_call_openai_json", fake_call)

    state = {
        "resume_json": {"summary": "original", "section_order": ["summary"]},
        "chat_history": [],
        "user_message": "rewrite",
        "profile_text": "",
        "job_context": "",
    }

    # Should not raise — the isinstance guard skips the restore.
    out = await agent.broad_rewrite(state)
    assert out["updated_json"] == ["unexpected", "list"]
