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
