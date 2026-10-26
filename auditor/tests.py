"""
Unit and Integration Tests for CureClaim:
1. Financial calculations (Decimal arithmetic, mismatches, reconciliation, missing values, negative numbers).
2. Rule engine (matching conditions, non-matching, unverified rules, demo-only isolation, deduplication).
3. AI extraction service (mocked Gemini API calls: valid JSON, malformed JSON, missing fields, API errors).
4. Web application views (dashboard, demo launch, manual entry, audit detail, history, PDF report, invalid file upload).
"""
import json
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from .management.commands.seed_demo import seed_demo_data
from .models import AuditFinding, AuditRule, AuditSession, BillItem
from .services.audit_engine import run_audit_for_session
from .services.calculations import (
    evaluate_line_item_arithmetic,
    reconcile_bill_grand_total,
)
from .services.extraction import (
    ExtractionServiceError,
    extract_bill_from_uploaded_file,
    validate_and_normalize_extraction_payload,
)
from .services.validation import validate_uploaded_bill_file


class FinancialCalculationsTest(TestCase):
    """Tests deterministic Decimal calculations in auditor/services/calculations.py."""

    def test_correct_line_item_multiplication(self):
        res = evaluate_line_item_arithmetic(
            line_number=1,
            description="Room charges",
            quantity=Decimal("2.00"),
            unit_price=Decimal("3000.00"),
            billed_line_total=Decimal("6000.00"),
        )
        self.assertFalse(res.has_arithmetic_mismatch)
        self.assertFalse(res.has_missing_or_invalid_data)
        self.assertEqual(res.expected_total, Decimal("6000.00"))
        self.assertEqual(res.difference, Decimal("0.00"))

    def test_incorrect_line_item_total_creates_mismatch(self):
        res = evaluate_line_item_arithmetic(
            line_number=2,
            description="Infusion Pump",
            quantity=Decimal("3.00"),
            unit_price=Decimal("450.00"),
            billed_line_total=Decimal("1800.00"),
        )
        self.assertTrue(res.has_arithmetic_mismatch)
        self.assertEqual(res.expected_total, Decimal("1350.00"))
        self.assertEqual(res.difference, Decimal("450.00"))

    def test_missing_monetary_values_flagged_for_verification(self):
        res = evaluate_line_item_arithmetic(
            line_number=3,
            description="Pathology Panel",
            quantity=Decimal("1.00"),
            unit_price=None,
            billed_line_total=Decimal("1200.00"),
        )
        self.assertTrue(res.has_missing_or_invalid_data)
        self.assertFalse(res.has_arithmetic_mismatch)
        self.assertIn("unit price", res.missing_fields)

    def test_decimal_rounding_and_negative_values(self):
        res = evaluate_line_item_arithmetic(
            line_number=4,
            description="Unexplained negative row",
            quantity=Decimal("-1.00"),
            unit_price=Decimal("250.555"),
            billed_line_total=Decimal("-250.56"),
        )
        self.assertTrue(res.has_negative_value)
        self.assertTrue(res.has_missing_or_invalid_data)
        self.assertEqual(res.expected_total, Decimal("-250.56"))

    def test_grand_total_reconciliation(self):
        session = seed_demo_data()
        recon_ok = reconcile_bill_grand_total(
            session.items.all(),
            reported_grand_total=Decimal("8500.00"),
        )
        self.assertTrue(recon_ok.is_reconciled)
        self.assertEqual(recon_ok.sum_of_billed_items, Decimal("8500.00"))

        recon_bad = reconcile_bill_grand_total(
            session.items.all(),
            reported_grand_total=Decimal("9200.00"),
        )
        self.assertFalse(recon_bad.is_reconciled)
        self.assertEqual(recon_bad.unexplained_difference, Decimal("700.00"))


class AuditRuleEngineTest(TestCase):
    """Tests rule matching, demo-rule isolation, unverified rules, and financial impact deduplication."""

    def setUp(self):
        self.demo_session = seed_demo_data()

    def test_seeded_demo_audit_produces_expected_500_finding(self):
        self.assertEqual(self.demo_session.reported_grand_total, Decimal("8500.00"))
        self.assertEqual(self.demo_session.computed_line_total, Decimal("8500.00"))
        self.assertEqual(self.demo_session.potential_amount_to_review, Decimal("500.00"))

        findings = list(self.demo_session.findings.all())
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.financial_impact, Decimal("500.00"))
        self.assertTrue(finding.is_demo_finding)
        self.assertTrue(finding.rule.is_demo_only)
        self.assertEqual(finding.rule.rule_id, "DEMO-PKG-GLOVES-01")

    def test_demo_rule_does_not_apply_to_real_general_private_bill(self):
        real_session = AuditSession.objects.create(
            hospital_name="Metro Private Hospital",
            bill_reference="REAL-101",
            scheme_context="GENERAL_PRIVATE",
            reported_grand_total=Decimal("500.00"),
            is_demo=False,
        )
        BillItem.objects.create(
            session=real_session,
            line_number=1,
            original_description="Surgical gloves",
            quantity=Decimal("1.00"),
            unit_price=Decimal("500.00"),
            billed_line_total=Decimal("500.00"),
        )
        run_audit_for_session(real_session)
        self.assertEqual(real_session.findings.count(), 0)
        self.assertEqual(real_session.potential_amount_to_review, Decimal("0.00"))

    def test_unverified_rule_does_not_produce_confirmed_financial_impact(self):
        scheme_session = AuditSession.objects.create(
            hospital_name="City Hospital",
            bill_reference="SCH-202",
            scheme_context="SCHEME_VERIFICATION_NEEDED",
            reported_grand_total=Decimal("800.00"),
            is_demo=False,
        )
        BillItem.objects.create(
            session=scheme_session,
            line_number=1,
            original_description="Specialist Consultation",
            quantity=Decimal("1.00"),
            unit_price=Decimal("800.00"),
            billed_line_total=Decimal("800.00"),
        )
        run_audit_for_session(scheme_session)
        finding = scheme_session.findings.first()
        self.assertIsNotNone(finding)
        self.assertEqual(finding.rule.verification_status, AuditRule.VerificationStatus.UNVERIFIED)
        # Unverified benchmark rule must not auto-add to Potential Amount to Review
        self.assertEqual(finding.financial_impact, Decimal("0.00"))
        self.assertEqual(scheme_session.potential_amount_to_review, Decimal("0.00"))

    def test_overlapping_findings_on_same_item_are_not_double_counted(self):
        dup_demo = AuditSession.objects.create(
            hospital_name="Northstar Demo",
            bill_reference="DEMO-OVERLAP",
            scheme_context="EXAMPLE_SURGICAL_PKG_01",
            package_code="EXAMPLE_SURGICAL_PKG_01",
            reported_grand_total=Decimal("1000.00"),
            is_demo=True,
        )
        # Two identical surgical gloves rows in the demo package:
        # Row 2 triggers BOTH Potential Duplicate (500) AND Package Inclusion Rule (500).
        BillItem.objects.create(
            session=dup_demo,
            line_number=1,
            original_description="Surgical gloves",
            quantity=Decimal("1.00"),
            unit_price=Decimal("500.00"),
            billed_line_total=Decimal("500.00"),
        )
        BillItem.objects.create(
            session=dup_demo,
            line_number=2,
            original_description="Surgical gloves",
            quantity=Decimal("1.00"),
            unit_price=Decimal("500.00"),
            billed_line_total=Decimal("500.00"),
        )
        run_audit_for_session(dup_demo)
        # Total billed for both rows is 1,000. Deduplication ensures Row 2 is only counted once (500 + 500 = 1000, not 1500).
        self.assertEqual(dup_demo.potential_amount_to_review, Decimal("1000.00"))


class AIExtractionMockTest(TestCase):
    """Tests AI extraction validation and error handling using unittest.mock without requiring a live API key."""

    def test_valid_structured_json_output(self):
        sample_json = json.dumps(
            {
                "hospital_name": "Sunrise Care Hospital",
                "invoice_number": "INV-900",
                "invoice_date": "2026-10-20",
                "currency": "INR",
                "grand_total": "1500.00",
                "items": [
                    {
                        "description": "Complete Blood Count",
                        "quantity": "1",
                        "unit": "test",
                        "unit_price": "1500.00",
                        "line_total": "1500.00",
                        "confidence": 0.98,
                        "needs_verification": False,
                    }
                ],
            }
        )
        parsed = validate_and_normalize_extraction_payload(sample_json)
        self.assertEqual(parsed["hospital_name"], "Sunrise Care Hospital")
        self.assertEqual(parsed["grand_total"], Decimal("1500.00"))
        self.assertFalse(parsed["has_uncertain_fields"])
        self.assertEqual(len(parsed["items"]), 1)

    def test_malformed_model_output_raises_error(self):
        with self.assertRaises(ExtractionServiceError):
            validate_and_normalize_extraction_payload("NOT VALID JSON {{{")

    def test_missing_fields_flagged_for_verification(self):
        sample_json = json.dumps(
            {
                "hospital_name": "Test Clinic",
                "invoice_number": "INV-901",
                "grand_total": "500.00",
                "items": [
                    {
                        "description": "X-Ray Chest",
                        "quantity": None,
                        "unit_price": "500.00",
                        "line_total": "500.00",
                        "confidence": 0.60,
                        "needs_verification": True,
                    }
                ],
            }
        )
        parsed = validate_and_normalize_extraction_payload(sample_json)
        self.assertTrue(parsed["has_uncertain_fields"])
        self.assertTrue(parsed["items"][0]["needs_verification"])
        self.assertIsNone(parsed["items"][0]["quantity"])

    @patch("auditor.services.extraction.call_gemini_document_extraction")
    def test_api_failure_handled_gracefully(self, mock_gemini):
        mock_gemini.side_effect = ExtractionServiceError("Simulated API timeout")
        dummy_file = SimpleUploadedFile("bill.jpg", b"fake-bytes", content_type="image/jpeg")
        with self.assertRaises(ExtractionServiceError):
            extract_bill_from_uploaded_file(dummy_file)


class WebApplicationWorkflowTest(TestCase):
    """Tests end-to-end HTTP workflows, views, PDF generation, and file upload security."""

    def setUp(self):
        self.client = Client()

    def test_dashboard_and_demo_launch_workflow(self):
        resp = self.client.get(reverse("auditor:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CureClaim")

        demo_resp = self.client.get(reverse("auditor:launch_demo"), follow=True)
        self.assertEqual(demo_resp.status_code, 200)
        self.assertContains(demo_resp, "Northstar General Hospital")
        self.assertContains(demo_resp, "DEMO-001")
        self.assertContains(demo_resp, "500.00")

    def test_manual_bill_creation_and_audit_execution(self):
        post_data = {
            "hospital_name": "Apex Metro Hospital",
            "bill_reference": "MAN-2026-01",
            "bill_date": "2026-10-25",
            "treatment_package": "General Admission",
            "scheme_context": "GENERAL_PRIVATE",
            "reported_grand_total": "2500.00",
            "documented_adjustments": "0.00",
            "item_description[]": ["ECG Test", "Ward Nursing Charge"],
            "item_quantity[]": ["1", "2"],
            "item_unit[]": ["test", "days"],
            "item_unit_price[]": ["500.00", "800.00"],
            # Intentional arithmetic mismatch on row 2: 2 * 800 = 1600, but billed 2000
            "item_billed_total[]": ["500.00", "2000.00"],
            "item_code[]": ["ECG1", "WRD2"],
        }
        resp = self.client.post(reverse("auditor:manual_entry"), data=post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        session = AuditSession.objects.get(bill_reference="MAN-2026-01")
        self.assertEqual(session.verified_arithmetic_discrepancy, Decimal("400.00"))
        self.assertEqual(session.potential_amount_to_review, Decimal("400.00"))

    def test_pdf_report_download_returns_valid_pdf_bytes(self):
        session = seed_demo_data()
        resp = self.client.get(reverse("auditor:download_report_pdf", args=[session.audit_uid]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    def test_invalid_file_upload_rejected_by_validator(self):
        bad_exe = SimpleUploadedFile("malicious.exe", b"MZ-fake-binary", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            validate_uploaded_bill_file(bad_exe)

        corrupt_pdf = SimpleUploadedFile("corrupt.pdf", b"not-a-pdf-header", content_type="application/pdf")
        with self.assertRaises(ValidationError):
            validate_uploaded_bill_file(corrupt_pdf)