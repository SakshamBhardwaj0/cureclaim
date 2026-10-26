from decimal import Decimal, InvalidOperation
from django import forms
from .models import AuditSession, BillItem
from .services.validation import validate_uploaded_bill_file


class BillUploadForm(forms.Form):
    """Form for uploading a hospital bill image or PDF."""

    SCHEME_CHOICES = [
        ("GENERAL_PRIVATE", "General Private Hospital Billing (Standard Review)"),
        ("EXAMPLE_SURGICAL_PKG_01", "Synthetic Demo Context: Example Surgical Package"),
        ("SCHEME_VERIFICATION_NEEDED", "Scheme / Package Rate Check (Requires Eligibility Verification)"),
    ]

    bill_file = forms.FileField(
        label="Hospital Bill Document (JPG, PNG, or PDF — max 10 MB)",
        required=True,
    )
    scheme_context = forms.ChoiceField(
        choices=SCHEME_CHOICES,
        initial="GENERAL_PRIVATE",
        label="Billing Context / Applicable Package",
    )
    confirm_external_ai = forms.BooleanField(
        required=True,
        label=(
            "I understand and confirm that this uploaded document will be sent to the "
            "configured external Google Gemini API for optical text extraction."
        ),
    )

    def clean_bill_file(self):
        uploaded_file = self.cleaned_data.get("bill_file")
        if uploaded_file:
            validate_uploaded_bill_file(uploaded_file)
        return uploaded_file


class ManualAuditHeaderForm(forms.Form):
    """Header fields for manual bill entry or editing an extracted bill."""

    SCHEME_CHOICES = [
        ("GENERAL_PRIVATE", "General Private Hospital Billing (Standard Checks)"),
        ("EXAMPLE_SURGICAL_PKG_01", "Synthetic Demo Scenario: Example Surgical Package"),
        ("SCHEME_VERIFICATION_NEEDED", "Insurance / Scheme Package Context (Verification Mode)"),
    ]

    hospital_name = forms.CharField(
        max_length=200,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "e.g., City Care Multispeciality Hospital"}),
    )
    bill_reference = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "e.g., INV-2026-0419"}),
    )
    bill_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    treatment_package = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Optional: e.g., Laparoscopic Appendectomy Package"}),
    )
    scheme_context = forms.ChoiceField(
        choices=SCHEME_CHOICES,
        initial="GENERAL_PRIVATE",
        required=True,
    )
    reported_grand_total = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    documented_adjustments = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        initial=Decimal("0.00"),
        help_text="Optional net taxes (+) or discounts (-) stated separately from line items.",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )

    def clean_documented_adjustments(self):
        val = self.cleaned_data.get("documented_adjustments")
        return val if val is not None else Decimal("0.00")


def parse_line_items_from_post(post_data):
    """
    Safely parses parallel arrays of line-item fields from a POST request
    (used by both manual_entry and review_extraction views).
    Never trusts client-side computed totals.
    """
    descriptions = post_data.getlist("item_description[]")
    quantities = post_data.getlist("item_quantity[]")
    units = post_data.getlist("item_unit[]")
    unit_prices = post_data.getlist("item_unit_price[]")
    billed_totals = post_data.getlist("item_billed_total[]")
    item_codes = post_data.getlist("item_code[]")

    parsed_items = []
    errors = []

    row_count = max(
        len(descriptions),
        len(quantities),
        len(unit_prices),
        len(billed_totals),
    )

    for idx in range(row_count):
        desc = (descriptions[idx] if idx < len(descriptions) else "").strip()
        qty_raw = (quantities[idx] if idx < len(quantities) else "").strip()
        unit_val = (units[idx] if idx < len(units) else "unit").strip() or "unit"
        price_raw = (unit_prices[idx] if idx < len(unit_prices) else "").strip()
        total_raw = (billed_totals[idx] if idx < len(billed_totals) else "").strip()
        code_val = (item_codes[idx] if idx < len(item_codes) else "").strip() or None

        # Skip completely empty trailing rows
        if not any([desc, qty_raw, price_raw, total_raw]):
            continue

        if not desc:
            errors.append(f"Row {idx + 1}: Item description is required.")
            continue

        def to_decimal_or_none(raw_str, field_label):
            if raw_str == "":
                return None
            cleaned = raw_str.replace(",", "").replace("₹", "").strip()
            try:
                val = Decimal(cleaned)
                if not val.is_finite():
                    raise InvalidOperation
                return val.quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                errors.append(f"Row {idx + 1} ({desc}): Invalid numeric value for {field_label} ('{raw_str}').")
                return None

        qty_dec = to_decimal_or_none(qty_raw, "Quantity")
        price_dec = to_decimal_or_none(price_raw, "Unit Price")
        total_dec = to_decimal_or_none(total_raw, "Billed Line Total")

        needs_verif = qty_dec is None or price_dec is None or total_dec is None
        parsed_items.append(
            {
                "line_number": len(parsed_items) + 1,
                "original_description": desc,
                "quantity": qty_dec,
                "unit": unit_val[:40],
                "unit_price": price_dec,
                "billed_line_total": total_dec,
                "item_code": code_val[:64] if code_val else None,
                "verification_status": (
                    BillItem.VerificationStatus.NEEDS_VERIFICATION
                    if needs_verif
                    else BillItem.VerificationStatus.USER_CORRECTED
                ),
                "extraction_confidence": Decimal("1.00"),
            }
        )

    if not parsed_items and not errors:
        errors.append("Please enter at least one bill line item before running the audit.")

    return parsed_items, errors