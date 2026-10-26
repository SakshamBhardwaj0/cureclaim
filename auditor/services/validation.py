"""
Server-side file and schema validation for uploaded hospital bills.
Validates extensions, file size, and actual binary signatures / content using Pillow and pypdf.
"""
import os
from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "application/pdf",
}


def validate_uploaded_bill_file(uploaded_file) -> str:
    """
    Validates file extension, size limit, MIME type, and actual binary integrity
    for JPG, PNG, and PDF files. Returns normalized file type ('image' or 'pdf').
    Raises django.core.exceptions.ValidationError on any issue.
    """
    if not uploaded_file:
        raise ValidationError("No file was uploaded.")

    max_bytes = getattr(settings, "MAX_UPLOAD_SIZE_BYTES", 10 * 1024 * 1024)
    if uploaded_file.size > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise ValidationError(
            f"File is too large ({uploaded_file.size / (1024 * 1024):.1f} MB). "
            f"Maximum permitted size is {max_mb} MB."
        )

    if uploaded_file.size == 0:
        raise ValidationError("The uploaded file is empty (0 bytes).")

    ext = os.path.splitext(uploaded_file.name or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file extension '{ext}'. Allowed formats: JPG, JPEG, PNG, and PDF."
        )

    content_type = getattr(uploaded_file, "content_type", "")
    if content_type and content_type not in ALLOWED_MIME_TYPES:
        raise ValidationError(
            f"Unsupported content type '{content_type}'. Please upload a valid JPG, PNG, or PDF document."
        )

    # Deep binary content verification
    uploaded_file.seek(0)
    if ext == ".pdf":
        header = uploaded_file.read(5)
        uploaded_file.seek(0)
        if header != b"%PDF-":
            raise ValidationError("Invalid or corrupted PDF file: missing %PDF header signature.")
        try:
            reader = PdfReader(uploaded_file)
            if len(reader.pages) == 0:
                raise ValidationError("The uploaded PDF contains 0 pages.")
            # Access first page to confirm stream is readable
            _ = reader.pages[0]
        except ValidationError:
            raise
        except (PdfReadError, Exception) as exc:
            raise ValidationError(
                "The uploaded PDF file appears to be corrupted or password-protected and could not be read."
            ) from exc
        finally:
            uploaded_file.seek(0)
        return "pdf"

    # Otherwise verify image integrity with Pillow
    try:
        img = Image.open(uploaded_file)
        img.verify()
        fmt = (img.format or "").upper()
        if fmt not in ("JPEG", "PNG"):
            raise ValidationError(f"Unsupported image encoding '{fmt}'. Only JPEG and PNG images are supported.")
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(
            "The uploaded image file is corrupted or unreadable. Please upload a clear JPG or PNG photograph."
        ) from exc
    finally:
        uploaded_file.seek(0)

    return "image"