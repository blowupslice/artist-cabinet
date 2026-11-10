import mimetypes
import os
import re
from decimal import Decimal
from io import BytesIO
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import BankDetailsForm, EmailLoginForm, PasswordChangeFormRu, SignedActForm
from .models import Artist, Contract, PlaylistPlacement, Release, Report, RoyaltyLine
from .stats import breakdown, country_name, pct_change, rate_per_1000, split


class LoginView(auth_views.LoginView):
    template_name = "cabinet/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True
    LOCK_SECONDS = 15 * 60

    def _key(self):
        email = (self.request.POST.get("username") or "").strip().lower()
        return f"login-fail:{email}"

    def post(self, request, *args, **kwargs):
        if cache.get(self._key(), 0) >= settings.LOGIN_MAX_FAILURES:
            form = self.get_form()
            form.errors.clear()
            form.add_error(None, "Слишком много неудачных попыток. Попробуйте через 15 минут.")
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key()
        cache.set(key, cache.get(key, 0) + 1, self.LOCK_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key())
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Какого артиста показываем
# ---------------------------------------------------------------------------

def get_artist(request):
    """Артист текущего пользователя. Администратор может открыть кабинет
    любого артиста (?as=<id>), чтобы увидеть его глазами артиста."""
    user = request.user
    if user.is_staff:
        as_id = request.GET.get("as")
        if as_id:
            request.session["view_as"] = as_id
        as_id = request.session.get("view_as")
        if as_id:
            artist = Artist.objects.filter(pk=as_id).select_related("user").first()
            if artist:
                return artist
    return Artist.objects.filter(user=user).select_related("user").first()


def artist_required(view):
    @login_required
    def wrapper(request, *args, **kwargs):
        artist = get_artist(request)
        if artist is None:
            if request.user.is_staff:
                return redirect("admin:cabinet_artist_changelist")
            return render(request, "cabinet/no_profile.html", status=403)
        request.artist = artist
        request.viewing_as = request.user.is_staff and artist.user_id != request.user.id
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


@login_required
def stop_view_as(request):
    request.session.pop("view_as", None)
    return redirect("admin:cabinet_artist_changelist")


def published_reports(artist):
    return artist.reports.filter(is_published=True).order_by("-year", "-quarter")


# ---------------------------------------------------------------------------
# Страницы
# ---------------------------------------------------------------------------

def _by_quantity(lines, field, limit=None):
    """Прослушивания по полю (площадка, страна, трек), по убыванию прослушиваний.
    Строки без прослушиваний, но с доходом (например, синхронизация) тоже показываем."""
    rows = [r for r in lines.values(field).annotate(q=Sum("quantity"), a=Sum("amount"))
            if (r["q"] or 0) > 0 or (r["a"] or 0) > 0]
    rows.sort(key=lambda r: (-(r["q"] or 0), -(r["a"] or 0)))
    total_q = sum(r["q"] or 0 for r in rows)
    total_a = sum(r["a"] or 0 for r in rows)
    by_q = total_q > 0
    top = (rows[0]["q"] if by_q else rows[0]["a"]) if rows else 0
    out = []
    for r in rows[:limit] if limit else rows:
        key = r[field]
        name = (country_name(key) if field == "country" else key) or "Не указано"
        v = (r["q"] or 0) if by_q else (r["a"] or 0)
        out.append({"key": key or "", "name": name, "quantity": r["q"] or 0, "amount": r["a"] or 0,
                    "share": float(v / (total_q if by_q else total_a) * 100) if (total_q if by_q else total_a) else 0,
                    "bar": float(v / top * 100) if top else 0})
    return out


def _period_options(reports):
    years = sorted({r.year for r in reports}, reverse=True)
    opts = [("all", "За всё время")]
    opts += [(r.slug, r.period_short) for r in reports]
    opts += [(str(y), f"{y} год") for y in years]
    return opts


def _select_period(reports, sel):
    if sel and sel.isdigit():
        chosen = [r for r in reports if r.year == int(sel)]
        if chosen:
            return sel, chosen, f"{sel} год"
    if sel and sel != "all":
        r = next((r for r in reports if r.slug == sel), None)
        if r:
            return sel, [r], r.period_label
    return "all", reports, "За всё время"


MUSIC_TABS = [("overview", "Обзор"), ("tracks", "Песни"), ("platforms", "Площадки"), ("countries", "Страны")]


def _todo(artist):
    from .acts import payout_overview
    payout = payout_overview(artist)
    todo = []
    if payout.report and payout.report.act_status == Report.ActStatus.TO_SIGN:
        todo.append(("act", payout.report))
    if not artist.has_bank_details:
        todo.append(("bank", None))
    return payout, todo


@artist_required
def dashboard(request):
    """«Музыка»: сколько слушают, где, откуда и какие песни."""
    artist = request.artist
    reports = list(published_reports(artist))
    payout, todo = _todo(artist)
    tab = request.GET.get("tab") if request.GET.get("tab") in dict(MUSIC_TABS) else "overview"
    ctx = {"artist": artist, "reports": reports, "nav": "music", "tab": tab, "tabs": MUSIC_TABS,
           "todo": todo, "payout": payout,
           "playlists": list(artist.playlist_placements.all()[:5])}
    if not reports:
        return render(request, "cabinet/music.html", ctx)

    sel, selected, title = _select_period(reports, request.GET.get("p"))
    base_lines = RoyaltyLine.objects.filter(report__in=selected)
    platforms_all = _by_quantity(base_lines, "platform")
    pl = request.GET.get("pl") or ""
    if pl and pl not in {p["key"] for p in platforms_all}:
        pl = ""
    lines = base_lines.filter(platform=pl) if pl else base_lines

    streams = lines.aggregate(q=Sum("quantity"))["q"] or 0
    countries = [c for c in _by_quantity(lines, "country") if c["key"]]
    tracks = _by_quantity(lines, "track")
    if tab == "tracks":
        per = {}
        for row in lines.values("track", "platform").annotate(q=Sum("quantity")).order_by("-q"):
            if row["q"]:
                per.setdefault(row["track"], []).append(row)
        for t in tracks:
            rows = per.get(t["key"], [])
            t["platforms"] = [{"name": r["platform"] or "Другие", "share": r["q"] / t["quantity"] * 100}
                              for r in rows[:4]] if t["quantity"] else []

    # Столбики: последние 4 квартала (прослушивания, с учётом площадки)
    last4 = list(reversed(reports[:4]))
    q_lines = RoyaltyLine.objects.filter(report__in=last4)
    if pl:
        q_lines = q_lines.filter(platform=pl)
    per_report = {r["report"]: r["q"] or 0 for r in q_lines.values("report").annotate(q=Sum("quantity"))}
    top_q = max(per_report.values(), default=0) or 1
    quarters = [{"report": r, "quantity": per_report.get(r.pk, 0),
                 "bar": per_report.get(r.pk, 0) / top_q * 100,
                 "selected": sel == r.slug} for r in last4]
    delta = None
    if len(quarters) >= 2 and quarters[-2]["quantity"]:
        delta = pct_change(quarters[-1]["quantity"], quarters[-2]["quantity"])

    tile_total = sum(p["quantity"] for p in platforms_all)
    ctx.update({
        "sel": sel, "title": title, "options": _period_options(reports), "pl": pl,
        "streams": streams, "platforms": platforms_all, "tile_total": tile_total,
        "platform_tiles": platforms_all[:5], "more_platforms": max(len(platforms_all) - 5, 0),
        "countries": countries, "tracks": tracks, "quarters": quarters, "delta": delta,
        "track_query": request.GET.get("q", ""),
    })
    return render(request, "cabinet/music.html", ctx)


@artist_required
def finance(request, slug=None):
    artist = request.artist
    reports = list(published_reports(artist))
    if not reports:
        payout, todo = _todo(artist)
        return render(request, "cabinet/finance_list.html", {"artist": artist, "reports": [], "nav": "finance",
                                                             "payout": payout, "todo": todo, "tab": "reports"})

    if not slug:
        return _finance_list(request, artist, reports)
    report = next((r for r in reports if r.slug == slug), None)
    if report is None:
        raise Http404("Отчёт не найден")

    lines = report.lines.all()
    by_platform = list(
        lines.values("platform").annotate(amount=Sum("amount"), quantity=Sum("quantity")).order_by("-amount")
    )
    by_track = list(
        lines.values("track").annotate(amount=Sum("amount"), quantity=Sum("quantity")).order_by("-amount")
    )
    pairs = {}
    for row in lines.values("track", "platform").annotate(amount=Sum("amount"), quantity=Sum("quantity")).order_by("-amount"):
        pairs.setdefault(row["track"], []).append(row)

    total = report.total_amount or Decimal(0)
    top = by_platform[0]["amount"] if by_platform else 1
    for p in by_platform:
        p["share"] = float(p["amount"] / total * 100) if total else 0
        p["bar"] = float(p["amount"] / top * 100) if top else 0
        p["rate"] = rate_per_1000(p["amount"], p["quantity"])
    for t in by_track:
        t["share"] = float(t["amount"] / total * 100) if total else 0
        t["platforms"] = pairs.get(t["track"], [])

    idx = reports.index(report)
    prev_report = reports[idx + 1] if idx + 1 < len(reports) else None
    delta = None
    if prev_report and prev_report.total_amount:
        delta = float((report.total_amount - prev_report.total_amount) / prev_report.total_amount * 100)

    countries = breakdown(lines, "country")
    leaders = {
        "track": by_track[0] if by_track else None,
        "platform": by_platform[0] if by_platform else None,
        "country": countries[0] if countries and any(c["key"] for c in countries) else None,
    }
    from .acts import compute
    act = compute(report)

    return render(request, "cabinet/finance.html", {
        "artist": artist,
        "reports": reports,
        "report": report,
        "leaders": leaders,
        "rights": split(lines, "rights_type"),
        "usage": split(lines, "usage_type"),
        "act": act,
        "act_form": SignedActForm(),
        "prev_report": prev_report,
        "delta": delta,
        "by_platform": by_platform,
        "by_track": by_track,
        "nav": "finance",
        "todo": _todo(artist)[1],
    })


def _finance_list(request, artist, reports):
    payout, todo = _todo(artist)
    tab = "payouts" if request.GET.get("tab") == "payouts" else "reports"
    years = sorted({r.year for r in reports}, reverse=True)
    year = request.GET.get("y")
    shown = [r for r in reports if str(r.year) == year] if year and year.isdigit() else reports
    last8 = list(reversed(reports[:8]))
    top = max((r.total_amount for r in last8), default=0) or 1
    bars = [{"report": r, "bar": float(r.total_amount / top * 100)} for r in last8]
    return render(request, "cabinet/finance_list.html", {
        "artist": artist, "reports": reports, "shown": shown, "years": years, "year": year or "",
        "bars": bars, "payout": payout, "todo": todo, "tab": tab, "nav": "finance",
    })


@artist_required
def stats(request):
    """Старый адрес «Статистики» — теперь это раздел «Музыка»."""
    from django.urls import reverse
    url = reverse("dashboard")
    p = request.GET.get("p")
    return redirect(f"{url}?p={quote(p)}" if p else url)


@artist_required
def account(request):
    artist = request.artist
    which = request.POST.get("form") if request.method == "POST" else None
    form = PasswordChangeFormRu(request.user, request.POST if which == "password" else None)
    bank_form = BankDetailsForm(request.POST if which == "bank" else None, instance=artist)
    if which and request.viewing_as:
        messages.error(request, "В режиме просмотра за артиста сохранять нельзя")
        return redirect("account")
    if which == "password" and form.is_valid():
        form.save()
        update_session_auth_hash(request, request.user)
        messages.success(request, "Пароль изменён")
        return redirect("account")
    if which == "bank" and bank_form.is_valid():
        a = bank_form.save(commit=False)
        a.bank_updated_at = timezone.now()
        a.save()
        messages.success(request, "Реквизиты сохранены. Выплаты придут на этот счёт.")
        nxt = request.GET.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "account")
    return render(request, "cabinet/profile.html", {
        "artist": artist, "form": form, "bank_form": bank_form, "nav": "profile",
        "contracts": artist.contracts.all(), "todo": _todo(artist)[1],
        "open_bank": which == "bank" or request.GET.get("tab") == "bank" or not artist.has_bank_details,
    })


@artist_required
@require_POST
def upload_signed_act(request, pk):
    report = get_object_or_404(Report, pk=pk, artist=request.artist, is_published=True)
    back = redirect("finance_period", report.slug)
    if request.viewing_as:
        messages.error(request, "В режиме просмотра за артиста загружать файлы нельзя")
        return back
    if report.act_status == Report.ActStatus.SIGNED:
        messages.error(request, "Этот акт уже принят — загружать заново не нужно")
        return back
    form = SignedActForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, " ".join(form.errors.get("file", ["Не удалось загрузить файл"])))
        return back
    if report.signed_act_file:
        report.signed_act_file.delete(save=False)
    report.signed_act_file = form.cleaned_data["file"]
    report.act_status = Report.ActStatus.REVIEW
    report.signed_uploaded_at = timezone.now()
    report._from_artist = True
    report.save()
    from .notify import signed_act_uploaded
    signed_act_uploaded(report, request)
    messages.success(request, "Акт получили, спасибо! Менеджер проверит его и отправит выплату.")
    return back


@artist_required
def releases(request):
    artist = request.artist
    items = list(artist.releases.filter(is_published=True))
    upcoming = sorted([r for r in items if r.is_upcoming], key=lambda r: r.release_date)
    past = [r for r in items if not r.is_upcoming]
    return render(request, "cabinet/releases.html", {
        "artist": artist, "upcoming": upcoming, "past": past, "nav": "releases",
    })


# ---------------------------------------------------------------------------
# Скачивание файлов (только своих)
# ---------------------------------------------------------------------------

def _safe_name(text):
    text = re.sub(r"[^\w\-. ]+", "", text, flags=re.UNICODE).strip().replace(" ", "_")
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "file"


def _send(field, download_name):
    if not field:
        raise Http404("Файл не загружен")
    try:
        fh = field.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Файл не найден") from exc
    ext = os.path.splitext(field.name)[1].lower()
    ctype = mimetypes.guess_type(field.name)[0] or "application/octet-stream"
    return FileResponse(fh, as_attachment=True, filename=f"{download_name}{ext}", content_type=ctype)


@artist_required
@require_GET
def download_contract(request, pk):
    contract = get_object_or_404(Contract, pk=pk, artist=request.artist)
    return _send(contract.file, _safe_name(f"{contract}"))


@artist_required
@require_GET
def download_report_file(request, pk, kind):
    report = get_object_or_404(Report, pk=pk, artist=request.artist, is_published=True)
    base = _safe_name(f"{request.artist.name}_{report.period_short}")
    if kind == "detail":
        return _send(report.detail_file, f"Отчет_{base}")
    if kind == "act":
        if report.act_file:
            return _send(report.act_file, f"Акт_{base}")
        return _act_pdf_response(report, f"Акт_{base}")
    if kind == "signed-act":
        return _send(report.signed_act_file, f"Акт_подписанный_{base}")
    raise Http404


@artist_required
@require_GET
def export_summary(request, pk):
    """Сводка по кварталу в Excel: по трекам, по площадкам и трек × площадка."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    report = get_object_or_404(Report, pk=pk, artist=request.artist, is_published=True)
    lines = report.lines.all()
    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F2937")

    def sheet(ws, title, headers, rows, widths):
        ws.title = title
        ws.append([f"{request.artist.name} — {report.period_label}"])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append([])
        ws.append(headers)
        for c in ws[3]:
            c.font, c.fill = head_font, head_fill
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = w
        for row in ws.iter_rows(min_row=4):
            row[-1].number_format = "#,##0.00"
        ws.freeze_panes = "A4"

    cur = report.currency
    sheet(wb.active, "По трекам", ["Трек", "Прослушивания", f"Сумма, {cur}"],
          [(r["track"], r["q"], float(r["a"])) for r in
           lines.values("track").annotate(q=Sum("quantity"), a=Sum("amount")).order_by("-a")], [50, 16, 16])
    sheet(wb.create_sheet(), "По площадкам", ["Площадка", "Прослушивания", f"Сумма, {cur}"],
          [(r["platform"], r["q"], float(r["a"])) for r in
           lines.values("platform").annotate(q=Sum("quantity"), a=Sum("amount")).order_by("-a")], [30, 16, 16])
    sheet(wb.create_sheet(), "Трек × площадка", ["Трек", "Площадка", "Прослушивания", f"Сумма, {cur}"],
          [(r["track"], r["platform"], r["q"], float(r["a"])) for r in
           lines.values("track", "platform").annotate(q=Sum("quantity"), a=Sum("amount")).order_by("track", "-a")],
          [50, 30, 16, 16])

    buf = BytesIO()
    wb.save(buf)
    name = f"Сводка_{_safe_name(request.artist.name)}_{report.period_short.replace(' ', '_')}.xlsx"
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    return resp


def _act_pdf_response(report, name):
    from .acts import render_pdf
    pdf = render_pdf(report)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name + '.pdf')}"
    return resp


@staff_member_required
def admin_act_pdf(request, pk):
    report = get_object_or_404(Report, pk=pk)
    return _act_pdf_response(report, _safe_name(f"Акт_{report.artist.name}_{report.period_short}"))


@artist_required
@require_GET
def release_cover(request, pk):
    release = get_object_or_404(Release, pk=pk, artist=request.artist, is_published=True)
    if not release.cover:
        raise Http404
    try:
        fh = release.cover.open("rb")
    except FileNotFoundError as exc:
        raise Http404 from exc
    ctype = mimetypes.guess_type(release.cover.name)[0] or "image/jpeg"
    resp = FileResponse(fh, content_type=ctype)
    resp["Cache-Control"] = "private, max-age=86400"
    return resp


@staff_member_required
def protected_file(request, path):
    """Ссылки на файлы в админке. Доступны только администраторам."""
    from django.core.files.storage import default_storage
    if ".." in path or not default_storage.exists(path):
        raise Http404
    return FileResponse(default_storage.open(path, "rb"), as_attachment=False,
                        filename=os.path.basename(path))


def healthz(request):
    return HttpResponse("ok")
