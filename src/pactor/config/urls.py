from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView

from pactor.core import health

urlpatterns = [
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("api/v1/schema", SpectacularAPIView.as_view(), name="api-schema"),
    path("admin/", admin.site.urls),
]
