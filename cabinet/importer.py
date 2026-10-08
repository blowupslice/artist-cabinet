"""
Разбор отчётов дистрибьютора (Excel .xlsx или .csv).

Формат у разных дистрибьюторов отличается, поэтому колонки ищутся по названиям
заголовков. Если ваш дистрибьютор называет колонку иначе — добавьте вариант
в словарь COLUMN_ALIASES ниже.
"""

import csv
import io
import re
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

# Варианты названий колонок (сравниваются без учёта регистра, пробелов и знаков).
COLUMN_ALIASES = {
    "track": [
        "трек", "название трека", "наименование трека", "название композиции", "композиция",
        "произведение", "фонограмма", "название фонограммы", "название", "наименование",
        "track", "track title", "track name", "title", "song", "song title", "release track",
        "asset title", "product title",
    ],
    "platform": [
        "площадка", "платформа", "сервис", "магазин", "витрина", "dsp", "партнер", "партнёр",
        "platform", "store", "store name", "service", "shop", "retailer", "partner", "channel",
        "provider", "dsp name", "source",
    ],
    "amount": [
        "сумма", "доход", "вознаграждение", "к выплате", "роялти", "сумма к выплате",
        "вознаграждение артиста", "доля артиста", "итого к выплате", "выручка", "сумма руб",
        "сумма, руб", "сумма (руб)", "доход руб", "amount", "revenue", "royalty", "royalties",
        "net revenue", "net amount", "earnings", "payable", "net payable", "net", "total",
        "your share", "artist share", "net royalty", "amount payable",
    ],
    "quantity": [
        "прослушивания", "прослушиваний", "количество", "кол-во", "кол во", "стримы",
        "количество прослушиваний", "количество продаж", "продажи", "скачивания",
        "streams", "quantity", "qty", "units", "plays", "count", "downloads", "sales",
    ],
    "isrc": ["isrc", "код isrc"],
    "country": ["страна", "территория", "регион", "country", "territory", "region", "country code"],
    "rights_type": ["тип прав", "вид прав", "права", "rights type", "right type", "rights"],
    "usage_type": [
        "вид использования контента", "вид использования", "тип использования", "модель использования",
        "usage type", "sale type", "transaction type", "use type",
    ],
    "period": [
        "период использования", "период продаж", "месяц использования", "отчетный месяц", "период", "месяц",
        "usage period", "sales period", "sale month", "reporting period", "period", "month",
    ],
}

# Строки с таким «треком» — это итоги, а не данные
TOTAL_MARKERS = {"итого", "всего", "total", "grand total", "итог", "subtotal"}

MAX_HEADER_SCAN = 30


class ImportError_(Exception):
    """Понятная пользователю ошибка разбора файла."""


def _norm(value):
    s = str(value or "").strip().lower().replace("ё", "е")
    s = re.sub(r"[\s_\-.:/,;()\[\]№#*]+", " ", s)
    return s.strip()


def _alias_index():
    return {key: [_norm(a) for a in aliases] for key, aliases in COLUMN_ALIASES.items()}


def detect_columns(header_row):
    """Возвращает {поле: индекс колонки} по строке заголовков."""
    headers = [_norm(h) for h in header_row]
    aliases = _alias_index()
    found = {}
    used = set()
    # 1) точные совпадения, 2) заголовок начинается с варианта / содержит его
    for strict in (True, False):
        for field in ("amount", "quantity", "isrc", "period", "usage_type", "rights_type", "track", "platform", "country"):
            if field in found:
                continue
            for alias in aliases[field]:
                for i, h in enumerate(headers):
                    if i in used or not h:
                        continue
                    ok = h == alias if strict else (h.startswith(alias + " ") or (len(alias) > 4 and alias in h))
                    if ok:
                        found[field] = i
                        used.add(i)
                        break
                if field in found:
                    break
    return found


def parse_number(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    s = str(value).strip().replace("\u00a0", "").replace(" ", "")
    s = re.sub(r"[^\d,.\-eE]", "", s)  # убираем символы валют
    if not s or s in "-.,":
        return None
    if "," in s and "." in s:
        # 1,234.56 или 1.234,56 — десятичный разделитель тот, что правее
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _read_rows(uploaded_file):
    name = (getattr(uploaded_file, "name", "") or "").lower()
    uploaded_file.seek(0)
    raw = uploaded_file.read()
    uploaded_file.seek(0)

    if name.endswith((".xlsx", ".xlsm")):
        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001
            raise ImportError_(f"Не удалось открыть Excel-файл: {exc}") from exc
        # берём первый лист, на котором нашлись нужные колонки
        sheets = [[list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets]
        wb.close()
        return sheets

    if name.endswith((".csv", ".txt", ".tsv")):
        text = None
        for enc in ("utf-8-sig", "cp1251", "utf-16"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ImportError_("Не удалось определить кодировку CSV-файла")
        sample = text[:5000]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
        return [list(csv.reader(io.StringIO(text), delimiter=delimiter))]

    if name.endswith(".xls"):
        raise ImportError_("Старый формат .xls не поддерживается — пересохраните файл как .xlsx")
    raise ImportError_("Поддерживаются файлы .xlsx и .csv")


def find_table(uploaded_file):
    """Находит в файле таблицу с данными: (строка заголовков, строки данных, колонки)."""
    sheets = _read_rows(uploaded_file)
    last_headers = []
    for rows in sheets:
        for header_idx, row in enumerate(rows[:MAX_HEADER_SCAN]):
            cols = detect_columns(row)
            if {"track", "platform", "amount"} <= cols.keys():
                return row, rows[header_idx + 1:], cols
            if any(c not in (None, "") for c in row) and not last_headers:
                last_headers = [str(c) for c in row if c not in (None, "")]
    missing_hint = ", ".join(last_headers[:15]) or "—"
    raise ImportError_(
        "Не нашёл в файле колонки с треком, площадкой и суммой. "
        f"Заголовки в файле: {missing_hint}. "
        "Добавьте свои названия колонок в cabinet/importer.py (COLUMN_ALIASES) "
        "или переименуйте колонки в файле."
    )


def parse_report(uploaded_file):
    """Разбирает файл и возвращает список строк для RoyaltyLine.

    Строки с одинаковыми трек/площадка/страна/ISRC/тип прав суммируются
    (например, если в файле разбивка по месяцам)."""
    _, rows, cols = find_table(uploaded_file)
    return _collect(rows, cols)


MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def parse_period(value):
    """Год и месяц из значения колонки периода: 2025-12, 12.2025, 01.12.2025, дата, «Декабрь 2025»."""
    import datetime as dt
    if value is None or value == "":
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.year, value.month
    s = str(value).strip().lower()
    m = re.search(r"(\d{4})\s*[-./]\s*(\d{1,2})", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{1,2})\s*[-./]\s*(\d{4})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(2)), int(m.group(1))
    m = re.search(r"(\d{4})", s)
    if m:
        for stem, num in sorted(MONTHS_RU.items(), key=lambda x: -len(x[0])):
            if stem in s:
                return int(m.group(1)), num
    return None


def split_by_quarter(uploaded_file):
    """Делит общий отчёт по кварталам по колонке периода.

    Возвращает (заголовки, колонки, {(год, квартал): [строки]})."""
    header, rows, cols = find_table(uploaded_file)
    if "period" not in cols:
        raise ImportError_(
            "В файле нет колонки с периодом (например, «Период использования»), "
            "поэтому разделить его по кварталам нельзя. Загрузите его как отчёт за один квартал."
        )
    groups = {}
    bad = 0
    for row in rows:
        track = str(_cell(row, cols["track"]) or "").strip()
        if not track or _norm(track) in TOTAL_MARKERS or parse_number(_cell(row, cols["amount"])) is None:
            continue
        ym = parse_period(_cell(row, cols["period"]))
        if not ym:
            bad += 1
            continue
        year, month = ym
        groups.setdefault((year, (month - 1) // 3 + 1), []).append(row)
    if not groups:
        raise ImportError_("Не удалось прочитать периоды в колонке периода")
    return header, cols, groups, bad


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _collect(rows, cols):
    acc = {}
    for row in rows:
        track = str(_cell(row, cols["track"]) or "").strip()
        if not track or _norm(track) in TOTAL_MARKERS:
            continue
        amount = parse_number(_cell(row, cols["amount"]))
        if amount is None:
            continue
        platform = str(_cell(row, cols["platform"]) or "").strip() or "Не указана"
        isrc = str(_cell(row, cols.get("isrc")) or "").strip().upper()[:20]
        country = str(_cell(row, cols.get("country")) or "").strip()[:100]
        qty = parse_number(_cell(row, cols.get("quantity"))) or Decimal(0)
        rights = str(_cell(row, cols.get("rights_type")) or "").strip()[:100]
        usage = str(_cell(row, cols.get("usage_type")) or "").strip()[:100]

        key = (track[:500], platform[:200], country, isrc, rights, usage)
        item = acc.setdefault(key, {
            "track": key[0], "platform": key[1], "country": country, "isrc": isrc,
            "rights_type": rights, "usage_type": usage, "quantity": 0, "amount": Decimal(0),
        })
        item["quantity"] += int(qty)
        item["amount"] += amount

    if not acc:
        raise ImportError_("Колонки найдены, но в файле нет строк с данными")
    for item in acc.values():
        item["amount"] = item["amount"].quantize(Decimal("0.000001"))
    return list(acc.values())
