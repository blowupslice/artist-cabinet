"""Акт-отчёт по лицензионному договору: расчёт сумм и PDF по шаблону лейбла."""

import base64
import os
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO

from django.db.models import Sum

from .brand import LOGO_PNG_B64
from .models import LabelSettings, Report

ZERO = Decimal("0.00")
QUARTER_BOUNDS = {1: ("01.01", "31.03"), 2: ("01.04", "30.06"), 3: ("01.07", "30.09"), 4: ("01.10", "31.12")}

FONT_PATHS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]


def q2(v):
    return Decimal(v or 0).quantize(Decimal("0.01"))


def money(v):
    """1 234.56 — как в шаблоне акта."""
    return f"{q2(v):,.2f}".replace(",", " ")


def rights_split(report):
    """Начисления по типам прав (доля артиста)."""
    out = {"neighboring": ZERO, "author": ZERO, "both": ZERO, "other": ZERO}
    for row in report.lines.values("rights_type").annotate(a=Sum("amount")):
        t = (row["rights_type"] or "").lower()
        author, neigh = "автор" in t, "смежн" in t
        key = "both" if author and neigh else "author" if author else "neighboring" if neigh else "other"
        out[key] += q2(row["a"])
    return out


@dataclass
class ActData:
    report: Report
    number: str
    contract_no: str
    contract_date: str
    period: str
    carry_prev: Decimal
    payable_prev: Decimal
    paid_prev: Decimal
    accrued: Decimal
    rights: dict
    payable_now: Decimal
    balance: Decimal
    threshold: Decimal
    label: LabelSettings = field(repr=False, default=None)


def compute(report):
    """Суммы для акта с учётом предыдущих кварталов и порога выплаты."""
    label = LabelSettings.get()
    threshold = q2(label.payout_threshold)
    reports = list(Report.objects.filter(artist=report.artist).order_by("year", "quarter"))
    balance = ZERO          # начислено и не выплачено на конец периода
    payable = ZERO          # к выплате за период
    prev = None
    for r in reports:
        carry = balance
        accrued = q2(r.total_amount)
        balance = carry + accrued
        # уже наступившая, но не выплаченная выплата остаётся к выплате и дальше
        overdue = prev is not None and q2(getattr(prev, "_payable", ZERO)) > q2(prev.paid_amount)
        payable = balance if (balance >= threshold or r.quarter == 4 or overdue) else ZERO
        if r.pk == report.pk:
            prev_payable = getattr(prev, "_payable", ZERO) if prev else ZERO
            prev_paid = q2(prev.paid_amount) if prev else ZERO
            contract = report.artist.contracts.order_by("-signed_date", "-uploaded_at").first()
            start, end = QUARTER_BOUNDS[report.quarter]
            data = ActData(
                report=report,
                number=f"{(contract.number if contract and contract.number else 'Р')}-{report.year}-{report.quarter}",
                contract_no=contract.number if contract and contract.number else "",
                contract_date=contract.signed_date.strftime("%d.%m.%Y") if contract and contract.signed_date else "",
                period=f"{start}.{report.year} — {end}.{report.year}",
                carry_prev=carry,
                payable_prev=prev_payable,
                paid_prev=prev_paid,
                accrued=accrued,
                rights=rights_split(report),
                payable_now=payable,
                balance=balance,
                threshold=threshold,
                label=label,
            )
            return data
        r._payable = payable
        balance -= q2(r.paid_amount)
        if balance < 0:
            balance = ZERO
        prev = r
    raise ValueError("Отчёт не найден среди отчётов артиста")


def _fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "ActSans" in pdfmetrics.getRegisteredFontNames():
        return "ActSans", "ActSans-Bold"
    for regular, bold in FONT_PATHS:
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("ActSans", regular))
            pdfmetrics.registerFont(TTFont("ActSans-Bold", bold))
            return "ActSans", "ActSans-Bold"
    raise RuntimeError("На сервере нет шрифта с кириллицей. Установите пакет fonts-dejavu-core "
                       "(достаточно перезапустить команду установки).")


def render_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    d = compute(report)
    regular, bold = _fonts()
    label = d.label
    artist = report.artist

    st = ParagraphStyle("base", fontName=regular, fontSize=9.5, leading=13)
    st_small = ParagraphStyle("small", parent=st, fontSize=8.5, leading=11, textColor=colors.HexColor("#444444"))
    st_right = ParagraphStyle("right", parent=st_small, alignment=TA_RIGHT)
    st_right_b = ParagraphStyle("rightb", parent=st_right, fontName=bold, fontSize=10, textColor=colors.black)
    st_title = ParagraphStyle("title", parent=st, fontName=bold, fontSize=13, leading=17, alignment=TA_CENTER)
    st_b = ParagraphStyle("b", parent=st, fontName=bold)

    def blank(v, width=24):
        return v if v else "_" * width

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"Акт-отчет {report.period_short} — {artist.name}", author=label.name or "")
    story = []

    logo = Image(BytesIO(base64.b64decode(LOGO_PNG_B64)), width=46 * mm, height=13.7 * mm)
    lic_lines = [Paragraph(label.name or "", st_right_b)]
    if label.address:
        lic_lines.append(Paragraph(f"Адрес: {label.address}", st_right))
    req = " ".join(x for x in [f"ИНН: {label.inn}" if label.inn else "", f"ОГРНИП: {label.ogrn}" if label.ogrn else ""] if x)
    if req:
        lic_lines.append(Paragraph(req, st_right))
    head = Table([[logo, lic_lines]], colWidths=[60 * mm, None])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor("#E2583B")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [head, Spacer(1, 6 * mm)]

    meta = Table([[Paragraph(f"Дата документа: <b>{report.act_date.strftime('%d.%m.%Y')}</b>", st),
                   Paragraph(f"Номер расчета/#: <b>{d.number}</b>", ParagraphStyle('r', parent=st, alignment=TA_RIGHT))]],
                 colWidths=[None, None])
    meta.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta, Spacer(1, 5 * mm), Paragraph("Акт-отчет по лицензионному договору", st_title), Spacer(1, 5 * mm)]

    licensor = artist.legal_name or artist.name
    if artist.legal_name and artist.name and artist.name not in artist.legal_name:
        licensor = f"{artist.legal_name} (творческий псевдоним — {artist.name})"
    info = [
        ["Номер договора:", f"№ {blank(d.contract_no, 12)} от {blank(d.contract_date, 10)}"],
        ["Лицензиар:", licensor],
        ["Адрес:", blank(artist.address, 40)],
        ["ИНН:", blank(artist.inn, 14)],
        ["Период:", d.period],
        ["Валюта расчета:", "RUR"],
    ]
    t = Table([[Paragraph(a, st_small), Paragraph(b, st)] for a, b in info], colWidths=[38 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [t, Spacer(1, 5 * mm)]

    r = d.rights
    rows = [
        ("Роялти к проведению на конец прошлого периода", f"{money(d.carry_prev)}", "из них проведено 0.00"),
        ("Остаток расходов на конец прошлого периода", "0.00", ""),
        ("Остаток аванса на конец прошлого периода", "0.00", ""),
        ("К выплате за прошлый период", f"{money(d.payable_prev)}", f"из них выплачено {money(d.paid_prev)}"),
        ("Начислено роялти в соответствии с прилагаемым отчетом", f"{money(d.accrued)}", ""),
        ("    в том числе: по смежным правам", money(r["neighboring"]), ""),
        ("    по авторским правам", money(r["author"]), ""),
    ]
    if r["both"]:
        rows.append(("    по авторским и смежным правам", money(r["both"]), ""))
    if r["other"]:
        rows.append(("    без указания вида прав", money(r["other"]), ""))
    rows += [
        ("Расходы в части лицензиара за текущий период", "0.00", ""),
        ("Авансы за текущий период", "0.00", ""),
        ("Сумма акта без НДС", money(d.accrued), ""),
        ("К выплате за текущий период без НДС", money(d.payable_now), ""),
        ("Остаток расходов на конец текущего периода", "0.00", ""),
        ("Остаток аванса на конец текущего периода", "0.00", ""),
    ]
    key_rows = ("Сумма акта без НДС", "К выплате за текущий период без НДС",
                "Начислено роялти в соответствии с прилагаемым отчетом")
    st_num = ParagraphStyle("m", parent=st, alignment=TA_RIGHT)
    st_num_b = ParagraphStyle("mb", parent=st_num, fontName=bold)
    st_ind = ParagraphStyle("ind", parent=st, leftIndent=12)
    data = [[Paragraph(a.strip(), st_b if a in key_rows else st_ind if a.startswith("    ") else st), Paragraph(b, st_num_b if a in key_rows else st_num),
             Paragraph(c, st_small)] for a, b, c in rows]
    ft = Table(data, colWidths=[None, 32 * mm, 44 * mm])
    style = [
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i, (a, _, _) in enumerate(rows):
        if a in key_rows:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FBEDE9")))
    ft.setStyle(TableStyle(style))
    story += [ft]

    if d.payable_now == 0 and d.balance > 0:
        story += [Spacer(1, 3 * mm), Paragraph(
            f"Начисленная сумма меньше {money(d.threshold)} ₽ и переносится на следующий период "
            "(выплачивается после накопления, но не позднее окончания года).", st_small)]

    story += [Spacer(1, 16 * mm)]
    lic_short = label.signer or ""
    art_short = artist.legal_name or artist.name
    parts = art_short.split()
    if len(parts) >= 3:
        art_short = f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
    sign = Table([
        [Paragraph(label.name or "Лицензиат", st), Paragraph("Лицензиар", st)],
        [Paragraph(f"____________ / {lic_short}", st), Paragraph(f"____________ / {art_short}", st)],
    ], colWidths=[None, None])
    sign.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 1), (-1, 1), 14)]))
    story.append(sign)

    doc.build(story)
    return buf.getvalue()


@dataclass
class PayoutOverview:
    total_accrued: Decimal
    total_paid: Decimal
    payable_now: Decimal       # к выплате по последнему акту
    unpaid: Decimal            # начислено и ещё не выплачено
    threshold: Decimal
    to_threshold: Decimal      # сколько не хватает до порога
    progress: float            # % накопления до порога
    report: Report = None      # акт, по которому положена выплата
    year_end_year: int = None  # год, по итогам которого выплатим остаток


def payout_overview(artist):
    """Сводка для блока «Баланс и выплаты» — по тем же правилам, что и акты."""
    label = LabelSettings.get()
    threshold = q2(label.payout_threshold)
    reports = list(Report.objects.filter(artist=artist).order_by("year", "quarter"))
    balance = ZERO
    prev = None
    last_payable = ZERO
    for r in reports:
        balance += q2(r.total_amount)
        overdue = prev is not None and q2(getattr(prev, "_payable", ZERO)) > q2(prev.paid_amount)
        r._payable = balance if (balance >= threshold or r.quarter == 4 or overdue) else ZERO
        last_payable = max(r._payable - q2(r.paid_amount), ZERO)
        balance = max(balance - q2(r.paid_amount), ZERO)
        prev = r
    total_accrued = sum((q2(r.total_amount) for r in reports), ZERO)
    total_paid = sum((q2(r.paid_amount) for r in reports), ZERO)
    last = reports[-1] if reports else None
    to_threshold = max(threshold - balance, ZERO)
    progress = float(min(balance / threshold * 100, 100)) if threshold else 100.0
    return PayoutOverview(
        total_accrued=total_accrued, total_paid=total_paid,
        payable_now=last_payable, unpaid=balance, threshold=threshold,
        to_threshold=to_threshold, progress=progress,
        report=last if last_payable > 0 else None,
        year_end_year=(last.year if last and last.quarter < 4 else (last.year + 1 if last else None)),
    )
"""Акт-отчёт по лицензионному договору: расчёт сумм и PDF по шаблону лейбла."""

import base64
import os
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO

from django.db.models import Sum

from .brand import LOGO_PNG_B64
from .models import LabelSettings, Report

ZERO = Decimal("0.00")
QUARTER_BOUNDS = {1: ("01.01", "31.03"), 2: ("01.04", "30.06"), 3: ("01.07", "30.09"), 4: ("01.10", "31.12")}

FONT_PATHS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]


def q2(v):
    return Decimal(v or 0).quantize(Decimal("0.01"))


def money(v):
    """1 234.56 — как в шаблоне акта."""
    return f"{q2(v):,.2f}".replace(",", " ")


def rights_split(report):
    """Начисления по типам прав (доля артиста)."""
    out = {"neighboring": ZERO, "author": ZERO, "both": ZERO, "other": ZERO}
    for row in report.lines.values("rights_type").annotate(a=Sum("amount")):
        t = (row["rights_type"] or "").lower()
        author, neigh = "автор" in t, "смежн" in t
        key = "both" if author and neigh else "author" if author else "neighboring" if neigh else "other"
        out[key] += q2(row["a"])
    return out


@dataclass
class ActData:
    report: Report
    number: str
    contract_no: str
    contract_date: str
    period: str
    carry_prev: Decimal
    payable_prev: Decimal
    paid_prev: Decimal
    accrued: Decimal
    rights: dict
    payable_now: Decimal
    balance: Decimal
    threshold: Decimal
    label: LabelSettings = field(repr=False, default=None)


def compute(report):
    """Суммы для акта с учётом предыдущих кварталов и порога выплаты."""
    label = LabelSettings.get()
    threshold = q2(label.payout_threshold)
    reports = list(Report.objects.filter(artist=report.artist).order_by("year", "quarter"))
    balance = ZERO          # начислено и не выплачено на конец периода
    payable = ZERO          # к выплате за период
    prev = None
    for r in reports:
        carry = balance
        accrued = q2(r.total_amount)
        balance = carry + accrued
        # уже наступившая, но не выплаченная выплата остаётся к выплате и дальше
        overdue = prev is not None and q2(getattr(prev, "_payable", ZERO)) > q2(prev.paid_amount)
        payable = balance if (balance >= threshold or r.quarter == 4 or overdue) else ZERO
        if r.pk == report.pk:
            prev_payable = getattr(prev, "_payable", ZERO) if prev else ZERO
            prev_paid = q2(prev.paid_amount) if prev else ZERO
            contract = report.artist.contracts.order_by("-signed_date", "-uploaded_at").first()
            start, end = QUARTER_BOUNDS[report.quarter]
            data = ActData(
                report=report,
                number=f"{(contract.number if contract and contract.number else 'Р')}-{report.year}-{report.quarter}",
                contract_no=contract.number if contract and contract.number else "",
                contract_date=contract.signed_date.strftime("%d.%m.%Y") if contract and contract.signed_date else "",
                period=f"{start}.{report.year} — {end}.{report.year}",
                carry_prev=carry,
                payable_prev=prev_payable,
                paid_prev=prev_paid,
                accrued=accrued,
                rights=rights_split(report),
                payable_now=payable,
                balance=balance,
                threshold=threshold,
                label=label,
            )
            return data
        r._payable = payable
        balance -= q2(r.paid_amount)
        if balance < 0:
            balance = ZERO
        prev = r
    raise ValueError("Отчёт не найден среди отчётов артиста")


def _fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "ActSans" in pdfmetrics.getRegisteredFontNames():
        return "ActSans", "ActSans-Bold"
    for regular, bold in FONT_PATHS:
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("ActSans", regular))
            pdfmetrics.registerFont(TTFont("ActSans-Bold", bold))
            return "ActSans", "ActSans-Bold"
    raise RuntimeError("На сервере нет шрифта с кириллицей. Установите пакет fonts-dejavu-core "
                       "(достаточно перезапустить команду установки).")


def render_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    d = compute(report)
    regular, bold = _fonts()
    label = d.label
    artist = report.artist

    st = ParagraphStyle("base", fontName=regular, fontSize=9.5, leading=13)
    st_small = ParagraphStyle("small", parent=st, fontSize=8.5, leading=11, textColor=colors.HexColor("#444444"))
    st_right = ParagraphStyle("right", parent=st_small, alignment=TA_RIGHT)
    st_right_b = ParagraphStyle("rightb", parent=st_right, fontName=bold, fontSize=10, textColor=colors.black)
    st_title = ParagraphStyle("title", parent=st, fontName=bold, fontSize=13, leading=17, alignment=TA_CENTER)
    st_b = ParagraphStyle("b", parent=st, fontName=bold)

    def blank(v, width=24):
        return v if v else "_" * width

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"Акт-отчет {report.period_short} — {artist.name}", author=label.name or "")
    story = []

    logo = Image(BytesIO(base64.b64decode(LOGO_PNG_B64)), width=46 * mm, height=13.7 * mm)
    lic_lines = [Paragraph(label.name or "", st_right_b)]
    if label.address:
        lic_lines.append(Paragraph(f"Адрес: {label.address}", st_right))
    req = " ".join(x for x in [f"ИНН: {label.inn}" if label.inn else "", f"ОГРНИП: {label.ogrn}" if label.ogrn else ""] if x)
    if req:
        lic_lines.append(Paragraph(req, st_right))
    head = Table([[logo, lic_lines]], colWidths=[60 * mm, None])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor("#E2583B")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [head, Spacer(1, 6 * mm)]

    meta = Table([[Paragraph(f"Дата документа: <b>{report.act_date.strftime('%d.%m.%Y')}</b>", st),
                   Paragraph(f"Номер расчета/#: <b>{d.number}</b>", ParagraphStyle('r', parent=st, alignment=TA_RIGHT))]],
                 colWidths=[None, None])
    meta.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta, Spacer(1, 5 * mm), Paragraph("Акт-отчет по лицензионному договору", st_title), Spacer(1, 5 * mm)]

    licensor = artist.legal_name or artist.name
    if artist.legal_name and artist.name and artist.name not in artist.legal_name:
        licensor = f"{artist.legal_name} (творческий псевдоним — {artist.name})"
    info = [
        ["Номер договора:", f"№ {blank(d.contract_no, 12)} от {blank(d.contract_date, 10)}"],
        ["Лицензиар:", licensor],
        ["Адрес:", blank(artist.address, 40)],
        ["ИНН:", blank(artist.inn, 14)],
        ["Период:", d.period],
        ["Валюта расчета:", "RUR"],
    ]
    t = Table([[Paragraph(a, st_small), Paragraph(b, st)] for a, b in info], colWidths=[38 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [t, Spacer(1, 5 * mm)]

    r = d.rights
    rows = [
        ("Роялти к проведению на конец прошлого периода", f"{money(d.carry_prev)}", "из них проведено 0.00"),
        ("Остаток расходов на конец прошлого периода", "0.00", ""),
        ("Остаток аванса на конец прошлого периода", "0.00", ""),
        ("К выплате за прошлый период", f"{money(d.payable_prev)}", f"из них выплачено {money(d.paid_prev)}"),
        ("Начислено роялти в соответствии с прилагаемым отчетом", f"{money(d.accrued)}", ""),
        ("    в том числе: по смежным правам", money(r["neighboring"]), ""),
        ("    по авторским правам", money(r["author"]), ""),
    ]
    if r["both"]:
        rows.append(("    по авторским и смежным правам", money(r["both"]), ""))
    if r["other"]:
        rows.append(("    без указания вида прав", money(r["other"]), ""))
    rows += [
        ("Расходы в части лицензиара за текущий период", "0.00", ""),
        ("Авансы за текущий период", "0.00", ""),
        ("Сумма акта без НДС", money(d.accrued), ""),
        ("К выплате за текущий период без НДС", money(d.payable_now), ""),
        ("Остаток расходов на конец текущего периода", "0.00", ""),
        ("Остаток аванса на конец текущего периода", "0.00", ""),
    ]
    key_rows = ("Сумма акта без НДС", "К выплате за текущий период без НДС",
                "Начислено роялти в соответствии с прилагаемым отчетом")
    st_num = ParagraphStyle("m", parent=st, alignment=TA_RIGHT)
    st_num_b = ParagraphStyle("mb", parent=st_num, fontName=bold)
    st_ind = ParagraphStyle("ind", parent=st, leftIndent=12)
    data = [[Paragraph(a.strip(), st_b if a in key_rows else st_ind if a.startswith("    ") else st), Paragraph(b, st_num_b if a in key_rows else st_num),
             Paragraph(c, st_small)] for a, b, c in rows]
    ft = Table(data, colWidths=[None, 32 * mm, 44 * mm])
    style = [
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i, (a, _, _) in enumerate(rows):
        if a in key_rows:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FBEDE9")))
    ft.setStyle(TableStyle(style))
    story += [ft]

    if d.payable_now == 0 and d.balance > 0:
        story += [Spacer(1, 3 * mm), Paragraph(
            f"Начисленная сумма меньше {money(d.threshold)} ₽ и переносится на следующий период "
            "(выплачивается после накопления, но не позднее окончания года).", st_small)]

    story += [Spacer(1, 16 * mm)]
    lic_short = label.signer or ""
    art_short = artist.legal_name or artist.name
    parts = art_short.split()
    if len(parts) >= 3:
        art_short = f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
    sign = Table([
        [Paragraph(label.name or "Лицензиат", st), Paragraph("Лицензиар", st)],
        [Paragraph(f"____________ / {lic_short}", st), Paragraph(f"____________ / {art_short}", st)],
    ], colWidths=[None, None])
    sign.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 1), (-1, 1), 14)]))
    story.append(sign)

    doc.build(story)
    return buf.getvalue()
