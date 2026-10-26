"""
AI Extraction Service for CureClaim using the official Google GenAI SDK (`google-genai`).
Strictly separates optical document transcription from financial auditing.
Never fabricates missing prices/quantities and never runs without a configured API key.
"""
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from django.conf import settings


class ExtractionServiceError(Exception):
    """Raised when AI extraction cannot be completed or returns invalid data."""


EXTRACTION_SYSTEM_PROMPT = """
You are a precise medical-billing optical transcription engine for CureClaim.
Your ONLY task is to transcribe what is visibly present on the hospital bill into the exact JSON schema requested.

CRITICAL TRANSCRIPTION RULES:
1. Transcribe ONLY what is present in the original document.
2. Preserve the original line-item descriptions verbatim.
3. Do NOT silently drop rows. Include every visible bill line item.
4. NEVER invent, guess, or calculate missing quantities, unit prices, or line totals.
5. Return null for any unavailable or omitted field.
6. Flag illegible, blurry, or ambiguous values by setting "needs_verification": true and lowering "confidence" below 0.75.
7. Never convert an uncertain number into a confident value.
8. Carefully distinguish a unit price (rate) from a billed line-item total (amount).
9. Preserve the exact relationship between item descriptions, quantities, rates, and amounts on each row.
10. Return ONLY valid JSON matching the schema below. Do NOT decide whether a charge is illegal or an overcharge.

REQUIRED JSON SCHEMA:
{
  "hospital_name": "string or null",
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "patient_name": "string or null",
  "treatment_package": "string or null",
  "currency": "INR",
  "grand_total": "decimal string e.g. '8500.00' or null",
  "documented_adjustments": "decimal string e.g. '0.00' for net taxes/discounts or null",
  "items": [
    {
      "description": "string",
      "quantity": "decimal string or null",
      "unit": "string or null",
      "unit_price": "decimal string or null",
      "line_total": "decimal string or null",
      "item_code": "string or null",
      "page_number": 1,
      "confidence": 0.95,
      "needs_verification": false
    }
  ]
}
""".strip()


def is_ai_configured() -> bool:
    """Returns True if a non-empty GEMINI_API_KEY is configured in settings/environment."""
    api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
    return bool(api_key)


def _parse_decimal_safe(val: Any) -> Optional[Decimal]:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float, Decimal)):
        try:
            d = Decimal(str(val))
            return d.quantize(Decimal("0.01")) if d.is_finite() else None
        except InvalidOperation:
            return None
    if isinstance(val, str):
        cleaned = val.replace(",", "").replace("₹", "").replace("\$", "").strip()
        if not cleaned:
            return None
        try:
            d = Decimal(cleaned)
            return d.quantize(Decimal("0.01")) if d.is_finite() else None
        except InvalidOperation:
            return None
    return None


def _parse_date_safe(date_str: Any):
    if not date_str or not isinstance(date_str, str):
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def validate_and_normalize_extraction_payload(raw_text: str) -> Dict[str, Any]:
    """
    Parses and validates raw JSON text from the Gemini model into safe Python types
    (using Decimal for all monetary figures).
    Raises ExtractionServiceError if JSON is malformed or contains 0 extracted items.
    """
    if not raw_text or not raw_text.strip():
        raise ExtractionServiceError("The AI model returned an empty response.")

    cleaned_text = raw_text.strip()
    if cleaned_text.startswith("```"):
        lines = cleaned_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned_text = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        raise ExtractionServiceError(
            "The AI extraction service returned malformed JSON that could not be parsed. "
            "Please try uploading a clearer image or use Manual Entry."
        ) from exc

    if not isinstance(data, dict):
        raise ExtractionServiceError("Invalid extraction structure: root element must be a JSON object.")

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or len(raw_items) == 0:
        raise ExtractionServiceError(
            "No bill line items could be extracted from the uploaded document. "
            "The image may be blurry, unreadable, or not a hospital bill. You can enter the items manually instead."
        )

    normalized_items: List[Dict[str, Any]] = []
    any_uncertain = False

    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description") or "").strip()
        if not desc:
            desc = f"Unlabeled Line Item #{idx}"
            item_needs_verif = True
        else:
            item_needs_verif = bool(item.get("needs_verification", False))

        qty = _parse_decimal_safe(item.get("quantity"))
        unit_price = _parse_decimal_safe(item.get("unit_price"))
        line_total = _parse_decimal_safe(item.get("line_total"))

        # Check if raw string was present but failed numeric parsing
        for raw_field, parsed_val in [
            (item.get("quantity"), qty),
            (item.get("unit_price"), unit_price),
            (item.get("line_total"), line_total),
        ]:
            if raw_field not in (None, "") and parsed_val is None:
                item_needs_verif = True

        if qty is None or unit_price is None or line_total is None:
            item_needs_verif = True

        try:
            conf = Decimal(str(item.get("confidence", "0.90"))).quantize(Decimal("0.01"))
            conf = max(Decimal("0.00"), min(Decimal("1.00"), conf))
        except InvalidOperation:
            conf = Decimal("0.50")
            item_needs_verif = True

        if conf < Decimal("0.80"):
            item_needs_verif = True

        if item_needs_verif:
            any_uncertain = True

        normalized_items.append(
            {
                "line_number": idx,
                "original_description": desc[:255],
                "quantity": qty,
                "unit": str(item.get("unit") or "unit").strip()[:40],
                "unit_price": unit_price,
                "billed_line_total": line_total,
                "item_code": (str(item.get("item_code")).strip()[:64] if item.get("item_code") else None),
                "source_page": int(item.get("page_number") or 1),
                "extraction_confidence": conf,
                "needs_verification": item_needs_verif,
            }
        )

    if not normalized_items:
        raise ExtractionServiceError("No valid line items were found in the extraction output.")

    grand_total = _parse_decimal_safe(data.get("grand_total"))
    if grand_total is None:
        # Sum available line totals as fallback but flag uncertainty
        grand_total = sum(
            (i["billed_line_total"] for i in normalized_items if i["billed_line_total"] is not None),
            Decimal("0.00"),
        )
        any_uncertain = True

    adjustments = _parse_decimal_safe(data.get("documented_adjustments")) or Decimal("0.00")

    return {
        "hospital_name": str(data.get("hospital_name") or "Unspecified Hospital (Verify Name)").strip()[:200],
        "invoice_number": str(data.get("invoice_number") or "UNSPECIFIED-REF").strip()[:100],
        "invoice_date": _parse_date_safe(data.get("invoice_date")),
        "patient_name": str(data.get("patient_name") or "").strip()[:150],
        "treatment_package": str(data.get("treatment_package") or "").strip()[:150],
        "currency": str(data.get("currency") or "INR").strip()[:10] or "INR",
        "grand_total": grand_total,
        "documented_adjustments": adjustments,
        "items": normalized_items,
        "has_uncertain_fields": any_uncertain,
    }


def call_gemini_document_extraction(file_bytes: bytes, mime_type: str) -> str:
    """
    Calls the configured Google Gemini multimodal model via the official `google-genai` SDK.
    Isolated in its own function so automated unit tests can easily mock it or test error handling.
    """
    api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
    model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

    if not api_key:
        raise ExtractionServiceError(
            "Gemini API key is not configured. Set GEMINI_API_KEY in your .env file to enable live AI extraction, "
            "or use the Interactive Demo or Manual Entry workflow."
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ExtractionServiceError(
            "The 'google-genai' package is not installed. Run 'pip install -r requirements.txt'."
        ) from exc

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                EXTRACTION_SYSTEM_PROMPT,
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        return response.text or ""
    except Exception as exc:
        # Never expose the API key or raw stack trace in user-facing error messages
        raise ExtractionServiceError(
            f"External AI extraction request failed ({exc.__class__.__name__}). "
            "Please verify your network connection and GEMINI_API_KEY/GEMINI_MODEL settings, "
            "or proceed using Manual Bill Entry."
        ) from exc


def extract_bill_from_uploaded_file(uploaded_file) -> Dict[str, Any]:
    """
    High-level service entry point: reads bytes and MIME type from a validated uploaded file,
    invokes Gemini multimodal extraction, and validates/normalizes the structured result.
    """
    uploaded_file.seek(0)
    file_bytes = uploaded_file.read()
    uploaded_file.seek(0)

    ext = (uploaded_file.name or "").lower()
    if ext.endswith(".pdf"):
        mime_type = "application/pdf"
    elif ext.endswith(".png"):
        mime_type = "image/png"
    else:
        mime_type = "image/jpeg"

    raw_json_str = call_gemini_document_extraction(file_bytes=file_bytes, mime_type=mime_type)
    return validate_and_normalize_extraction_payload(raw_json_str)