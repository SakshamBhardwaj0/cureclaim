"""
PDF Audit Report Generator for CureClaim using ReportLab.
Produces a downloadable evidence-backed PDF report directly from saved database audit records.
"""
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from auditor.models import AuditFinding, AuditSession


def generate_audit_pdf_report(session: AuditSession) -> bytes:
    """
    Generates a complete, structured PDF audit report for the given AuditSession
    and returns the PDF file contents as bytes.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"CureClaim Audit Report - {session.audit_uid}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CureClaimTitle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "CureClaimSubtitle",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#475569"),
        spaceAfter=10,
    )
    section_heading = ParagraphStyle(
        "CureClaimSection",
        parent=styles["Heading2"],
        fontSize=12.5,
        leading=16,
        textColor=colors.HexColor("#0D9488"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "CureClaimBody",
        parent=styles["Normal"],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#1E293B"),
    )
    small_note = ParagraphStyle(
        "CureClaimSmall",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#64748B"),
    )
    demo_banner_style = ParagraphStyle(
        "CureClaimDemoBanner",
        parent=styles["Normal"],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#92400E"),
        backColor=colors.HexColor("#FEF3C7"),
        borderPadding=6,
        spaceAfter=10,
    )

    story = []

    # Header
    story.append(Paragraph("CureClaim — Hospital Bill Audit Report", title_style))
    story.append(
        Paragraph(
            f"Report ID: <b>{session.audit_uid}</b> | Generated: {session.updated_at.strftime('%Y-%m-%d %H:%M')} | "
            f"Audit Status: <b>{session.get_processing_status_display()}</b>",
            subtitle_style,
        )
    )
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1"), spaceAfter=10))

    if session.is_demo or session.scheme_context == "EXAMPLE_SURGICAL_PKG_01":
        story.append(
            Paragraph(
                "<b>SYNTHETIC DEMONSTRATION REPORT (DEMO MODE):</b> This audit was generated using fictional "
                "demonstration data and/or a synthetic demonstration rule ('DEMO ONLY — NOT OFFICIAL GUIDANCE'). "
                "The figures and package rules shown here are invented solely to demonstrate software functionality "
                "and do not represent real hospital tariffs or official government regulations.",
                demo_banner_style,
            )
        )

    # Bill & Summary Metadata Table
    story.append(Paragraph("1. Bill Summary & Financial Overview", section_heading))
    meta_data = [
        [
            Paragraph("<b>Hospital Name:</b>", body_style),
            Paragraph(session.hospital_name, body_style),
            Paragraph("<b>Bill Reference:</b>", body_style),
            Paragraph(session.bill_reference, body_style),
        ],
        [
            Paragraph("<b>Bill Date:</b>", body_style),
            Paragraph(str(session.bill_date or "Not specified"), body_style),
            Paragraph("<b>Package / Context:</b>", body_style),
            Paragraph(session.treatment_package or session.scheme_context, body_style),
        ],
        [
            Paragraph("<b>Reported Grand Total:</b>", body_style),
            Paragraph(f"{session.currency} {session.reported_grand_total:,.2f}", body_style),
            Paragraph("<b>Computed Line Total:</b>", body_style),
            Paragraph(f"{session.currency} {session.computed_line_total:,.2f}", body_style),
        ],
        [
            Paragraph("<b>Verified Arithmetic Discrepancy:</b>", body_style),
            Paragraph(f"{session.currency} {session.verified_arithmetic_discrepancy:,.2f}", body_style),
            Paragraph("<b>Potential Amount to Review:</b>", body_style),
            Paragraph(f"<b>{session.currency} {session.potential_amount_to_review:,.2f}</b>", body_style),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[38 * mm, 50 * mm, 44 * mm, 46 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 8))

    # Line-Item Table
    story.append(Paragraph("2. Audited Bill Line Items", section_heading))
    item_rows = [
        [
            Paragraph("<b>#</b>", body_style),
            Paragraph("<b>Item Description</b>", body_style),
            Paragraph("<b>Qty</b>", body_style),
            Paragraph("<b>Unit Price</b>", body_style),
            Paragraph("<b>Billed Total</b>", body_style),
            Paragraph("<b>Calculated</b>", body_style),
            Paragraph("<b>Audit Status</b>", body_style),
        ]
    ]
    for item in session.items.all():
        item_rows.append(
            [
                Paragraph(str(item.line_number), body_style),
                Paragraph(item.original_description, body_style),
                Paragraph(str(item.quantity if item.quantity is not None else "Missing"), body_style),
                Paragraph(
                    f"{session.currency} {item.unit_price:,.2f}" if item.unit_price is not None else "Missing",
                    body_style,
                ),
                Paragraph(
                    f"{session.currency} {item.billed_line_total:,.2f}"
                    if item.billed_line_total is not None
                    else "Missing",
                    body_style,
                ),
                Paragraph(
                    f"{session.currency} {item.computed_line_total:,.2f}"
                    if item.computed_line_total is not None
                    else "N/A",
                    body_style,
                ),
                Paragraph(item.get_audit_badge_display(), body_style),
            ]
        )

    items_table = Table(
        item_rows,
        colWidths=[10 * mm, 52 * mm, 16 * mm, 24 * mm, 24 * mm, 24 * mm, 28 * mm],
        repeatRows=1,
    )
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(items_table)
    story.append(Spacer(1, 10))

    # Detailed Findings Section
    story.append(Paragraph("3. Evidence-Backed Audit Findings", section_heading))
    active_findings = session.findings.exclude(review_status=AuditFinding.ReviewStatus.EXCLUDED)

    if not active_findings.exists():
        story.append(
            Paragraph(
                "No discrepancies were detected by the currently configured arithmetic and rule checks. "
                "Note: This does not guarantee that every clinical charge is comprehensive or error-free.",
                body_style,
            )
        )
    else:
        for idx, finding in enumerate(active_findings, start=1):
            rule_badge = "Deterministic Check"
            source_info = "Mathematical validation of extracted bill figures."
            if finding.rule:
                rule_badge = (
                    f"{finding.rule.rule_id} ({finding.rule.get_verification_status_display()})"
                )
                source_info = finding.rule.source_title
                if finding.rule.source_url:
                    source_info += f" ({finding.rule.source_url})"
                if finding.rule.effective_date:
                    source_info += f" | Effective: {finding.rule.effective_date}"

            impact_label = (
                "Confirmed Arithmetic Discrepancy"
                if finding.is_impact_confirmed
                else "Estimated Potential Impact (Requires Review)"
            )
            story.append(
                Paragraph(
                    f"<b>Finding {idx}: {finding.title}</b> "
                    f"[{finding.get_category_display()} | Review Status: {finding.get_review_status_display()}]",
                    body_style,
                )
            )
            story.append(
                Paragraph(
                    f"<b>Amount to Review:</b> {session.currency} {finding.financial_impact:,.2f} ({impact_label}) | "
                    f"<b>Rule / Check:</b> {rule_badge}",
                    small_note,
                )
            )
            story.append(Paragraph(f"<b>Explanation:</b> {finding.explanation}", body_style))
            story.append(Paragraph(f"<b>Calculation Basis:</b> {finding.calculation_basis}", body_style))
            story.append(Paragraph(f"<b>Supporting Evidence:</b> {finding.supporting_evidence}", body_style))
            story.append(Paragraph(f"<b>Source Reference:</b> {source_info}", small_note))
            story.append(Paragraph(f"<b>Recommended Next Step:</b> {finding.recommended_action}", body_style))
            story.append(Spacer(1, 6))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E2E8F0"), spaceAfter=6))

    # Important Limitations & Disclaimers
    story.append(Paragraph("4. Important Limitations & Methodology Disclaimer", section_heading))
    story.append(
        Paragraph(
            "CureClaim is an educational and informational hospital bill review aid. It separates optical document "
            "transcription from deterministic Decimal arithmetic and explicit rule matching. A flagged item or "
            "'Potential Amount to Review' is an invitation for human verification with the hospital's billing desk — "
            "it is NOT an accusation of hospital fraud, a clinical determination of medical necessity, or a guarantee "
            "of refund or insurance reimbursement. Demonstration rules marked 'DEMO ONLY' apply solely to synthetic "
            "sample bills.",
            small_note,
        )
    )

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes