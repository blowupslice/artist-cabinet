from decimal import Decimal

from django import template

register = template.Library()

SYMBOLS = {"RUB": "₽", "USD": "$", "EUR": "€", "KZT": "₸", "BYN": "Br"}
NBSP = " "


@register.filter
def money(value, currency=""):
    try:
        v = Decimal(value or 0)
    except Exception:  # noqa: BLE001
        return value
    s = f"{v:,.2f}".replace(",", NBSP).replace(".", ",")
    sym = SYMBOLS.get(currency, currency)
    return f"{s}{NBSP}{sym}".strip() if sym else s


@register.filter
def num(value):
    try:
        return f"{int(value or 0):,}".replace(",", NBSP)
    except (TypeError, ValueError):
        return value


@register.filter
def pct(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{v:.1f}".replace(".", ",") + "%"


@register.filter
def cssnum(value):
    """Число для CSS (точка вместо запятой)."""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0"


@register.filter
def plural(n, forms):
    """{{ 5|plural:"день,дня,дней" }} → «дней»."""
    try:
        n = abs(int(n))
    except (TypeError, ValueError):
        return ""
    one, few, many = forms.split(",")
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


@register.filter
def short(value):
    """Короткое число: 1 846 300 → «1,8 млн», 425 108 → «425 тыс.», 9 870 → «9 870»."""
    try:
        v = int(value or 0)
    except (TypeError, ValueError):
        return value
    if abs(v) >= 1_000_000:
        s = f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{s}{NBSP}млн"
    if abs(v) >= 10_000:
        return f"{round(v / 1000)}{NBSP}тыс."
    return f"{v:,}".replace(",", NBSP)


@register.filter
def initials(name):
    """«Лиза Север» → «ЛС», «AZAGIR» → «AZ», «RISHAT THV» → «RT»."""
    words = [w for w in str(name or "").replace("-", " ").split() if w[:1].isalnum()]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return (words[0][:2] if words else "").upper()
