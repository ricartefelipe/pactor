from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView
from rest_framework.routers import SimpleRouter

from pactor.audit.api import AuditEventViewSet
from pactor.core import health
from pactor.identity import api as identity

router = SimpleRouter(trailing_slash=False)
router.register("members", identity.MemberViewSet, basename="member")
router.register("invitations", identity.InvitationViewSet, basename="invitation")
router.register("departments", identity.DepartmentViewSet, basename="department")
router.register("teams", identity.TeamViewSet, basename="team")
router.register("audit-events", AuditEventViewSet, basename="audit-event")

api_v1 = [
    path("me", identity.MeView.as_view(), name="me"),
    path("session/organization", identity.ActiveOrganizationView.as_view(), name="active-org"),
    path("organizations", identity.OrganizationCreateView.as_view(), name="organization-create"),
    path("invitations/accept", identity.AcceptInvitationView.as_view(), name="invitation-accept"),
    path("subscription", identity.SubscriptionView.as_view(), name="subscription"),
    path("schema", SpectacularAPIView.as_view(), name="api-schema"),
    *router.urls,
]

urlpatterns = [
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("_allauth/", include("allauth.headless.urls")),
    path("api/v1/", include(api_v1)),
    path(settings.PACTOR_ADMIN_PATH, admin.site.urls),
]
