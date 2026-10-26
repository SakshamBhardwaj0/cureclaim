from django.urls import path
from . import views

app_name = "auditor"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("demo/launch/", views.launch_demo_view, name="launch_demo"),
    path("upload/", views.upload_bill_view, name="upload"),
    path("manual-entry/", views.manual_entry_view, name="manual_entry"),
    path("audits/", views.audit_history_view, name="audit_history"),
    path("audits/<str:audit_uid>/", views.audit_detail_view, name="audit_detail"),
    path("audits/<str:audit_uid>/review/", views.review_extraction_view, name="review_extraction"),
    path("audits/<str:audit_uid>/report/pdf/", views.download_report_pdf_view, name="download_report_pdf"),
    path("audits/<str:audit_uid>/delete/", views.delete_audit_view, name="delete_audit"),
    path(
        "audits/<str:audit_uid>/findings/<int:finding_id>/action/",
        views.finding_action_view,
        name="finding_action",
    ),
    path("rules/", views.rules_list_view, name="rules"),
    path("settings-about/", views.settings_about_view, name="settings_about"),
]