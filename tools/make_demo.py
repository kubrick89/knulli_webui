#!/usr/bin/env python3
"""Create a fake ROM library to try Knulli WebUI on a PC.

Usage:
    python3 tools/make_demo.py demo
    python3 knulli_webui/webui.py --config demo/config.json -v
"""
import json
import os
import random
import struct
import sys
import zlib

SYSTEMS = [
    ("snes", "Super Nintendo", "Nintendo", "1990", ".smc .sfc .zip .7z"),
    ("gba", "Game Boy Advance", "Nintendo", "2001", ".gba .zip .7z"),
    ("megadrive", "Mega Drive", "Sega", "1988", ".md .bin .gen .zip .7z"),
    ("psx", "PlayStation", "Sony", "1994", ".cue .chd .m3u .pbp"),
    ("mame", "MAME", "Arcade", "1980", ".zip .7z"),
    ("n64", "Nintendo 64", "Nintendo", "1996", ".z64 .n64 .zip"),
]
GENRES = ["Platform", "Action / Platform", "RPG", "Puzzle", "Shoot'em up", "Racing", "Fighting", "Sports"]
DEVS = ["Nintendo", "Capcom", "Konami", "Sega", "Square", "Hudson Soft", "Treasure"]
WORDS = ["Super", "Mega", "Dragon", "Quest", "Star", "Fighter", "Legend", "Space", "Racer", "Castle",
         "Ninja", "Kart", "Puzzle", "World", "Turbo", "Shadow", "Crystal", "Island"]


def png(color, w=60, h=80):
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main(out):
    rnd = random.Random(42)
    roms = os.path.join(out, "roms")
    cfg = ['<?xml version="1.0"?>', "<systemList>"]
    for name, full, manu, year, exts in SYSTEMS:
        cfg.append("<system><name>%s</name><fullname>%s</fullname><manufacturer>%s</manufacturer>"
                   "<release>%s</release><path>/userdata/roms/%s</path><extension>%s</extension></system>"
                   % (name, full, manu, year, name, exts))
        sysdir = os.path.join(roms, name)
        os.makedirs(os.path.join(sysdir, "images"), exist_ok=True)
        open(os.path.join(sysdir, "_info.txt"), "w").write("Put your %s ROMs here\n" % full)
        games = []
        for i in range(rnd.randint(8, 30)):
            title = " ".join(rnd.sample(WORDS, rnd.randint(2, 3))) + ("" if i % 4 else " II")
            ext = exts.split()[0]
            fname = "%s (Europe)%s" % (title, ext)
            if os.path.exists(os.path.join(sysdir, fname)):
                continue
            with open(os.path.join(sysdir, fname), "wb") as f:
                f.write(os.urandom(rnd.randint(2, 200) * 1024))
            if rnd.random() < 0.8:  # 80% with metadata
                img = "./images/%s-image.png" % os.path.splitext(fname)[0]
                with open(os.path.join(sysdir, img[2:]), "wb") as f:
                    f.write(png((rnd.randint(40, 220), rnd.randint(40, 220), rnd.randint(40, 220))))
                games.append(
                    "\t<game>\n\t\t<path>./%s</path>\n\t\t<name>%s</name>\n\t\t<desc>A %s classic from %d.</desc>\n"
                    "\t\t<image>%s</image>\n\t\t<rating>%.2f</rating>\n\t\t<releasedate>%d0101T000000</releasedate>\n"
                    "\t\t<developer>%s</developer>\n\t\t<publisher>%s</publisher>\n\t\t<genre>%s</genre>\n"
                    "\t\t<players>%s</players>\n%s%s\t</game>" % (
                        esc(fname), esc(title), full, int(year) + rnd.randint(0, 8), esc(img), rnd.random(),
                        int(year) + rnd.randint(0, 8), rnd.choice(DEVS), rnd.choice(DEVS), esc(rnd.choice(GENRES)),
                        rnd.choice(["1", "1-2", "1-4", "2"]),
                        "\t\t<favorite>true</favorite>\n" if rnd.random() < 0.2 else "",
                        "\t\t<playcount>%d</playcount>\n\t\t<gametime>%d</gametime>\n\t\t<lastplayed>2026%02d%02dT201500</lastplayed>\n"
                        % (rnd.randint(1, 30), rnd.randint(300, 40 * 3600), rnd.randint(1, 10), rnd.randint(1, 22))
                        if rnd.random() < 0.4 else ""))
        with open(os.path.join(sysdir, "gamelist.xml"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0"?>\n<gameList>\n%s\n</gameList>\n' % "\n".join(games))
    # Empty systems (as on Batocera, where all the folders already exist)
    for empty in ("nes", "gb", "psp"):
        os.makedirs(os.path.join(roms, empty), exist_ok=True)
    cfg.append("<system><name>nes</name><fullname>NES</fullname><path>/userdata/roms/nes</path>"
               "<extension>.nes .zip</extension></system></systemList>")
    with open(os.path.join(out, "es_systems.cfg"), "w") as f:
        f.write("\n".join(cfg))

    # EmulationStation collections, written like ES does (paths relative to the userdata folder)
    es_dir = os.path.join(out, "system", "configs", "emulationstation")
    col_dir = os.path.join(es_dir, "collections")
    os.makedirs(col_dir, exist_ok=True)
    picks = {"Couch co-op": ("snes", "megadrive", "n64"), "Handheld classics": ("gba",)}
    for name, systems in picks.items():
        lines = []
        for system in systems:
            files = sorted(f for f in os.listdir(os.path.join(roms, system)) if f.endswith(")" + os.path.splitext(f)[1]))
            lines += ["./roms/%s/%s" % (system, f) for f in files[:4]]
        with open(os.path.join(col_dir, "custom-%s.cfg" % name), "w", encoding="utf-8") as f:
            f.write("\n".join(lines + ["./roms/snes/Deleted game (Europe).smc"]) + "\n")
    with open(os.path.join(es_dir, "es_settings.cfg"), "w") as f:
        f.write('<?xml version="1.0"?>\n<config>\n\t<string name="CollectionSystemsCustom" value="Couch co-op" />\n</config>\n')

    # Saves and save states (RetroArch naming, as configured by Knulli)
    with open(os.path.join(es_dir, "es_savestates.cfg"), "w") as f:
        f.write('<savestates>\n  <emulator name="libretro" directory="{{system}}" file="{{romfilename}}.state{{slot}}" '
                'image="{{romfilename}}.state{{slot}}.png" nofileextension="true" firstslot="0" autosave="true" '
                'autosave_file="{{romfilename}}.state.auto" autosave_image="{{romfilename}}.state.auto.png" />\n</savestates>\n')
    saves = os.path.join(out, "saves")
    for system in ("snes", "gba", "megadrive"):
        os.makedirs(os.path.join(saves, system), exist_ok=True)
        files = sorted(os.listdir(os.path.join(roms, system)))
        for fname in [f for f in files if not f.startswith("_") and os.path.isfile(os.path.join(roms, system, f))][:3]:
            stem = os.path.splitext(fname)[0]
            with open(os.path.join(saves, system, stem + ".srm"), "wb") as f:
                f.write(os.urandom(8192))
            for slot in ("", "1", "2", ".auto")[:rnd.randint(1, 4)]:
                with open(os.path.join(saves, system, "%s.state%s" % (stem, slot)), "wb") as f:
                    f.write(os.urandom(rnd.randint(20, 90) * 1024))
                with open(os.path.join(saves, system, "%s.state%s.png" % (stem, slot)), "wb") as f:
                    f.write(png((rnd.randint(30, 230), rnd.randint(30, 230), rnd.randint(30, 230)), 160, 120))
    with open(os.path.join(saves, "snes", "Removed Game (USA).srm"), "wb") as f:
        f.write(os.urandom(2048))

    # BIOS database (same structure as Knulli's batocera-systems) and a few BIOS files
    good, bad = b"demo gba bios", b"corrupted"
    import hashlib
    db = {"gba": {"name": "Game Boy Advance", "biosFiles": [{"md5": hashlib.md5(good).hexdigest(), "file": "bios/gba_bios.bin"}]},
          "psx": {"name": "PlayStation", "biosFiles": [
              {"md5": "490f666e1afb15b7362b406ed1cea246", "file": "bios/scph5501.bin"},
              {"md5": "32736f17079d0b2b7024407c39bd3050", "file": "bios/scph5500.bin"},
              {"md5": "8dd7d5296a650fac7319bce665a6a53c", "file": "bios/scph5502.bin"}]},
          "megadrive": {"name": "Mega Drive", "biosFiles": [
              {"md5": "45e298905a08f9cfb38fd504cd6dbc84", "file": "bios/bios_MD.bin", "emulator": "libretro", "core": "genesisplusgx"}]},
          "n64": {"name": "Nintendo 64", "biosFiles": [{"md5": "", "file": "bios/Mupen64plus/IPL.n64"}]}}
    with open(os.path.join(out, "batocera-systems"), "w") as f:
        f.write("#!/usr/bin/env python\n# demo BIOS list\nsystems = %r\n" % db)
    os.makedirs(os.path.join(out, "bios"), exist_ok=True)
    with open(os.path.join(out, "bios", "gba_bios.bin"), "wb") as f:
        f.write(good)
    with open(os.path.join(out, "bios", "scph5501.bin"), "wb") as f:
        f.write(bad)

    # Screenshots, named like RetroArch does (<rom>-<yymmdd>-<hhmmss>.png)
    shots = os.path.join(out, "screenshots")
    os.makedirs(shots, exist_ok=True)
    for system in ("snes", "gba", "n64"):
        files = sorted(f for f in os.listdir(os.path.join(roms, system)) if f.endswith(")" + os.path.splitext(f)[1]))[:3]
        for i, fname in enumerate(files):
            name = "%s-2610%02d-2015%02d.png" % (os.path.splitext(fname)[0], i + 10, i)
            with open(os.path.join(shots, name), "wb") as f:
                f.write(png((rnd.randint(30, 230), rnd.randint(30, 230), rnd.randint(30, 230)), 320, 240))
    with open(os.path.join(shots, "screenshot-2026.10.20-21h05.33.png"), "wb") as f:
        f.write(png((40, 40, 120), 320, 240))

    out_abs = os.path.abspath(out)
    with open(os.path.join(out, "config.json"), "w") as f:
        json.dump({"port": 8088, "roms_dir": os.path.join(out_abs, "roms"), "userdata_dir": out_abs,
                   "es_systems_files": [os.path.join(out_abs, "es_systems.cfg")],
                   "collections_dir": os.path.join(out_abs, col_dir[len(out) + 1:]),
                   "es_settings_file": os.path.join(out_abs, es_dir[len(out) + 1:], "es_settings.cfg"),
                   "saves_dir": os.path.join(out_abs, "saves"),
                   "es_savestates_files": [os.path.join(out_abs, es_dir[len(out) + 1:], "es_savestates.cfg")],
                   "bios_db": os.path.join(out_abs, "batocera-systems"),
                   "screenshots_dir": os.path.join(out_abs, "screenshots")}, f, indent=2)
    print("Demo library created in", out_abs)
    print("Run: python3 knulli_webui/webui.py --config %s -v" % os.path.join(out_abs, "config.json"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo")
