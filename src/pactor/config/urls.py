from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView
from rest_framework.routers import SimpleRouter

from pactor.approvals.api import ApprovalRoundViewSet, DelegationViewSet, WorkflowViewSet
from pactor.audit.api import AuditEventViewSet
from pactor.contracts import lifecycle_api
from pactor.contracts.api import ContractViewSet
from pactor.core import health
from pactor.counterparties.api import CounterpartyViewSet
from pactor.drafting.api import TemplateViewSet
from pactor.files import api as files
from pactor.identity import api as identity
from pactor.intake.api import RequestViewSet
from pactor.signatures import webhooks
from pactor.signatures.api import EnvelopeViewSet

# Conversor de caminho uuid: tipagem correta no OpenAPI e 404 para ids malformados.
router = SimpleRouter(trailing_slash=False, use_regex_path=False)
router.register("members", identity.MemberViewSet, basename="member")
router.register("invitations", identity.InvitationViewSet, basename="invitation")
router.register("departments", identity.DepartmentViewSet, basename="department")
router.register("teams", identity.TeamViewSet, basename="team")
router.register("audit-events", AuditEventViewSet, basename="audit-event")
router.register("counterparties", CounterpartyViewSet, basename="counterparty")
router.register("contracts", ContractViewSet, basename="contract")
router.register("workflows", WorkflowViewSet, basename="workflow")
router.register("approval-rounds", ApprovalRoundViewSet, basename="approval-round")
router.register("delegations", DelegationViewSet, basename="delegation")
router.register("envelopes", EnvelopeViewSet, basename="envelope")
router.register("templates", TemplateViewSet, basename="template")
router.register("requests", RequestViewSet, basename="request")

api_v1 = [
    path("me", identity.MeView.as_view(), name="me"),
    path("session/organization", identity.ActiveOrganizationView.as_view(), name="active-org"),
    path("organizations", identity.OrganizationCreateView.as_view(), name="organization-create"),
    path("invitations/accept", identity.AcceptInvitationView.as_view(), name="invitation-accept"),
    path("subscription", identity.SubscriptionView.as_view(), name="subscription"),
    path("schema", SpectacularAPIView.as_view(), name="api-schema"),
    path(
        "contracts/<uuid:contract_id>/lifecycle",
        lifecycle_api.LifecycleView.as_view(),
        name="contract-lifecycle",
    ),
    path(
        "contracts/<uuid:contract_id>/lifecycle/<str:operation>",
        lifecycle_api.LifecycleActionView.as_view(),
        name="contract-lifecycle-action",
    ),
    path(
        "contracts/<uuid:contract_id>/amendment-changes",
        lifecycle_api.AmendmentChangesView.as_view(),
        name="contract-amendment-changes",
    ),
    path("files", files.FileUploadView.as_view(), name="file-upload"),
    path("files/<uuid:file_id>", files.FileDetailView.as_view(), name="file-detail"),
    path("files/<uuid:file_id>/content", files.FileContentView.as_view(), name="file-content"),
    path("files/<uuid:file_id>/preview", files.FilePreviewView.as_view(), name="file-preview"),
    path(
        "contracts/<uuid:contract_id>/attachments",
        files.ContractAttachmentsView.as_view(),
        name="contract-attachments",
    ),
    path(
        "contracts/<uuid:contract_id>/attachments/<uuid:attachment_id>",
        files.ContractAttachmentDetailView.as_view(),
        name="contract-attachment-detail",
    ),
    path(
        "contracts/<uuid:contract_id>/conditions",
        lifecycle_api.ConditionsView.as_view(),
        name="contract-conditions",
    ),
    *router.urls,
]

urlpatterns = [
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("_allauth/", include("allauth.headless.urls")),
    path("webhooks/signatures/<str:provider>", webhooks.receive, name="signature-webhook"),
    path("api/v1/", include(api_v1)),
    path(settings.PACTOR_ADMIN_PATH, admin.site.urls),
]
