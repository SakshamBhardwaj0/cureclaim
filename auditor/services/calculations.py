"""
Deterministic financial validation engine for CureClaim.
Uses Python's Decimal exclusively. Never uses binary floating-point arithmetic.
"""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import List, Optional

CENT = Decimal("0.01")
ARITHMETIC_TOLERANCE = Decimal("0.01")


def quantize_currency(value: Optional[Decimal]) -> Optional[Decimal]:
    """Round a Decimal to 2 decimal places using standard financial ROUND_HALF_UP."""
    if value is None:
        return None
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    if not value.is_finite():
        raise InvalidOperation("Non-finite Decimal value encountered.")
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class LineCalculationResult:
    line_number: int
    expected_total: Optional[Decimal]
    billed_total: Optional[Decimal]
    difference: Decimal
    has_arithmetic_mismatch: bool
    has_missing_or_invalid_data: bool
    missing_fields: List[str]
    has_negative_value: bool
    explanation: str
    calculation_basis: str


@dataclass
class ReconciliationResult:
    sum_of_billed_items: Decimal
    documented_adjustments: Decimal
    expected_grand_total: Decimal
    reported_grand_total: Decimal
    unexplained_difference: Decimal
    is_reconciled: bool
    explanation: str
    calculation_basis: str


@dataclass
class DuplicateCandidate:
    primary_item_id: int
    duplicate_item_id: int
    primary_line_number: int
    duplicate_line_number: int
    description: str
    billed_amount: Decimal
    explanation: str
    calculation_basis: str


def evaluate_line_item_arithmetic(
    line_number: int,
    description: str,
    quantity: Optional[Decimal],
    unit_price: Optional[Decimal],
    billed_line_total: Optional[Decimal],
    tolerance: Decimal = ARITHMETIC_TOLERANCE,
) -> LineCalculationResult:
    """
    Check 1 & Check 4:
    Validates presence of numeric values, checks for unexplained negative amounts,
    and verifies Expected Line Total = Quantity * Unit Price against Billed Line Total.
    """
    missing_fields = []
    if quantity is None:
        missing_fields.append("quantity")
    if unit_price is None:
        missing_fields.append("unit price")
    if billed_line_total is None:
        missing_fields.append("billed line total")

    if missing_fields:
        missing_str = ", ".join(missing_fields)
        return LineCalculationResult(
            line_number=line_number,
            expected_total=None,
            billed_total=quantize_currency(billed_line_total),
            difference=Decimal("0.00"),
            has_arithmetic_mismatch=False,
            has_missing_or_invalid_data=True,
            missing_fields=missing_fields,
            has_negative_value=False,
            explanation=(
                f"Line {line_number} ('{description}') is missing required financial data: {missing_str}. "
                "A missing value is not evidence of overcharging, but prevents automated arithmetic verification."
            ),
            calculation_basis=f"Cannot compute Quantity × Unit Price because {missing_str} is unavailable.",
        )

    qty_q = quantize_currency(quantity)
    price_q = quantize_currency(unit_price)
    billed_q = quantize_currency(billed_line_total)

    has_negative = qty_q < Decimal("0.00") or price_q < Decimal("0.00") or billed_q < Decimal("0.00")
    expected_q = quantize_currency(qty_q * price_q)
    diff = quantize_currency(billed_q - expected_q)

    if has_negative:
        return LineCalculationResult(
            line_number=line_number,
            expected_total=expected_q,
            billed_total=billed_q,
            difference=diff,
            has_arithmetic_mismatch=abs(diff) > tolerance,
            has_missing_or_invalid_data=True,
            missing_fields=[],
            has_negative_value=True,
            explanation=(
                f"Line {line_number} ('{description}') contains a negative quantity, rate, or amount "
                f"(Quantity: {qty_q}, Unit Price: {price_q}, Billed: {billed_q}). "
                "Verify whether this represents a documented credit/refund or a transcription error."
            ),
            calculation_basis=f"Quantity ({qty_q}) × Unit Price ({price_q}) = {expected_q} vs. Billed ({billed_q}).",
        )

    has_mismatch = abs(diff) > tolerance
    if has_mismatch:
        direction = "exceeds" if diff > Decimal("0.00") else "is less than"
        explanation = (
            f"Line {line_number} ('{description}') billed total ({billed_q}) {direction} "
            f"the calculated product of Quantity ({qty_q}) × Unit Price ({price_q}) = {expected_q} "
            f"by {abs(diff)}."
        )
    else:
        explanation = (
            f"Line {line_number} arithmetic verified: {qty_q} × {price_q} = {expected_q}."
        )

    return LineCalculationResult(
        line_number=line_number,
        expected_total=expected_q,
        billed_total=billed_q,
        difference=diff,
        has_arithmetic_mismatch=has_mismatch,
        has_missing_or_invalid_data=False,
        missing_fields=[],
        has_negative_value=False,
        explanation=explanation,
        calculation_basis=(
            f"Expected Total = {qty_q} × {price_q} = {expected_q}; "
            f"Billed Total = {billed_q}; Discrepancy = {billed_q} - {expected_q} = {diff}."
        ),
    )


def detect_potential_duplicate_items(items_iterable) -> List[DuplicateCandidate]:
    """
    Check 2: Duplicate-looking items.
    Groups items by normalized description and matching positive unit price / billed total.
    Flags matches as potential duplicates requiring human verification (since repeated daily
    tests or consumables may be clinically valid).
    """
    grouped = defaultdict(list)
    for item in items_iterable:
        norm_desc = (item.normalized_description or item.original_description or "").strip().lower()
        if not norm_desc or item.billed_line_total is None or item.billed_line_total <= Decimal("0.00"):
            continue
        key = (
            norm_desc,
            quantize_currency(item.unit_price) if item.unit_price is not None else None,
            quantize_currency(item.billed_line_total),
        )
        grouped[key].append(item)

    candidates = []
    for (norm_desc, unit_price, billed_total), group in grouped.items():
        if len(group) < 2:
            continue
        primary = group[0]
        for dup in group[1:]:
            candidates.append(
                DuplicateCandidate(
                    primary_item_id=primary.id,
                    duplicate_item_id=dup.id,
                    primary_line_number=primary.line_number,
                    duplicate_line_number=dup.line_number,
                    description=dup.original_description,
                    billed_amount=billed_total,
                    explanation=(
                        f"Line {dup.line_number} ('{dup.original_description}') has an identical description, "
                        f"unit rate ({unit_price}), and billed amount ({billed_total}) as Line {primary.line_number}. "
                        "Two identically named rows are not automatically an error (for example, repeat diagnostics "
                        "across different dates/shifts), so human verification of clinical dates/logs is required."
                    ),
                    calculation_basis=(
                        f"Potential duplicate entry on Line {dup.line_number} matching Line {primary.line_number}: "
                        f"Estimated review amount = {billed_total} (pending date/time log verification)."
                    ),
                )
            )
    return candidates


def reconcile_bill_grand_total(
    items_iterable,
    reported_grand_total: Decimal,
    documented_adjustments: Decimal = Decimal("0.00"),
    tolerance: Decimal = ARITHMETIC_TOLERANCE,
) -> ReconciliationResult:
    """
    Check 3: Bill total reconciliation.
    Sums recorded line totals, adds documented adjustments (taxes/discounts),
    and compares with the reported grand total on the bill.
    """
    sum_items = Decimal("0.00")
    for item in items_iterable:
        if item.billed_line_total is not None:
            sum_items += quantize_currency(item.billed_line_total)

    sum_items = quantize_currency(sum_items)
    adj_q = quantize_currency(documented_adjustments or Decimal("0.00"))
    reported_q = quantize_currency(reported_grand_total or Decimal("0.00"))
    expected_grand = quantize_currency(sum_items + adj_q)
    diff = quantize_currency(reported_q - expected_grand)
    is_reconciled = abs(diff) <= tolerance

    if is_reconciled:
        explanation = (
            f"Sum of billed line items ({sum_items})"
            + (f" plus documented adjustments ({adj_q})" if adj_q != Decimal("0.00") else "")
            + f" equals the reported grand total ({reported_q})."
        )
    else:
        direction = "higher" if diff > Decimal("0.00") else "lower"
        explanation = (
            f"Reported grand total ({reported_q}) is {abs(diff)} {direction} than the sum of recorded "
            f"line items ({sum_items})"
            + (f" adjusted by {adj_q}" if adj_q != Decimal("0.00") else "")
            + f" (Expected: {expected_grand}). Check for omitted pages, unextracted rows, or subtotal rounding."
        )

    return ReconciliationResult(
        sum_of_billed_items=sum_items,
        documented_adjustments=adj_q,
        expected_grand_total=expected_grand,
        reported_grand_total=reported_q,
        unexplained_difference=diff,
        is_reconciled=is_reconciled,
        explanation=explanation,
        calculation_basis=(
            f"Sum of Billed Line Items ({sum_items}) + Documented Adjustments ({adj_q}) = {expected_grand}; "
            f"Reported Grand Total = {reported_q}; Difference = {diff}."
        ),
    )