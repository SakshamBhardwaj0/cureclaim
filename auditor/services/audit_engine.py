"""
Core Rule & Financial-Impact Engine for CureClaim.
Evaluates bill line items against deterministic calculations and structured database rules,
while strictly enforcing:
1. Separation of DEMO_ONLY rules from real bills.
2. Separation of UNVERIFIED rules from definitive conclusions.
3. Deduplication of financial impact per line item so the same charge is never counted twice.
"""
from decimal import Decimal
from django.db import transaction
from auditor.models import AuditFinding, AuditRule, AuditSession, BillItem
from auditor.services.calculations import (
    detect_potential_duplicate_items,
    evaluate_line_item_arithmetic,
    quantize_currency,
    reconcile_bill_grand_total,
)


def _rule_matches_item(rule: AuditRule, session: AuditSession, item: BillItem) -> dict:
    """
    Deterministically checks whether an AuditRule applies to the given session and BillItem.
    Returns a dictionary with keys:
      matched (bool), impact (Decimal), is_confirmed (bool), needs_verification (bool),
      explanation (str), calculation_basis (str), evidence (str)
    """
    if rule.rule_id in (item.excluded_rule_ids or []):
        return {"matched": False}

    cond = rule.applicability_conditions or {}

    # 1. Enforce strict isolation for demonstration-only rules
    if rule.is_demo_only or rule.verification_status == AuditRule.VerificationStatus.DEMO_ONLY:
        is_demo_context = (
            session.is_demo
            or session.scheme_context == "EXAMPLE_SURGICAL_PKG_01"
            or session.package_code == "EXAMPLE_SURGICAL_PKG_01"
        )
        if not is_demo_context:
            return {"matched": False}

    # 2. Check required package codes if specified by the rule
    required_packages = cond.get("package_codes", [])
    if required_packages:
        active_pkg = (session.package_code or session.scheme_context or "").strip().upper()
        if active_pkg not in [p.upper() for p in required_packages]:
            return {"matched": False}

    # 3. Check required scheme contexts if specified
    required_schemes = cond.get("scheme_contexts", [])
    if required_schemes:
        if (session.scheme_context or "").upper() not in [s.upper() for s in required_schemes]:
            return {"matched": False}

    # 4. Keyword matching against normalized description
    keywords = [kw.strip().lower() for kw in cond.get("keywords", []) if kw.strip()]
    norm_desc = (item.normalized_description or item.original_description or "").lower()
    if not keywords:
        return {"matched": False}

    matched_keyword = next((kw for kw in keywords if kw in norm_desc), None)
    if not matched_keyword:
        return {"matched": False}

    billed_total = quantize_currency(item.billed_line_total or Decimal("0.00"))
    unit_price = quantize_currency(item.unit_price or Decimal("0.00"))
    qty = quantize_currency(item.quantity or Decimal("1.00"))

    # Minimum line total threshold check (for lump-sum documentation rules)
    min_line_total = cond.get("min_line_total")
    if min_line_total is not None:
        if billed_total < Decimal(str(min_line_total)):
            return {"matched": False}

    # Evaluate impact based on rule category & verification status
    is_unverified_rule = rule.verification_status == AuditRule.VerificationStatus.UNVERIFIED
    is_demo_rule = rule.is_demo_only or rule.verification_status == AuditRule.VerificationStatus.DEMO_ONLY

    if rule.rule_type == AuditRule.RuleType.PACKAGE_INCLUSION:
        # If the rule is unverified, do not claim a definitive calculable impact without human verification
        impact = billed_total if rule.can_calculate_impact else Decimal("0.00")
        demo_prefix = "[SYNTHETIC DEMO SCENARIO — NOT OFFICIAL GUIDANCE] " if is_demo_rule else ""
        explanation = (
            f"{demo_prefix}Line {item.line_number} ('{item.original_description}') matched rule "
            f"'{rule.title}' ({rule.rule_id}) under package context '{session.treatment_package or session.scheme_context}'. "
            f"{rule.description}"
        )
        basis = (
            f"Separately billed item amount = {session.currency} {billed_total}. "
            f"Under rule {rule.rule_id}, items included in the defined package require review of whether "
            f"separate billing ({session.currency} {billed_total}) is permissible."
        )
        evidence = (
            f"Matched keyword '{matched_keyword}' in line description '{item.original_description}' "
            f"(Qty: {qty}, Unit Price: {unit_price}, Billed Total: {billed_total}). "
            f"Rule Verification Status: {rule.get_verification_status_display()}. Source: {rule.source_title}."
        )
        return {
            "matched": True,
            "impact": impact,
            "is_confirmed": False,
            "needs_verification": True,
            "explanation": explanation,
            "calculation_basis": basis,
            "evidence": evidence,
        }

    if rule.rule_type == AuditRule.RuleType.BENCHMARK_RATE:
        benchmark_raw = cond.get("benchmark_max_unit_price")
        if benchmark_raw is None:
            return {"matched": False}
        benchmark_rate = quantize_currency(Decimal(str(benchmark_raw)))
        if unit_price <= benchmark_rate:
            return {"matched": False}

        rate_diff_total = quantize_currency((unit_price - benchmark_rate) * qty)
        # If rule is UNVERIFIED or scheme eligibility is unconfirmed, do not declare it recoverable automatically
        if is_unverified_rule or cond.get("requires_eligibility_verification", True):
            return {
                "matched": True,
                "impact": Decimal("0.00"),
                "is_confirmed": False,
                "needs_verification": True,
                "explanation": (
                    f"Line {item.line_number} ('{item.original_description}') has a unit price of "
                    f"{session.currency} {unit_price}, which differs from the reference rate of "
                    f"{session.currency} {benchmark_rate} in rule '{rule.title}'. "
                    "However, reference scheme rates apply ONLY when hospital empanelment, patient scheme "
                    "eligibility, and ward category are verified. Because eligibility documentation is not "
                    "established, the difference is flagged for verification rather than added to the review total."
                ),
                "calculation_basis": (
                    f"Reference comparison only: (Billed Unit Price {unit_price} - Reference {benchmark_rate}) × "
                    f"Qty {qty} = {rate_diff_total}. Counted as 0.00 toward review total pending scheme eligibility proof."
                ),
                "evidence": (
                    f"Line {item.line_number}: '{item.original_description}' billed at {unit_price}/unit vs. "
                    f"reference {benchmark_rate}/unit ({rule.source_title}). Status: {rule.get_verification_status_display()}."
                ),
            }

        return {
            "matched": True,
            "impact": rate_diff_total,
            "is_confirmed": False,
            "needs_verification": True,
            "explanation": (
                f"Line {item.line_number} ('{item.original_description}') unit rate ({unit_price}) exceeds "
                f"the applicable benchmark ({benchmark_rate}) defined in '{rule.title}'."
            ),
            "calculation_basis": (
                f"(Billed Unit Price {unit_price} - Benchmark {benchmark_rate}) × Quantity {qty} = {rate_diff_total}."
            ),
            "evidence": f"Rule {rule.rule_id} ({rule.source_title}): matched '{matched_keyword}'.",
        }

    if rule.rule_type in (AuditRule.RuleType.MISSING_DOCUMENTATION, AuditRule.RuleType.MANUAL_REVIEW):
        return {
            "matched": True,
            "impact": billed_total if rule.can_calculate_impact and not is_unverified_rule else Decimal("0.00"),
            "is_confirmed": False,
            "needs_verification": True,
            "explanation": (
                f"Line {item.line_number} ('{item.original_description}', billed at {session.currency} {billed_total}) "
                f"triggered documentation check '{rule.title}': {rule.description}"
            ),
            "calculation_basis": (
                f"Bundled or un-itemized charge of {session.currency} {billed_total} requires supporting "
                "consumable/pharmacy breakup ledger to verify underlying quantities and unit rates."
            ),
            "evidence": (
                f"Matched non-specific billing description '{matched_keyword}' with billed total {billed_total} "
                f"(Rule {rule.rule_id}: {rule.source_title})."
            ),
        }

    return {"matched": False}


@transaction.atomic
def run_audit_for_session(session: AuditSession) -> AuditSession:
    """
    Executes the complete deterministic financial and rule-based audit for an AuditSession.
    Clears prior auto-generated findings (while preserving item-level rule exclusions),
    recomputes all metrics, prevents double-counting of financial impacts on the same line item,
    and updates session status.
    """
    session.findings.all().delete()
    items = list(session.items.all().order_by("line_number", "id"))
    active_rules = list(AuditRule.objects.filter(is_active=True))

    # Track per-item candidate findings so we never double-count financial impact on the same BillItem
    per_item_findings = {item.id: [] for item in items}
    session_level_findings = []

    verified_arithmetic_total = Decimal("0.00")

    # Step 1: Line-item arithmetic and missing/uncertain data checks
    for item in items:
        item.audit_badge = BillItem.ItemAuditBadge.NO_ISSUE
        calc = evaluate_line_item_arithmetic(
            line_number=item.line_number,
            description=item.original_description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            billed_line_total=item.billed_line_total,
        )
        item.computed_line_total = calc.expected_total

        if calc.has_missing_or_invalid_data or item.extraction_confidence < Decimal("0.80"):
            item.verification_status = BillItem.VerificationStatus.NEEDS_VERIFICATION
            item.audit_badge = BillItem.ItemAuditBadge.NEEDS_VERIFICATION
            reason_text = calc.explanation
            if not calc.has_missing_or_invalid_data and item.extraction_confidence < Decimal("0.80"):
                reason_text = (
                    f"Line {item.line_number} ('{item.original_description}') was extracted with low optical "
                    f"confidence ({int(item.extraction_confidence * 100)}%). Human verification against the "
                    "original document is required before relying on this row."
                )
            per_item_findings[item.id].append(
                {
                    "bill_item": item,
                    "rule": None,
                    "category": AuditFinding.Category.MISSING_DATA,
                    "severity": AuditFinding.Severity.MEDIUM,
                    "title": f"Verification Required on Line {item.line_number}: {item.original_description}",
                    "explanation": reason_text,
                    "calculation_basis": calc.calculation_basis,
                    "supporting_evidence": (
                        f"Extracted values — Qty: {item.quantity}, Unit Price: {item.unit_price}, "
                        f"Billed Total: {item.billed_line_total}, Confidence: {item.extraction_confidence}."
                    ),
                    "recommended_action": (
                        "Inspect the original bill image/PDF and confirm or correct the quantity, "
                        "unit rate, and billed line total."
                    ),
                    "financial_impact": Decimal("0.00"),
                    "is_impact_confirmed": False,
                    "is_demo_finding": session.is_demo,
                    "needs_human_verification": True,
                }
            )

        if calc.has_arithmetic_mismatch and calc.difference != Decimal("0.00"):
            item.audit_badge = BillItem.ItemAuditBadge.CALC_MISMATCH
            overcharge_amount = max(calc.difference, Decimal("0.00"))
            impact_val = overcharge_amount if overcharge_amount > Decimal("0.00") else abs(calc.difference)
            per_item_findings[item.id].append(
                {
                    "bill_item": item,
                    "rule": None,
                    "category": AuditFinding.Category.ARITHMETIC,
                    "severity": AuditFinding.Severity.HIGH,
                    "title": f"Line-Item Calculation Mismatch: {item.original_description}",
                    "explanation": calc.explanation,
                    "calculation_basis": calc.calculation_basis,
                    "supporting_evidence": (
                        f"Line {item.line_number} states Quantity = {item.quantity}, "
                        f"Unit Price = {item.unit_price}, Billed Total = {item.billed_line_total}. "
                        f"Deterministic product = {calc.expected_total}."
                    ),
                    "recommended_action": (
                        "Request arithmetic correction on the invoice so the line total matches Quantity × Unit Price."
                    ),
                    "financial_impact": impact_val,
                    "is_impact_confirmed": True,
                    "is_demo_finding": session.is_demo,
                    "needs_human_verification": False,
                }
            )

        item.save()

    # Step 2: Potential duplicate line items check
    duplicate_candidates = detect_potential_duplicate_items(items)
    items_by_id = {item.id: item for item in items}
    for dup in duplicate_candidates:
        dup_item = items_by_id[dup.duplicate_item_id]
        if dup_item.audit_badge == BillItem.ItemAuditBadge.NO_ISSUE:
            dup_item.audit_badge = BillItem.ItemAuditBadge.POTENTIAL_DUPLICATE
            dup_item.save(update_fields=["audit_badge"])

        per_item_findings[dup_item.id].append(
            {
                "bill_item": dup_item,
                "rule": None,
                "category": AuditFinding.Category.DUPLICATE,
                "severity": AuditFinding.Severity.MEDIUM,
                "title": f"Potential Duplicate Charge (Line {dup.duplicate_line_number} & Line {dup.primary_line_number})",
                "explanation": dup.explanation,
                "calculation_basis": dup.calculation_basis,
                "supporting_evidence": (
                    f"Line {dup.primary_line_number} and Line {dup.duplicate_line_number} both bill "
                    f"'{dup.description}' for {session.currency} {dup.billed_amount}."
                ),
                "recommended_action": (
                    "Check clinical administration timestamps or lab report serial numbers to verify "
                    "whether the service was rendered twice or billed twice in error."
                ),
                "financial_impact": dup.billed_amount,
                "is_impact_confirmed": False,
                "is_demo_finding": session.is_demo,
                "needs_human_verification": True,
            }
        )

    # Step 3: Evaluate structured database rules against each line item
    for item in items:
        for rule in active_rules:
            match_res = _rule_matches_item(rule, session, item)
            if not match_res.get("matched"):
                continue

            if item.audit_badge == BillItem.ItemAuditBadge.NO_ISSUE:
                item.audit_badge = (
                    BillItem.ItemAuditBadge.NEEDS_VERIFICATION
                    if rule.verification_status == AuditRule.VerificationStatus.UNVERIFIED
                    else BillItem.ItemAuditBadge.RULE_FINDING
                )
                item.save(update_fields=["audit_badge"])

            cat_map = {
                AuditRule.RuleType.PACKAGE_INCLUSION: AuditFinding.Category.PACKAGE_INCLUSION,
                AuditRule.RuleType.BENCHMARK_RATE: AuditFinding.Category.BENCHMARK_RATE,
                AuditRule.RuleType.MISSING_DOCUMENTATION: AuditFinding.Category.MISSING_DATA,
                AuditRule.RuleType.MANUAL_REVIEW: AuditFinding.Category.MANUAL_REVIEW,
            }
            finding_cat = cat_map.get(rule.rule_type, AuditFinding.Category.MANUAL_REVIEW)

            per_item_findings[item.id].append(
                {
                    "bill_item": item,
                    "rule": rule,
                    "category": finding_cat,
                    "severity": rule.severity,
                    "title": f"{'[DEMO RULE] ' if rule.is_demo_only else ''}{rule.title}: {item.original_description}",
                    "explanation": match_res["explanation"],
                    "calculation_basis": match_res["calculation_basis"],
                    "supporting_evidence": match_res["evidence"],
                    "recommended_action": (
                        "Verify package inclusion sheet / tariff schedule with the billing desk. "
                        "Note: Demo rules apply only to synthetic scenarios."
                        if rule.is_demo_only
                        else "Review supporting tariff documentation and request itemization or adjustment if applicable."
                    ),
                    "financial_impact": match_res["impact"],
                    "is_impact_confirmed": match_res["is_confirmed"],
                    "is_demo_finding": rule.is_demo_only or session.is_demo,
                    "needs_human_verification": match_res["needs_verification"],
                }
            )

    # Step 4: Deduplicate financial impact per BillItem and persist findings
    potential_review_total = Decimal("0.00")

    for item_id, f_list in per_item_findings.items():
        if not f_list:
            continue
        # Sort so confirmed arithmetic impacts or highest financial impact is primary
        f_list.sort(
            key=lambda x: (1 if x["is_impact_confirmed"] else 0, x["financial_impact"]),
            reverse=True,
        )
        primary_assigned = False
        item_billed_cap = quantize_currency(items_by_id[item_id].billed_line_total or Decimal("0.00"))

        for f_data in f_list:
            impact = quantize_currency(f_data["financial_impact"])
            if impact > Decimal("0.00") and not primary_assigned:
                # Cap reviewable impact on a normal positive item to its billed total unless it's an arithmetic error
                if item_billed_cap > Decimal("0.00") and impact > item_billed_cap:
                    impact = item_billed_cap
                f_data["financial_impact"] = impact
                f_data["contributes_to_review_total"] = True
                primary_assigned = True
                potential_review_total += impact
                if f_data["is_impact_confirmed"]:
                    verified_arithmetic_total += impact
            else:
                if impact > Decimal("0.00") and primary_assigned:
                    f_data["calculation_basis"] += (
                        " (Deduplication Note: Financial impact for this line item is already counted "
                        "under another finding on the same row to prevent double-counting.)"
                    )
                f_data["contributes_to_review_total"] = False

            AuditFinding.objects.create(session=session, **f_data)

    # Step 5: Bill Grand Total Reconciliation Check
    recon = reconcile_bill_grand_total(
        items,
        reported_grand_total=session.reported_grand_total,
        documented_adjustments=session.documented_adjustments,
    )
    session.computed_line_total = recon.sum_of_billed_items

    if not recon.is_reconciled:
        recon_impact = max(recon.unexplained_difference, Decimal("0.00"))
        if recon_impact == Decimal("0.00"):
            recon_impact = abs(recon.unexplained_difference)
        session_level_findings.append(
            AuditFinding.objects.create(
                session=session,
                bill_item=None,
                rule=None,
                category=AuditFinding.Category.RECONCILIATION,
                severity=AuditFinding.Severity.HIGH,
                title="Grand Total Reconciliation Discrepancy",
                explanation=recon.explanation,
                calculation_basis=recon.calculation_basis,
                supporting_evidence=(
                    f"Reported Grand Total = {session.currency} {recon.reported_grand_total}; "
                    f"Sum of Line Items = {session.currency} {recon.sum_of_billed_items}; "
                    f"Documented Adjustments = {session.currency} {recon.documented_adjustments}."
                ),
                recommended_action=(
                    "Verify that all bill pages and line items were included and ask the billing "
                    "department to reconcile the subtotal with the stated grand total."
                ),
                financial_impact=recon_impact,
                is_impact_confirmed=True,
                contributes_to_review_total=True,
                is_demo_finding=session.is_demo,
                needs_human_verification=True,
            )
        )
        verified_arithmetic_total += recon_impact
        potential_review_total += recon_impact

    session.verified_arithmetic_discrepancy = quantize_currency(verified_arithmetic_total)
    session.potential_amount_to_review = quantize_currency(potential_review_total)

    # Determine overall session processing status
    total_findings = session.findings.count()
    has_unverified_items = any(
        i.verification_status == BillItem.VerificationStatus.NEEDS_VERIFICATION for i in items
    )

    if has_unverified_items:
        session.processing_status = AuditSession.ProcessingStatus.VERIFICATION_REQUIRED
    elif total_findings > 0:
        session.processing_status = AuditSession.ProcessingStatus.FINDINGS_IDENTIFIED
    else:
        session.processing_status = AuditSession.ProcessingStatus.NO_DISCREPANCIES

    session.save()
    return session