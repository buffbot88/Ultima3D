"""Generate the dashboard tray icons (stdlib only): green idle, orange busy, gray off.

Run from the repo root:  python dashboard/icons/generate_icons.py
"""
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

COLORS = {
    "idle": (46, 160, 67),    # green: daemon up, worker quiet
    "busy": (230, 150, 20),   # orange: a build/render/bake/validate is running
    "off": (128, 128, 128),   # gray: daemon not running
}


def _png_circle(rgb, size=32):
    r, g, b = rgb
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter byte
        for x in range(size):
            dx, dy = x - size / 2 + 0.5, y - size / 2 + 0.5
            inside = dx * dx + dy * dy <= (size / 2 - 2) ** 2
            raw += bytes((r, g, b, 255 if inside else 0))

    def chunk(ctype, payload):
        c = struct.pack(">I", len(payload)) + ctype + payload
        return c + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    for name, rgb in COLORS.items():
        (HERE / f"tray-{name}.png").write_bytes(_png_circle(rgb))
        print(f"wrote tray-{name}.png")


if __name__ == "__main__":
    main()
