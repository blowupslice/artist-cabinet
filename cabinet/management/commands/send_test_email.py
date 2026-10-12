from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Отправить тестовое письмо на адреса из NOTIFY_EMAILS"

    def handle(self, *args, **opts):
        if not settings.NOTIFY_EMAILS:
            raise CommandError("NOTIFY_EMAILS не задан")
        if not settings.EMAIL_HOST_PASSWORD:
            raise CommandError("EMAIL_HOST_PASSWORD не задан")
        msg = EmailMessage(
            "Проверка уведомлений личного кабинета",
            "Это тестовое письмо. Если вы его видите — уведомления о подписанных актах будут приходить сюда.",
            settings.DEFAULT_FROM_EMAIL, settings.NOTIFY_EMAILS,
        )
        try:
            msg.send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Не удалось отправить: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Тестовое письмо отправлено на " + ", ".join(settings.NOTIFY_EMAILS)))
