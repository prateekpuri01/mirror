"""Build tailored .docx resumes from LLM-generated content.

Loads styling from docs/resume_style.yaml (user-customizable, gitignored).
Falls back to neutral defaults if the file doesn't exist.
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor, Twips

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Style loading from YAML (with neutral fallbacks)
# ---------------------------------------------------------------------------

_DEFAULT_STYLE = {
    "colors": {
        "navy": "#2B3E50",
        "orange": "#E67E22",
        "contact": "#555555",
        "separator": "#999999",
        "dark": "#333333",
        "name_first": "#666666",
        "link": "#2980B9",
        "border": "#AABBCC",
    },
    "fonts": {
        "name_light": "Calibri Light",
        "name_bold": "Calibri",
        "body": "Calibri Light",
        "skill_label": "Calibri",
    },
    "sizes": {
        "name": 28,
        "tagline": 11,
        "contact": 9.5,
        "section_header": 12,
        "body": 10,
        "body_small": 9.5,
    },
    "margins": {
        "top": 0.5,
        "bottom": 0.5,
        "left": 0.6,
        "right": 0.6,
    },
}


def _load_style() -> dict:
    """Load resume style from YAML, falling back to defaults."""
    for path in [
        Path("/app/docs/resume_style.yaml"),
        Path("docs/resume_style.yaml"),
    ]:
        if path.exists():
            try:
                with open(path) as f:
                    user_style = yaml.safe_load(f) or {}
                # Merge: user overrides defaults
                merged = {}
                for section in _DEFAULT_STYLE:
                    merged[section] = {**_DEFAULT_STYLE[section], **(user_style.get(section) or {})}
                logger.info("Loaded resume style from %s", path)
                return merged
            except Exception:
                logger.warning("Failed to load resume style from %s, using defaults", path)
    return _DEFAULT_STYLE


def _hex_to_rgb(hex_str: str) -> RGBColor:
    """Convert '#RRGGBB' hex string to RGBColor."""
    h = hex_str.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _inches_to_emu(inches: float) -> int:
    """Convert inches to EMU (English Metric Units)."""
    return int(inches * 914400)


# Load on import
_STYLE = _load_style()

COLOR_NAVY = _hex_to_rgb(_STYLE["colors"]["navy"])
COLOR_ORANGE = _hex_to_rgb(_STYLE["colors"]["orange"])
COLOR_CONTACT = _hex_to_rgb(_STYLE["colors"]["contact"])
COLOR_SEPARATOR = _hex_to_rgb(_STYLE["colors"]["separator"])
COLOR_DARK = _hex_to_rgb(_STYLE["colors"]["dark"])
COLOR_NAME_FIRST = _hex_to_rgb(_STYLE["colors"]["name_first"])
COLOR_LINK = _hex_to_rgb(_STYLE["colors"]["link"])
BORDER_COLOR = _STYLE["colors"]["border"].lstrip("#")

FONT_NAME_LIGHT = _STYLE["fonts"]["name_light"]
FONT_NAME_BOLD = _STYLE["fonts"]["name_bold"]
FONT_BODY = _STYLE["fonts"]["body"]
FONT_SKILL_LABEL = _STYLE["fonts"]["skill_label"]

NAME_SIZE = Pt(_STYLE["sizes"]["name"])
TAGLINE_SIZE = Pt(_STYLE["sizes"]["tagline"])
CONTACT_SIZE = Pt(_STYLE["sizes"]["contact"])
SECTION_HEADER_SIZE = Pt(_STYLE["sizes"]["section_header"])
BODY_SIZE = Pt(_STYLE["sizes"]["body"])
BODY_SMALL = Pt(_STYLE["sizes"]["body_small"])

MARGIN_TOP = Emu(_inches_to_emu(_STYLE["margins"]["top"]))
MARGIN_BOTTOM = Emu(_inches_to_emu(_STYLE["margins"]["bottom"]))
MARGIN_LEFT = Emu(_inches_to_emu(_STYLE["margins"]["left"]))
MARGIN_RIGHT = Emu(_inches_to_emu(_STYLE["margins"]["right"]))

OUTPUT_DIR = "/app/output/resumes"

# Line spacing constants
LINE_SPACING_BODY = Emu(248 * 12700 // 240)    # ~1.033
LINE_SPACING_COMPACT = Emu(244 * 12700 // 240)  # ~1.017


def _set_run(run, font_name=FONT_BODY, size=BODY_SIZE, color=None, bold=False, italic=False, underline=False):
    """Apply formatting to a run."""
    run.font.name = font_name
    run.font.size = size
    if color:
        run.font.color.rgb = color
    run.bold = bold
    run.italic = italic
    if underline:
        run.font.underline = True


def _add_bottom_border(paragraph, sz="6", space="2", color=BORDER_COLOR):
    """Add a bottom border to a paragraph."""
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), sz)
    bottom.set(qn("w:space"), space)
    bottom.set(qn("w:color"), color)
    pBdr.append(bottom)
    pPr.append(pBdr)


def _add_hyperlink(paragraph, url: str, text: str):
    """Add a clickable hyperlink to a paragraph."""
    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    run_elem = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")

    # Font
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:ascii"), FONT_BODY)
    rFonts.set(qn("w:hAnsi"), FONT_BODY)
    rPr.append(rFonts)

    # Size
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "19")  # 9.5pt in half-points
    rPr.append(sz)

    # Color
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rPr.append(color)

    # Underline
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rPr.append(u)

    run_elem.append(rPr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run_elem.append(t)

    hyperlink.append(run_elem)
    paragraph._p.append(hyperlink)


def _add_bullet_paragraph(doc, text: str, font_size=None):
    """Add a bulleted paragraph using a text bullet character + hanging indent."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Emu(19050)   # 1.5pt
    p.paragraph_format.space_after = Emu(31750)     # 2.5pt
    p.paragraph_format.left_indent = Twips(360)     # 0.25"
    p.paragraph_format.first_line_indent = Twips(-200)  # hanging

    run = p.add_run("\u2022  ")
    _set_run(run, size=font_size or BODY_SMALL)

    run = p.add_run(text)
    _set_run(run, size=font_size or BODY_SMALL)
    return p


def _add_header(doc, profile_data: dict, tagline: str):
    """Add the name, tagline, contact line, and links."""
    personal = profile_data.get("personal", {})
    full_name = personal.get("name", "")
    name_parts = full_name.split(" ", 1)
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    # Name: first name (gray, light) + last name (dark, bold)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(0)

    if first_name:
        run = p.add_run(first_name.upper())
        _set_run(run, font_name=FONT_NAME_LIGHT, size=NAME_SIZE, color=COLOR_NAME_FIRST)
    if last_name:
        run = p.add_run(" ")
        run.font.size = NAME_SIZE
        run = p.add_run(last_name.upper())
        _set_run(run, font_name=FONT_NAME_BOLD, size=NAME_SIZE, color=COLOR_DARK, bold=True)

    # Tagline (centered, orange) with bottom border
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(tagline)
    _set_run(run, font_name=FONT_BODY, size=TAGLINE_SIZE, color=COLOR_ORANGE)
    _add_bottom_border(p, sz="2", space="4", color=BORDER_COLOR)

    # Contact line: 📱 phone    ✉ email    🌐 linkedin
    email = personal.get("email", "")
    phone = personal.get("phone", "")
    linkedin = personal.get("linkedin", "")

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(1)

    contact_items: list[tuple[str, str, str | None]] = []
    if phone:
        # Format phone as xxx-xxx-xxxx if it's 10 raw digits
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) == 10:
            formatted_phone = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        else:
            formatted_phone = phone
        contact_items.append(("\U0001f4f1 ", formatted_phone, None))  # 📱
    if email:
        contact_items.append(("\u2709 ", email, f"mailto:{email}"))  # ✉
    if linkedin:
        linkedin_url = linkedin if linkedin.startswith("http") else f"https://{linkedin}"
        # Display as short path: linkedin.com/in/username
        display = linkedin_url.replace("https://www.", "").replace("http://www.", "").replace("https://", "").replace("http://", "").rstrip("/")
        contact_items.append(("\U0001f310 ", display, linkedin_url))  # 🌐

    for i, (emoji, text, url) in enumerate(contact_items):
        if i > 0:
            run = p.add_run("    ")
            _set_run(run, font_name=FONT_BODY, size=CONTACT_SIZE, color=COLOR_CONTACT)
        run = p.add_run(emoji)
        _set_run(run, font_name=FONT_BODY, size=CONTACT_SIZE, color=COLOR_CONTACT)
        if url:
            _add_hyperlink(p, url, text)
        else:
            run = p.add_run(text)
            _set_run(run, font_name=FONT_BODY, size=CONTACT_SIZE, color=COLOR_CONTACT)

    # Professional links line: Google Scholar | RAND Profile | etc.
    # Built from google_scholar (auto) + personal.professional_links[]
    scholar = personal.get("google_scholar", "")
    professional_links: list[dict] = personal.get("professional_links", [])

    links: list[tuple[str, str]] = []
    if scholar:
        scholar_url = scholar if scholar.startswith("http") else f"https://{scholar}"
        links.append(("Google Scholar", scholar_url))
    for pl in professional_links:
        url = pl.get("url", "")
        label = pl.get("label", "")
        if url and label:
            if not url.startswith("http"):
                url = f"https://{url}"
            links.append((label, url))

    if links:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(2)

        for i, (label, url) in enumerate(links):
            if i > 0:
                run = p.add_run("  |  ")
                _set_run(run, font_name=FONT_BODY, size=CONTACT_SIZE, color=COLOR_SEPARATOR)
            _add_hyperlink(p, url, label)


def _add_section_header(doc, title: str):
    """Add a bold navy section header with bottom border."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Emu(127000)  # 10pt
    p.paragraph_format.space_after = Emu(38100)     # 3pt
    run = p.add_run(title.upper())
    _set_run(run, font_name=FONT_BODY, size=SECTION_HEADER_SIZE, color=COLOR_NAVY, bold=True)
    _add_bottom_border(p, sz="6", space="2", color=BORDER_COLOR)


def _add_summary(doc, summary: str):
    """Add the professional summary section."""
    _add_section_header(doc, "Summary")
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(summary)
    _set_run(run, size=BODY_SIZE)


def _add_selected_research(doc, research_entries: list):
    """Add the Selected Research section with colored category labels."""
    _add_section_header(doc, "Selected Research")

    for entry in research_entries:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(0)

        # Category label (bold, orange)
        label = entry.get("category_label", "RESEARCH")
        run = p.add_run(f"{label.upper()} — ")
        _set_run(run, font_name=FONT_SKILL_LABEL, size=BODY_SIZE, color=COLOR_ORANGE, bold=True)

        # Title (bold, navy)
        run = p.add_run(entry.get("title", ""))
        _set_run(run, font_name=FONT_BODY, size=BODY_SIZE, color=COLOR_NAVY, bold=True)

        # Description
        desc = entry.get("description", "")
        if desc:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.left_indent = Inches(0.25)
            run = p.add_run(desc)
            _set_run(run, size=BODY_SMALL)


def _add_experience_block(doc, org: str, title: str, location: str, dates: str, bullets: list):
    """Add an employer block with org/title/dates and bulleted accomplishments."""
    # Org name + location
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(0)

    run = p.add_run(org)
    _set_run(run, font_name=FONT_BODY, size=BODY_SIZE, color=COLOR_NAVY, bold=True)

    run = p.add_run(f"  —  {location}")
    _set_run(run, size=BODY_SIZE, color=COLOR_CONTACT)

    # Title + dates
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)

    run = p.add_run(title)
    _set_run(run, size=BODY_SIZE, italic=True)

    run = p.add_run(f"  |  {dates}")
    _set_run(run, size=BODY_SMALL, color=COLOR_SEPARATOR)

    # Bullets — support both old (string) and new (dict with "text") format
    for bullet in bullets:
        bullet_text = bullet["text"] if isinstance(bullet, dict) else bullet
        _add_bullet_paragraph(doc, bullet_text)


def _add_experience(doc, experience_data: dict, profile_data: dict):
    """Add the Professional Experience section.

    Dynamically iterates over work_history from profile data, matching
    employer keys to experience blocks from the LLM output.
    """
    from app.ai.utils import employer_key

    _add_section_header(doc, "Professional Experience")

    work_history = profile_data.get("work_history", [])

    # Build a lookup from employer_key → work history metadata
    wh_by_key = {}
    for wh in work_history:
        key = employer_key(wh.get("employer", ""))
        end = wh.get("end") or "Present"
        wh_by_key[key] = {
            "org": wh.get("employer", ""),
            "title": wh.get("title", ""),
            "location": wh.get("location", ""),
            "dates": f"{wh.get('start', '')} – {end}",
        }

    # Iterate experience blocks in work_history order (most recent first)
    for wh in work_history:
        key = employer_key(wh.get("employer", ""))
        emp_data = experience_data.get(key, {})
        bullets = emp_data.get("bullets", [])
        if not bullets:
            continue

        info = wh_by_key.get(key, {})
        _add_experience_block(
            doc,
            info.get("org", key),
            info.get("title", ""),
            info.get("location", ""),
            info.get("dates", ""),
            bullets,
        )

    # Also render any experience blocks that didn't match a work_history entry
    rendered_keys = {employer_key(wh.get("employer", "")) for wh in work_history}
    for key, emp_data in experience_data.items():
        if key in rendered_keys:
            continue
        bullets = emp_data.get("bullets", [])
        if bullets:
            _add_experience_block(doc, key, "", "", "", bullets)


def _add_publications(doc, publications: list):
    """Add the Publications section with bullets."""
    _add_section_header(doc, "Selected Publications")

    for pub in publications:
        citation = pub.get("citation", "")
        if not citation:
            continue
        _add_bullet_paragraph(doc, citation, font_size=BODY_SMALL)


def _add_skills(doc, skills_data: dict):
    """Add the Technical Skills section."""
    _add_section_header(doc, "Technical Skills")

    category_labels = {
        "ai_systems": "AI Systems",
        "data_science": "Data Science",
        "engineering": "Engineering",
    }

    for key, label in category_labels.items():
        value = skills_data.get(key, "")
        if not value:
            continue
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Emu(19050)   # 1.5pt
        p.paragraph_format.space_after = Emu(31750)     # 2.5pt

        run = p.add_run(f"{label}: ")
        _set_run(run, font_name=FONT_SKILL_LABEL, size=BODY_SMALL, color=COLOR_NAVY, bold=True)

        run = p.add_run(value)
        _set_run(run, size=BODY_SMALL)


def _add_education(doc, profile_data: dict):
    """Add the Education section."""
    _add_section_header(doc, "Education")

    for edu in profile_data.get("education", []):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Emu(6350)    # 0.5pt
        p.paragraph_format.space_after = Emu(6350)     # 0.5pt
        p.paragraph_format.left_indent = Emu(63500)    # ~0.07"

        degree_field = f"{edu.get('degree', '')} {edu.get('field', '')}".strip()
        institution = edu.get("institution", "")
        year = edu.get("year", "")
        honors = edu.get("honors", "")

        run = p.add_run(f"{degree_field}, {institution}, {year}")
        _set_run(run, size=BODY_SIZE)

        if honors:
            run = p.add_run(f" — {honors}")
            _set_run(run, size=BODY_SIZE, italic=True)


def _add_awards(doc, awards_text: str):
    """Add the Awards section."""
    _add_section_header(doc, "Awards & Honors")

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(awards_text)
    _set_run(run, size=BODY_SMALL)


def _sanitize_filename(s: str) -> str:
    """Remove characters unsafe for filenames, preserve spaces."""
    s = re.sub(r'[<>:"/\\|?*]', '', s)
    s = re.sub(r'\s+', ' ', s.strip())
    return s


def build_docx(
    resume_data: dict,
    profile_data: dict,
    job_id: str,
    company: str | None = None,
    title: str | None = None,
) -> str:
    """Build a formatted .docx resume from LLM-generated content.

    Returns path to the written .docx file.
    """
    doc = Document()

    # Page setup
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.orientation = WD_ORIENT.PORTRAIT
    section.top_margin = MARGIN_TOP
    section.bottom_margin = MARGIN_BOTTOM
    section.left_margin = MARGIN_LEFT
    section.right_margin = MARGIN_RIGHT

    # Set default style
    style = doc.styles["Normal"]
    style.font.name = FONT_BODY
    style.font.size = BODY_SIZE
    style.paragraph_format.space_before = Pt(0)
    style.paragraph_format.space_after = Pt(0)

    # Build sections
    tagline = resume_data.get("tagline", "")
    _add_header(doc, profile_data, tagline)
    _add_summary(doc, resume_data.get("summary", ""))
    _add_selected_research(doc, resume_data.get("selected_research", []))
    _add_experience(doc, resume_data.get("experience", {}), profile_data)
    _add_publications(doc, resume_data.get("publications", []))
    _add_skills(doc, resume_data.get("technical_skills", {}))
    _add_education(doc, profile_data)

    awards = resume_data.get("awards", "")
    if awards:
        _add_awards(doc, awards)

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if company and title:
        # Use candidate name from profile if available
        personal = profile_data.get("personal", {})
        name = personal.get("name", "Resume")
        safe_name = _sanitize_filename(name)
        safe_company = _sanitize_filename(company)
        safe_title = _sanitize_filename(title)
        filename = f"{safe_name} {safe_company} {safe_title}.docx"
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{job_id}_{timestamp}.docx"
    filepath = os.path.join(OUTPUT_DIR, filename)

    doc.save(filepath)
    logger.info("Resume saved to %s", filepath)
    return filepath
