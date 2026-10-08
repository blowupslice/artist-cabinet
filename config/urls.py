from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from cabinet import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("finance/", views.finance, name="finance"),
    path("finance/<slug:slug>/", views.finance, name="finance_period"),
    path("stats/", views.stats, name="stats"),
    path("account/", views.account, name="account"),
    path("releases/", views.releases, name="releases"),
    path("releases/<int:pk>/cover/", views.release_cover, name="release_cover"),
    path("reports/<int:pk>/signed-act/upload/", views.upload_signed_act, name="upload_signed_act"),
    path("contracts/<int:pk>/download/", views.download_contract, name="download_contract"),
    path("reports/<int:pk>/summary.xlsx", views.export_summary, name="export_summary"),
    path("reports/<int:pk>/<str:kind>/", views.download_report_file, name="download_report_file"),
    path("view-as/stop/", views.stop_view_as, name="stop_view_as"),
    path("files/<path:path>", views.protected_file, name="protected_file"),
    path("staff/acts/<int:pk>.pdf", views.admin_act_pdf, name="admin_act_pdf"),
    path("healthz", views.healthz),
    path("admin/", admin.site.urls),
]
