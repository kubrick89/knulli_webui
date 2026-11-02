# Knulli WebUI

A web service for devices running **Knulli OS** (and Batocera in general) that serves, on port **8088**, a page (in English) with seven tabs:

- **Dashboard**: real-time telemetry (per-core CPU usage and frequencies, GPU/devfreq, RAM and swap, temperatures, battery and charger, network and Wi-Fi, storage volumes, the running game or emulator) with charts of the last few minutes.
- **ROMs**: a library with four views (**By system**, **Favorites**, **Collections**, **All**), search, sorting, grid or table layout, and filters on the `gamelist.xml` metadata (system, collection, genre, developer, publisher, region, year, players, rating, play history, presence of metadata or images, hidden games), plus **favorites management** (add and remove, single or multiple), **EmulationStation collections** (create, rename, delete, show/hide, add and remove ROMs), **upload**, **download** (single file or multi-ROM zip) and **deletion** of ROMs.
- **Stats**: play time, games played, launches, most and recently played games, play time by system and genre, share of each system's games played.
- **Saves**: battery saves and save states grouped by game, with the screenshot of each slot; download, upload, delete, and a zip backup of the whole saves folder.
- **BIOS**: for each system, which BIOS files are present, missing or have the wrong checksum, with upload straight to the right folder.
- **Screenshots**: gallery of screenshots, linked to their game when possible, with a full-screen viewer, download and delete.
- **Remote**: the device's screen live in the browser, and a virtual controller (on-screen buttons or the keyboard) that drives EmulationStation and the games.

It has no dependencies: it only uses the Python 3 already present on the system and loads nothing from the internet, so it also works offline.

## Installing on the device

1. Enable networking and SSH on Knulli (default credentials: `root` / `linux`).
2. From the project root, copy everything to the device:
   ```sh
   scp -r . root@<device-ip>:/userdata/system/knulli-webui-src
   ```
   Alternatively you can copy it through the network share `\\KNULLI\share\system\`.
3. Install:
   ```sh
   ssh root@<device-ip>
   sh /userdata/system/knulli-webui-src/install.sh
   ```
4. Open `http://<device-ip>:8088`.

The installer copies the application to `/userdata/system/knulli-webui/` and the service to `/userdata/system/services/knulli_webui`, then enables and starts it with `batocera-services`. The service therefore starts at every boot and can also be turned on or off from the EmulationStation **System Settings → Services** menu.

Useful commands:

```sh
batocera-services start|stop|status knulli_webui
tail -f /userdata/system/logs/knulli-webui.log
sh /userdata/system/knulli-webui-src/install.sh --uninstall
```

To update, copy the new version and run `install.sh` again. The existing `config.json` is kept.

> If your version has no `batocera-services`, the installer starts the service directly. To start it automatically at boot, add `/userdata/system/services/knulli_webui start` to `/userdata/system/custom.sh`.

## Configuration

`/userdata/system/knulli-webui/config.json` (see [config.example.json](config.example.json)):

| Key | Default | Description |
|---|---|---|
| `port` | `8088` | HTTP port |
| `roms_dir` | `/userdata/roms` | ROMs folder |
| `username` / `password` | empty | If set, they enable HTTP Basic authentication |
| `allow_upload` / `allow_delete` | `true` | Enable or disable uploads and deletion |
| `max_upload_mb` | `0` | Maximum size per file (0 = no limit) |
| `es_reload` | `true` | After an upload or deletion, ask EmulationStation to reload the game list |
| `sample_interval` | `2.0` | Seconds between two telemetry samples |
| `collections_dir` | `/userdata/system/configs/emulationstation/collections` | EmulationStation collections folder |
| `es_settings_file` | `/userdata/system/configs/emulationstation/es_settings.cfg` | EmulationStation settings (list of the collections it shows) |
| `saves_dir` | `/userdata/saves` | Battery saves and save states |
| `es_savestates_files` | user, then system `es_savestates.cfg` | Save state naming rules; the first file found is used, as in EmulationStation |
| `bios_db` | `/usr/bin/batocera-systems` | Script holding Knulli's BIOS list (only its `systems` dictionary is read, the script is never run); also looked up in `PATH` |
| `bios_readme_files` | `/userdata/bios/readme.txt`, `/usr/share/batocera/datainit/bios/readme.txt` | Used when the script is missing: the list Knulli generates from it at build time |
| `screenshots_dir` | `/userdata/screenshots` | Screenshots |
| `screen_capture` | `auto` | How the **Remote** tab captures the screen: `auto`, `grim`, `fbdev` or `command` (see [Remote](#remote)) |
| `screen_command` | empty | Command that prints the screen as a PNG, JPEG or BMP image on stdout; when set, `auto` uses it |
| `framebuffer` | `/dev/fb0` | Framebuffer read by the `fbdev` method |
| `screen_jpeg_quality` | `70` | JPEG quality used with `grim` |
| `allow_remote_control` | `true` | Enable the virtual controller of the **Remote** tab |
| `es_input_files` | user, then system `es_input.cfg` | EmulationStation's controller configurations, used to find the controller and its mapping |
| `pad_device` | empty | Force the controller's event node (e.g. `/dev/input/event1`) |

After a change: `batocera-services stop knulli_webui && batocera-services start knulli_webui`.

## How it works

- **Systems**: names, manufacturers and valid extensions come from `/usr/share/emulationstation/es_systems.cfg` (plus any custom `es_systems_*.cfg`). A ROM is a file with an extension valid for its system, or a folder with that extension (e.g. `Game.ps3`). Media subfolders (`images`, `videos`, `manuals`, …) are ignored.
- **Metadata and favorites**: read from each system's `gamelist.xml`, so the richness of the filters depends on the scraping done in EmulationStation. ROMs without an entry in `gamelist.xml` still show up, with their file name.
- **Favorites**: the ☆/★ star on every game (grid, list or detail) and the **★ Favorite** / **☆ Unfavorite** buttons in the selection bar write the `<favorite>` tag in `gamelist.xml` (a copy is first saved to `gamelist.xml.bak`); for ROMs without an entry, a minimal one is created. EmulationStation is then asked to reload the game list. If EmulationStation has unsaved changes on the same system, it may overwrite the file when it exits: when in doubt, edit favorites from EmulationStation's main menu.
- **Upload**: the file is streamed without being loaded into memory, written to a hidden temporary file and renamed only when the transfer is complete. Free space is checked before starting. If the extension is not among those of the system, a warning is shown.
- **Download**: supports `Range` requests, so downloads can be resumed. Folder ROMs and multiple selections are downloaded as a zip generated on the fly, uncompressed so as not to load the CPU.
- **Deletion**: removes the file or folder and its entry in `gamelist.xml` (a copy is first saved to `gamelist.xml.bak`). On request it also deletes images, videos and manuals that no other game uses.
- **Performance**: the service runs with `nice 10` and, when nobody is watching the dashboard, samples telemetry 5 times less often. The ROM scan stays cached and is refreshed when the folders or `gamelist.xml` change.

## Stats, saves, BIOS and screenshots

- **Stats** are computed in the browser from the data EmulationStation keeps in `gamelist.xml` (`playcount`, `gametime`, `lastplayed`); when no play time was recorded, the charts use the number of launches.
- **Saves**: save states are recognised with the same rules as EmulationStation, read from `es_savestates.cfg` (RetroArch `Game.state`, `Game.state1`…, `Game.state.auto` with their `.png` screenshots, and the standalone emulators listed there, e.g. mupen64plus, PPSSPP, Dolphin, PCSX2). Battery saves (`.srm`, `.sav`, …) and other files in the same folders are listed too. Each file is matched to its ROM by name; saves whose ROM no longer exists are flagged. Deleting or downloading a save state includes its screenshot. **Backup all** downloads the whole saves folder as a zip.
- **BIOS**: the list of files and MD5 checksums is the `systems` dictionary of Knulli's `batocera-systems` script (the same data as EmulationStation's *missing BIOS* check), read with `ast.literal_eval` without running the script. Some Knulli images do not include the script: then the list is read from `bios/readme.txt`, which Knulli generates from it at build time; its system names are matched to the system folders, and since it does not say which file inside a zip an MD5 refers to, a zip is OK when the archive or any file in it has an expected MD5. The page shows which list is used. Files inside zip archives are checked too, checksums are cached until a file changes, and only the systems available on the device are shown. Some systems list several alternative versions and need only one. An uploaded file is placed where Knulli expects it, recognised by its name (and by its checksum when the same name is listed in several places); files that are not in the list are saved in `/userdata/bios`.
- **Screenshots** are linked to a game when their name starts with the ROM file name, as RetroArch does (`Game-240115-203011.png`).

## Collections

The **Collections** view manages EmulationStation's own collections, so they show up on the device too:

- **Custom collections** are the `custom-<name>.cfg` files in `collections_dir`: one ROM per line, written like EmulationStation does (relative to `/userdata`, e.g. `./roms/snes/Game.zip`). Open a collection to rename it, delete it (the ROMs are kept), show or hide it in EmulationStation, or drop the entries whose ROM no longer exists. To add ROMs, select them in any view and use **+ Collection** in the selection bar, or use **+ Add to collection** in a game's details; **− Remove from collection** removes the selected ROMs from the open collection. The **Collection** filter shows the ROMs of a collection in the other views. Deleting a ROM also removes it from every collection.
- **Dynamic collections** (`<name>.xcc`) are filled by EmulationStation using filters: they are listed and can be renamed, deleted or shown/hidden, but their contents are edited in EmulationStation.
- Which collections EmulationStation shows is the `CollectionSystemsCustom` setting in `es_settings.cfg` (a `.bak` copy is saved before each change). EmulationStation reads it **only at startup**: after creating, renaming, showing or hiding a collection the page offers **Restart EmulationStation**, which closes EmulationStation and lets Knulli start it again (the device is *not* rebooted; it is refused while a game is running). Changes to the contents of a collection are picked up right away, through EmulationStation's game list reload.
- If settings were changed in EmulationStation's menus and not saved yet, EmulationStation writes its own copy of `es_settings.cfg` when it quits, which can undo a collection being shown or hidden. Knulli WebUI checks this after the restart, re-applies the change and asks for one more restart.

## Remote

The **Remote** tab shows what the device is showing and lets you control it from the browser.

- **Screen**: captured only while the tab is open, at the chosen rate (2–30 fps); when nothing changed, no image is sent. With `screen_capture` set to `auto`, the first available method is used: `screen_command` if set, `grim` when a Wayland compositor is running, otherwise the framebuffer (`/dev/fb0`), whose raw pixels are compressed and converted in the browser. If the framebuffer stays black (some devices draw through DRM/KMS planes), set `screen_command`, for example to an `ffmpeg -f kmsgrab …` pipeline that prints one PNG frame. **Rotate** fixes devices whose panel is mounted sideways, and **Save image** downloads the current frame.
- **Controller**: the buttons write input events straight to the device's own controller (the one configured in EmulationStation's `es_input.cfg`), so EmulationStation and the emulators see them exactly as real presses, with the same mapping, hotkeys included. Buttons that are not mapped are greyed out. Each press lasts at least 80 ms so that emulators do not miss it, and a button the page stops refreshing (closed tab, lost network) is released after a few seconds. Keyboard: arrows = D-pad, X = A, Z = B, S = X, A = Y, Q / W = L1 / R1, 1 / 2 = L2 / R2, Enter = Start, Backspace = Select, H = Hotkey.
- If something else grabs the controller exclusively, the injected events are not delivered: set `pad_device` to another node, or disable the remote control with `allow_remote_control`.

## System icons

Each system is shown with its console icon, from [Retro game console icons](https://github.com/KyleBing/retro-game-console-icons) by KyleBing, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The icons have been cropped to their visible area and resized to 128 px; the credit is shown at the bottom of the page and in `knulli_webui/static/icons/systems/LICENSE.txt`. Systems without an icon keep the coloured badge with their name.

The icons and `map.json` (system folder → icon) are generated by `tools/build_icons.py`, which also holds the mapping for about 110 Knulli/Batocera systems. To change the mapping or refresh the icons:

```sh
git clone --depth 1 https://github.com/KyleBing/retro-game-console-icons /tmp/icons
python3 tools/build_icons.py /tmp/icons
```

## Security

The service is meant for a home network. Since it can delete files, **if the device is on a shared network, set `username` and `password`**. Endpoints that modify files require a custom header, so a page on another site cannot call them (CSRF protection). All paths are validated to stay inside the ROMs folder.

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/telemetry` | Telemetry and history |
| GET | `/api/roms[?refresh=1]` | All ROMs (without descriptions) and the systems |
| GET | `/api/systems` | Systems with counts |
| GET | `/api/rom?id=<system/file>` | Details of one ROM, description included |
| GET | `/api/media?p=<path>` | Images and videos referenced by `gamelist.xml` |
| GET | `/api/download?id=…[&id=…]` | Download (zip for multiple ROMs or a folder) |
| POST | `/api/download` | Multi-download via form (`ids` = JSON array) |
| POST | `/api/upload?system=…&name=…[&overwrite=1]` | Body = file contents |
| POST | `/api/delete` | `{"ids": [...], "deleteMedia": true}` |
| POST | `/api/favorite` | `{"ids": [...], "favorite": true}` (false to remove) |
| POST | `/api/es-reload` | Reload EmulationStation's game list |
| GET | `/api/collections` | Collections, their ROM ids and whether EmulationStation must be restarted |
| POST | `/api/collections` | `{"action": "create" \| "rename" \| "delete" \| "enable" \| "add" \| "remove" \| "prune", "name": …, "newName": …, "ids": [...], "enabled": true}` |
| POST | `/api/es-restart` | Restart EmulationStation (not the device); refused while a game is running |
| GET | `/api/saves` | Saves and save states grouped by game |
| GET | `/api/saves/file?p=<path>[&inline=1]` | One save file (or a state screenshot, inline) |
| GET | `/api/saves/download?p=…[&p=…]` / `?all=1` | Zip of the files (with state screenshots) / of the whole saves folder |
| POST | `/api/saves/delete` | `{"paths": [...]}` |
| POST | `/api/saves/upload?dir=<folder>&name=…[&overwrite=1]` | Body = file contents |
| GET | `/api/bios` | BIOS status per system |
| POST | `/api/bios/upload?name=…` | Body = file contents; placed by name and MD5 |
| GET | `/api/captures` | Screenshots |
| GET | `/api/captures/file?id=screenshots/<name>[&download=1]` | One screenshot |
| GET/POST | `/api/captures/download` | Zip of several files (`id` parameters, or form field `ids`) |
| POST | `/api/captures/delete` | `{"ids": [...]}` |
| GET | `/api/screen` | Screen capture method and controller (name, mapped buttons) or why it is not available |
| GET | `/api/screen/frame` | The current screen: an image, or raw framebuffer pixels (gzip, layout in `X-Frame-Format`); supports `If-None-Match` |
| POST | `/api/pad` | `{"button": "a", "down": true}` (`up`, `down`, `left`, `right`, `a`, `b`, `x`, `y`, `pageup`, `pagedown`, `l2`, `r2`, `l3`, `r3`, `select`, `start`, `hotkey`), or `{"releaseAll": true}` |

POST requests that modify files require the header `X-Requested-With: knulli-webui`.

## Development on a PC

```sh
python3 tools/make_demo.py demo        # also writes demo/config.json, collections, saves, BIOS and screenshots
python3 knulli_webui/webui.py --config demo/config.json -v
# http://localhost:8088
python3 -m unittest discover -s tests -v
```

## Layout

```
knulli_webui/        application installed on the device
  webui.py           HTTP server, telemetry, ROM library
  static/            user interface (HTML/CSS/JS, no dependencies): app.js (dashboard, ROMs),
                     stats.js, saves.js, bios.js, captures.js
    icons/systems/   system icons, map.json and their license
services/knulli_webui  Batocera service script
install.sh           install and uninstall
config.example.json  example configuration
tools/make_demo.py   generates a fake library for testing
tools/build_icons.py builds the system icons from the source repository
tests/               integration tests
```
