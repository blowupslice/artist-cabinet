"""Импорт свежей статистики: прослушивания по дням/за период и попадания в плейлисты.

Подходит выгрузка из DataLens («Сохранить как» → XLSX/CSV) и любая таблица с колонками
«Дата / Трек / Площадка / Прослушивания». Если колонки даты нет — укажите период при загрузке.
Если в файле много артистов, строки раскладываются по названию «Артист - Трек»
или по колонке «Артист»; фиты попадают обоим артистам.
"""

import datetime as dt
import re
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import F

from .importer import ImportError_, _norm, _read_rows, parse_number
from .models import Artist, PlaylistPlacement, StreamStat

ALIASES = {
    "date": ["дата", "date", "день", "day", "дата стрима", "дата прослушивания"],
    "track": ["трек", "track", "название трека", "track title", "title", "название", "композиция"],
    "artist": ["артист", "исполнитель", "artist", "artists", "исполнители"],
    "platform": ["dsp", "площадка", "platform", "сервис", "store", "платформа"],
    "streams": ["стримы", "прослушивания", "streams", "количество прослушиваний", "plays", "прослушиваний"],
    "playlist": ["плейлист", "playlist", "название плейлиста"],
    "peak": ["пиковая позиция", "позиция", "peak position", "peak"],
    "days": ["количество дней в плейлисте", "дней в плейлисте", "дней", "days in playlist", "days"],
    "first": ["первая дата", "first date", "дата попадания", "добавлен"],
    "last": ["последняя дата", "last date"],
}

SPLIT_ARTISTS = re.compile(r"\s*(?:&|,|\bfeat\.?|\bft\.?|\bx\b|\bи\b)\s*", re.I)


def _cols(header):
    names = [_norm(h) for h in header]
    found = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            a = _norm(alias)
            idx = next((i for i, h in enumerate(names) if h == a and i not in found.values()), None)
            if idx is not None:
                found[field] = idx
                break
    return found


def _find(sheets, required):
    for rows in sheets:
        for i, row in enumerate(rows[:30]):
            cols = _cols(row)
            if required <= cols.keys():
                return row, rows[i + 1:], cols
    return None


def parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return dt.datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    return None


class ArtistMatcher:
    """Находит артистов кабинета по строке «Артист1 & Артист2 - Трек» или колонке «Артист»."""

    def __init__(self):
        self.by_name = {_norm(a.name): a for a in Artist.objects.all()}

    def split(self, full_title, artist_cell=None):
        title = str(full_title or "").strip()
        names = str(artist_cell or "").strip()
        if not names and " - " in title:
            names, title = title.split(" - ", 1)
        found = []
        for part in SPLIT_ARTISTS.split(names) if names else []:
            a = self.by_name.get(_norm(part))
            if a and a not in found:
                found.append(a)
        if not found and names:
            a = self.by_name.get(_norm(names))
            if a:
                found.append(a)
        return found, title.strip()


def _cell(row, idx):
    return row[idx] if idx is not None and idx < len(row) else None


def import_streams(uploaded_file, artist=None, period_start=None, period_end=None, source=""):
    """Возвращает {артист: число строк}, список не найденных артистов."""
    sheets = _read_rows(uploaded_file)
    table = _find(sheets, {"track", "streams"}) or _find(sheets, {"date"})
    if not table:
        raise ImportError_("Не нашёл колонки «Трек» и «Прослушивания» (или «Дата» с колонками-треками)")
    header, rows, cols = table
    records = []  # (date_from, date_to, artist_cell, track, platform, streams)

    if "streams" in cols and "track" in cols:
        for row in rows:
            track = _cell(row, cols["track"])
            if not track or _norm(track) in ("итого", "всего", "total"):
                continue
            d = parse_date(_cell(row, cols.get("date"))) if "date" in cols else None
            start, end = (d, d) if d else (period_start, period_end)
            if not start or not end:
                raise ImportError_("В файле нет колонки «Дата» — укажите период «с/по» при загрузке")
            streams = int(parse_number(_cell(row, cols["streams"])) or 0)
            records.append((start, end, _cell(row, cols.get("artist")), track,
                            str(_cell(row, cols.get("platform")) or "").strip(), streams))
    else:
        # «Широкая» выгрузка графика DataLens: строка = дата, колонка = трек
        date_idx = cols["date"]
        series = [(i, str(h).strip()) for i, h in enumerate(header) if i != date_idx and h not in (None, "")]
        for row in rows:
            d = parse_date(_cell(row, date_idx))
            if not d:
                continue
            for i, name in series:
                v = parse_number(_cell(row, i))
                if v:
                    records.append((d, d, None, name, "", int(v)))

    matcher = ArtistMatcher()
    agg = defaultdict(int)
    missing = set()
    for start, end, artist_cell, track, platform, streams in records:
        if artist:
            targets, title = [artist], matcher.split(track, artist_cell)[1] if " - " in str(track) else str(track)
        else:
            targets, title = matcher.split(track, artist_cell)
        if not targets:
            missing.add(str(artist_cell or str(track).split(" - ")[0]).strip())
            continue
        for a in targets:
            agg[(a.pk, start, end, title[:500], platform[:200])] += streams

    counts = defaultdict(int)
    with transaction.atomic():
        for (artist_id, start, end, track, platform), streams in agg.items():
            StreamStat.objects.update_or_create(
                artist_id=artist_id, period_start=start, period_end=end, track=track, platform=platform,
                defaults={"streams": streams, "source": source[:100]},
            )
            counts[artist_id] += 1
    names = {a.pk: a.name for a in Artist.objects.filter(pk__in=counts)}
    return {names[k]: v for k, v in counts.items()}, sorted(missing)


def import_playlists(uploaded_file, artist=None):
    sheets = _read_rows(uploaded_file)
    table = _find(sheets, {"track", "playlist"})
    if not table:
        raise ImportError_("Не нашёл колонки «Трек» и «Плейлист»")
    _, rows, cols = table
    matcher = ArtistMatcher()
    counts = defaultdict(int)
    missing = set()
    last_track = last_platform = None
    with transaction.atomic():
        for row in rows:
            track = _cell(row, cols["track"]) or last_track      # в DataLens ячейка трека объединена
            platform = str(_cell(row, cols.get("platform")) or last_platform or "").strip()
            last_track, last_platform = track, platform
            playlist = str(_cell(row, cols["playlist"]) or "").strip()
            if not track or not playlist:
                continue
            if artist:
                targets, title = [artist], matcher.split(track)[1] if " - " in str(track) else str(track)
            else:
                targets, title = matcher.split(track, _cell(row, cols.get("artist")))
            if not targets:
                missing.add(str(track).split(" - ")[0].strip())
                continue
            peak = parse_number(_cell(row, cols.get("peak")))
            days = parse_number(_cell(row, cols.get("days")))
            for a in targets:
                PlaylistPlacement.objects.update_or_create(
                    artist=a, track=title[:500], platform=platform[:200], playlist=playlist[:300],
                    defaults={
                        "peak_position": int(peak) if peak else None,
                        "days": int(days or 0),
                        "first_date": parse_date(_cell(row, cols.get("first"))),
                        "last_date": parse_date(_cell(row, cols.get("last"))),
                    },
                )
                counts[a.name] += 1
    return dict(counts), sorted(missing)


def recent_summary(artist):
    """Последний загруженный период: всего, по площадкам, по трекам, плюс дневной ряд, если есть."""
    qs = artist.stream_stats.all()
    latest = qs.order_by("-period_end").first()
    if not latest:
        return None
    # Ежедневные данные: ряд за последние 30 дней
    series = defaultdict(int)
    daily_qs = qs.filter(period_start=F("period_end"), period_start__gte=latest.period_end - dt.timedelta(days=29))
    for r in daily_qs:
        series[r.period_start] += r.streams
    periods = []
    for start, end in qs.values_list("period_start", "period_end").distinct().order_by("-period_end", "-period_start"):
        if start != end and (start, end) not in periods:
            periods.append((start, end))
    blocks = []
    for start, end in periods[:2]:
        rows = qs.filter(period_start=start, period_end=end)
        total = sum(r.streams for r in rows)
        plat = defaultdict(int)
        trk = defaultdict(int)
        for r in rows:
            plat[r.platform or "Все площадки"] += r.streams
            trk[r.track] += r.streams
        top = max(plat.values()) if plat else 1
        blocks.append({
            "start": start, "end": end, "days": (end - start).days + 1, "total": total,
            "platforms": [{"name": k, "streams": v, "share": v / total * 100 if total else 0,
                           "bar": v / top * 100 if top else 0}
                          for k, v in sorted(plat.items(), key=lambda x: -x[1])],
            "tracks": sorted(trk.items(), key=lambda x: -x[1]),
        })
    days = sorted(series.items())
    mx = max((v for _, v in days), default=0) or 1
    return {
        "blocks": blocks,
        "daily": [{"date": d, "streams": v, "bar": v / mx * 100} for d, v in days],
        "daily_total": sum(v for _, v in days),
        "updated": latest.uploaded_at,
    }
