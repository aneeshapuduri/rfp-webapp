"""
Phase 4: assembles the full proposal into a .docx following config/template_spec.md's
12-section order (cover through closing), including the sections Phases 1-3 didn't cover
directly: Assumptions, Staffing Plan, and Effort & Pricing Summary.
"""
from __future__ import annotations

import datetime

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from template_mapper import OUR_SECTIONS

NAVY = RGBColor(0x1F, 0x2D, 0x50)
ACCENT = RGBColor(0x2E, 0x74, 0xB5)
GRAY = RGBColor(0x59, 0x59, 0x59)
GREEN = "D9EAD3"
ORANGE = "FCE5CD"


def _shade(cell, hex_color: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.makeelement(qn("w:shd"), {qn("w:val"): "clear", qn("w:color"): "auto", qn("w:fill"): hex_color})
    tc_pr.append(shd)


def _style_base(doc: Document):
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = 1.15
    for level, size in [(1, 18), (2, 14)]:
        style = doc.styles[f"Heading {level}"]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.color.rgb = NAVY
        style.font.bold = True
        style.paragraph_format.space_before = Pt(18)
        style.paragraph_format.space_after = Pt(8)


def _add_footer(doc: Document, project_title: str):
    section = doc.sections[0]
    footer = section.footer
    p = footer.paragraphs[0]
    p.text = f"{project_title} — Confidential — Proposal"
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in p.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = GRAY


def _add_cover_page(doc: Document, project_title: str, agency: str, company: dict):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    for _ in range(4):
        doc.add_paragraph()

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("PROPOSAL")
    run.font.size = Pt(16)
    run.font.color.rgb = ACCENT
    run.font.bold = True

    proj = doc.add_paragraph()
    proj.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = proj.add_run(project_title)
    run.font.size = Pt(26)
    run.font.color.rgb = NAVY
    run.font.bold = True

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = sub.add_run(f"Submitted in response to a Request for Proposal from {agency}")
    run.font.size = Pt(13)
    run.font.color.rgb = GRAY
    run.italic = True

    for _ in range(6):
        doc.add_paragraph()

    for text, size, bold, italic in [
        (company.get("company_name", ""), 15, True, False),
        (company.get("tagline", ""), 11, False, True),
        (company.get("hq_location", ""), 10, False, False),
    ]:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        run.font.size = Pt(size)
        run.font.bold = bold
        run.italic = italic
        run.font.color.rgb = NAVY if bold else GRAY

    for _ in range(3):
        doc.add_paragraph()
    date_p = doc.add_paragraph()
    date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = date_p.add_run(datetime.date.today().strftime("%B %d, %Y"))
    run.font.size = Pt(10)
    run.font.color.rgb = GRAY

    doc.add_section(WD_SECTION.NEW_PAGE)


def _add_body(doc: Document, text: str):
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            doc.add_paragraph(line[2:], style="List Bullet")
        else:
            doc.add_paragraph(line)


def _add_table(doc: Document, headers: list[str], rows: list[list[str]], widths_in: list[float], status_col: int | None = None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    widths = [Inches(w) for w in widths_in]
    hdr = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = h
        hdr[i].width = widths[i]
        _shade(hdr[i], "1F2D50")
        for p in hdr[i].paragraphs:
            for r in p.runs:
                r.font.bold = True
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for row_data in rows:
        row = table.add_row().cells
        for i, val in enumerate(row_data):
            row[i].text = str(val)
            row[i].width = widths[i]
        if status_col is not None:
            status_val = str(row_data[status_col])
            _shade(row[status_col], GREEN if "Full" in status_val else ORANGE)
    return table


def _find_heading_element(doc: Document, heading_text: str):
    """Locates a paragraph in an opened (client-supplied) template whose style is a Heading
    style and whose text matches exactly — this is always a heading text returned by
    template_mapper.extract_template_headings() on this same document, so an exact match is
    expected. Returns the underlying XML element (not the python-docx Paragraph wrapper) since
    that's what the insertion helpers below operate on."""
    for p in doc.paragraphs:
        if p.style.name.startswith("Heading") and p.text.strip() == heading_text:
            return p._p
    return None


def _insert_paragraph_after(doc: Document, anchor_element, text: str = "", style: str | None = None):
    """python-docx only ever appends new paragraphs/tables at the very end of the document body.
    To place generated content immediately under an existing heading somewhere in the middle of
    a client's uploaded template, this creates the paragraph normally (so it lands at the end),
    then relocates its underlying XML element to sit right after anchor_element. Returns the new
    element so a caller inserting multiple lines in a row can chain them in order."""
    new_p = doc.add_paragraph(text, style=style)
    anchor_element.addnext(new_p._p)
    return new_p._p


def _insert_body_after(doc: Document, anchor_element, text: str):
    cursor = anchor_element
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            cursor = _insert_paragraph_after(doc, cursor, line[2:], style="List Bullet")
        else:
            cursor = _insert_paragraph_after(doc, cursor, line)
    return cursor


def _insert_table_after(doc: Document, anchor_element, headers, rows, widths_in, status_col=None):
    table = _add_table(doc, headers, rows, widths_in, status_col)  # appended at document end
    anchor_element.addnext(table._tbl)
    return table._tbl


def _insert_matched_section(doc: Document, our_section: str, template_heading: str, content: dict, company: dict):
    """Inserts generated content for one of the 10 canonical sections immediately after the
    matching heading found in the client's uploaded template. No section numbering is added
    here — the client's own template heading is used exactly as they wrote it."""
    anchor = _find_heading_element(doc, template_heading)
    if anchor is None:
        return
    cursor = anchor

    if our_section == "Executive Summary":
        cursor = _insert_body_after(doc, cursor, content["executive_summary"])
    elif our_section == "Understanding of the Problem":
        cursor = _insert_body_after(doc, cursor, content["understanding"])
    elif our_section == "Technical Approach and Methodology":
        cursor = _insert_body_after(doc, cursor, content["technology_approach"])
    elif our_section == "Assumptions":
        if content["assumptions"]:
            for a in content["assumptions"]:
                cursor = _insert_paragraph_after(doc, cursor, a, style="List Bullet")
        else:
            cursor = _insert_paragraph_after(
                doc, cursor,
                "No assumptions were required — all requirements were fully specified in the RFP.",
            )
    elif our_section == "Proposed Team & Staffing Plan":
        for person in company.get("key_personnel", []):
            cursor = _insert_paragraph_after(doc, cursor, f"{person['name']} — {person['role']}")
            cursor = _insert_paragraph_after(doc, cursor, person.get("credentials", ""))
            cursor = _insert_paragraph_after(doc, cursor, person.get("bio", ""))
        staffing_rows = [
            [l["role"], l["headcount"], l["hours_per_person"], l["total_hours"]]
            for l in content["staffing"]
        ]
        cursor = _insert_table_after(doc, cursor, ["Role", "Headcount", "Hours/Person", "Total Hours"],
                                      staffing_rows, [2.5, 1.2, 1.4, 1.4])
    elif our_section == "Proposed Project Timeline":
        timeline_rows = [[p["phase"], p["duration"], p["description"]] for p in content["timeline"]]
        cursor = _insert_table_after(doc, cursor, ["Phase", "Duration", "Description"], timeline_rows,
                                      [1.6, 1.3, 4.1])
    elif our_section == "Effort & Pricing Summary":
        pricing = content["pricing"]
        pricing_rows = [
            [l["role"], l["total_hours"], f"${l['hourly_rate']:,.2f}", f"${l['subtotal']:,.2f}"]
            for l in pricing["lines"]
        ]
        cursor = _insert_table_after(doc, cursor, ["Role", "Total Hours", "Rate", "Subtotal"],
                                      pricing_rows, [2.3, 1.3, 1.3, 1.6])
        summary_text = (
            f"Labor Subtotal: ${pricing['labor_subtotal']:,.2f}\n"
            f"Contingency ({pricing['contingency_pct']}%): ${pricing['contingency_amount']:,.2f}\n"
            f"TOTAL: ${pricing['total']:,.2f}"
        )
        cursor = _insert_body_after(doc, cursor, summary_text)
    elif our_section == "Relevant Past Performance":
        cursor = _insert_body_after(doc, cursor, content["past_performance"])
    elif our_section == "Compliance Matrix":
        matrix_rows = [[m["requirement"], m["response"], m["status"]] for m in content["compliance_matrix"]]
        cursor = _insert_table_after(doc, cursor, ["Requirement", "Our Response", "Status"], matrix_rows,
                                      [2.3, 3.4, 1.3], status_col=2)
    elif our_section == "Closing Statement":
        cursor = _insert_body_after(doc, cursor, content["closing"])


def _append_section_content(doc: Document, our_section: str, content: dict, company: dict):
    """Same section content as _insert_matched_section, but appended normally at the end of the
    document — used for sections that couldn't be confidently matched to a heading in the
    client's template (see the 'Needs Manual Placement' block in build_final_proposal)."""
    if our_section == "Executive Summary":
        _add_body(doc, content["executive_summary"])
    elif our_section == "Understanding of the Problem":
        _add_body(doc, content["understanding"])
    elif our_section == "Technical Approach and Methodology":
        _add_body(doc, content["technology_approach"])
    elif our_section == "Assumptions":
        if content["assumptions"]:
            for a in content["assumptions"]:
                doc.add_paragraph(a, style="List Bullet")
        else:
            doc.add_paragraph("No assumptions were required — all requirements were fully specified in the RFP.")
    elif our_section == "Proposed Team & Staffing Plan":
        for person in company.get("key_personnel", []):
            p = doc.add_paragraph()
            run = p.add_run(f"{person['name']} — {person['role']}")
            run.font.bold = True
            run.font.color.rgb = NAVY
            cred = doc.add_paragraph()
            cred_run = cred.add_run(person.get("credentials", ""))
            cred_run.italic = True
            cred_run.font.color.rgb = GRAY
            doc.add_paragraph(person.get("bio", ""))
        staffing_rows = [
            [l["role"], l["headcount"], l["hours_per_person"], l["total_hours"]]
            for l in content["staffing"]
        ]
        _add_table(doc, ["Role", "Headcount", "Hours/Person", "Total Hours"], staffing_rows, [2.5, 1.2, 1.4, 1.4])
    elif our_section == "Proposed Project Timeline":
        timeline_rows = [[p["phase"], p["duration"], p["description"]] for p in content["timeline"]]
        _add_table(doc, ["Phase", "Duration", "Description"], timeline_rows, [1.6, 1.3, 4.1])
    elif our_section == "Effort & Pricing Summary":
        pricing = content["pricing"]
        pricing_rows = [
            [l["role"], l["total_hours"], f"${l['hourly_rate']:,.2f}", f"${l['subtotal']:,.2f}"]
            for l in pricing["lines"]
        ]
        _add_table(doc, ["Role", "Total Hours", "Rate", "Subtotal"], pricing_rows, [2.3, 1.3, 1.3, 1.6])
        summary_p = doc.add_paragraph()
        summary_p.add_run(f"Labor Subtotal: ${pricing['labor_subtotal']:,.2f}\n")
        summary_p.add_run(f"Contingency ({pricing['contingency_pct']}%): ${pricing['contingency_amount']:,.2f}\n")
        total_run = summary_p.add_run(f"TOTAL: ${pricing['total']:,.2f}")
        total_run.bold = True
        total_run.font.size = Pt(13)
        total_run.font.color.rgb = NAVY
    elif our_section == "Relevant Past Performance":
        _add_body(doc, content["past_performance"])
    elif our_section == "Compliance Matrix":
        matrix_rows = [[m["requirement"], m["response"], m["status"]] for m in content["compliance_matrix"]]
        _add_table(doc, ["Requirement", "Our Response", "Status"], matrix_rows, [2.3, 3.4, 1.3], status_col=2)
    elif our_section == "Closing Statement":
        _add_body(doc, content["closing"])


def _build_final_proposal_with_template(output_path: str, content: dict, template_path: str,
                                         section_mapping: dict | None):
    """Writes generated content into the client's own uploaded .docx template instead of a
    fresh document — deliberately does NOT call _style_base or _add_cover_page or _add_footer:
    the whole point of a custom template is to keep the client's own styling and cover/branding
    intact, so this only ever inserts new content, never restyles or replaces what's already
    there. Any of the 10 canonical sections template_mapper couldn't confidently match to a
    heading in the template are appended at the end under a 'Needs Manual Placement' heading
    instead of being silently dropped."""
    company = content["company"]
    doc = Document(template_path)

    mapping = section_mapping or {"matched": {}, "unmatched": list(OUR_SECTIONS)}
    for our_section, template_heading in mapping.get("matched", {}).items():
        _insert_matched_section(doc, our_section, template_heading, content, company)

    unmatched = mapping.get("unmatched", [])
    if unmatched:
        doc.add_heading("Needs Manual Placement", level=1)
        note = doc.add_paragraph()
        note.add_run(
            "The following sections could not be confidently matched to a heading in your "
            "uploaded template and have been placed here instead — please move each into the "
            "appropriate place in the document."
        ).italic = True
        for our_section in unmatched:
            doc.add_heading(our_section, level=2)
            _append_section_content(doc, our_section, content, company)

    doc.save(output_path)
    return output_path


def build_final_proposal(output_path: str, content: dict, template_path: str | None = None,
                          section_mapping: dict | None = None):
    """content: the dict returned by phase4_pipeline.run_phase4() (optionally edited by the user
    in the preview stage). When template_path is given (a client-uploaded .docx), section_mapping
    should be the dict returned by template_mapper.map_sections_to_template() for that same
    template's headings — content is then written into the client's own document instead of a
    fresh one (see _build_final_proposal_with_template). template_path is None reproduces the
    original default-template behavior exactly as before this parameter existed."""
    if template_path is not None:
        return _build_final_proposal_with_template(output_path, content, template_path, section_mapping)

    company = content["company"]
    doc = Document()
    _style_base(doc)
    _add_cover_page(doc, content["project_title"], content["agency"], company)
    _add_footer(doc, content["project_title"])

    doc.add_heading("1. Executive Summary", level=1)
    _add_body(doc, content["executive_summary"])

    doc.add_heading("2. Understanding of the Problem", level=1)
    _add_body(doc, content["understanding"])

    doc.add_heading("3. Technical Approach and Methodology", level=1)
    _add_body(doc, content["technology_approach"])

    doc.add_heading("4. Assumptions", level=1)
    if content["assumptions"]:
        for a in content["assumptions"]:
            doc.add_paragraph(a, style="List Bullet")
    else:
        doc.add_paragraph("No assumptions were required — all requirements were fully specified in the RFP.")

    doc.add_heading("5. Proposed Team & Staffing Plan", level=1)
    if company.get("key_personnel"):
        for person in company["key_personnel"]:
            p = doc.add_paragraph()
            run = p.add_run(f"{person['name']} — {person['role']}")
            run.font.bold = True
            run.font.color.rgb = NAVY
            cred = doc.add_paragraph()
            cred_run = cred.add_run(person["credentials"])
            cred_run.italic = True
            cred_run.font.color.rgb = GRAY
            doc.add_paragraph(person["bio"])
    doc.add_paragraph()
    staffing_rows = [
        [l["role"], l["headcount"], l["hours_per_person"], l["total_hours"]]
        for l in content["staffing"]
    ]
    _add_table(doc, ["Role", "Headcount", "Hours/Person", "Total Hours"], staffing_rows,
               [2.5, 1.2, 1.4, 1.4])

    doc.add_heading("6. Proposed Project Timeline", level=1)
    timeline_rows = [[p["phase"], p["duration"], p["description"]] for p in content["timeline"]]
    _add_table(doc, ["Phase", "Duration", "Description"], timeline_rows, [1.6, 1.3, 4.1])

    doc.add_heading("7. Effort & Pricing Summary", level=1)
    note = doc.add_paragraph()
    note.add_run(
        "Pricing below reflects competitive market-rate estimates for internal review prior "
        "to submission."
    ).italic = True
    pricing = content["pricing"]
    pricing_rows = [
        [l["role"], l["total_hours"], f"${l['hourly_rate']:,.2f}", f"${l['subtotal']:,.2f}"]
        for l in pricing["lines"]
    ]
    _add_table(doc, ["Role", "Total Hours", "Rate", "Subtotal"], pricing_rows, [2.3, 1.3, 1.3, 1.6])
    doc.add_paragraph()
    summary_p = doc.add_paragraph()
    summary_p.add_run(f"Labor Subtotal: ${pricing['labor_subtotal']:,.2f}\n").bold = False
    summary_p.add_run(f"Contingency ({pricing['contingency_pct']}%): ${pricing['contingency_amount']:,.2f}\n")
    total_run = summary_p.add_run(f"TOTAL: ${pricing['total']:,.2f}")
    total_run.bold = True
    total_run.font.size = Pt(13)
    total_run.font.color.rgb = NAVY

    doc.add_heading("8. Relevant Past Performance", level=1)
    _add_body(doc, content["past_performance"])

    doc.add_heading("9. Compliance Matrix", level=1)
    matrix_rows = [
        [m["requirement"], m["response"], m["status"]] for m in content["compliance_matrix"]
    ]
    _add_table(doc, ["Requirement", "Our Response", "Status"], matrix_rows, [2.3, 3.4, 1.3], status_col=2)

    doc.add_heading("10. Closing Statement", level=1)
    _add_body(doc, content["closing"])

    contact = company.get("contact", {})
    if contact:
        doc.add_paragraph()
        cp = doc.add_paragraph()
        run = cp.add_run(
            f"{contact.get('name', '')}, {contact.get('title', '')}  |  "
            f"{contact.get('email', '')}  |  {contact.get('phone', '')}"
        )
        run.font.size = Pt(9)
        run.font.color.rgb = GRAY

    doc.save(output_path)
    return output_path
