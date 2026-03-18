"""Build tailored .docx resumes from LLM-generated content.

Replicates the formatting of the base resume (Prateek_Puri_Base_Resume.docx)
using python-docx from scratch.
"""

import logging
import os
from datetime import datetime, timezone

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor, Twips

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Formatting constants (extracted from base resume)
# ---------------------------------------------------------------------------
COLOR_NAVY = RGBColor(0x1F, 0x38, 0x64)
COLOR_ORANGE = RGBColor(0xC4, 0x59, 0x11)
COLOR_CONTACT = RGBColor(0x44, 0x54, 0x6A)  # dark blue-gray for contact text
COLOR_SEPARATOR = RGBColor(0x76, 0x71, 0x71)  # gray for separators
COLOR_DARK = RGBColor(0x32, 0x3E, 0x4F)  # last name, body text
COLOR_NAME_FIRST = RGBColor(0x7F, 0x7F, 0x7F)  # first name gray
COLOR_LINK = RGBColor(0x05, 0x63, 0xC1)  # hyperlink blue
BORDER_COLOR = "8496B0"  # blue-gray for borders

FONT_NAME_LIGHT = "Dubai Light"  # first name
FONT_NAME_BOLD = "Dubai"  # last name
FONT_BODY = "Calibri Light"  # body text + section headers
FONT_SKILL_LABEL = "Calibri"  # skill category labels

NAME_SIZE = Pt(34)
TAGLINE_SIZE = Pt(11)
CONTACT_SIZE = Pt(9.5)
SECTION_HEADER_SIZE = Pt(12)
BODY_SIZE = Pt(10)
BODY_SMALL = Pt(9.5)

# Margins from base resume
MARGIN_TOP = Emu(342900)     # 0.375"
MARGIN_BOTTOM = Emu(342900)  # 0.375"
MARGIN_LEFT = Emu(457200)    # 0.5"
MARGIN_RIGHT = Emu(457200)   # 0.5"

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

    # Contact line
    email = personal.get("email", "")
    phone = personal.get("phone", "")
    location = personal.get("location", "")
    contact_parts = [x for x in [email, phone, location] if x]
    contact_line = "  |  ".join(contact_parts)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(1)
    run = p.add_run(contact_line)
    _set_run(run, font_name=FONT_BODY, size=CONTACT_SIZE, color=COLOR_CONTACT)

    # Links line with hyperlinks
    linkedin = personal.get("linkedin", "")
    scholar = personal.get("google_scholar", "")

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)

    links = []
    if scholar:
        scholar_url = scholar if scholar.startswith("http") else f"https://{scholar}"
        links.append(("Google Scholar", scholar_url))
    if linkedin:
        linkedin_url = linkedin if linkedin.startswith("http") else f"https://{linkedin}"
        links.append(("LinkedIn", linkedin_url))

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

    # Bullets
    for bullet_text in bullets:
        _add_bullet_paragraph(doc, bullet_text)


def _add_experience(doc, experience_data: dict, profile_data: dict):
    """Add the Professional Experience section."""
    _add_section_header(doc, "Professional Experience")

    work_history = profile_data.get("work_history", [])
    employer_info = {}
    for wh in work_history:
        employer = wh.get("employer", "")
        key = employer.lower()
        end = wh.get("end") or "Present"
        employer_info[key] = {
            "org": employer,
            "title": wh.get("title", ""),
            "location": wh.get("location", "Washington, D.C."),
            "dates": f"{wh.get('start', '')} – {end}",
        }

    # RAND
    rand_info = employer_info.get("the rand corporation", employer_info.get("rand corporation", {
        "org": "The RAND Corporation", "title": "Information Scientist",
        "location": "Washington, D.C.", "dates": "2022 – Present",
    }))
    rand_bullets = experience_data.get("rand", {}).get("bullets", [])
    if rand_bullets:
        _add_experience_block(doc, rand_info["org"], rand_info["title"],
                              rand_info["location"], rand_info["dates"], rand_bullets)

    # FINRA
    finra_info = employer_info.get("finra", {
        "org": "FINRA", "title": "Senior Data Scientist",
        "location": "Washington, D.C.", "dates": "2020 – 2022",
    })
    finra_bullets = experience_data.get("finra", {}).get("bullets", [])
    if finra_bullets:
        _add_experience_block(doc, finra_info["org"], finra_info["title"],
                              finra_info["location"], finra_info["dates"], finra_bullets)

    # UCLA
    ucla_info = employer_info.get("ucla physics", employer_info.get("ucla", {
        "org": "UCLA Physics", "title": "PhD Researcher",
        "location": "Los Angeles, CA", "dates": "2014 – 2019",
    }))
    ucla_bullets = experience_data.get("ucla", {}).get("bullets", [])
    if ucla_bullets:
        _add_experience_block(doc, ucla_info["org"], ucla_info["title"],
                              ucla_info["location"], ucla_info["dates"], ucla_bullets)


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
        "communication": "Communication",
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


def build_docx(resume_data: dict, profile_data: dict, job_id: str) -> str:
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

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{job_id}_{timestamp}.docx"
    filepath = os.path.join(OUTPUT_DIR, filename)

    doc.save(filepath)
    logger.info("Resume saved to %s", filepath)
    return filepath
