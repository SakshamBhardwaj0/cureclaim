import os
import uuid
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models


class AuditRule(models.Model):
    """
    Structured billing audit rule evaluated deterministically by the rule engine.
    Explicitly separates official source-verified rules, unverified rules, and demo-only rules.
    """

    class RuleType(models.TextChoices):
        ARITHMETIC = "ARITHMETIC", "Arithmetic Discrepancy"
        DUPLICATE = "DUPLICATE", "Potential Duplicate Charge"
        PACKAGE_INCLUSION = "PACKAGE_INCLUSION", "Package Inclusion Rule"
        BENCHMARK_RATE = "BENCHMARK_RATE", "Applicable Benchmark Rate Comparison"
        MISSING_DOCUMENTATION = "MISSING_DOCUMENTATION", "Missing Documentation / Eligibility Verification"
        MANUAL_REVIEW = "MANUAL_REVIEW", "General Manual Review Flag"

    class VerificationStatus(models.TextChoices):
        SOURCE_VERIFIED = "SOURCE_VERIFIED", "Source-Verified Rule"
        DEMO_ONLY = "DEMO_ONLY", "DEMO ONLY â€” Not Official Guidance"
        UNVERIFIED = "UNVERIFIED", "Unverified Rule â€” Awaiting Source Verification"

    class Severity(models.TextChoices):
        HIGH = "HIGH", "High â€” Direct Discrepancy"
        MEDIUM = "MEDIUM", "Medium â€” Review Recommended"
        LOW = "LOW", "Low â€” Informational / Verification Needed"

    rule_id = models.CharField(max_length=64, unique=True, db_index=True)
    title = models.CharField(max_length=200)
    rule_type = models.CharField(max_length=32, choices=RuleType.choices)
    description = models.TextField()
    jurisdiction_or_scheme = models.CharField(
        max_length=120,
        help_text="E.g., Synthetic Demo Package, General Private Billing, or Specific Scheme (subject to eligibility).",
    )
    facility_category = models.CharField(
        max_length=120,
        blank=True,
        default="All Applicable",
        help_text="Relevant hospital or facility category when applicable.",
    )
    applicability_conditions = models.JSONField(
        default=dict,
        help_text=(
            "Structured JSON conditions: keywords, package_codes, demo_only_context, "
            "requires_scheme_verification, benchmark_max_unit_price, min_line_total, etc."
        ),
    )
    source_title = models.CharField(max_length=255)
    source_url = models.URLField(blank=True, null=True)
    effective_date = models.DateField(blank=True, null=True)
    verification_status = models.CharField(
        max_length=24,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    is_demo_only = models.BooleanField(
        default=False,
        help_text="If True, this rule is fictional and restricted strictly to synthetic demo scenarios.",
    )
    severity = models.CharField(
        max_length=16,
        choices=Severity.choices,
        default=Severity.MEDIUM,
    )
    can_calculate_impact = models.BooleanField(
        default=True,
        help_text="Whether the financial impact can be deterministically calculated when matched.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_demo_only", "rule_id"]

    def clean(self):
        if self.is_demo_only and self.verification_status == self.VerificationStatus.SOURCE_VERIFIED:
            raise ValidationError("A demonstration-only rule cannot be marked as Source-Verified.")
        if not isinstance(self.applicability_conditions, dict):
            raise ValidationError("Applicability conditions must be a valid JSON object.")

    def __str__(self):
        prefix = "[DEMO] " if self.is_demo_only else ""
        return f"{prefix}{self.rule_id}: {self.title}"


class AuditSession(models.Model):
    """
    Represents a single hospital bill auditing session, storing extracted/entered metadata
    and reconciled totals. Historical records remain intact even if the uploaded file is deleted.
    """

    class ProcessingStatus(models.TextChoices):
        PENDING_REVIEW = "PENDING_REVIEW", "Awaiting Data Confirmation"
        FINDINGS_IDENTIFIED = "FINDINGS_IDENTIFIED", "Findings Identified"
        VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED", "Verification Required"
        NO_DISCREPANCIES = "NO_DISCREPANCIES", "No Discrepancies Detected by Available Checks"
        FAILED = "FAILED", "Processing Error"

    class AIExtractionStatus(models.TextChoices):
        NOT_USED = "NOT_USED", "Manual Entry / Synthetic Seed"
        SUCCESS = "SUCCESS", "AI Extracted â€” User Reviewed"
        PARTIAL_UNCERTAIN = "PARTIAL_UNCERTAIN", "AI Extracted â€” Contains Uncertain Fields"
        FAILED = "FAILED", "AI Extraction Failed"

    audit_uid = models.CharField(max_length=32, unique=True, db_index=True, editable=False)
    uploaded_file = models.FileField(upload_to="bills/%Y/%m/", blank=True, null=True)
    original_filename = models.CharField(max_length=255, blank=True, default="")

    hospital_name = models.CharField(max_length=200)
    bill_reference = models.CharField(max_length=100, db_index=True)
    bill_date = models.DateField(blank=True, null=True)
    patient_name = models.CharField(max_length=150, blank=True, default="")
    treatment_package = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="Stated procedure or package name, if any.",
    )
    package_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Normalized package or scheme identifier for rule matching.",
    )
    scheme_context = models.CharField(
        max_length=64,
        blank=True,
        default="GENERAL_PRIVATE",
        help_text="Billing context: DEMO_PACKAGE, GENERAL_PRIVATE, or SCHEME_VERIFICATION_NEEDED.",
    )
    currency = models.CharField(max_length=10, default="INR")

    reported_grand_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    documented_adjustments = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Net documented taxes (+) or discounts (-) separately stated on the bill.",
    )
    computed_line_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    verified_arithmetic_discrepancy = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    potential_amount_to_review = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    processing_status = models.CharField(
        max_length=32,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING_REVIEW,
    )
    ai_extraction_status = models.CharField(
        max_length=24,
        choices=AIExtractionStatus.choices,
        default=AIExtractionStatus.NOT_USED,
    )
    is_demo = models.BooleanField(default=False, db_index=True)
    user_confirmed_data = models.BooleanField(default=False)
    external_ai_consent = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.audit_uid:
            self.audit_uid = f"AUD-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def delete_uploaded_file_safely(self):
        """Remove the underlying file from disk without deleting the database audit history."""
        if self.uploaded_file:
            try:
                file_path = self.uploaded_file.path
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except Exception:
                pass
            self.uploaded_file = None

    def delete(self, *args, **kwargs):
        self.delete_uploaded_file_safely()
        super().delete(*args, **kwargs)

    @property
    def findings_count(self):
        return self.findings.exclude(review_status=AuditFinding.ReviewStatus.EXCLUDED).count()

    @property
    def items_needing_verification_count(self):
        return self.items.filter(
            verification_status=BillItem.VerificationStatus.NEEDS_VERIFICATION
        ).count()

    def __str__(self):
        return f"{self.audit_uid} â€” {self.hospital_name} ({self.bill_reference})"


class BillItem(models.Model):
    """Individual line item extracted or entered from a hospital bill."""

    class VerificationStatus(models.TextChoices):
        VERIFIED = "VERIFIED", "Verified"
        NEEDS_VERIFICATION = "NEEDS_VERIFICATION", "Needs Verification"
        USER_CORRECTED = "USER_CORRECTED", "User Corrected"

    class ItemAuditBadge(models.TextChoices):
        NO_ISSUE = "NO_ISSUE", "No Issue Detected"
        CALC_MISMATCH = "CALC_MISMATCH", "Calculation Mismatch"
        POTENTIAL_DUPLICATE = "POTENTIAL_DUPLICATE", "Potential Duplicate"
        RULE_FINDING = "RULE_FINDING", "Rule-Based Finding"
        NEEDS_VERIFICATION = "NEEDS_VERIFICATION", "Needs Verification"

    session = models.ForeignKey(
        AuditSession,
        on_delete=models.CASCADE,
        related_name="items",
    )
    line_number = models.PositiveIntegerField(default=1)
    original_description = models.CharField(max_length=255)
    normalized_description = models.CharField(max_length=255, blank=True, default="", db_index=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=40, blank=True, default="unit")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    billed_line_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    computed_line_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    item_code = models.CharField(max_length=64, blank=True, null=True)
    source_page = models.PositiveIntegerField(default=1, blank=True, null=True)
    extraction_confidence = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("1.00"),
    )
    verification_status = models.CharField(
        max_length=24,
        choices=VerificationStatus.choices,
        default=VerificationStatus.VERIFIED,
    )
    audit_badge = models.CharField(
        max_length=24,
        choices=ItemAuditBadge.choices,
        default=ItemAuditBadge.NO_ISSUE,
    )
    excluded_rule_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of rule_ids explicitly excluded by the user for this line item.",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["line_number", "id"]

    def save(self, *args, **kwargs):
        if self.original_description:
            self.normalized_description = " ".join(
                self.original_description.lower().strip().split()
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Line {self.line_number}: {self.original_description} ({self.billed_line_total})"


class AuditFinding(models.Model):
    """
    A specific, evidence-backed audit finding linked to an AuditSession,
    an optional BillItem, and an optional AuditRule.
    """

    class Category(models.TextChoices):
        ARITHMETIC = "ARITHMETIC", "Calculation Mismatch"
        DUPLICATE = "DUPLICATE", "Potential Duplicate"
        PACKAGE_INCLUSION = "PACKAGE_INCLUSION", "Package-Inclusion Rule"
        BENCHMARK_RATE = "BENCHMARK_RATE", "Benchmark Rate Comparison"
        RECONCILIATION = "RECONCILIATION", "Grand Total Reconciliation"
        MISSING_DATA = "MISSING_DATA", "Needs Verification / Missing Data"
        MANUAL_REVIEW = "MANUAL_REVIEW", "Manual Review Flag"

    class Severity(models.TextChoices):
        HIGH = "HIGH", "High"
        MEDIUM = "MEDIUM", "Medium"
        LOW = "LOW", "Low"

    class ReviewStatus(models.TextChoices):
        OPEN = "OPEN", "Open for Review"
        REVIEWED = "REVIEWED", "Reviewed & Acknowledged"
        EXCLUDED = "EXCLUDED", "Excluded (False Match)"

    session = models.ForeignKey(
        AuditSession,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    bill_item = models.ForeignKey(
        BillItem,
        on_delete=models.CASCADE,
        related_name="findings",
        null=True,
        blank=True,
    )
    rule = models.ForeignKey(
        AuditRule,
        on_delete=models.SET_NULL,
        related_name="findings",
        null=True,
        blank=True,
    )

    category = models.CharField(max_length=32, choices=Category.choices)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.MEDIUM)
    title = models.CharField(max_length=255)
    explanation = models.TextField()
    calculation_basis = models.TextField(
        blank=True,
        default="",
        help_text="Explicit mathematical formula and assumptions used to derive the financial impact.",
    )
    supporting_evidence = models.TextField(
        help_text="Extracted facts and rule reference supporting this finding.",
    )
    recommended_action = models.TextField(
        default="Request an itemized clarification from the hospital billing desk."
    )

    financial_impact = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    is_impact_confirmed = models.BooleanField(
        default=False,
        help_text="True only for deterministic arithmetic discrepancies; False for rule/duplicate estimates.",
    )
    contributes_to_review_total = models.BooleanField(
        default=True,
        help_text="Prevents double-counting when multiple findings target the same line item.",
    )
    is_demo_finding = models.BooleanField(default=False)
    needs_human_verification = models.BooleanField(default=True)
    review_status = models.CharField(
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-financial_impact", "id"]

    def __str__(self):
        return f"{self.title} (Impact: {self.financial_impact})"
