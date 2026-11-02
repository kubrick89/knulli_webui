#!/usr/bin/env python3
"""Build the system icons shipped with Knulli WebUI.

The icons come from "Retro game console icons" by KyleBing
(https://github.com/KyleBing/retro-game-console-icons, CC BY 4.0).
They are cropped to their visible area and resized to fit SIZE x SIZE.

Usage:
    git clone --depth 1 https://github.com/KyleBing/retro-game-console-icons /tmp/icons
    python3 tools/build_icons.py /tmp/icons

Only the Python standard library is used (an 8-bit RGBA PNG codec is included).
"""
import json
import os
import struct
import sys
import zlib

SIZE = 128
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "knulli_webui", "static", "icons", "systems")

TRIMUI = "series_trimui/300w@1x/%s.png"
MIYOO = "series_miyoo/132w@1x/%s@132w.png"

# Knulli/Batocera system folder -> icon in the source repository.
# Folder names come from Knulli's es_systems.yml; a few common aliases are included.
SYSTEMS = {
    # Nintendo
    "nes": TRIMUI % "FC", "famicom": TRIMUI % "FC", "fds": TRIMUI % "FDS",
    "snes": TRIMUI % "SFC", "sfc": TRIMUI % "SFC", "snes-msu1": TRIMUI % "SFCMSU",
    "satellaview": TRIMUI % "SATELLAVIEW", "sufami": TRIMUI % "SUFAMI",
    "n64": TRIMUI % "N64", "n64dd": TRIMUI % "N64DD", "gamecube": TRIMUI % "NGC",
    "gb": TRIMUI % "GB", "gb2players": TRIMUI % "GB", "gbc": TRIMUI % "GBC", "gbc2players": TRIMUI % "GBC",
    "sgb": TRIMUI % "SGB", "gba": TRIMUI % "GBA", "nds": TRIMUI % "NDS", "virtualboy": TRIMUI % "VB",
    "gameandwatch": TRIMUI % "GW", "lcdgames": TRIMUI % "GW", "pokemini": TRIMUI % "POKEMINI",
    # Sega
    "mastersystem": TRIMUI % "MS", "megadrive": TRIMUI % "MD", "genesis": TRIMUI % "MD",
    "msu-md": TRIMUI % "MDMSU", "segacd": TRIMUI % "SEGACD", "sega32x": TRIMUI % "SEGA32X",
    "gamegear": TRIMUI % "GG", "sg1000": TRIMUI % "SG1000", "saturn": TRIMUI % "SATURN",
    "dreamcast": TRIMUI % "DC", "naomi": TRIMUI % "NAOMI", "naomi2": TRIMUI % "NAOMI",
    "atomiswave": TRIMUI % "ATOMISWAVE",
    # Sony
    "psx": TRIMUI % "PS", "psp": TRIMUI % "PSP",
    # NEC
    "pcengine": TRIMUI % "PCE", "tg16": TRIMUI % "PCE", "pcenginecd": TRIMUI % "PCECD",
    "supergrafx": TRIMUI % "SFX", "pcfx": TRIMUI % "PCFX", "pc88": TRIMUI % "PC88", "pc98": TRIMUI % "PC98",
    # SNK
    "neogeo": TRIMUI % "NEOGEO", "neogeocd": TRIMUI % "NEOCD", "ngp": TRIMUI % "NGP", "ngpc": MIYOO % "ngpc",
    # Bandai and other handhelds
    "wswan": TRIMUI % "WS", "wswanc": TRIMUI % "WSC", "supervision": TRIMUI % "SUPERVISION",
    "megaduck": TRIMUI % "MEGADUCK", "arduboy": TRIMUI % "ARDUBOY", "lynx": TRIMUI % "LYNX",
    # Atari
    "atari2600": TRIMUI % "ATARI2600", "atari5200": TRIMUI % "ATARI5200", "atari7800": TRIMUI % "ATARI7800",
    "atari800": TRIMUI % "ATARI800", "xegs": TRIMUI % "ATARI800", "atarist": TRIMUI % "ATARIST",
    # Other consoles
    "3do": TRIMUI % "PANASONIC", "colecovision": TRIMUI % "COLECO", "intellivision": TRIMUI % "INTELLIVISION",
    "channelf": TRIMUI % "CHANNELF", "vectrex": TRIMUI % "VECTREX", "o2em": TRIMUI % "VIDEOPAC",
    "videopacplus": TRIMUI % "VIDEOPAC", "uzebox": TRIMUI % "UZEBOX",
    # Arcade
    "mame": TRIMUI % "MAME", "arcade": TRIMUI % "ARCADE", "fbneo": TRIMUI % "FBNEO",
    "advmame": TRIMUI % "ADVMAME", "cps1": TRIMUI % "CPS1", "cps2": TRIMUI % "CPS2", "cps3": TRIMUI % "CPS3",
    "daphne": TRIMUI % "DAPHNE",
    # Computers
    "c64": TRIMUI % "C64", "c20": TRIMUI % "VIC20", "cplus4": TRIMUI % "CPLUS4", "pet": TRIMUI % "CPET",
    "amiga": TRIMUI % "AMIGA", "amiga500": TRIMUI % "AMIGA", "amiga1200": TRIMUI % "AMIGA",
    "amigacd32": TRIMUI % "AMIGACD", "amigacdtv": TRIMUI % "AMIGACDTV", "amstradcpc": TRIMUI % "CPC",
    "msx": TRIMUI % "MSX", "msx1": TRIMUI % "MSX", "msx2": TRIMUI % "MSX2", "msx2+": TRIMUI % "MSX2",
    "msxturbor": TRIMUI % "MSX2", "zxspectrum": TRIMUI % "ZXS", "x68000": TRIMUI % "X68000", "x1": TRIMUI % "X1",
    "thomson": TRIMUI % "THOMSON", "macintosh": TRIMUI % "VMAC", "dos": TRIMUI % "DOS",
    # Fantasy consoles, engines and ports
    "pico8": TRIMUI % "PICO", "tic80": TRIMUI % "TIC", "lowresnx": TRIMUI % "LOWRESNX", "lutro": TRIMUI % "LUTRO",
    "scummvm": TRIMUI % "SCUMMVM", "openbor": TRIMUI % "OPENBOR", "easyrpg": TRIMUI % "EASYRPG",
    "prboom": TRIMUI % "DOOM", "gzdoom": TRIMUI % "DOOM", "tyrquake": TRIMUI % "TYRQUAKE",
    "ecwolf": MIYOO % "wolf", "cannonball": TRIMUI % "CANNONBALL", "cavestory": TRIMUI % "CAVESTORY",
    "xrick": TRIMUI % "XRICK", "ports": TRIMUI % "PORTS", "mpv": TRIMUI % "VIDEOS",
}


# --------------------------------------------------------------------------
# Minimal PNG codec (8-bit RGBA, non-interlaced)
# --------------------------------------------------------------------------

def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s: not a PNG" % path)
    pos, idat, header = 8, [], None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    width, height, depth, color, _comp, _filt, interlace = header
    if (depth, color, interlace) != (8, 6, 0):
        raise ValueError("%s: only 8-bit RGBA non-interlaced PNGs are supported" % path)
    raw = zlib.decompress(b"".join(idat))
    stride, bpp = width * 4, 4
    rows, prev = [], bytearray(stride)
    for y in range(height):
        ftype = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        rows.append(line)
        prev = line
    return width, height, rows


def write_png(path, width, height, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------
# Crop and resize
# --------------------------------------------------------------------------

def crop_visible(width, height, rows, threshold=8):
    xs = [x for y in range(height) for x in range(width) if rows[y][x * 4 + 3] > threshold]
    ys = [y for y in range(height) if any(rows[y][x * 4 + 3] > threshold for x in range(width))]
    if not xs:
        return width, height, rows
    x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
    return x1 - x0, y1 - y0, [r[x0 * 4:x1 * 4] for r in rows[y0:y1]]


def _weights(src, dst):
    """Area-average weights for resampling `src` samples down to `dst`."""
    scale = src / dst
    out = []
    for i in range(dst):
        start, end = i * scale, (i + 1) * scale
        w = []
        for s in range(int(start), min(src, int(end + 0.999999))):
            cover = min(end, s + 1) - max(start, s)
            if cover > 0:
                w.append((s, cover / scale))
        out.append(w)
    return out


def resize(width, height, rows, new_w, new_h):
    # Premultiplied alpha avoids dark fringes around transparent edges
    pm = []
    for r in rows:
        line = []
        for x in range(width):
            cr, cg, cb, ca = r[x * 4:x * 4 + 4]
            line.append((cr * ca, cg * ca, cb * ca, ca))
        pm.append(line)
    wx, wy = _weights(width, new_w), _weights(height, new_h)
    horiz = []
    for line in pm:
        out = []
        for w in wx:
            acc = [0.0, 0.0, 0.0, 0.0]
            for s, f in w:
                px = line[s]
                acc[0] += px[0] * f; acc[1] += px[1] * f; acc[2] += px[2] * f; acc[3] += px[3] * f
            out.append(acc)
        horiz.append(out)
    result = []
    for w in wy:
        line = bytearray()
        for x in range(new_w):
            acc = [0.0, 0.0, 0.0, 0.0]
            for s, f in w:
                px = horiz[s][x]
                acc[0] += px[0] * f; acc[1] += px[1] * f; acc[2] += px[2] * f; acc[3] += px[3] * f
            a = acc[3]
            if a > 0:
                line += bytes((min(255, round(acc[0] / a)), min(255, round(acc[1] / a)),
                               min(255, round(acc[2] / a)), min(255, round(a))))
            else:
                line += b"\x00\x00\x00\x00"
        result.append(line)
    return result


def build_icon(src, dst):
    width, height, rows = crop_visible(*read_png(src))
    scale = min(1.0, SIZE / max(width, height))
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    if (new_w, new_h) != (width, height):
        rows = resize(width, height, rows, new_w, new_h)
    write_png(dst, new_w, new_h, rows)


def main(repo):
    os.makedirs(OUT_DIR, exist_ok=True)
    built, mapping = {}, {}
    for system, rel in sorted(SYSTEMS.items()):
        src = os.path.join(repo, rel)
        if not os.path.isfile(src):
            print("missing in source repository:", rel)
            continue
        name = os.path.splitext(os.path.basename(rel))[0].replace("@132w", "").lower() + ".png"
        if rel not in built:
            build_icon(src, os.path.join(OUT_DIR, name))
            built[rel] = name
        mapping[system] = built[rel]
    with open(os.path.join(OUT_DIR, "map.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=1, sort_keys=True)
        f.write("\n")
    print("%d icons for %d systems written to %s" % (len(built), len(mapping), OUT_DIR))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
