"""Скачивание обложки по ссылке (для релизов)."""

import os
import urllib.request
from urllib.parse import urlparse

from django.core.files.base import ContentFile

MAX_BYTES = 8 * 1024 * 1024
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def fetch_cover(release):
    """Скачивает release.cover_url в release.cover. Возвращает текст ошибки или None."""
    url = release.cover_url
    if urlparse(url).scheme not in ("http", "https"):
        return "Ссылка должна начинаться с http:// или https://"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (qbiq-cabinet)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ctype = (resp.headers.get_content_type() or "").lower()
            if ctype not in EXT:
                return f"По ссылке не картинка ({ctype or 'неизвестный тип'})"
            data = resp.read(MAX_BYTES + 1)
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось скачать обложку: {exc}"
    if len(data) > MAX_BYTES:
        return "Картинка больше 8 МБ"
    name = os.path.splitext(os.path.basename(urlparse(url).path))[0][:40] or "cover"
    release.cover.save(f"{name}{EXT[ctype]}", ContentFile(data), save=False)
    return None
