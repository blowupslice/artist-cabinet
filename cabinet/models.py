import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone


# ---------------------------------------------------------------------------
# Пользователь: логин — это email
# ---------------------------------------------------------------------------

class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Нужен email")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra)

    def get_by_natural_key(self, username):
        # вход не зависит от регистра букв в почте
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})


class User(AbstractUser):
    username = None
    email = models.EmailField("Email (логин)", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


# ---------------------------------------------------------------------------
# Артист
# ---------------------------------------------------------------------------

class Artist(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="artist",
        verbose_name="аккаунт",
    )
    name = models.CharField("Имя / псевдоним", max_length=200)
    legal_name = models.CharField("ФИО / юр. лицо", max_length=300, blank=True)
    phone = models.CharField("Телефон", max_length=50, blank=True)
    royalty_rate = models.DecimalField(
        "Доля артиста, %", max_digits=5, decimal_places=2, default=Decimal("50"),
        help_text="Какой процент дохода лейбла получает артист по договору. "
                  "В кабинете артист видит только свою долю.",
    )
    address = models.CharField("Адрес (для акта)", max_length=400, blank=True)
    inn = models.CharField("ИНН (для акта)", max_length=20, blank=True)
    notes = models.TextField("Заметки (видны только вам)", blank=True)

    # Реквизиты для выплат — артист заполняет сам в настройках кабинета
    bank_recipient = models.CharField("Получатель (ФИО полностью)", max_length=300, blank=True)
    bank_name = models.CharField("Банк", max_length=300, blank=True)
    bank_bik = models.CharField("БИК", max_length=9, blank=True)
    bank_account = models.CharField("Расчётный счёт", max_length=20, blank=True)
    bank_corr_account = models.CharField("Корр. счёт", max_length=20, blank=True)
    bank_updated_at = models.DateTimeField("Реквизиты обновлены", null=True, blank=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "артист"
        verbose_name_plural = "артисты"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def email(self):
        return self.user.email

    @property
    def has_bank_details(self):
        return bool(self.bank_recipient and self.bank_bik and self.bank_account)


def _upload_path(instance, filename, kind):
    """Файлы кладём в папку артиста со случайным префиксом, чтобы имя не угадать."""
    artist_id = instance.artist_id or "unknown"
    base, ext = os.path.splitext(os.path.basename(filename))
    return f"artists/{artist_id}/{kind}/{uuid.uuid4().hex[:10]}_{base[:80]}{ext.lower()}"


def upload_contract(instance, filename):
    return _upload_path(instance, filename, "contracts")


def upload_report(instance, filename):
    return _upload_path(instance, filename, "reports")


def upload_act(instance, filename):
    return _upload_path(instance, filename, "acts")


def upload_signed_act(instance, filename):
    return _upload_path(instance, filename, "signed_acts")


class LabelSettings(models.Model):
    """Реквизиты лейбла для шапки и подписи в актах. Запись одна на весь сайт."""

    name = models.CharField("Название (как в акте)", max_length=300, default="", blank=True,
                            help_text="Например: ИП Сайкин Михаил Викторович")
    address = models.CharField("Адрес", max_length=400, blank=True)
    inn = models.CharField("ИНН", max_length=20, blank=True)
    ogrn = models.CharField("ОГРН / ОГРНИП", max_length=20, blank=True)
    signer = models.CharField("Подпись (Фамилия И.О.)", max_length=100, blank=True,
                              help_text="Например: Сайкин М.В.")
    payout_threshold = models.DecimalField(
        "Минимальная сумма выплаты, ₽", max_digits=12, decimal_places=2, default=Decimal("5000"),
        help_text="Меньшие суммы копятся до этого порога, но выплачиваются не позднее конца года.",
    )

    class Meta:
        verbose_name = "реквизиты лейбла"
        verbose_name_plural = "реквизиты лейбла"

    def __str__(self):
        return self.name or "Реквизиты лейбла"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Contract(models.Model):
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="contracts", verbose_name="артист")
    title = models.CharField("Название", max_length=300, help_text="Например: Лицензионный договор")
    number = models.CharField("Номер", max_length=100, blank=True)
    signed_date = models.DateField("Дата подписания", null=True, blank=True)
    file = models.FileField("Файл договора", upload_to=upload_contract)
    uploaded_at = models.DateTimeField("Загружен", auto_now_add=True)

    class Meta:
        verbose_name = "договор"
        verbose_name_plural = "договоры"
        ordering = ["-signed_date", "-uploaded_at"]

    def __str__(self):
        parts = [self.title]
        if self.number:
            parts.append(f"№ {self.number}")
        return " ".join(parts)

    @property
    def filename(self):
        return os.path.basename(self.file.name).split("_", 1)[-1]


QUARTERS = [(1, "I квартал"), (2, "II квартал"), (3, "III квартал"), (4, "IV квартал")]


def current_year():
    return timezone.localdate().year


class Report(models.Model):
    """Квартальный отчёт артиста: детализация от дистрибьютора + акт."""

    class ActStatus(models.TextChoices):
        NONE = "none", "Акт не загружен"
        TO_SIGN = "to_sign", "Ожидает подписания"
        REVIEW = "review", "На проверке"
        SIGNED = "signed", "Подписан"

    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="reports", verbose_name="артист")
    year = models.PositiveIntegerField("Год", default=current_year)
    quarter = models.PositiveSmallIntegerField("Квартал", choices=QUARTERS)
    currency = models.CharField("Валюта", max_length=10, default=settings.DEFAULT_CURRENCY)

    detail_file = models.FileField(
        "Детализированный отчёт (Excel/CSV)", upload_to=upload_report,
        help_text="Файл от дистрибьютора. Из него строится таблица по трекам и площадкам; "
                  "артист сможет скачать его целиком.",
    )
    act_file = models.FileField("Акт для подписания", upload_to=upload_act, blank=True)
    signed_act_file = models.FileField(
        "Подписанный акт", upload_to=upload_signed_act, blank=True,
        help_text="Скан или фото акта, подписанного артистом. Артист может загрузить его сам из кабинета.",
    )
    signed_uploaded_at = models.DateTimeField("Подписанный акт загружен", null=True, blank=True)
    act_status = models.CharField("Статус акта", max_length=10, choices=ActStatus.choices, default=ActStatus.TO_SIGN)
    act_date = models.DateField("Дата акта", null=True, blank=True, help_text="Если пусто — дата загрузки отчёта.")
    royalty_rate = models.DecimalField(
        "Доля артиста, %", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Если пусто — берётся доля из карточки артиста.",
    )
    paid_amount = models.DecimalField(
        "Выплачено артисту по этому акту, ₽", max_digits=14, decimal_places=2, default=0,
        help_text="Заполните после выплаты — сумма учтётся в следующих актах.",
    )

    is_published = models.BooleanField("Показывать артисту", default=True)
    comment = models.TextField("Комментарий для артиста", blank=True)

    total_amount = models.DecimalField("Итого артисту", max_digits=16, decimal_places=2, default=0, editable=False)
    total_gross = models.DecimalField("Доход лейбла", max_digits=16, decimal_places=2, default=0, editable=False)
    total_quantity = models.BigIntegerField("Всего прослушиваний", default=0, editable=False)
    lines_count = models.PositiveIntegerField("Строк в отчёте", default=0, editable=False)

    created_at = models.DateTimeField("Загружен", auto_now_add=True)
    updated_at = models.DateTimeField("Изменён", auto_now=True)

    class Meta:
        verbose_name = "квартальный отчёт"
        verbose_name_plural = "квартальные отчёты"
        ordering = ["-year", "-quarter"]
        constraints = [
            models.UniqueConstraint(fields=["artist", "year", "quarter"], name="uniq_report_per_quarter"),
        ]

    def __str__(self):
        return f"{self.artist} — {self.period_label}"

    @property
    def period_label(self):
        return f"{dict(QUARTERS)[self.quarter]} {self.year}"

    @property
    def period_short(self):
        return f"Q{self.quarter} {self.year}"

    @property
    def slug(self):
        return f"{self.year}-q{self.quarter}"

    @property
    def rate(self):
        if self.royalty_rate is not None:
            return self.royalty_rate
        return self.artist.royalty_rate if self.artist_id else Decimal("100")

    def save(self, *args, **kwargs):
        # Акт формируется автоматически, поэтому он всегда есть и ждёт подписи
        # Скан от админа сразу считается принятым; скан от артиста ждёт проверки
        if self.signed_act_file and self.act_status == self.ActStatus.TO_SIGN \
                and not getattr(self, "_from_artist", False):
            self.act_status = self.ActStatus.SIGNED
        elif self.act_status == self.ActStatus.NONE:
            self.act_status = self.ActStatus.TO_SIGN
        if self.royalty_rate is None and self.artist_id:
            self.royalty_rate = self.artist.royalty_rate
        if not self.act_date:
            self.act_date = timezone.localdate()
        rows = getattr(self, "_parsed_rows", None)
        with transaction.atomic():
            super().save(*args, **kwargs)
            if rows is not None:
                self.replace_lines(rows)
                self._parsed_rows = None

    def replace_lines(self, rows):
        """rows — строки из импортёра, где amount = доход лейбла от дистрибьютора.
        В базе amount хранит долю артиста, gross — доход лейбла."""
        share = Decimal(self.rate) / Decimal(100)
        objs = []
        for row in rows:
            row = dict(row)
            gross = Decimal(row.pop("amount"))
            objs.append(RoyaltyLine(report=self, gross=gross,
                                    amount=(gross * share).quantize(Decimal("0.000001")), **row))
        self.lines.all().delete()
        RoyaltyLine.objects.bulk_create(objs, batch_size=2000)
        self.recalc_totals()

    def recalc_totals(self):
        agg = self.lines.aggregate(a=Sum("amount"), g=Sum("gross"), q=Sum("quantity"))
        Report.objects.filter(pk=self.pk).update(
            total_amount=(agg["a"] or Decimal("0")).quantize(Decimal("0.01")),
            total_gross=(agg["g"] or Decimal("0")).quantize(Decimal("0.01")),
            total_quantity=agg["q"] or 0,
            lines_count=self.lines.count(),
        )
        self.refresh_from_db(fields=["total_amount", "total_gross", "total_quantity", "lines_count"])


class RoyaltyLine(models.Model):
    """Одна строка отчёта: трек × площадка (× страна/месяц, если они есть в файле)."""

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="lines")
    track = models.CharField("Трек", max_length=500)
    isrc = models.CharField("ISRC", max_length=20, blank=True)
    platform = models.CharField("Площадка", max_length=200)
    country = models.CharField("Страна", max_length=100, blank=True)
    rights_type = models.CharField("Тип прав", max_length=100, blank=True)
    usage_type = models.CharField("Вид использования", max_length=100, blank=True)
    quantity = models.BigIntegerField("Прослушивания", default=0)
    gross = models.DecimalField("Доход лейбла", max_digits=18, decimal_places=6, default=0)
    amount = models.DecimalField("Доля артиста", max_digits=18, decimal_places=6, default=0)

    class Meta:
        verbose_name = "строка отчёта"
        verbose_name_plural = "строки отчёта"
        indexes = [models.Index(fields=["report", "track"]), models.Index(fields=["report", "platform"])]

    def __str__(self):
        return f"{self.track} / {self.platform}: {self.amount}"


# ---------------------------------------------------------------------------
# Релизы
# ---------------------------------------------------------------------------

def upload_cover(instance, filename):
    return _upload_path(instance, filename, "covers")


class Release(models.Model):
    class Status(models.TextChoices):
        PREPARING = "preparing", "Готовится"
        MODERATION = "moderation", "На модерации"
        SCHEDULED = "scheduled", "Отгружен, ждёт даты"
        RELEASED = "released", "Вышел"
        TAKEN_DOWN = "taken_down", "Снят с площадок"

    class Kind(models.TextChoices):
        SINGLE = "single", "Сингл"
        EP = "ep", "EP"
        ALBUM = "album", "Альбом"

    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="releases", verbose_name="артист")
    title = models.CharField("Название", max_length=300)
    artists_line = models.CharField("Исполнители (как на площадках)", max_length=300, blank=True,
                                    help_text="Например: Эсчевский feat. Кто-то. Если пусто — имя артиста.")
    kind = models.CharField("Тип", max_length=10, choices=Kind.choices, default=Kind.SINGLE)
    release_date = models.DateField("Дата релиза", null=True, blank=True)
    status = models.CharField("Статус", max_length=12, choices=Status.choices, default=Status.PREPARING)
    upc = models.CharField("UPC", max_length=20, blank=True)
    isrc = models.CharField("ISRC", max_length=100, blank=True, help_text="Можно несколько через запятую")
    smart_link = models.URLField("Смарт-ссылка", blank=True, help_text="Например: https://band.link/…")
    presave_date = models.DateField("Пресейв с", null=True, blank=True)
    cover = models.FileField("Обложка (jpg/png)", upload_to=upload_cover, blank=True)
    cover_url = models.URLField("…или ссылка на обложку", blank=True,
                                help_text="Вставьте ссылку на картинку — сервер сам скачает её в «Обложку».")
    note = models.CharField("Заметка для артиста", max_length=300, blank=True,
                            help_text="Например: «Питчинг в Яндекс Музыку отправлен»")
    is_published = models.BooleanField("Показывать артисту", default=True)
    created_at = models.DateTimeField("Добавлен", auto_now_add=True)

    class Meta:
        verbose_name = "релиз"
        verbose_name_plural = "релизы"
        ordering = ["-release_date", "-created_at"]

    def __str__(self):
        return f"{self.display_artists} — {self.title}"

    @property
    def display_artists(self):
        return self.artists_line or self.artist.name

    @property
    def days_left(self):
        if not self.release_date:
            return None
        return (self.release_date - timezone.localdate()).days

    @property
    def is_upcoming(self):
        d = self.days_left
        return d is not None and d > 0 and self.status != self.Status.TAKEN_DOWN


# ---------------------------------------------------------------------------
# Свежая статистика прослушиваний (выгрузки из DataLens / кабинетов дистрибьюторов)
# ---------------------------------------------------------------------------

class StreamStat(models.Model):
    """Прослушивания трека на площадке за период. Для ежедневных данных начало = конец."""

    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="stream_stats")
    period_start = models.DateField("С")
    period_end = models.DateField("По")
    track = models.CharField("Трек", max_length=500)
    platform = models.CharField("Площадка", max_length=200, blank=True)
    streams = models.PositiveIntegerField("Прослушивания", default=0)
    source = models.CharField("Источник", max_length=100, blank=True)
    uploaded_at = models.DateTimeField("Загружено", auto_now=True)

    class Meta:
        verbose_name = "прослушивания за период"
        verbose_name_plural = "свежая статистика прослушиваний"
        ordering = ["-period_end", "-streams"]
        indexes = [models.Index(fields=["artist", "period_end"])]
        constraints = [models.UniqueConstraint(fields=["artist", "period_start", "period_end", "track", "platform"],
                                               name="uniq_stream_stat")]

    @property
    def days(self):
        return (self.period_end - self.period_start).days + 1


class PlaylistPlacement(models.Model):
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="playlist_placements")
    track = models.CharField("Трек", max_length=500)
    platform = models.CharField("Площадка", max_length=200)
    playlist = models.CharField("Плейлист", max_length=300)
    peak_position = models.PositiveIntegerField("Пиковая позиция", null=True, blank=True)
    days = models.PositiveIntegerField("Дней в плейлисте", default=0)
    first_date = models.DateField("Первая дата", null=True, blank=True)
    last_date = models.DateField("Последняя дата", null=True, blank=True)

    class Meta:
        verbose_name = "попадание в плейлист"
        verbose_name_plural = "попадания в плейлисты"
        ordering = ["-last_date", "peak_position"]
        constraints = [models.UniqueConstraint(fields=["artist", "track", "platform", "playlist"],
                                               name="uniq_playlist_placement")]
