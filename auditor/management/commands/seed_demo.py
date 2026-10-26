"""
Management command: `python manage.py seed_demo`
Safely and idempotently seeds sample audit rules and the synthetic DEMO-001 bill.
"""
from datetime import date
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from auditor.models import AuditRule, AuditSession, BillItem
from auditor.services.audit_engine import run_audit_for_session


def seed_demo_data() -> AuditSession:
    """
    Idempotent function that seeds demonstration rules and the DEMO-001 synthetic bill.
    Called by both `python manage.py seed_demo` and the web UI's 'Try Interactive Demo' button.
    """
    with transaction.atomic():
        # 1. Fictional Demonstration Rule (DEMO ONLY)
        AuditRule.objects.update_or_create(
            rule_id="DEMO-PKG-GLOVES-01",
            defaults={
                "title": "Surgical consumable included in example package",
                "rule_type": AuditRule.RuleType.PACKAGE_INCLUSION,
                "description": (
                    "In this synthetic demonstration scenario, the defined surgical package "
                    "(EXAMPLE_SURGICAL_PKG_01) already includes standard sterile surgical gloves. "
                    "A separate line-item charge for surgical gloves is flagged for review."
                ),
                "jurisdiction_or_scheme": "Synthetic Demo Scenario Only (EXAMPLE_SURGICAL_PKG_01)",
                "facility_category": "Fictional Demonstration Hospital",
                "applicability_conditions": {
                    "keywords": ["surgical gloves", "sterile gloves"],
                    "package_codes": ["EXAMPLE_SURGICAL_PKG_01"],
                    "demo_only_context": True,
                },
                "source_title": "CureClaim Synthetic Hackathon Demonstration Specification (Fictional Rule)",
                "source_url": "",
                "effective_date": date(2026, 1, 1),
                "verification_status": AuditRule.VerificationStatus.DEMO_ONLY,
                "is_demo_only": True,
                "severity": AuditRule.Severity.MEDIUM,
                "can_calculate_impact": True,
                "is_active": True,
            },
        )

        # 2. Source-Verified General Documentation Rule
        AuditRule.objects.update_or_create(
            rule_id="RULE-DOC-LUMPSUM-01",
            defaults={
                "title": "Un-itemized miscellaneous or consumable bundle requires supporting breakup",
                "rule_type": AuditRule.RuleType.MISSING_DOCUMENTATION,
                "description": (
                    "When a hospital bill lists a consolidated lump-sum row such as 'Miscellaneous charges', "
                    "'Consumables bundle', or 'Pharmacy lump sum' exceeding ₹1,000 without an attached "
                    "itemized schedule, individual quantities and unit rates cannot be audited without the breakup."
                ),
                "jurisdiction_or_scheme": "General Patient Billing Transparency & Consumer Charter Principles",
                "facility_category": "All Hospitals (Requires Detailed Breakup Ledger)",
                "applicability_conditions": {
                    "keywords": [
                        "miscellaneous charges",
                        "consumables bundle",
                        "pharmacy lump sum",
                        "sundry charges",
                    ],
                    "min_line_total": "1000.00",
                },
                "source_title": "MoHFW Patient Charter — Right to Itemized Hospital Bills & Supporting Invoices",
                "source_url": "https://main.mohfw.gov.in/",
                "effective_date": date(2019, 6, 1),
                "verification_status": AuditRule.VerificationStatus.SOURCE_VERIFIED,
                "is_demo_only": False,
                "severity": AuditRule.Severity.MEDIUM,
                "can_calculate_impact": False,
                "is_active": True,
            },
        )

        # 3. Unverified Benchmark Rule (Verification Prompt Only)
        AuditRule.objects.update_or_create(
            rule_id="RULE-SCHEME-CONSULT-UNVERIFIED",
            defaults={
                "title": "Scheme Reference Consultation Tariff Check (Requires Eligibility Verification)",
                "rule_type": AuditRule.RuleType.BENCHMARK_RATE,
                "description": (
                    "Compares specialist consultation charges against a placeholder scheme reference rate ONLY "
                    "when the user selects Scheme Verification mode. Because scheme rates depend on hospital "
                    "empanelment, city tier, ward entitlement, and beneficiary ID, this unverified rule never "
                    "adds its difference automatically to the Potential Amount to Review."
                ),
                "jurisdiction_or_scheme": "Scheme Reference Context (Subject to Empanelment & Eligibility Proof)",
                "facility_category": "Empaneled Facilities Only (Unverified)",
                "applicability_conditions": {
                    "keywords": ["consultation", "specialist visit"],
                    "scheme_contexts": ["SCHEME_VERIFICATION_NEEDED"],
                    "benchmark_max_unit_price": "500.00",
                    "requires_eligibility_verification": True,
                },
                "source_title": "Awaiting Official Tier-Specific Tariff Schedule Verification",
                "source_url": "",
                "effective_date": None,
                "verification_status": AuditRule.VerificationStatus.UNVERIFIED,
                "is_demo_only": False,
                "severity": AuditRule.Severity.LOW,
                "can_calculate_impact": False,
                "is_active": True,
            },
        )

        # 4. Synthetic Sample Bill: DEMO-001 (Northstar General Hospital)
        demo_session, _ = AuditSession.objects.update_or_create(
            bill_reference="DEMO-001",
            is_demo=True,
            defaults={
                "audit_uid": "AUD-DEMO001",
                "hospital_name": "Northstar General Hospital (Fictional Demo Hospital)",
                "bill_date": date(2026, 10, 25),
                "patient_name": "Synthetic Demo Patient",
                "treatment_package": "Example surgical package",
                "package_code": "EXAMPLE_SURGICAL_PKG_01",
                "scheme_context": "EXAMPLE_SURGICAL_PKG_01",
                "currency": "INR",
                "reported_grand_total": Decimal("8500.00"),
                "documented_adjustments": Decimal("0.00"),
                "ai_extraction_status": AuditSession.AIExtractionStatus.NOT_USED,
                "user_confirmed_data": True,
                "notes": (
                    "Synthetic demonstration bill created by seed_demo. All rates and rules in this scenario "
                    "are fictional and exist solely to demonstrate deterministic calculations and rule evaluation."
                ),
            },
        )

        demo_session.items.all().delete()

        demo_items = [
            {
                "line_number": 1,
                "original_description": "Consultation",
                "quantity": Decimal("1.00"),
                "unit": "visit",
                "unit_price": Decimal("800.00"),
                "billed_line_total": Decimal("800.00"),
                "item_code": "CONS-01",
            },
            {
                "line_number": 2,
                "original_description": "Room charges",
                "quantity": Decimal("2.00"),
                "unit": "days",
                "unit_price": Decimal("3000.00"),
                "billed_line_total": Decimal("6000.00"),
                "item_code": "ROOM-STD",
            },
            {
                "line_number": 3,
                "original_description": "Surgical gloves",
                "quantity": Decimal("1.00"),
                "unit": "pair",
                "unit_price": Decimal("500.00"),
                "billed_line_total": Decimal("500.00"),
                "item_code": "CONS-GLV",
            },
            {
                "line_number": 4,
                "original_description": "Laboratory test",
                "quantity": Decimal("1.00"),
                "unit": "panel",
                "unit_price": Decimal("1200.00"),
                "billed_line_total": Decimal("1200.00"),
                "item_code": "LAB-104",
            },
        ]

        for item_data in demo_items:
            BillItem.objects.create(
                session=demo_session,
                extraction_confidence=Decimal("1.00"),
                verification_status=BillItem.VerificationStatus.VERIFIED,
                **item_data,
            )

        run_audit_for_session(demo_session)
        return demo_session


class Command(BaseCommand):
    help = "Seeds CureClaim with sample audit rules and the synthetic Northstar General Hospital bill (DEMO-001)."

    def handle(self, *args, **options):
        session = seed_demo_data()
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded CureClaim demo data! Audit UID: {session.audit_uid}"
            )
        )
