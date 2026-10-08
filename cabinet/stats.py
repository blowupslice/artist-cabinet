"""Подсчёт статистики по строкам отчётов для страницы «Статистика»."""

from decimal import Decimal

from django.db.models import Sum

COUNTRY_NAMES = {
    "RU": "Россия", "KZ": "Казахстан", "BY": "Беларусь", "UA": "Украина", "UZ": "Узбекистан",
    "KG": "Киргизия", "TJ": "Таджикистан", "AM": "Армения", "AZ": "Азербайджан", "GE": "Грузия",
    "MD": "Молдова", "TM": "Туркменистан", "US": "США", "DE": "Германия", "GB": "Великобритания",
    "FR": "Франция", "IT": "Италия", "ES": "Испания", "PL": "Польша", "TR": "Турция", "IL": "Израиль",
    "AE": "ОАЭ", "CN": "Китай", "IN": "Индия", "BR": "Бразилия", "CA": "Канада", "NL": "Нидерланды",
    "LV": "Латвия", "LT": "Литва", "EE": "Эстония", "FI": "Финляндия", "CZ": "Чехия", "RS": "Сербия",
    "TH": "Таиланд", "ID": "Индонезия", "VN": "Вьетнам", "MN": "Монголия", "CY": "Кипр",
}


def country_name(code):
    code = (code or "").strip()
    if not code:
        return "Не указана"
    return COUNTRY_NAMES.get(code.upper(), code)


def rate_per_1000(amount, quantity):
    if not quantity:
        return None
    return Decimal(amount) / Decimal(quantity) * 1000


def pct_change(new, old):
    if old is None or not old:
        return None
    return float((Decimal(new) - Decimal(old)) / Decimal(old) * 100)


def breakdown(lines, field, prev_lines=None):
    """Сумма/прослушивания/доля/стоимость 1000 по полю (track, platform, country),
    и изменение к предыдущему периоду, если он передан."""
    rows = list(lines.values(field).annotate(amount=Sum("amount"), quantity=Sum("quantity")).order_by("-amount"))
    total = sum((r["amount"] for r in rows), Decimal(0))
    total_q = sum((r["quantity"] for r in rows), 0)
    prev = {}
    if prev_lines is not None:
        prev = {r[field]: r for r in prev_lines.values(field).annotate(amount=Sum("amount"), quantity=Sum("quantity"))}
    top = rows[0]["amount"] if rows else 0
    out = []
    for r in rows:
        key = r[field]
        p = prev.get(key)
        out.append({
            "key": key,
            "name": country_name(key) if field == "country" else (key or "Не указано"),
            "amount": r["amount"],
            "quantity": r["quantity"],
            "share": float(r["amount"] / total * 100) if total else 0,
            "q_share": r["quantity"] / total_q * 100 if total_q else 0,
            "bar": float(r["amount"] / top * 100) if top else 0,
            "rate": rate_per_1000(r["amount"], r["quantity"]),
            "delta": pct_change(r["amount"], p["amount"]) if p else None,
            "is_new": prev_lines is not None and p is None,
        })
    return out


def _rights_label(value):
    t = (value or "").lower()
    author, neigh = "автор" in t, "смежн" in t
    if author and neigh:
        return "Авторские и смежные"
    if author:
        return "Авторские права"
    if neigh:
        return "Смежные права"
    return value or "Не указано"


def _usage_label(value):
    t = (value or "").lower()
    if "подпис" in t or "subscr" in t or "premium" in t:
        return "Подписка"
    if "реклам" in t or "ad" == t or "ads" in t or "ad-supported" in t or "free" in t:
        return "Реклама"
    return value or "Не указано"


def split(lines, field):
    """Доля дохода по типу прав или виду использования: [{name, amount, share}]."""
    label = _rights_label if field == "rights_type" else _usage_label
    acc = {}
    for r in lines.values(field).annotate(a=Sum("amount")):
        name = label(r[field])
        acc[name] = acc.get(name, Decimal(0)) + (r["a"] or 0)
    total = sum(acc.values(), Decimal(0))
    out = [{"name": k, "amount": v, "share": float(v / total * 100) if total else 0}
           for k, v in sorted(acc.items(), key=lambda x: -x[1])]
    # Если всё в одной категории «Не указано» — показывать нечего
    if len(out) == 1 and out[0]["name"] == "Не указано":
        return []
    return out
