from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django import forms
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.contrib.auth.forms import UserCreationForm, UserChangeForm

from .forms import ArtistAdminForm, ReportAdminForm
from .importer import ImportError_, parse_report
from .models import Artist, Contract, LabelSettings, Report, User

admin.site.site_header = f"{settings.SITE_NAME} — управление"
admin.site.site_title = settings.SITE_NAME
admin.site.index_title = "Артисты, договоры и отчёты"


def money(value, currency=""):
    s = f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")
    return f"{s} {currency}".strip()


class ContractInline(admin.TabularInline):
    model = Contract
    extra = 1
    fields = ["title", "number", "signed_date", "file"]


def act_link(obj):
    if not obj or not obj.pk:
        return "Появится после сохранения"
    url = reverse("admin_act_pdf", args=[obj.pk])
    return format_html('<a href="{}" target="_blank">Скачать акт-отчёт (PDF) ↓</a>', url)


def report_summary(obj):
    if not obj or not obj.pk:
        return "Появится после сохранения"
    return (f"Доход лейбла {money(obj.total_gross, obj.currency)} · артисту {money(obj.total_amount, obj.currency)} "
            f"({obj.rate.normalize():f}%) · {obj.total_quantity:,} прослушиваний · строк: {obj.lines_count}").replace(",", " ")


class ReportInline(admin.StackedInline):
    model = Report
    form = ReportAdminForm
    extra = 0
    fields = [
        ("year", "quarter", "currency", "is_published"),
        "detail_file",
        ("act_file", "signed_act_file"),
        ("paid_amount", "act_status"),
        "comment",
        "summary",
        "act",
    ]
    readonly_fields = ["summary", "act"]
    show_change_link = True

    @admin.display(description="Итог по файлу")
    def summary(self, obj):
        return report_summary(obj)

    @admin.display(description="Акт")
    def act(self, obj):
        return act_link(obj)


class BulkUploadForm(forms.Form):
    file = forms.FileField(label="Отчёт дистрибьютора (.xlsx или .csv)",
                           help_text="Файл за любой срок. Строки разложатся по кварталам по колонке периода.")
    publish = forms.BooleanField(label="Сразу показать новые отчёты артисту", required=False, initial=True)


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    form = ArtistAdminForm
    list_display = ["name", "login_email", "contracts_n", "reports_n", "last_report", "access", "open_as_artist"]
    search_fields = ["name", "legal_name", "user__email"]
    inlines = [ContractInline, ReportInline]
    readonly_fields = ["bulk_upload"]

    def get_fieldsets(self, request, obj=None):
        sets = [
            ("Вход в кабинет", {"fields": ["email", "password", "is_active"]}),
            ("Профиль", {"fields": ["name", "legal_name", "phone", "royalty_rate", "notes"]}),
            ("Реквизиты для акта", {"fields": ["address", "inn"]}),
        ]
        if obj:
            sets.insert(1, ("Отчёты", {"fields": ["bulk_upload"]}))
        return sets

    def get_readonly_fields(self, request, obj=None):
        return ["bulk_upload"] if obj else []

    @admin.display(description="Общий отчёт")
    def bulk_upload(self, obj):
        url = reverse("admin:cabinet_artist_bulk_upload", args=[obj.pk])
        return format_html('<a class="button" href="{}">Загрузить отчёт за несколько кварталов</a>'
                           '<div class="help">Один файл дистрибьютора — кабинет сам разложит его по кварталам '
                           'и посчитает долю артиста.</div>', url)

    def save_model(self, request, obj, form, change):
        form.save_user(obj)
        super().save_model(request, obj, form, change)
        pwd = getattr(form, "generated_password", None)
        if pwd:
            self.message_user(request, format_html(
                "Пароль для входа артиста <b>{}</b>: <code style='font-size:15px'>{}</code> — "
                "скопируйте и отправьте артисту. Повторно он не показывается.", obj.user.email, pwd),
                messages.WARNING)

    def get_urls(self):
        return [
            path("<int:pk>/bulk-upload/", self.admin_site.admin_view(self.bulk_upload_view),
                 name="cabinet_artist_bulk_upload"),
        ] + super().get_urls()

    def bulk_upload_view(self, request, pk):
        from .bulk import import_by_quarters
        artist = get_object_or_404(Artist, pk=pk)
        form = BulkUploadForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            try:
                results, bad = import_by_quarters(artist, form.cleaned_data["file"], form.cleaned_data["publish"])
            except ImportError_ as exc:
                form.add_error("file", str(exc))
            else:
                parts = [f"{r.period_short}: артисту {money(r.total_amount)} ₽" + (" (новый)" if c else " (обновлён)")
                         for r, c in results]
                self.message_user(request, f"Загружено кварталов: {len(results)}. " + "; ".join(parts), messages.SUCCESS)
                if bad:
                    self.message_user(request, f"Строк без понятного периода пропущено: {bad}", messages.WARNING)
                return redirect("admin:cabinet_artist_change", artist.pk)
        context = dict(self.admin_site.each_context(request), title=f"Общий отчёт — {artist.name}",
                       form=form, artist=artist, opts=self.model._meta)
        return render(request, "admin/cabinet/bulk_upload.html", context)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user").annotate(
            _contracts=Count("contracts", distinct=True),
            _reports=Count("reports", distinct=True),
            _last=Max("reports__year"),
        )

    @admin.display(description="Email (логин)", ordering="user__email")
    def login_email(self, obj):
        return obj.user.email

    @admin.display(description="Договоров", ordering="_contracts")
    def contracts_n(self, obj):
        return obj._contracts

    @admin.display(description="Отчётов", ordering="_reports")
    def reports_n(self, obj):
        return obj._reports

    @admin.display(description="Последний отчёт")
    def last_report(self, obj):
        r = obj.reports.first()
        return r.period_short if r else "—"

    @admin.display(description="Доступ", boolean=True)
    def access(self, obj):
        return obj.user.is_active

    @admin.display(description="")
    def open_as_artist(self, obj):
        url = reverse("dashboard") + f"?as={obj.pk}"
        return format_html('<a href="{}" target="_blank">Кабинет глазами артиста ↗</a>', url)


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    form = ReportAdminForm
    list_display = ["artist", "period", "total", "total_quantity", "act_status", "is_published", "updated_at"]
    list_filter = ["year", "quarter", "act_status", "is_published", "artist"]
    search_fields = ["artist__name", "artist__user__email"]
    autocomplete_fields = ["artist"]
    list_editable = ["is_published"]
    readonly_fields = ["total_gross", "total_amount", "total_quantity", "lines_count", "top_tracks", "act"]
    actions = ["reimport", "mark_signed"]
    fieldsets = [
        (None, {"fields": ["artist", ("year", "quarter", "currency"), "royalty_rate", "is_published"]}),
        ("Файлы", {"fields": ["detail_file", "signed_act_file", "act_status"]}),
        ("Акт-отчёт", {"fields": ["act", "act_date", "paid_amount", "act_file"],
                       "description": "Акт формируется автоматически по данным отчёта. Загрузите свой файл "
                                      "в «Акт для подписания», только если хотите заменить автоматический."}),
        ("Для артиста", {"fields": ["comment"]}),
        ("Что получилось из файла", {"fields": ["total_gross", "total_amount", "total_quantity", "lines_count", "top_tracks"]}),
    ]

    @admin.display(description="Акт")
    def act(self, obj):
        return act_link(obj)

    @admin.display(description="Период", ordering="year")
    def period(self, obj):
        return obj.period_short

    @admin.display(description="Сумма", ordering="total_amount")
    def total(self, obj):
        return money(obj.total_amount, obj.currency)

    @admin.display(description="Топ треков")
    def top_tracks(self, obj):
        if not obj.pk:
            return "—"
        from django.db.models import Sum
        rows = obj.lines.values("track").annotate(s=Sum("amount")).order_by("-s")[:10]
        if not rows:
            return "—"
        return format_html_join(
            mark_safe("<br>"), "{} — {}", ((r["track"], money(r["s"], obj.currency)) for r in rows)
        )

    @admin.action(description="Перечитать выбранные отчёты из файлов")
    def reimport(self, request, queryset):
        ok = 0
        for report in queryset:
            try:
                with report.detail_file.open("rb") as f:
                    report.replace_lines(parse_report(f))
                ok += 1
            except (ImportError_, FileNotFoundError) as exc:
                self.message_user(request, f"{report}: {exc}", messages.ERROR)
        if ok:
            self.message_user(request, f"Перечитано отчётов: {ok}", messages.SUCCESS)

    @admin.action(description="Отметить акты как подписанные")
    def mark_signed(self, request, queryset):
        n = queryset.exclude(act_file="").update(act_status=Report.ActStatus.SIGNED)
        self.message_user(request, f"Отмечено: {n}", messages.SUCCESS)


@admin.register(LabelSettings)
class LabelSettingsAdmin(admin.ModelAdmin):
    fields = ["name", "address", "inn", "ogrn", "signer", "payout_threshold"]

    def has_add_permission(self, request):
        return not LabelSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = LabelSettings.get()
        return redirect("admin:cabinet_labelsettings_change", obj.pk)


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ["title", "number", "artist", "signed_date", "uploaded_at"]
    list_filter = ["artist"]
    search_fields = ["title", "number", "artist__name"]
    autocomplete_fields = ["artist"]


class EmailUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email",)


class EmailUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = EmailUserCreationForm
    form = EmailUserChangeForm
    ordering = ["email"]
    list_display = ["email", "first_name", "is_staff", "is_active", "last_login"]
    search_fields = ["email", "first_name", "last_name"]
    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Данные", {"fields": ["first_name", "last_name"]}),
        ("Права", {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]}),
        ("Даты", {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [(None, {"classes": ["wide"], "fields": ["email", "password1", "password2", "is_staff"]})]
