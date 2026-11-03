"""Headless tileable texture synthesis (stdlib only, no Photoshop needed).

Value-noise fBm on a wrapping lattice, so every map tiles seamlessly. For each
preset we emit an albedo / roughness / normal trio that drops straight into
the Blender bake channels or a GLB material. Deterministic per (preset, seed).
"""

from __future__ import annotations

import math
import random
import struct
import zlib
from pathlib import Path

PRESETS = ("stone", "wood_grain", "noise")

_ALBEDO = {
    "stone": ((88, 86, 84), (170, 167, 161)),
    "wood_grain": ((101, 67, 33), (178, 134, 84)),
    "noise": ((96, 96, 96), (168, 168, 168)),
}
_NORMAL_STRENGTH = {"stone": 2.0, "wood_grain": 1.2, "noise": 0.8}
_BASE_FREQ = {"stone": 6, "wood_grain": 4, "noise": 8}
_RINGS = 14  # wood band count across the tile


def _lattice(n: int, seed: int) -> list[list[float]]:
    rng = random.Random(seed)
    return [[rng.random() for _ in range(n)] for _ in range(n)]


def _noise(latt: list[list[float]], x: float, y: float) -> float:
    n = len(latt)
    xi, yi = math.floor(x), math.floor(y)
    xf, yf = x - xi, y - yi
    sx = xf * xf * (3.0 - 2.0 * xf)
    sy = yf * yf * (3.0 - 2.0 * yf)
    x0, x1, y0, y1 = xi % n, (xi + 1) % n, yi % n, (yi + 1) % n
    a = latt[y0][x0] + (latt[y0][x1] - latt[y0][x0]) * sx
    b = latt[y1][x0] + (latt[y1][x1] - latt[y1][x0]) * sx
    return a + (b - a) * sy


def _fbm(size: int, base: int, octaves: int, seed: int) -> list[list[float]]:
    latts = [_lattice(base * (1 << o), seed + o * 7919) for o in range(octaves)]
    out = [[0.0] * size for _ in range(size)]
    amp, total = 1.0, 0.0
    for o, latt in enumerate(latts):
        freq = (base * (1 << o)) / size
        for y in range(size):
            row = out[y]
            for x in range(size):
                row[x] += _noise(latt, x * freq, y * freq) * amp
        total += amp
        amp *= 0.5
    for y in range(size):
        row = out[y]
        for x in range(size):
            row[x] /= total
    return out


def _height(preset: str, size: int, seed: int) -> list[list[float]]:
    base = _BASE_FREQ[preset]
    h = _fbm(size, base, 4, seed)
    if preset == "wood_grain":
        warp = _fbm(size, base, 3, seed + 101)
        out = [[0.0] * size for _ in range(size)]
        for y in range(size):
            for x in range(size):
                u = x / size * _RINGS + warp[y][x] * 3.0
                band = abs(math.sin(u * math.pi))
                out[y][x] = min(1.0, max(0.0, h[y][x] * 0.45 + band * 0.55))
        return out
    return h


def _write_png(path: Path, pixels: list[bytes], size: int) -> None:
    def chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + row for row in pixels)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    )


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> bytes:
    return bytes([round(a[i] + (b[i] - a[i]) * t) for i in range(3)])


def make_texture(preset: str, size: int = 256, seed: int = 0, out_dir: str | Path = ".", name: str | None = None) -> dict:
    if preset not in PRESETS:
        raise ValueError(f"unknown texture preset {preset!r}; choose from {list(PRESETS)}")
    size = min(1024, max(64, int(size)))
    tag = name or f"{preset}_{seed}"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    h = _height(preset, size, seed)
    lo, hi = _ALBEDO[preset]
    strength = _NORMAL_STRENGTH[preset]

    albedo_rows, rough_rows, normal_rows = [], [], []
    for y in range(size):
        a_row, r_row, n_row = bytearray(), bytearray(), bytearray()
        for x in range(size):
            v = h[y][x]
            a_row += _lerp(lo, hi, v)
            g = round(255 * (0.55 + 0.45 * (1.0 - v)))  # crevices rougher
            r_row += bytes((g, g, g))
            dx = (h[y][(x + 1) % size] - h[y][(x - 1) % size]) * strength
            dy = (h[(y + 1) % size][x] - h[(y - 1) % size][x]) * strength
            inv = 1.0 / math.sqrt(dx * dx + dy * dy + 1.0)
            n_row += bytes((round((-dx * inv * 0.5 + 0.5) * 255),
                            round((-dy * inv * 0.5 + 0.5) * 255),
                            round((inv * 0.5 + 0.5) * 255)))
        albedo_rows.append(bytes(a_row))
        rough_rows.append(bytes(r_row))
        normal_rows.append(bytes(n_row))

    files = {}
    for kind, rows in (("albedo", albedo_rows), ("rough", rough_rows), ("normal", normal_rows)):
        p = out / f"{tag}_{kind}.png"
        _write_png(p, rows, size)
        files[kind] = str(p)
    return {"preset": preset, "size": size, "seed": seed, "tileable": True, "files": files}
