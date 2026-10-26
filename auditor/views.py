from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import BillUploadForm, ManualAuditHeaderForm, parse_line_items_from_post
from .management.commands.seed_demo import seed_demo_data
from .models import AuditFinding, AuditRule, AuditSession, BillItem
from .services.audit_engine import run_audit_for_session
from .services.extraction import (
    ExtractionServiceError,
    extract_bill_from_uploaded_file,
    is_ai_configured,
)
from .services.report_generator import generate_audit_pdf_report


def ai_status_context(request):
    """Global template context processor exposing AI configuration status and model name."""
    ai_active = is_ai_configured()
    return {
        "AI_EXTRACTION_ACTIVE": ai_active,
        "CONFIGURED_GEMINI_MODEL": getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash"),
        "DEMO_MODE_BANNER_TEXT": (
            "AI EXTRACTION READY — Configured with Google Gemini ("
            + getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
            + ")"
            if ai_active
            else "DEMO MODE — Synthetic sample data & manual auditing active. External AI extraction is not active."
        ),
    }


def dashboard_view(request):
    """Main CureClaim dashboard with live database KPIs and recent audits."""
    sessions_qs = AuditSession.objects.all()

    total_bills_audited = sessions_qs.count()
    total_potential_findings = AuditFinding.objects.exclude(
        review_status=AuditFinding.ReviewStatus.EXCLUDED
    ).count()

    agg = sessions_qs.aggregate(
        total_review_amount=Sum("potential_amount_to_review"),
    )
    amount_requiring_review = agg["total_review_amount"] or Decimal("0.00")

    bills_awaiting_verification = sessions_qs.filter(
        Q(processing_status=AuditSession.ProcessingStatus.VERIFICATION_REQUIRED)
        | Q(processing_status=AuditSession.ProcessingStatus.PENDING_REVIEW)
    ).count()

    recent_audits = sessions_qs.prefetch_related("findings", "items")[:10]

    context = {
        "page_title": "Dashboard",
        "page_subtitle": "Understand your hospital bill. Find discrepancies. Review the evidence.",
        "total_bills_audited": total_bills_audited,
        "total_potential_findings": total_potential_findings,
        "amount_requiring_review": amount_requiring_review,
        "bills_awaiting_verification": bills_awaiting_verification,
        "recent_audits": recent_audits,
    }
    return render(request, "auditor/dashboard.html", context)


def launch_demo_view(request):
    """
    Seeds or refreshes the synthetic Northstar General Hospital sample audit (DEMO-001)
    without requiring an API key and opens the audit detail page immediately.
    """
    demo_session = seed_demo_data()
    messages.info(
        request,
        "Loaded synthetic demonstration audit (DEMO-001 — Northstar General Hospital). "
        "Note: This scenario uses fictional demo data and a DEMO ONLY package rule.",
    )
    return redirect("auditor:audit_detail", audit_uid=demo_session.audit_uid)


def upload_bill_view(request):
    """
    Handles document upload (JPG, JPEG, PNG, PDF), server-side file validation,
    explicit external AI consent check, and Gemini extraction.
    """
    ai_ready = is_ai_configured()

    if request.method == "POST":
        if not ai_ready:
            messages.error(
                request,
                "External AI extraction is currently unavailable because GEMINI_API_KEY is not configured "
                "in your environment. We never substitute synthetic data for an uploaded real document. "
                "Please use Manual Bill Entry or configure GEMINI_API_KEY in .env.",
            )
            return redirect("auditor:upload")

        form = BillUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["bill_file"]
            scheme_context = form.cleaned_data["scheme_context"]
            consent = form.cleaned_data["confirm_external_ai"]

            try:
                extracted = extract_bill_from_uploaded_file(uploaded_file)
            except ExtractionServiceError as exc:
                messages.error(request, str(exc))
                return render(
                    request,
                    "auditor/upload.html",
                    {
                        "page_title": "Upload Hospital Bill",
                        "page_subtitle": "Extract line items from a bill image or PDF invoice for auditing.",
                        "form": form,
                    },
                )

            session = AuditSession.objects.create(
                uploaded_file=uploaded_file,
                original_filename=uploaded_file.name,
                hospital_name=extracted["hospital_name"],
                bill_reference=extracted["invoice_number"],
                bill_date=extracted["invoice_date"],
                patient_name=extracted["patient_name"],
                treatment_package=extracted["treatment_package"],
                package_code=scheme_context if scheme_context == "EXAMPLE_SURGICAL_PKG_01" else "",
                scheme_context=scheme_context,
                currency=extracted["currency"],
                reported_grand_total=extracted["grand_total"],
                documented_adjustments=extracted["documented_adjustments"],
                processing_status=AuditSession.ProcessingStatus.PENDING_REVIEW,
                ai_extraction_status=(
                    AuditSession.AIExtractionStatus.PARTIAL_UNCERTAIN
                    if extracted["has_uncertain_fields"]
                    else AuditSession.AIExtractionStatus.SUCCESS
                ),
                is_demo=(scheme_context == "EXAMPLE_SURGICAL_PKG_01"),
                user_confirmed_data=False,
                external_ai_consent=consent,
            )

            for item_dict in extracted["items"]:
                BillItem.objects.create(
                    session=session,
                    line_number=item_dict["line_number"],
                    original_description=item_dict["original_description"],
                    quantity=item_dict["quantity"],
                    unit=item_dict["unit"],
                    unit_price=item_dict["unit_price"],
                    billed_line_total=item_dict["billed_line_total"],
                    item_code=item_dict["item_code"],
                    source_page=item_dict["source_page"],
                    extraction_confidence=item_dict["extraction_confidence"],
                    verification_status=(
                        BillItem.VerificationStatus.NEEDS_VERIFICATION
                        if item_dict["needs_verification"]
                        else BillItem.VerificationStatus.VERIFIED
                    ),
                )

            messages.success(
                request,
                "Document extracted successfully. Please review and confirm the extracted line items below "
                "before running the deterministic audit.",
            )
            return redirect("auditor:review_extraction", audit_uid=session.audit_uid)
    else:
        form = BillUploadForm()

    return render(
        request,
        "auditor/upload.html",
        {
            "page_title": "Upload Hospital Bill",
            "page_subtitle": "Upload a clear JPG, PNG, or PDF invoice for structured extraction and auditing.",
            "form": form,
        },
    )


def review_extraction_view(request, audit_uid):
    """
    Allows the user to inspect, verify, and correct extracted (or existing) line items
    before running or re-running the deterministic financial and rule audit.
    """
    session = get_object_or_404(AuditSession, audit_uid=audit_uid)

    if request.method == "POST":
        header_form = ManualAuditHeaderForm(request.POST)
        parsed_items, item_errors = parse_line_items_from_post(request.POST)

        if header_form.is_valid() and not item_errors:
            cd = header_form.cleaned_data
            session.hospital_name = cd["hospital_name"]
            session.bill_reference = cd["bill_reference"]
            session.bill_date = cd["bill_date"]
            session.treatment_package = cd["treatment_package"]
            session.scheme_context = cd["scheme_context"]
            session.package_code = (
                "EXAMPLE_SURGICAL_PKG_01" if cd["scheme_context"] == "EXAMPLE_SURGICAL_PKG_01" else ""
            )
            session.reported_grand_total = cd["reported_grand_total"]
            session.documented_adjustments = cd["documented_adjustments"]
            session.user_confirmed_data = True
            session.save()

            session.items.all().delete()
            for item_data in parsed_items:
                BillItem.objects.create(session=session, **item_data)

            run_audit_for_session(session)
            messages.success(request, "Line items confirmed and deterministic audit completed.")
            return redirect("auditor:audit_detail", audit_uid=session.audit_uid)

        for err in item_errors:
            messages.error(request, err)
    else:
        header_form = ManualAuditHeaderForm(
            initial={
                "hospital_name": session.hospital_name,
                "bill_reference": session.bill_reference,
                "bill_date": session.bill_date,
                "treatment_package": session.treatment_package,
                "scheme_context": session.scheme_context,
                "reported_grand_total": session.reported_grand_total,
                "documented_adjustments": session.documented_adjustments,
            }
        )

    return render(
        request,
        "auditor/review_extraction.html",
        {
            "page_title": f"Review & Verify Bill Data ({session.audit_uid})",
            "page_subtitle": "Verify uncertain fields and correct any transcription issues before finalizing the audit.",
            "session": session,
            "header_form": header_form,
            "items": session.items.all(),
        },
    )


def manual_entry_view(request):
    """Allows entering a hospital bill manually without an API key or uploaded document."""
    if request.method == "POST":
        header_form = ManualAuditHeaderForm(request.POST)
        parsed_items, item_errors = parse_line_items_from_post(request.POST)

        if header_form.is_valid() and not item_errors:
            cd = header_form.cleaned_data
            is_demo_pkg = cd["scheme_context"] == "EXAMPLE_SURGICAL_PKG_01"
            session = AuditSession.objects.create(
                hospital_name=cd["hospital_name"],
                bill_reference=cd["bill_reference"],
                bill_date=cd["bill_date"],
                treatment_package=cd["treatment_package"],
                scheme_context=cd["scheme_context"],
                package_code="EXAMPLE_SURGICAL_PKG_01" if is_demo_pkg else "",
                currency="INR",
                reported_grand_total=cd["reported_grand_total"],
                documented_adjustments=cd["documented_adjustments"],
                ai_extraction_status=AuditSession.AIExtractionStatus.NOT_USED,
                is_demo=is_demo_pkg,
                user_confirmed_data=True,
            )

            for item_data in parsed_items:
                BillItem.objects.create(session=session, **item_data)

            run_audit_for_session(session)
            messages.success(
                request,
                f"Audit {session.audit_uid} created and evaluated using deterministic calculations and rules.",
            )
            return redirect("auditor:audit_detail", audit_uid=session.audit_uid)

        for err in item_errors:
            messages.error(request, err)
    else:
        header_form = ManualAuditHeaderForm()

    return render(
        request,
        "auditor/manual_entry.html",
        {
            "page_title": "Manual Bill Entry",
            "page_subtitle": "Enter hospital bill line items directly to run deterministic calculations and rule checks.",
            "header_form": header_form,
        },
    )


def audit_detail_view(request, audit_uid):
    """Displays the comprehensive Audit Results page with expandable evidence panels."""
    session = get_object_or_404(
        AuditSession.objects.prefetch_related("items", "findings__rule", "findings__bill_item"),
        audit_uid=audit_uid,
    )
    active_findings = session.findings.exclude(review_status=AuditFinding.ReviewStatus.EXCLUDED)
    excluded_findings = session.findings.filter(review_status=AuditFinding.ReviewStatus.EXCLUDED)

    reconciliation_diff = session.reported_grand_total - (
        session.computed_line_total + session.documented_adjustments
    )

    context = {
        "page_title": f"Audit Report: {session.bill_reference}",
        "page_subtitle": f"{session.hospital_name} • Audit ID {session.audit_uid}",
        "session": session,
        "items": session.items.all(),
        "active_findings": active_findings,
        "excluded_findings": excluded_findings,
        "reconciliation_diff": reconciliation_diff,
        "is_reconciled": abs(reconciliation_diff) <= Decimal("0.01"),
    }
    return render(request, "auditor/audit_detail.html", context)


@require_POST
def finding_action_view(request, audit_uid, finding_id):
    """
    Handles cautious user review actions on a specific finding:
    - 'mark_reviewed': Marks the finding as Reviewed & Acknowledged.
    - 'exclude_and_rerun': Excludes an incorrect rule match on that line item and reruns the audit.
    """
    session = get_object_or_404(AuditSession, audit_uid=audit_uid)
    finding = get_object_or_404(AuditFinding, id=finding_id, session=session)
    action = request.POST.get("action")

    if action == "mark_reviewed":
        finding.review_status = AuditFinding.ReviewStatus.REVIEWED
        finding.save(update_fields=["review_status"])
        messages.success(request, f"Marked finding '{finding.title}' as reviewed.")
    elif action == "exclude_and_rerun":
        if finding.bill_item and finding.rule:
            item = finding.bill_item
            excluded = list(item.excluded_rule_ids or [])
            if finding.rule.rule_id not in excluded:
                excluded.append(finding.rule.rule_id)
                item.excluded_rule_ids = excluded
                item.save(update_fields=["excluded_rule_ids"])
            run_audit_for_session(session)
            messages.info(
                request,
                f"Excluded rule '{finding.rule.rule_id}' from Line {item.line_number} and recalculated the audit.",
            )
        else:
            finding.review_status = AuditFinding.ReviewStatus.EXCLUDED
            finding.save(update_fields=["review_status"])
            messages.info(request, f"Excluded finding '{finding.title}'.")

    return redirect("auditor:audit_detail", audit_uid=session.audit_uid)


def download_report_pdf_view(request, audit_uid):
    """Generates and downloads the PDF audit report for the saved AuditSession."""
    session = get_object_or_404(
        AuditSession.objects.prefetch_related("items", "findings__rule"),
        audit_uid=audit_uid,
    )
    try:
        pdf_bytes = generate_audit_pdf_report(session)
    except Exception:
        messages.error(
            request,
            "PDF generation encountered an unexpected error. You can use your browser's Print to PDF on this page.",
        )
        return redirect("auditor:audit_detail", audit_uid=session.audit_uid)

    filename = f"CureClaim_Audit_{session.audit_uid}_{session.bill_reference}.pdf".replace(" ", "_")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def audit_history_view(request):
    """Browsable, searchable list of historical audits with safe deletion."""
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    audits = AuditSession.objects.annotate(num_items=Count("items", distinct=True))
    if query:
        audits = audits.filter(
            Q(hospital_name__icontains=query)
            | Q(bill_reference__icontains=query)
            | Q(audit_uid__icontains=query)
        )
    if status_filter:
        audits = audits.filter(processing_status=status_filter)

    return render(
        request,
        "auditor/audit_history.html",
        {
            "page_title": "Audit History",
            "page_subtitle": "Search and reopen historical hospital bill audits.",
            "audits": audits,
            "search_query": query,
            "status_filter": status_filter,
            "status_choices": AuditSession.ProcessingStatus.choices,
        },
    )


@require_POST
def delete_audit_view(request, audit_uid):
    """Deletes an audit session and safely removes any associated uploaded document file."""
    session = get_object_or_404(AuditSession, audit_uid=audit_uid)
    ref_label = f"{session.audit_uid} ({session.bill_reference})"
    session.delete()
    messages.success(request, f"Deleted audit {ref_label} and removed any associated uploaded file.")
    return redirect("auditor:audit_history")


def rules_list_view(request):
    """Displays all structured billing audit rules, separating Source-Verified, Demo, and Unverified rules."""
    # Ensure default rules exist if user visits rules page before seeding demo
    if not AuditRule.objects.exists():
        seed_demo_data()

    rules = AuditRule.objects.all()
    verified_rules = rules.filter(
        verification_status=AuditRule.VerificationStatus.SOURCE_VERIFIED,
        is_demo_only=False,
    )
    demo_rules = rules.filter(is_demo_only=True)
    unverified_rules = rules.filter(
        verification_status=AuditRule.VerificationStatus.UNVERIFIED,
        is_demo_only=False,
    )

    return render(
        request,
        "auditor/rules.html",
        {
            "page_title": "Billing Audit Rules",
            "page_subtitle": "Structured, deterministic rules with explicit source and verification status.",
            "verified_rules": verified_rules,
            "demo_rules": demo_rules,
            "unverified_rules": unverified_rules,
        },
    )


def settings_about_view(request):
    """Displays environment status, architecture principles, and privacy safeguards."""
    return render(
        request,
        "auditor/settings_about.html",
        {
            "page_title": "Settings & Methodology",
            "page_subtitle": "AI configuration status, deterministic audit methodology, and privacy safeguards.",
        },
    )