import shutil
import tempfile
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from .importer import ImportError_, parse_number, parse_report
from .models import Artist, Contract, Report, User

TMP = tempfile.mkdtemp()


def xlsx(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = BytesIO()
    wb.save(buf)
    return SimpleUploadedFile("r.xlsx", buf.getvalue())


class ImporterTests(TestCase):
    def test_numbers(self):
        self.assertEqual(parse_number("1 234,56"), Decimal("1234.56"))
        self.assertEqual(parse_number("1,234.56"), Decimal("1234.56"))
        self.assertEqual(parse_number("1.234,56 ₽"), Decimal("1234.56"))
        self.assertIsNone(parse_number(""))

    def test_xlsx_with_title_rows_and_totals(self):
        f = xlsx([
            ["Отчёт за квартал"], [],
            ["Название трека", "Площадка", "Кол-во", "Сумма, руб"],
            ["Трек А", "Яндекс Музыка", 100, 10.5],
            ["Трек А", "Яндекс Музыка", 50, "4,5"],   # суммируется
            ["Трек Б", "VK Музыка", 10, 1],
            ["Итого", "", 160, 16],                     # пропускается
        ])
        rows = parse_report(f)
        self.assertEqual(len(rows), 2)
        a = next(r for r in rows if r["track"] == "Трек А")
        self.assertEqual(a["amount"], Decimal("15"))
        self.assertEqual(a["quantity"], 150)

    def test_english_csv_cp1251_semicolon(self):
        data = "Track Title;Store;Streams;Net Revenue\nSong;Spotify;1000;3,20\n".encode("cp1251")
        rows = parse_report(SimpleUploadedFile("r.csv", data))
        self.assertEqual(rows[0]["platform"], "Spotify")
        self.assertEqual(rows[0]["amount"], Decimal("3.2"))

    def test_unknown_columns(self):
        with self.assertRaises(ImportError_):
            parse_report(xlsx([["foo", "bar"], [1, 2]]))


@override_settings(MEDIA_ROOT=TMP)
class AccessTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def make_artist(self, email, name):
        u = User.objects.create_user(email, "Pass-word-123")
        a = Artist.objects.create(user=u, name=name)
        c = Contract(artist=a, title="Договор")
        c.file.save("c.pdf", ContentFile(b"%PDF"), save=False)
        c.save()
        r = Report(artist=a, year=2026, quarter=2)
        r._parsed_rows = parse_report(xlsx([["Трек", "Площадка", "Сумма"], ["X", "Звук", 5]]))
        r.detail_file.save("d.xlsx", ContentFile(b"x"), save=False)
        r.act_file.save("a.pdf", ContentFile(b"%PDF"), save=False)
        r.save()
        return a, c, r

    def setUp(self):
        self.a1, self.c1, self.r1 = self.make_artist("one@example.com", "Один")
        self.a2, self.c2, self.r2 = self.make_artist("two@example.com", "Два")

    def test_login_by_email_case_insensitive(self):
        resp = self.client.post(reverse("login"), {"username": "ONE@Example.com", "password": "Pass-word-123"})
        self.assertRedirects(resp, reverse("dashboard"))

    def test_bruteforce_lock(self):
        from django.core.cache import cache
        cache.clear()
        for _ in range(10):
            self.client.post(reverse("login"), {"username": "one@example.com", "password": "wrong"})
        resp = self.client.post(reverse("login"), {"username": "one@example.com", "password": "Pass-word-123"})
        self.assertContains(resp, "Слишком много")
        cache.clear()

    def test_report_totals_and_status(self):
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.total_amount, Decimal("5.00"))
        self.assertEqual(self.r1.act_status, Report.ActStatus.TO_SIGN)

    def test_pages(self):
        self.client.login(username="one@example.com", password="Pass-word-123")
        for url in [reverse("dashboard"), reverse("finance"), reverse("finance_period", args=["2026-q2"]),
                    reverse("account"), reverse("export_summary", args=[self.r1.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_own_files_ok_foreign_files_404(self):
        self.client.login(username="one@example.com", password="Pass-word-123")
        self.assertEqual(self.client.get(reverse("download_contract", args=[self.c1.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("download_report_file", args=[self.r1.pk, "act"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("download_contract", args=[self.c2.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("download_report_file", args=[self.r2.pk, "detail"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("export_summary", args=[self.r2.pk])).status_code, 404)

    def test_raw_files_only_for_staff(self):
        self.client.login(username="one@example.com", password="Pass-word-123")
        resp = self.client.get("/files/" + self.c1.file.name)
        self.assertEqual(resp.status_code, 302)  # отправляет на вход в админку
        self.assertIn("/admin/login/", resp["Location"])

    def test_unpublished_hidden(self):
        self.r1.is_published = False
        self.r1.save()
        self.client.login(username="one@example.com", password="Pass-word-123")
        self.assertEqual(self.client.get(reverse("download_report_file", args=[self.r1.pk, "detail"])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("finance")), "Q2 2026")

    def test_anonymous_redirected(self):
        resp = self.client.get(reverse("finance"))
        self.assertEqual(resp.status_code, 302)

    def test_admin_creates_artist_with_login(self):
        User.objects.create_superuser("boss@example.com", "Pass-word-123")
        self.client.login(username="boss@example.com", password="Pass-word-123")
        resp = self.client.post(reverse("admin:cabinet_artist_add"), {
            "email": "New@Example.com", "password": "Fresh-pass-2026", "is_active": "on", "name": "Новый",
            "contracts-TOTAL_FORMS": 0, "contracts-INITIAL_FORMS": 0,
            "reports-TOTAL_FORMS": 0, "reports-INITIAL_FORMS": 0,
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["adminform"].form.errors)
        self.client.logout()
        self.assertTrue(self.client.login(username="new@example.com", password="Fresh-pass-2026"))
