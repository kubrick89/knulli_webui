"""Knulli WebUI integration tests: start the server on a temporary library.

    python3 -m unittest discover -s tests -v
"""
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "knulli_webui"))
import webui  # noqa: E402

ES_SYSTEMS = """<?xml version="1.0"?><systemList>
<system><name>snes</name><fullname>Super Nintendo</fullname><path>/userdata/roms/snes</path><extension>.sfc .zip</extension></system>
<system><name>psx</name><fullname>PlayStation</fullname><path>/userdata/roms/psx</path><extension>.cue .m3u .ps3</extension></system>
</systemList>"""

GAMELIST = """<?xml version="1.0"?>
<gameList>
  <game><path>./Mario.sfc</path><name>Super Mario World</name><desc>Platform</desc>
    <image>./images/Mario-image.png</image><rating>0.9</rating><releasedate>19901121T000000</releasedate>
    <developer>Nintendo</developer><genre>Platform</genre><players>1-2</players><favorite>true</favorite>
    <playcount>3</playcount></game>
  <game><path>./Zelda.sfc</path><name>Zelda</name><image>./images/shared.png</image></game>
  <game><path>./Other.sfc</path><name>Other</name><image>./images/shared.png</image></game>
</gameList>"""


class WebUITest(unittest.TestCase):
    auth = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        roms = os.path.join(cls.tmp, "roms")
        snes = os.path.join(roms, "snes")
        os.makedirs(os.path.join(snes, "images"))
        os.makedirs(os.path.join(roms, "psx", "Game.ps3", "USRDIR"))
        for name, data in (("Mario.sfc", b"M" * 5000), ("Zelda.sfc", b"Z" * 10), ("Other.sfc", b"O"),
                           ("notes.txt", b"x"), ("_info.txt", b"x")):
            with open(os.path.join(snes, name), "wb") as f:
                f.write(data)
        for name in ("Mario-image.png", "shared.png"):
            open(os.path.join(snes, "images", name), "wb").write(b"\x89PNG")
        open(os.path.join(roms, "psx", "Game.ps3", "USRDIR", "EBOOT.BIN"), "wb").write(b"E" * 100)
        open(os.path.join(snes, "gamelist.xml"), "w").write(GAMELIST)
        es = os.path.join(cls.tmp, "es_systems.cfg")
        open(es, "w").write(ES_SYSTEMS)
        cfg = webui.load_config(None)
        cfg.update({"host": "127.0.0.1", "port": 0, "roms_dir": roms, "userdata_dir": cls.tmp,
                    "es_systems_files": [es], "es_api": "http://127.0.0.1:9", "es_reload": False,
                    "collections_dir": os.path.join(cls.tmp, "system", "configs", "emulationstation", "collections"),
                    "es_settings_file": os.path.join(cls.tmp, "system", "configs", "emulationstation", "es_settings.cfg"),
                    "saves_dir": os.path.join(cls.tmp, "saves"),
                    "es_savestates_files": [os.path.join(cls.tmp, "es_savestates.cfg")],
                    "bios_db": os.path.join(cls.tmp, "batocera-systems"),
                    "bios_readme_files": [os.path.join(cls.tmp, "bios", "readme.txt")],
                    "screenshots_dir": os.path.join(cls.tmp, "screenshots")})
        if cls.auth:
            cfg["username"], cfg["password"] = cls.auth
        cls.cfg = cfg
        cls.server = webui.make_server(cfg)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp)

    def req(self, path, data=None, headers=None, method=None, csrf=True):
        h = dict(headers or {})
        if csrf:
            h["X-Requested-With"] = "knulli-webui"
        if self.auth:
            h["Authorization"] = "Basic " + base64.b64encode(":".join(self.auth).encode()).decode()
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def jreq(self, *a, **kw):
        status, body, _ = self.req(*a, **kw)
        return status, json.loads(body) if body else None


class LibraryTests(WebUITest):
    def test_index_and_static(self):
        status, body, h = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Knulli WebUI", body)
        self.assertEqual(self.req("/static/app.js")[0], 200)
        self.assertEqual(self.req("/static/../webui.py")[0], 404)
        # CSS/JS links carry the file mtime, so browsers never use a stale copy
        css = int(os.path.getmtime(os.path.join(webui.STATIC_DIR, "style.css")))
        self.assertIn(b'href="/static/style.css?v=%d"' % css, body)
        self.assertRegex(body, rb'src="/static/app\.js\?v=\d+"')
        self.assertEqual(self.req("/static/style.css?v=%d" % css)[0], 200)

    def test_telemetry(self):
        status, data = self.jreq("/api/telemetry")
        self.assertEqual(status, 200)
        for key in ("system", "cpu", "memory", "storage", "network", "power", "history"):
            self.assertIn(key, data)

    def test_roms_listing_and_metadata(self):
        status, data = self.jreq("/api/roms")
        self.assertEqual(status, 200)
        ids = {r["id"] for r in data["roms"]}
        self.assertIn("snes/Mario.sfc", ids)
        self.assertIn("psx/Game.ps3", ids)              # folder ROM
        self.assertNotIn("snes/notes.txt", ids)          # invalid extension
        mario = next(r for r in data["roms"] if r["id"] == "snes/Mario.sfc")
        self.assertEqual(mario["name"], "Super Mario World")
        self.assertTrue(mario["favorite"])
        self.assertEqual(mario["year"], 1990)
        self.assertEqual(mario["rating"], 4.5)
        self.assertEqual(mario["playersMax"], 2)
        self.assertNotIn("desc", mario)                  # the description comes from /api/rom
        self.assertEqual(self.jreq("/api/rom?id=snes/Mario.sfc")[1]["desc"], "Platform")
        status, body, h = self.req(mario["image"][len(""):])
        self.assertEqual(status, 200)
        self.assertEqual(h["Content-Type"], "image/png")
        systems = {s["name"]: s for s in data["systems"]}
        self.assertEqual(systems["snes"]["fullname"], "Super Nintendo")
        self.assertEqual(systems["snes"]["favorites"], 1)

    def test_system_icons(self):
        systems = {s["name"]: s for s in self.jreq("/api/systems")[1]["systems"]}
        self.assertEqual(systems["snes"]["icon"], "/static/icons/systems/sfc.png")
        self.assertEqual(systems["psx"]["icon"], "/static/icons/systems/ps.png")
        status, body, h = self.req(systems["snes"]["icon"])
        self.assertEqual((status, body[:8], h["Content-Type"]), (200, b"\x89PNG\r\n\x1a\n", "image/png"))
        self.assertIn("max-age", h["Cache-Control"])
        # every entry of map.json points to an existing icon
        with open(os.path.join(webui.ICONS_DIR, "map.json")) as f:
            mapping = json.load(f)
        self.assertGreater(len(mapping), 100)
        for name in set(mapping.values()):
            self.assertTrue(os.path.isfile(os.path.join(webui.ICONS_DIR, name)), name)

    def test_download_and_range(self):
        status, body, h = self.req("/api/download?id=snes/Mario.sfc")
        self.assertEqual((status, len(body)), (200, 5000))
        self.assertIn("attachment", h["Content-Disposition"])
        status, body, h = self.req("/api/download?id=snes/Mario.sfc", headers={"Range": "bytes=100-199"})
        self.assertEqual((status, len(body)), (206, 100))
        self.assertEqual(h["Content-Range"], "bytes 100-199/5000")

    def test_download_zip(self):
        status, body, _ = self.req("/api/download?id=psx/Game.ps3")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertEqual(z.namelist(), ["Game.ps3/USRDIR/EBOOT.BIN"])
        form = "ids=" + urllib.request.quote(json.dumps(["snes/Mario.sfc", "psx/Game.ps3"]))
        status, body, _ = self.req("/api/download", data=form.encode(), csrf=False,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertEqual(sorted(z.namelist()), ["psx/Game.ps3/USRDIR/EBOOT.BIN", "snes/Mario.sfc"])
            self.assertEqual(len(z.read("snes/Mario.sfc")), 5000)

    def test_path_traversal_rejected(self):
        for bad in ("../etc/passwd", "snes/../../x", "snes/../psx/Game.ps3", "snes/gamelist.xml", "/etc/passwd"):
            status, _, _ = self.req("/api/download?id=" + urllib.request.quote(bad))
            self.assertIn(status, (400, 404), bad)
        self.assertEqual(self.req("/api/media?p=../../etc/passwd")[0], 404)
        self.assertEqual(self.req("/api/media?p=snes/gamelist.xml")[0], 404)

    def test_upload_and_conflicts(self):
        status, data = self.jreq("/api/upload?system=snes&name=New%20Game.sfc", data=b"N" * 1234)
        self.assertEqual(status, 200, data)
        self.assertEqual(os.path.getsize(os.path.join(self.cfg["roms_dir"], "snes", "New Game.sfc")), 1234)
        self.assertIn("snes/New Game.sfc", {r["id"] for r in self.jreq("/api/roms")[1]["roms"]})
        self.assertEqual(self.jreq("/api/upload?system=snes&name=New%20Game.sfc", data=b"x")[0], 409)
        self.assertEqual(self.jreq("/api/upload?system=snes&name=New%20Game.sfc&overwrite=1", data=b"x")[0], 200)
        self.assertEqual(self.jreq("/api/upload?system=snes&name=..%2Fevil.sfc", data=b"x")[0], 400)
        self.assertEqual(self.jreq("/api/upload?system=nope&name=a.sfc", data=b"x")[0], 400)
        self.assertEqual(self.jreq("/api/upload?system=snes&name=b.sfc", data=b"x", csrf=False)[0], 403)
        leftovers = [f for f in os.listdir(os.path.join(self.cfg["roms_dir"], "snes")) if f.endswith(".part")]
        self.assertEqual(leftovers, [])

    def test_delete_updates_gamelist_and_media(self):
        snes = os.path.join(self.cfg["roms_dir"], "snes")
        body = json.dumps({"ids": ["snes/Zelda.sfc", "snes/missing.sfc"], "deleteMedia": True}).encode()
        self.assertEqual(self.jreq("/api/delete", data=body, csrf=False)[0], 403)
        status, data = self.jreq("/api/delete", data=body, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(data["deleted"], 1)
        self.assertFalse(os.path.exists(os.path.join(snes, "Zelda.sfc")))
        gl = open(os.path.join(snes, "gamelist.xml")).read()
        self.assertNotIn("Zelda", gl)
        self.assertIn("Super Mario World", gl)
        self.assertTrue(os.path.exists(os.path.join(snes, "gamelist.xml.bak")))
        # image shared with another game: must not be deleted
        self.assertTrue(os.path.exists(os.path.join(snes, "images", "shared.png")))


    def test_favorites(self):
        snes = os.path.join(self.cfg["roms_dir"], "snes")
        favs = lambda: {r["id"] for r in self.jreq("/api/roms")[1]["roms"] if r["favorite"]}  # noqa: E731
        body = lambda ids, on: json.dumps({"ids": ids, "favorite": on}).encode()  # noqa: E731
        self.assertEqual(self.jreq("/api/favorite", data=body(["snes/Other.sfc"], True), csrf=False)[0], 403)
        # entry already in gamelist, ROM without an entry, nonexistent ROM
        status, data = self.jreq("/api/favorite", data=body(["snes/Other.sfc", "psx/Game.ps3", "snes/missing.sfc"], True))
        self.assertEqual(status, 200, data)
        self.assertEqual(data["updated"], 2)
        self.assertEqual(favs(), {"snes/Mario.sfc", "snes/Other.sfc", "psx/Game.ps3"})
        psx_gl = open(os.path.join(self.cfg["roms_dir"], "psx", "gamelist.xml")).read()
        self.assertIn("<path>./Game.ps3</path>", psx_gl)
        # true again: no duplicate tag
        self.jreq("/api/favorite", data=body(["snes/Other.sfc"], True))
        gl = open(os.path.join(snes, "gamelist.xml")).read()
        self.assertEqual(gl.count("<favorite>"), 2)
        status, data = self.jreq("/api/favorite", data=body(["snes/Mario.sfc", "snes/Other.sfc", "psx/Game.ps3"], False))
        self.assertEqual((status, data["updated"]), (200, 3))
        self.assertEqual(favs(), set())
        gl = open(os.path.join(snes, "gamelist.xml")).read()
        self.assertNotIn("<favorite>", gl)
        self.assertIn("Super Mario World", gl)              # the rest of the metadata is kept
        self.assertTrue(os.path.exists(os.path.join(snes, "gamelist.xml.bak")))
        self.assertEqual(self.jreq("/api/favorite", data=body([], True))[0], 400)
        self.jreq("/api/favorite", data=body(["snes/Mario.sfc"], True))  # initial state for the other tests


class CollectionTests(WebUITest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cdir = cls.cfg["collections_dir"]
        os.makedirs(cdir)
        # written the way EmulationStation does: paths relative to /userdata, "~/" = /userdata/system
        with open(os.path.join(cdir, "custom-Platformers.cfg"), "w") as f:
            f.write("./roms/snes/Mario.sfc\n# a comment\n~/../roms/psx/Game.ps3\n/elsewhere/gone.sfc\n")
        open(os.path.join(cdir, "Best rated.xcc"), "w").write("<filter/>")
        with open(cls.cfg["es_settings_file"], "w") as f:
            f.write('<?xml version="1.0"?>\n<config>\n\t<bool name="DrawFramerate" value="false" />\n'
                    '\t<string name="CollectionSystemsCustom" value="Platformers" />\n</config>\n')

    def post(self, **body):
        return self.jreq("/api/collections", data=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json"})

    def collections(self):
        return {c["name"]: c for c in self.jreq("/api/collections")[1]["collections"]}

    def cfg_lines(self, name):
        with open(os.path.join(self.cfg["collections_dir"], "custom-%s.cfg" % name)) as f:
            return f.read().splitlines()

    def enabled(self):
        return webui.Collections(self.cfg, None).enabled()

    def test_1_list_existing(self):
        cols = self.collections()
        plat = cols["Platformers"]
        self.assertEqual((plat["type"], plat["enabled"]), ("custom", True))
        self.assertEqual(plat["ids"], ["snes/Mario.sfc", "psx/Game.ps3"])
        self.assertEqual(plat["missing"], 1)
        self.assertEqual((cols["Best rated"]["type"], cols["Best rated"]["enabled"]), ("dynamic", False))

    def test_2_create_add_remove(self):
        status, data = self.post(action="create", name="Couch co-op", ids=["snes/Mario.sfc"])
        self.assertEqual(status, 200, data)
        self.assertEqual(self.cfg_lines("Couch co-op"), ["./roms/snes/Mario.sfc"])
        self.assertIn("Couch co-op", self.enabled())
        self.assertEqual(self.post(action="add", name="Couch co-op", ids=["snes/Mario.sfc", "snes/Other.sfc"])[1]["added"], 1)
        self.assertEqual(self.cfg_lines("Couch co-op"), ["./roms/snes/Mario.sfc", "./roms/snes/Other.sfc"])
        self.assertEqual(self.post(action="remove", name="Couch co-op", ids=["snes/Mario.sfc"])[1]["removed"], 1)
        self.assertEqual(self.cfg_lines("Couch co-op"), ["./roms/snes/Other.sfc"])
        # the rest of es_settings.cfg is kept
        with open(self.cfg["es_settings_file"]) as f:
            self.assertIn('name="DrawFramerate"', f.read())

    def test_3_invalid_names(self):
        for bad, status in (("all", 400), ("snes", 400), ("bad/name", 400), ("", 400), ("platformers", 409)):
            self.assertEqual(self.post(action="create", name=bad)[0], status, bad)
        self.assertEqual(self.post(action="add", name="Best rated", ids=["snes/Mario.sfc"])[0], 400)
        self.assertEqual(self.post(action="add", name="Nope", ids=["snes/Mario.sfc"])[0], 404)
        self.assertEqual(self.post(action="add", name="Platformers", ids=["snes/missing.sfc"])[0], 404)
        status, _, _ = self.req("/api/collections", data=b'{"action":"delete","name":"Platformers"}', csrf=False,
                                headers={"Content-Type": "application/json"})
        self.assertEqual(status, 403)

    def test_4_prune_rename_enable_delete(self):
        self.assertEqual(self.post(action="prune", name="Platformers")[1]["removed"], 1)
        self.assertEqual(self.cfg_lines("Platformers"), ["./roms/snes/Mario.sfc", "# a comment", "~/../roms/psx/Game.ps3"])
        status, data = self.post(action="rename", name="Platformers", newName="Jump n Run")
        self.assertEqual((status, data["name"]), (200, "Jump n Run"))
        self.assertIn("Jump n Run", self.enabled())
        self.assertNotIn("Platformers", self.enabled())
        self.post(action="enable", name="Jump n Run", enabled=False)
        self.assertNotIn("Jump n Run", self.enabled())
        self.post(action="enable", name="Best rated", enabled=True)
        self.assertIn("Best rated", self.enabled())
        self.assertEqual(self.post(action="delete", name="Best rated")[0], 200)
        self.assertNotIn("Best rated", self.collections())
        self.assertNotIn("Best rated", self.enabled())

    def test_6_restart_tracking_reapplies_lost_settings(self):
        app = self.server.app
        pid = {"value": 111}
        original = webui.find_es_pid
        webui.find_es_pid = lambda: pid["value"]
        self.addCleanup(setattr, webui, "find_es_pid", original)
        status, data = self.post(action="create", name="Pending one")
        self.assertTrue(data["restartNeeded"])
        # quitting, ES saved its own settings and dropped the new collection
        app.collections.apply_enabled({"Pending one": False})
        pid["value"] = 222
        self.assertTrue(app.restart_needed())             # re-applied, one more restart needed
        self.assertIn("Pending one", self.enabled())
        pid["value"] = 333
        self.assertFalse(app.restart_needed())            # this time the settings survived
        pid["value"] = None
        self.post(action="enable", name="Pending one", enabled=False)
        self.assertFalse(app.restart_needed())            # ES not running: nothing to restart

    def test_5_deleting_a_rom_removes_it_from_collections(self):
        self.post(action="create", name="Doomed", ids=["snes/Other.sfc", "snes/Mario.sfc"])
        self.jreq("/api/delete", data=json.dumps({"ids": ["snes/Other.sfc"]}).encode(),
                  headers={"Content-Type": "application/json"})
        self.assertEqual(self.cfg_lines("Doomed"), ["./roms/snes/Mario.sfc"])


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class SavesTests(WebUITest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        saves = cls.cfg["saves_dir"]
        for name in ("Mario.srm", "Mario.state", "Mario.state.png", "Mario.state3", "Mario.state3.png",
                     "Mario.state.auto", "Mario.state.auto.png", "Lost Game.srm", "notes.txt"):
            write(os.path.join(saves, "snes", name), name.encode())
        write(os.path.join(saves, "psx", "Game.state1"))
        write(os.path.join(saves, "n64", "Game.ps3.st0"))  # mupen64plus pattern, fixed folder
        with open(cls.cfg["es_savestates_files"][0], "w") as f:
            f.write('<savestates>\n<emulator name="libretro" directory="{{system}}" file="{{romfilename}}.state{{slot}}" '
                    'image="{{romfilename}}.state{{slot}}.png" nofileextension="true" autosave="true" '
                    'autosave_file="{{romfilename}}.state.auto" autosave_image="{{romfilename}}.state.auto.png" />\n'
                    '<emulator name="mupen64plus" directory="n64" file="{{romfilename}}.st{{slot0}}" '
                    'image="{{romfilename}}.st{{slot0}}.png" nofileextension="false" />\n</savestates>\n')

    def groups(self):
        return {g["id"]: g for g in self.jreq("/api/saves")[1]["groups"]}

    def test_1_scan_matches_roms_and_slots(self):
        groups = self.groups()
        mario = groups["snes/Mario.sfc"]
        self.assertEqual(mario["name"], "Super Mario World")
        files = {f["name"]: f for f in mario["files"]}
        self.assertEqual(set(files), {"Mario.srm", "Mario.state", "Mario.state3", "Mario.state.auto"})
        self.assertEqual((files["Mario.state"]["kind"], files["Mario.state"]["slot"]), ("state", 0))
        self.assertEqual((files["Mario.state3"]["slot"], files["Mario.state3"]["image"]), (3, "snes/Mario.state3.png"))
        self.assertEqual(files["Mario.state.auto"]["kind"], "auto")
        self.assertEqual(files["Mario.srm"]["kind"], "save")
        self.assertIn("psx/Game.ps3", groups)                      # libretro state of a folder ROM
        game = {f["path"]: f["slot"] for f in groups["psx/Game.ps3"]["files"]}
        self.assertEqual(game, {"psx/Game.state1": 1, "n64/Game.ps3.st0": 0})  # mupen64plus keeps the extension
        self.assertIsNone(groups["snes/?Lost Game"]["romId"])       # save without its ROM
        self.assertEqual(groups["snes/?notes"]["files"][0]["kind"], "other")

    def test_2_file_download_delete_upload(self):
        status, body, h = self.req("/api/saves/file?p=snes/Mario.state3.png&inline=1")
        self.assertEqual((status, h["Content-Type"]), (200, "image/png"))
        self.assertIn("inline", h["Content-Disposition"])
        self.assertEqual(self.req("/api/saves/file?p=../roms/snes/Mario.sfc")[0], 404)
        status, body, _ = self.req("/api/saves/download?p=snes/Mario.state3")
        with zipfile.ZipFile(io.BytesIO(body)) as z:  # the state comes with its screenshot
            self.assertEqual(sorted(z.namelist()), ["snes/Mario.state3", "snes/Mario.state3.png"])
        status, body, _ = self.req("/api/saves/download?all=1")
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertIn("snes/Mario.srm", z.namelist())
        status, data = self.jreq("/api/saves/delete", data=json.dumps({"paths": ["snes/Mario.state3"]}).encode(),
                                 headers={"Content-Type": "application/json"})
        self.assertEqual(data["deleted"], 2)
        self.assertFalse(os.path.exists(os.path.join(self.cfg["saves_dir"], "snes", "Mario.state3.png")))
        status, data = self.jreq("/api/saves/upload?dir=snes&name=Mario.state5", data=b"S5")
        self.assertEqual((status, data["path"]), (200, "snes/Mario.state5"))
        self.assertEqual(self.jreq("/api/saves/upload?dir=snes&name=Mario.state5", data=b"S5")[0], 409)
        self.assertEqual(self.jreq("/api/saves/upload?dir=../x&name=a.srm", data=b"x")[0], 400)
        self.assertEqual(self.jreq("/api/saves/upload?dir=unknown&name=a.srm", data=b"x")[0], 400)
        self.assertIn("Mario.state5", {f["name"] for f in self.groups()["snes/Mario.sfc"]["files"]})


class BiosTests(WebUITest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        md5 = lambda b: hashlib.md5(b).hexdigest()
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("A.ROM", b"inner")
        db = {"snes": {"name": "SNES", "biosFiles": [
                  {"md5": md5(b"good"), "file": "bios/good.bin"},
                  {"md5": md5(b"expected"), "file": "bios/bad.bin"},
                  {"md5": "", "file": "bios/any.bin"},
                  {"md5": md5(b"x"), "file": "bios/sub/missing.bin", "emulator": "libretro", "core": "snes9x"},
                  {"md5": md5(b"inner"), "file": "bios/set.zip", "zippedFile": "a.rom"},
                  {"md5": md5(b"other"), "file": "bios/set.zip", "zippedFile": "b.rom"}]},
              "dreamcast": {"name": "Dreamcast", "biosFiles": [{"md5": "", "file": "bios/dc/dc_boot.bin"}]}}
        with open(cls.cfg["bios_db"], "w") as f:  # the code after the dict must never run
            f.write("#!/usr/bin/env python\nsystems = %r\nraise SystemExit('executed!')\n" % db)
        bios = os.path.join(cls.tmp, "bios")
        write(os.path.join(bios, "good.bin"), b"good")
        write(os.path.join(bios, "bad.bin"), b"corrupt")
        write(os.path.join(bios, "any.bin"), b"whatever")
        write(os.path.join(bios, "set.zip"), inner.getvalue())

    def test_1_check(self):
        status, data = self.jreq("/api/bios")
        self.assertEqual(status, 200)
        self.assertIsNone(data["error"])
        systems = {s["name"]: s for s in data["systems"]}
        self.assertNotIn("dreamcast", systems)          # not a system of this device
        snes = systems["snes"]
        files = {(f["file"], f["zipped"]): f for f in snes["files"]}
        self.assertEqual(files[("bios/good.bin", None)]["status"], "ok")
        self.assertEqual(files[("bios/bad.bin", None)]["status"], "mismatch")
        self.assertEqual(files[("bios/any.bin", None)]["status"], "ok")
        self.assertEqual(files[("bios/sub/missing.bin", None)]["status"], "missing")
        self.assertEqual(files[("bios/sub/missing.bin", None)]["for"], "libretro/snes9x")
        self.assertEqual(files[("bios/set.zip", "a.rom")]["status"], "ok")
        self.assertEqual(files[("bios/set.zip", "b.rom")]["status"], "missing")
        self.assertEqual((snes["ok"], snes["total"], snes["fullname"]), (3, 6, "Super Nintendo"))

    def test_2_upload_places_files(self):
        status, data = self.jreq("/api/bios/upload?name=MISSING.BIN", data=b"x")
        self.assertEqual((status, data["placed"], data["matched"]), (200, ["bios/sub/missing.bin"], True))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "bios", "sub", "missing.bin")))
        status, data = self.jreq("/api/bios/upload?name=bad.bin", data=b"still wrong")
        self.assertEqual((data["placed"], data["known"], data["matched"]), (["bios/bad.bin"], True, False))
        status, data = self.jreq("/api/bios/upload?name=extra.rom", data=b"?")
        self.assertEqual((data["placed"], data["known"]), (["bios/extra.rom"], False))
        self.assertEqual(self.jreq("/api/bios/upload?name=../evil", data=b"?")[0], 400)
        files = {(f["file"], f["zipped"]): f for f in self.jreq("/api/bios")[1]["systems"][0]["files"]}
        self.assertEqual(files[("bios/sub/missing.bin", None)]["status"], "ok")


class BiosReadmeTests(WebUITest):
    """Knulli images without batocera-systems: the list comes from bios/readme.txt."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        md5 = lambda b: hashlib.md5(b).hexdigest()
        set_zip, pack = io.BytesIO(), io.BytesIO()
        with zipfile.ZipFile(set_zip, "w") as z:
            z.writestr("a.rom", b"inner")
        with zipfile.ZipFile(pack, "w") as z:
            z.writestr("x.rom", b"pack member")
        cls.pack = pack.getvalue()
        bios = os.path.join(cls.tmp, "bios")
        write(os.path.join(bios, "good.bin"), b"good")
        write(os.path.join(bios, "set.zip"), set_zip.getvalue())
        with open(os.path.join(bios, "readme.txt"), "w") as f:  # format of batocera-systems --createReadme
            f.write("Nintendo SNES:\n%s bios/good.bin\n bios/any.bin\n%s bios/set.zip\n%s bios/pack.zip\n\n"
                    "Sega Dreamcast:\n bios/dc/dc_boot.bin\n\n" % (md5(b"good"), md5(b"inner"), md5(b"pack member")))

    def test_1_readme_fallback(self):
        data = self.jreq("/api/bios")[1]
        self.assertIsNone(data["error"])
        self.assertTrue(data["database"].endswith("readme.txt"))
        systems = {s["name"]: s for s in data["systems"]}
        self.assertEqual(list(systems), ["snes"])                 # "Nintendo SNES" -> snes; Dreamcast not on this device
        files = {f["file"]: f["status"] for f in systems["snes"]["files"]}
        self.assertEqual(files, {"bios/good.bin": "ok", "bios/any.bin": "missing", "bios/set.zip": "ok", "bios/pack.zip": "missing"})

    def test_2_zip_upload_matched_by_member(self):
        status, data = self.jreq("/api/bios/upload?name=pack.zip", data=self.pack)
        self.assertEqual((status, data["placed"], data["matched"]), (200, ["bios/pack.zip"], True))
        files = {f["file"]: f["status"] for f in self.jreq("/api/bios")[1]["systems"][0]["files"]}
        self.assertEqual(files["bios/pack.zip"], "ok")

    def test_3_nothing_found(self):
        os.rename(os.path.join(self.tmp, "bios", "readme.txt"), os.path.join(self.tmp, "readme.bak"))
        self.addCleanup(os.rename, os.path.join(self.tmp, "readme.bak"), os.path.join(self.tmp, "bios", "readme.txt"))
        data = self.jreq("/api/bios")[1]
        self.assertEqual(data["systems"], [])
        self.assertIn("No BIOS list found", data["error"])
        self.assertIn("batocera-systems", data["error"])


class CaptureTests(WebUITest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        write(os.path.join(cls.cfg["screenshots_dir"], "Mario-240115-203011.png"), b"\x89PNG1")
        write(os.path.join(cls.cfg["screenshots_dir"], "screenshot-2024.01.15-20h30.12.png"), b"\x89PNG2")
        write(os.path.join(cls.cfg["screenshots_dir"], "readme.txt"))
        write(os.path.join(cls.cfg["screenshots_dir"], "Zelda.mp4"), b"MP4")  # videos are not listed
        write(os.path.join(cls.cfg["screenshots_dir"], "Zelda-240116-101010.jpg"), b"JPG")

    def test_list_serve_delete(self):
        caps = {c["id"]: c for c in self.jreq("/api/captures")[1]["captures"]}
        self.assertEqual(set(caps), {"screenshots/Mario-240115-203011.png", "screenshots/screenshot-2024.01.15-20h30.12.png",
                                     "screenshots/Zelda-240116-101010.jpg"})
        self.assertEqual(caps["screenshots/Mario-240115-203011.png"]["romId"], "snes/Mario.sfc")
        self.assertEqual(caps["screenshots/Zelda-240116-101010.jpg"]["romId"], "snes/Zelda.sfc")
        self.assertIsNone(caps["screenshots/screenshot-2024.01.15-20h30.12.png"]["romId"])
        status, body, h = self.req("/api/captures/file?id=screenshots/Mario-240115-203011.png")
        self.assertEqual((status, body, h["Content-Type"]), (200, b"\x89PNG1", "image/png"))
        self.assertEqual(self.req("/api/captures/file?id=screenshots/../roms/snes/Mario.sfc")[0], 404)
        self.assertEqual(self.req("/api/captures/file?id=etc/passwd")[0], 404)
        form = "ids=" + urllib.request.quote(json.dumps(list(caps)))
        status, body, _ = self.req("/api/captures/download", data=form.encode(), csrf=False,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertEqual(len(z.namelist()), 3)
        status, data = self.jreq("/api/captures/delete", data=json.dumps({"ids": ["screenshots/Zelda-240116-101010.jpg"]}).encode(),
                                 headers={"Content-Type": "application/json"})
        self.assertEqual(data["deleted"], 1)
        self.assertNotIn("screenshots/Zelda-240116-101010.jpg", {c["id"] for c in self.jreq("/api/captures")[1]["captures"]})


class RemoteTests(WebUITest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cfg.update({"screen_command": "printf '\\211PNG frame'", "framebuffer": os.path.join(cls.tmp, "fb0"),
                        "es_input_files": [os.path.join(cls.tmp, "es_input.cfg")]})

    def test_screen_frame_and_etag(self):
        status, info = self.jreq("/api/screen")
        self.assertEqual(info["capture"], "command")
        self.assertIsNone(info["pad"])
        self.assertIn("es_input.cfg", info["padError"])
        status, body, h = self.req("/api/screen/frame")
        self.assertEqual((status, body, h["Content-Type"]), (200, b"\x89PNG frame", "image/png"))
        self.assertEqual(self.req("/api/screen/frame", headers={"If-None-Match": h["ETag"]})[0], 304)

    def test_screen_unavailable(self):
        self.cfg["screen_command"] = ""
        self.server.app.screen._frame = None
        self.addCleanup(self.cfg.update, {"screen_command": "printf '\\211PNG frame'"})
        self.assertIsNone(self.jreq("/api/screen")[1]["capture"])
        status, data = self.jreq("/api/screen/frame")
        self.assertEqual(status, 503)
        self.assertIn("screen_command", data["error"])

    def test_pad_requests(self):
        body = json.dumps({"button": "a", "down": True}).encode()
        self.assertEqual(self.req("/api/pad", data=body, csrf=False)[0], 403)
        self.assertEqual(self.jreq("/api/pad", data=json.dumps({"button": "rm -rf"}).encode())[0], 400)
        status, data = self.jreq("/api/pad", data=body)
        self.assertEqual(status, 503)
        self.assertIn("es_input.cfg", data["error"])

    def test_sdl_layout(self):
        keys, absb = bytearray(webui.KEY_MAX // 8 + 1), bytearray(webui.ABS_MAX // 8 + 1)
        for code in (0x130, 0x131, 0x13a, 0x13b, 0x2c0, 103):  # BTN_SOUTH, BTN_EAST, SELECT, START, a trigger, KEY_UP
            keys[code // 8] |= 1 << code % 8
        for code in (0x00, 0x01, 0x05, 0x10, 0x11):  # ABS_X, ABS_Y, ABS_RZ, ABS_HAT0X/Y
            absb[code // 8] |= 1 << code % 8
        buttons, axes, hats = webui.sdl_layout(keys, absb)
        # SDL numbers BTN_JOYSTICK and above first, then the codes below it
        self.assertEqual(buttons, [0x130, 0x131, 0x13a, 0x13b, 0x2c0, 103])
        self.assertEqual(axes, [0x00, 0x01, 0x05])
        self.assertEqual(hats, [(0x10, 0x11)])


class PadMappingTests(unittest.TestCase):
    def test_es_codes_win_over_sdl_indexes(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        dev = os.path.join(tmp, "event1")
        write(dev)
        # as in Knulli's es_input.cfg: the SDL index (id) does not follow from the codes alone
        write(os.path.join(tmp, "es_input.cfg"), b"""<inputList>
<inputConfig type="joystick" deviceName="Pad" deviceGUID="0">
  <input name="a" type="button" id="3" value="1" code="304"/><input name="select" type="button" id="9" value="1" code="310"/>
  <input name="start" type="button" id="1" value="1"/><input name="up" type="hat" id="0" value="1"/>
</inputConfig></inputList>""")
        cfg = webui.load_config(None)
        cfg["es_input_files"] = [os.path.join(tmp, "es_input.cfg")]
        pad = webui.Pad(cfg)
        pad._devices = lambda: [(dev, "Pad")]

        def ioctl(fd, req, buf):
            codes = {0x20 + webui.EV_KEY: range(304, 317), 0x20 + webui.EV_ABS: (0x10, 0x11)}.get(req & 0xff, ())
            for c in codes:
                buf[c // 8] |= 1 << c % 8

        with unittest.mock.patch.object(webui.fcntl, "ioctl", ioctl):
            pad.info()
            pad.set("select", True)
            pad.set("select", False)
        mapping = pad._dev["map"]
        self.assertEqual(mapping["a"][:2], (webui.EV_KEY, 304))
        self.assertEqual(mapping["select"][:2], (webui.EV_KEY, 310))
        self.assertEqual(mapping["start"][:2], (webui.EV_KEY, 305))  # no code: SDL index 1
        self.assertEqual(mapping["up"][:3], (webui.EV_ABS, 0x11, -1))
        with open(dev, "rb") as f:
            data = f.read()
        events = [webui.INPUT_EVENT.unpack_from(data, i)[2:] for i in range(0, len(data), webui.INPUT_EVENT.size)]
        self.assertEqual(events, [(webui.EV_KEY, 310, 1), (0, 0, 0), (webui.EV_KEY, 310, 0), (0, 0, 0)])


class AuthTests(WebUITest):
    auth = ("admin", "secret")

    def test_auth_required(self):
        r = urllib.request.Request(self.base + "/api/config")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(r)
        self.assertEqual(ctx.exception.code, 401)
        self.assertEqual(self.jreq("/api/config")[0], 200)


@unittest.skipUnless(os.path.isdir("/proc/self"), "requires /proc")
class ActivityTests(unittest.TestCase):
    def telemetry(self, es_api="http://127.0.0.1:9"):
        cfg = webui.load_config(None)
        cfg["es_api"] = es_api
        return webui.Telemetry(cfg)

    def test_es_process_detected_despite_comm_truncation(self):
        # The kernel truncates comm to 15 characters: "emulationstation" -> "emulationstatio"
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        fake_es = os.path.join(tmp, "emulationstation")
        shutil.copy(shutil.which("bash"), fake_es)
        proc = subprocess.Popen([fake_es, "-c", "sleep 30; true"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        for _ in range(50):
            if webui.read_file("/proc/%d/comm" % proc.pid) == "emulationstatio":
                break
            time.sleep(0.02)
        self.assertTrue(self.telemetry()._activity()["es_running"])

    def test_es_api_answer_means_running(self):
        class FakeES(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"msg":"NO GAME RUNNING"}'
                self.send_response(201)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), FakeES)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        activity = self.telemetry("http://127.0.0.1:%d" % srv.server_address[1])._activity()
        self.assertTrue(activity["es_running"])
        self.assertIsNone(activity["game"])


if __name__ == "__main__":
    unittest.main()
