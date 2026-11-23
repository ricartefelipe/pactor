import re

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView
from oauth2_provider import views as oauth_views
from rest_framework.routers import SimpleRouter

from pactor.approvals.api import ApprovalRoundViewSet, DelegationViewSet, WorkflowViewSet
from pactor.audit.api import AuditEventViewSet
from pactor.contracts import lifecycle_api
from pactor.contracts.api import ContractViewSet
from pactor.core import health, spa
from pactor.core.api import operations
from pactor.counterparties.api import CounterpartyViewSet
from pactor.deadlines import api as deadlines
from pactor.drafting.api import TemplateViewSet
from pactor.files import api as files
from pactor.finance import api as finance
from pactor.identity import api as identity
from pactor.intake.api import RequestViewSet
from pactor.integrations.api import IntegrationViewSet
from pactor.obligations import api as obligations
from pactor.search import api as search
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
router.register("integrations", IntegrationViewSet, basename="integration")

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
    path(
        "contracts/<uuid:contract_id>/finance",
        finance.FinanceSummaryView.as_view(),
        name="contract-finance",
    ),
    path(
        "contracts/<uuid:contract_id>/finance/installments",
        finance.InstallmentsView.as_view(),
        name="contract-installments",
    ),
    path(
        "contracts/<uuid:contract_id>/finance/milestones",
        finance.MilestonesView.as_view(),
        name="contract-milestones",
    ),
    path(
        "contracts/<uuid:contract_id>/finance/adjustments",
        finance.AdjustmentsView.as_view(),
        name="contract-adjustments",
    ),
    path(
        "installments/<uuid:installment_id>/confirm",
        finance.ConfirmView.as_view(),
        name="installment-confirm",
    ),
    path("indexes", finance.IndexValuesView.as_view(), name="index-values"),
    path("search", search.SearchView.as_view(), name="search"),
    path("search/suggest", search.SuggestView.as_view(), name="search-suggest"),
    path("reports/dashboard", search.DashboardView.as_view(), name="reports-dashboard"),
    path(
        "reports/contracts.csv", search.ContractsExportView.as_view(), name="reports-contracts-csv"
    ),
    path("operations/failures", operations.FailuresView.as_view(), name="operations-failures"),
    path(
        "operations/failures/<uuid:failure_id>/requeue",
        operations.RequeueFailureView.as_view(),
        name="operations-requeue",
    ),
    path(
        "operations/dead-events/requeue",
        operations.RequeueDeadEventsView.as_view(),
        name="operations-dead",
    ),
    path("calendar", deadlines.CalendarView.as_view(), name="calendar"),
    path("alerts", deadlines.AlertsView.as_view(), name="alerts"),
    path("holiday-calendars", deadlines.HolidayCalendarsView.as_view(), name="holiday-calendars"),
    path(
        "contracts/<uuid:contract_id>/deadlines",
        deadlines.ContractDeadlinesView.as_view(),
        name="contract-deadlines",
    ),
    path(
        "deadlines/<uuid:deadline_id>/complete",
        deadlines.DeadlineCompleteView.as_view(),
        name="deadline-complete",
    ),
    path(
        "contracts/<uuid:contract_id>/renewal",
        deadlines.RenewalView.as_view(),
        name="contract-renewal",
    ),
    path(
        "contracts/<uuid:contract_id>/renewal/decline",
        deadlines.NonRenewalView.as_view(),
        name="contract-non-renewal",
    ),
    path(
        "contracts/<uuid:contract_id>/obligations",
        obligations.ContractObligationsView.as_view(),
        name="contract-obligations",
    ),
    path(
        "obligations/<uuid:obligation_id>/reschedule",
        obligations.RescheduleView.as_view(),
        name="obligation-reschedule",
    ),
    path(
        "occurrences/<uuid:occurrence_id>/complete",
        obligations.OccurrenceCompleteView.as_view(),
        name="occurrence-complete",
    ),
    path(
        "occurrences/<uuid:occurrence_id>/review",
        obligations.OccurrenceReviewView.as_view(),
        name="occurrence-review",
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

_SERVICE_PREFIXES = "|".join(
    re.escape(prefix)
    for prefix in (
        "api",
        "_allauth",
        "static",
        "webhooks",
        "o",
        "health",
        "metrics",
        settings.PACTOR_ADMIN_PATH.strip("/"),
    )
)

urlpatterns = [
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("metrics", health.metrics, name="metrics"),
    path("_allauth/", include("allauth.headless.urls")),
    # Somente os endpoints de token do servidor OAuth; a gestão de aplicações é pela API do Pactor.
    path("o/token/", oauth_views.TokenView.as_view(), name="oauth-token"),
    path("o/revoke_token/", oauth_views.RevokeTokenView.as_view(), name="oauth-revoke"),
    path("webhooks/signatures/<str:provider>", webhooks.receive, name="signature-webhook"),
    path("api/v1/", include(api_v1)),
    path(settings.PACTOR_ADMIN_PATH, admin.site.urls),
    # Demais caminhos são rotas da aplicação web. Prefixos de serviço nunca caem
    # aqui: um endereço inexistente da API continua respondendo 404.
    re_path(
        rf"^(?!(?:{_SERVICE_PREFIXES})(?:/|$)).*$",
        spa.index,
        name="web-app",
    ),
]
