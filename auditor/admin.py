from django.contrib import admin
from .models import AuditFinding, AuditRule, AuditSession, BillItem


class BillItemInline(admin.TabularInline):
    model = BillItem
    extra = 0


class AuditFindingInline(admin.StackedInline):
    model = AuditFinding
    extra = 0


@admin.register(AuditSession)
class AuditSessionAdmin(admin.ModelAdmin):
    list_display = (
        "audit_uid",
        "hospital_name",
        "bill_reference",
        "reported_grand_total",
        "potential_amount_to_review",
        "processing_status",
        "is_demo",
        "created_at",
    )
    list_filter = ("processing_status", "is_demo", "ai_extraction_status")
    search_fields = ("audit_uid", "hospital_name", "bill_reference")
    inlines = [BillItemInline, AuditFindingInline]


@admin.register(AuditRule)
class AuditRuleAdmin(admin.ModelAdmin):
    list_display = (
        "rule_id",
        "title",
        "rule_type",
        "jurisdiction_or_scheme",
        "verification_status",
        "is_demo_only",
        "is_active",
    )
    list_filter = ("rule_type", "verification_status", "is_demo_only", "is_active")
    search_fields = ("rule_id", "title", "description")