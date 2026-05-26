"""Tests for the resume design preset system.

Covers the docx_builder refactor: layouts × colors × fonts render, the
style resolver merges in the right precedence, and the two-column layout
uses native Word columns (not tables — that's the ATS regression we
explicitly want to guard against).
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest
from docx import Document

from app.ai import docx_builder
from app.ai.resume_presets import (
    COLOR_SCHEMES,
    FONTS,
    LAYOUTS,
    SAMPLE_PROFILE_DATA,
    SAMPLE_RESUME_CONTENT,
    resolve_style,
)


@pytest.fixture(autouse=True)
def _redirect_output_dir(tmp_path, monkeypatch):
    """Redirect docx output to a tmp dir so tests don't pollute /app/output."""
    monkeypatch.setattr(docx_builder, "OUTPUT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _bypass_yaml_overrides(monkeypatch):
    """Force tests to use the in-code default style.

    The dev/maintainer's ``docs/resume_style.yaml`` would otherwise alter
    the colors/fonts that show up in the rendered XML, which would defeat
    the color/font-hex assertions below.
    """
    monkeypatch.setattr(
        docx_builder,
        "_load_yaml_style",
        lambda: {k: (dict(v) if isinstance(v, dict) else v)
                 for k, v in docx_builder._DEFAULT_STYLE.items()},
    )


# ---------------------------------------------------------------------------
# Combinatorial smoke: every (layout, color, font) renders & re-parses
# ---------------------------------------------------------------------------


_LAYOUT_IDS = list(LAYOUTS.keys())
_COLOR_IDS = list(COLOR_SCHEMES.keys())
_FONT_IDS = list(FONTS.keys())


@pytest.mark.parametrize("layout", _LAYOUT_IDS)
@pytest.mark.parametrize("color_scheme", _COLOR_IDS)
@pytest.mark.parametrize("font", _FONT_IDS)
def test_build_docx_combinatorial(layout, color_scheme, font):
    design = {"layout": layout, "color_scheme": color_scheme, "font": font}
    path = docx_builder.build_docx(
        resume_data=SAMPLE_RESUME_CONTENT,
        profile_data=SAMPLE_PROFILE_DATA,
        job_id=f"smoke-{layout}-{color_scheme}-{font}",
        resume_design=design,
    )
    assert os.path.exists(path), f"build_docx didn't produce a file for {design}"
    assert os.path.getsize(path) > 5_000, f"docx unexpectedly small for {design}"

    # Re-parse: catches malformed OOXML that opens but breaks Word silently.
    Document(path)


# ---------------------------------------------------------------------------
# Color & font assertions — the chosen values must actually show up in the
# rendered XML, not just live in the style dict.
# ---------------------------------------------------------------------------


def _extract_document_xml(docx_path: str) -> str:
    with zipfile.ZipFile(docx_path) as z:
        with z.open("word/document.xml") as f:
            return f.read().decode("utf-8")


@pytest.mark.parametrize("color_scheme", _COLOR_IDS)
def test_color_hex_appears_in_xml(color_scheme):
    accent_hex = COLOR_SCHEMES[color_scheme]["colors"]["navy"].lstrip("#").upper()
    path = docx_builder.build_docx(
        resume_data=SAMPLE_RESUME_CONTENT,
        profile_data=SAMPLE_PROFILE_DATA,
        job_id=f"color-{color_scheme}",
        resume_design={"color_scheme": color_scheme},
    )
    xml = _extract_document_xml(path)
    assert accent_hex in xml, f"Accent {accent_hex} not found in rendered XML for {color_scheme}"


@pytest.mark.parametrize("font", _FONT_IDS)
def test_font_name_appears_in_xml(font):
    body_font = FONTS[font]["fonts"]["body"]
    path = docx_builder.build_docx(
        resume_data=SAMPLE_RESUME_CONTENT,
        profile_data=SAMPLE_PROFILE_DATA,
        job_id=f"font-{font}",
        resume_design={"font": font},
    )
    xml = _extract_document_xml(path)
    assert f'w:ascii="{body_font}"' in xml, f"Font {body_font} not asserted in XML for {font}"


@pytest.mark.parametrize("layout", _LAYOUT_IDS)
@pytest.mark.parametrize("font", _FONT_IDS)
def test_user_font_choice_wins_over_layout(layout, font):
    """A layout preset must not silently override the user's font.

    Regression test: the Executive layout used to ship a ``fonts`` block
    that forced Garamond regardless of the user's pick, producing
    Garamond .docx even when Aptos was selected.
    """
    body_font = FONTS[font]["fonts"]["body"]
    other_fonts = {FONTS[fid]["fonts"]["body"] for fid in _FONT_IDS if fid != font}

    path = docx_builder.build_docx(
        resume_data=SAMPLE_RESUME_CONTENT,
        profile_data=SAMPLE_PROFILE_DATA,
        job_id=f"font-vs-layout-{font}-{layout}",
        resume_design={"layout": layout, "font": font},
    )
    xml = _extract_document_xml(path)
    assert f'w:ascii="{body_font}"' in xml, (
        f"User-selected font {body_font} missing from XML when layout={layout}"
    )
    # And no other preset font should be present — that would mean a
    # layout preset is silently substituting fonts.
    for other in other_fonts:
        assert f'w:ascii="{other}"' not in xml, (
            f"layout={layout} silently overrode font {body_font} with {other}"
        )


# ---------------------------------------------------------------------------
# Two-column structural test — the regression we care about most. Tables /
# text boxes would break ATS parsing; native ``w:cols`` is the safe shape.
# ---------------------------------------------------------------------------


def test_two_column_uses_shaded_sidebar_table():
    """Two-column ships a filled-sidebar designer layout.

    This deliberately trades native-column ATS safety for the colored-
    sidebar look users expect from modern resume templates. The UI surfaces
    the trade-off via an "Best for direct submissions" warning chip.
    """
    path = docx_builder.build_docx(
        resume_data=SAMPLE_RESUME_CONTENT,
        profile_data=SAMPLE_PROFILE_DATA,
        job_id="two-col-structure",
        resume_design={"layout": "two_column", "color_scheme": "burgundy"},
    )
    xml = _extract_document_xml(path)

    # Single sidebar table — exactly one for the layout.
    assert xml.count("<w:tbl>") == 1, "Expected exactly one sidebar table"
    # Sidebar shading carries the accent hex.
    burgundy = COLOR_SCHEMES["burgundy"]["colors"]["navy"].lstrip("#").upper()
    assert f'w:fill="{burgundy}"' in xml, "Sidebar must be shaded with the accent color"


# ---------------------------------------------------------------------------
# Resolver precedence + fallback
# ---------------------------------------------------------------------------


def test_resolver_unknown_ids_fall_back_to_defaults(caplog):
    base = {k: (dict(v) if isinstance(v, dict) else v)
            for k, v in docx_builder._DEFAULT_STYLE.items()}
    out = resolve_style(
        {"layout": "spaceship", "color_scheme": "tangerine", "font": "wingdings"},
        base,
    )
    # Should still produce a valid style — falls back to default for each.
    assert out["layout"]["kind"] in {"banner"}, "Layout should fall back to default"
    assert out["colors"]["navy"] == COLOR_SCHEMES["navy"]["colors"]["navy"]
    assert out["fonts"]["body"] == FONTS["aptos"]["fonts"]["body"]


def test_resolver_explicit_color_overrides_yaml():
    """An explicit color_scheme selection must beat a YAML override."""
    base = {k: (dict(v) if isinstance(v, dict) else v)
            for k, v in docx_builder._DEFAULT_STYLE.items()}
    # Simulate a custom YAML "Mustard" navy color living in base
    base["colors"]["navy"] = "#B8851E"

    out = resolve_style({"color_scheme": "slate"}, base)
    # Selection beats YAML — slate must win for keys it defines.
    assert out["colors"]["navy"] == COLOR_SCHEMES["slate"]["colors"]["navy"]


def test_load_style_for_request_no_selection_returns_base(monkeypatch):
    """When no design is anywhere, the YAML/default base passes through.

    This is the backward-compat guarantee: existing users without a saved
    ``resume_design`` keep their current YAML styling.
    """
    sentinel_color = "#ABCDEF"
    base = {k: (dict(v) if isinstance(v, dict) else v)
            for k, v in docx_builder._DEFAULT_STYLE.items()}
    base["colors"]["navy"] = sentinel_color
    monkeypatch.setattr(docx_builder, "_load_yaml_style", lambda: base)

    out = docx_builder._load_style_for_request(None, None)
    assert out["colors"]["navy"] == sentinel_color


def test_load_style_for_request_explicit_beats_profile(monkeypatch):
    """Param to build_docx beats profile.data.resume_design."""
    base = {k: (dict(v) if isinstance(v, dict) else v)
            for k, v in docx_builder._DEFAULT_STYLE.items()}
    monkeypatch.setattr(docx_builder, "_load_yaml_style", lambda: base)

    profile_data = {"resume_design": {"color_scheme": "navy"}}
    out = docx_builder._load_style_for_request(
        {"color_scheme": "burgundy"}, profile_data,
    )
    assert out["colors"]["navy"] == COLOR_SCHEMES["burgundy"]["colors"]["navy"]


def test_load_style_for_request_profile_used_when_no_explicit(monkeypatch):
    base = {k: (dict(v) if isinstance(v, dict) else v)
            for k, v in docx_builder._DEFAULT_STYLE.items()}
    monkeypatch.setattr(docx_builder, "_load_yaml_style", lambda: base)

    profile_data = {"resume_design": {"color_scheme": "forest"}}
    out = docx_builder._load_style_for_request(None, profile_data)
    assert out["colors"]["navy"] == COLOR_SCHEMES["forest"]["colors"]["navy"]
