"""
Демо-данные, чтобы посмотреть, как выглядит кабинет:

    python manage.py seed_demo

Создаёт администратора admin@example.com и артиста demo@example.com
(пароль у обоих: demo-pass-2026) с договорами и четырьмя кварталами отчётов.
На рабочем сервере запускать не нужно.
"""

import random
from datetime import date
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from openpyxl import Workbook

from cabinet.importer import parse_report
from cabinet.models import Artist, Contract, Report, User

PASSWORD = "demo-pass-2026"
TRACKS = [
    ("Северный ветер", "RUA1A2400101"), ("Неон", "RUA1A2400102"), ("Пока горит свет", "RUA1A2400103"),
    ("Тише", "RUA1A2400104"), ("Мосты", "RUA1A2400105"), ("Лето в городе", "RUA1A2400106"),
    ("Не отпускай", "RUA1A2400107"),
]
PLATFORMS = {
    "Яндекс Музыка": 0.42, "VK Музыка": 0.24, "Apple Music": 0.1, "Spotify": 0.05,
    "YouTube Music": 0.07, "Звук": 0.08, "МТС Музыка": 0.03, "Deezer": 0.01,
}
RATE = {"Яндекс Музыка": 0.32, "VK Музыка": 0.21, "Apple Music": 0.55, "Spotify": 0.28,
        "YouTube Music": 0.12, "Звук": 0.3, "МТС Музыка": 0.25, "Deezer": 0.33}


def make_pdf(title, lines):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
        pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setFont("DejaVu", 16)
        c.drawString(60, 780, title)
        c.setFont("DejaVu", 11)
        y = 750
        for line in lines:
            c.drawString(60, y, line)
            y -= 18
        c.save()
        return buf.getvalue(), ".pdf"
    except Exception:  # noqa: BLE001
        return ("\n".join([title, *lines])).encode("utf-8"), ".txt"


def make_distributor_xlsx(artist_name, year, quarter, growth, rnd):
    wb = Workbook()
    ws = wb.active
    ws.title = "Отчёт"
    ws.append([f"Отчёт о начислениях за {quarter} квартал {year} г."])
    ws.append([])
    ws.append(["Месяц", "Исполнитель", "Название трека", "ISRC", "Площадка", "Страна", "Количество", "Сумма, руб"])
    months = [(quarter - 1) * 3 + i for i in (1, 2, 3)]
    for m in months:
        for ti, (track, isrc) in enumerate(TRACKS):
            popularity = (1.0 / (ti + 1.3)) * growth
            for platform, share in PLATFORMS.items():
                for country, cshare in (("RU", 0.85), ("KZ", 0.1), ("BY", 0.05)):
                    streams = int(52000 * popularity * share * cshare * rnd.uniform(0.75, 1.25))
                    if streams <= 0:
                        continue
                    amount = round(streams * RATE[platform] * rnd.uniform(0.9, 1.1), 4)
                    ws.append([f"{year}-{m:02d}", artist_name, track, isrc, platform, country, streams, amount])
    ws.append([])
    ws.append(["Итого", "", "", "", "", "", "", ""])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


class Command(BaseCommand):
    help = "Создать демо-данные для просмотра кабинета"

    def handle(self, *args, **options):
        rnd = random.Random(7)
        if not User.objects.filter(email="admin@example.com").exists():
            User.objects.create_superuser("admin@example.com", PASSWORD)

        user = User.objects.filter(email="demo@example.com").first()
        if user:
            user.delete()
        user = User.objects.create_user("demo@example.com", PASSWORD, first_name="Лиза Север")
        artist = Artist.objects.create(user=user, name="Лиза Север", legal_name="Северова Елизавета Андреевна",
                                       phone="+7 900 000-00-00")

        for title, number, d in [
            ("Лицензионный договор", "ЛД-2024/017", date(2024, 11, 12)),
            ("Дополнительное соглашение № 1", "ЛД-2024/017-1", date(2025, 6, 3)),
        ]:
            data, ext = make_pdf(title, [f"№ {number} от {d:%d.%m.%Y}", f"Артист: {artist.legal_name}", "Демо-документ"])
            c = Contract(artist=artist, title=title, number=number, signed_date=d)
            c.file.save(f"{number}{ext}", ContentFile(data), save=False)
            c.save()

        periods = [(2025, 3, 0.8), (2025, 4, 0.95), (2026, 1, 1.05), (2026, 2, 1.25)]
        for i, (year, q, growth) in enumerate(periods):
            xlsx = make_distributor_xlsx(artist.name, year, q, growth, rnd)
            r = Report(artist=artist, year=year, quarter=q)
            f = ContentFile(xlsx, name=f"report_{year}_Q{q}.xlsx")
            r._parsed_rows = parse_report(f)
            r.detail_file.save(f.name, f, save=False)
            act, ext = make_pdf(f"Акт за {q} квартал {year} г.", [f"Исполнитель: {artist.legal_name}", "Демо-документ"])
            r.act_file.save(f"act_{year}_Q{q}{ext}", ContentFile(act), save=False)
            if i < len(periods) - 1:
                r.signed_act_file.save(f"signed_act_{year}_Q{q}{ext}", ContentFile(act), save=False)
            if i == len(periods) - 1:
                r.comment = "Выплата за II квартал запланирована до 25 июля. Не забудьте вернуть подписанный акт."
            r.save()

        self.stdout.write(self.style.SUCCESS(
            f"Готово. Админка: admin@example.com / {PASSWORD}. Артист: demo@example.com / {PASSWORD}"))
