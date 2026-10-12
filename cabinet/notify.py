"""Письма лейблу о действиях артистов."""

import logging
import os
import threading

from django.conf import settings
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone

log = logging.getLogger(__name__)

MAX_ATTACH = 20 * 1024 * 1024


def enabled():
    return bool(settings.NOTIFY_EMAILS and settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD) \
        or (settings.NOTIFY_EMAILS and "locmem" in settings.EMAIL_BACKEND)


def _send(msg):
    try:
        msg.send(fail_silently=False)
        log.info("Письмо отправлено: %s", msg.subject)
    except Exception:  # noqa: BLE001 — сайт не должен падать из-за почты
        log.exception("Не удалось отправить письмо: %s", msg.subject)


def send(subject, body, attachments=()):
    if not enabled():
        return False
    msg = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, settings.NOTIFY_EMAILS)
    for name, data, ctype in attachments:
        msg.attach(name, data, ctype)
    if settings.NOTIFY_ASYNC:
        threading.Thread(target=_send, args=(msg,), daemon=False).start()
    else:
        _send(msg)
    return True


def signed_act_uploaded(report, request):
    """Артист загрузил подписанный акт — пишем лейблу, файл прикладываем."""
    if not enabled():
        return False
    artist = report.artist
    when = timezone.localtime(report.signed_uploaded_at or timezone.now()).strftime("%d.%m.%Y %H:%M")
    admin_url = request.build_absolute_uri(reverse("admin:cabinet_report_change", args=[report.pk]))
    list_url = request.build_absolute_uri(reverse("admin:cabinet_report_changelist") + "?act_status__exact=review")
    body = (
        f"Артист {artist.name} загрузил подписанный акт за {report.period_short}.\n"
        f"Время загрузки: {when}\n\n"
        f"Открыть отчёт в админке: {admin_url}\n"
        f"Все акты на проверке: {list_url}\n\n"
        "Если акт в порядке — в списке отчётов выберите его и действие «Принять подписанные акты, загруженные артистами».\n"
    )
    attachments = []
    f = report.signed_act_file
    if f and f.size <= MAX_ATTACH:
        ext = os.path.splitext(f.name)[1].lower() or ".pdf"
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in artist.name).strip().replace(" ", "_")
        name = f"Акт_{safe}_{report.year}_Q{report.quarter}{ext}"
        import mimetypes
        with f.open("rb") as fh:
            attachments.append((name, fh.read(), mimetypes.guess_type(name)[0] or "application/octet-stream"))
    elif f:
        body += "\nФайл больше 20 МБ, поэтому не приложен — скачайте его в админке.\n"
    return send(f"Подписанный акт: {artist.name}, {report.period_short}", body, attachments)
