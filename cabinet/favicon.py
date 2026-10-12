"""Иконка сайта (буква Q из логотипа QBIQ): вкладка браузера, ярлык на телефоне.

Картинки рисуются кодом (без файлов): SVG для современных браузеров,
PNG для /favicon.ico и для ярлыка на iPhone.
"""

import struct
import zlib
from functools import lru_cache

from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

COLOR = (248, 85, 84)

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-3.5 0 60 60"><path fill="#F85554" fill-rule="evenodd" '
    'd="M1.5 0h50A1.5 1.5 0 0 1 53 1.5v49.5a1.5 1.5 0 0 1-1.5 1.5h-18V57a3 3 0 0 1-3 3H23a3 3 0 0 1-3-3v-4.5'
    'H1.5A1.5 1.5 0 0 1 0 51V1.5A1.5 1.5 0 0 1 1.5 0zM14.5 13h24a1 1 0 0 1 1 1v25.5h-6V38a3 3 0 0 0-3-3H23'
    'a3 3 0 0 0-3 3v1.5h-6.5V14a1 1 0 0 1 1-1z"/></svg>'
)

# Буква Q в координатах 53×60: (x0, y0, x1, y1, радиус, закрашено?)
_SHAPES = (
    (0, 0, 53, 52.5, 1.5, True),     # корпус
    (20, 48, 33.5, 60, 3, True),     # хвостик снизу
    (13.5, 13, 39.5, 39.5, 1, False),  # окно
    (20, 35, 33.5, 45, 3, True),     # выступ внутри окна
)


def _in_rrect(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _filled(x, y):
    on = False
    for x0, y0, x1, y1, r, fill in _SHAPES:
        if _in_rrect(x, y, x0, y0, x1, y1, r):
            on = fill
    return on


def _png(size, pad=0.0, background=None):
    full = 60 + 2 * pad
    ox, oy = (full - 53) / 2, pad
    k = 4  # сглаживание: 4×4 подвыборки на пиксель
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            hit = 0
            for sy in range(k):
                for sx in range(k):
                    gx = (px + (sx + 0.5) / k) * full / size - ox
                    gy = (py + (sy + 0.5) / k) * full / size - oy
                    hit += _filled(gx, gy)
            a = hit / (k * k)
            if background:
                row += bytes(round(c * a + b * (1 - a)) for c, b in zip(COLOR, background)) + b"\xff"
            else:
                row += bytes(COLOR) + bytes([round(255 * a)])
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


@lru_cache(maxsize=None)
def favicon_png():
    return _png(48)


@lru_cache(maxsize=None)
def apple_png():
    return _png(180, pad=12, background=(255, 255, 255))


_CACHE = cache_control(public=True, max_age=7 * 24 * 3600)


@require_GET
@_CACHE
def favicon_svg(request):
    return HttpResponse(SVG, content_type="image/svg+xml")


@require_GET
@_CACHE
def favicon_ico(request):
    # Современные браузеры принимают PNG по адресу /favicon.ico
    return HttpResponse(favicon_png(), content_type="image/png")


@require_GET
@_CACHE
def apple_touch_icon(request):
    return HttpResponse(apple_png(), content_type="image/png")
