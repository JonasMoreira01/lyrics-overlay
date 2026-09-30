#!/usr/bin/env python3
"""Lyrics Overlay: letra sincronizada (e traducao EN->PT) da musica que esta tocando, no canto da tela.

Musica atual: MPRIS via D-Bus (Spotify, Chrome/Chromium, Firefox...). Letra: LRCLIB.
Traducao: argostranslate (offline) se instalado; senao Google gtx (sujeito a 429).
Controle pelo icone na bandeja do sistema. Linux/X11.
"""
import argparse
import bisect
import hashlib
import importlib
import json
import os
import re
import signal
import threading
import time
from pathlib import Path

import gi
import requests

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

try:
    import cairo
except ImportError:
    cairo = None

AppIndicator = None
for _ns in ("AyatanaAppIndicator3", "AppIndicator3"):
    try:
        gi.require_version(_ns, "0.1")
        AppIndicator = importlib.import_module(f"gi.repository.{_ns}")
        break
    except (ValueError, ImportError):
        continue

APP_ID = "io.github.jonasmoreira01.lyrics-overlay"
APP_DIR = Path(__file__).resolve().parent
ICON_DIR = APP_DIR / "icons"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "lyrics-overlay"
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lyrics-overlay" / "config.json"

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
LRC_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
UA = {"User-Agent": "lyrics-overlay/0.2 (https://github.com/JonasMoreira01/lyrics-overlay)"}
# Ruido comum em titulos de video: "(Official Video)", "[Lyric Video]", "(Remastered 2011)"...
TITLE_NOISE = re.compile(
    r"\s*[\(\[][^)\]]*(official|video|audio|lyric|visuali[sz]er|remaster|live|hd|4k)[^)\]]*[\)\]]", re.I)
CHANNEL_NOISE = re.compile(r"\s*(-\s*Topic|VEVO|Official)$", re.I)

CSS = b"""
window { background-color: transparent; }
#box { background-color: rgba(0, 0, 0, 0.60); border-radius: 10px; padding: 8px 14px; }
#cur { color: #ffffff; font-size: 15px; font-weight: bold; }
#tr  { color: #ffd166; font-size: 12px; }
#nxt { color: #9a9a9a; font-size: 11px; }
"""


# ---------------------------------------------------------------- config

def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except OSError:
        pass


# ---------------------------------------------------------------- letra / traducao

def parse_lrc(text):
    """Retorna lista ordenada de (segundos, texto)."""
    out = []
    for raw in text.splitlines():
        stamps = LRC_RE.findall(raw)
        if not stamps:
            continue
        line = LRC_RE.sub("", raw).strip()
        for m, s in stamps:
            out.append((int(m) * 60 + float(s), line))
    out.sort(key=lambda x: x[0])
    return out


def clean_browser_meta(artist, title):
    """YouTube: titulo costuma ser 'Artista - Musica (Official Video)' e o 'artista' e o canal."""
    title = TITLE_NOISE.sub("", title).strip()
    artist = CHANNEL_NOISE.sub("", artist).strip()
    if " - " in title:
        a, t = title.split(" - ", 1)
        return a.strip(), t.strip()
    return artist, title


def track_key(artist, title):
    return hashlib.sha1(f"{artist}|{title}".lower().encode()).hexdigest()[:16]


def fetch_lyrics(artist, title, album, duration):
    """Busca letra sincronizada no LRCLIB. Retorna lista [(t, texto)] ou None."""
    cache = CACHE_DIR / f"{track_key(artist, title)}.lrc"
    if cache.exists():
        return parse_lrc(cache.read_text()) or None
    params = {"artist_name": artist, "track_name": title, "album_name": album, "duration": int(duration)}
    synced = None
    try:
        r = requests.get("https://lrclib.net/api/get", params=params, headers=UA, timeout=8)
        if r.ok:
            synced = r.json().get("syncedLyrics")
        if not synced:
            r = requests.get("https://lrclib.net/api/search", params={"artist_name": artist, "track_name": title},
                             headers=UA, timeout=8)
            if r.ok:
                for item in r.json():
                    if item.get("syncedLyrics"):
                        synced = item["syncedLyrics"]
                        break
    except requests.RequestException:
        return None
    if not synced:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(synced)
    return parse_lrc(synced) or None


def translate_google(text):
    r = requests.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "en", "tl": "pt", "dt": "t", "q": text},
        headers=UA, timeout=6,
    )
    r.raise_for_status()
    return "".join(part[0] for part in r.json()[0])


def translate_argos(text):
    import argostranslate.translate as t
    return t.translate(text, "en", "pt")


def translate(text):
    """Offline (argostranslate) se instalado; senao Google gtx (sujeito a 429)."""
    try:
        return translate_argos(text)
    except ImportError:
        return translate_google(text)


# ---------------------------------------------------------------- overlay

class Overlay(Gtk.Window):
    def __init__(self, args, translate_on):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.args = args
        self.translate_on = translate_on
        self.tr_gen = 0          # invalida threads de traducao antigas
        self.lines = []          # [(t, texto)]
        self.times = []
        self.trans = {}          # texto -> traducao
        self.track = None        # (artist, title)
        self.idx = -2
        self.proxy = None

        self.set_title("Lyrics Overlay")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.stick()
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)

        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_name("box")
        box.set_size_request(380, -1)  # largura fixa: evita quebrar o texto em janela estreita
        self.cur, self.tr, self.nxt = (Gtk.Label() for _ in range(3))
        for name, lbl in (("cur", self.cur), ("tr", self.tr), ("nxt", self.nxt)):
            lbl.set_name(name)
            lbl.set_line_wrap(True)
            lbl.set_justify(Gtk.Justification.CENTER)
            lbl.set_max_width_chars(40)
            box.pack_start(lbl, False, False, 0)
        self.add(box)
        self.connect("size-allocate", lambda *_: self.reposition())
        self.connect("realize", self.make_click_through)
        self.show_all()
        self.tr.set_visible(False)

        GLib.timeout_add(150, self.tick)

    # -- janela

    def make_click_through(self, *_):
        if cairo:
            self.get_window().input_shape_combine_region(cairo.Region(), 0, 0)

    def reposition(self):
        display = Gdk.Display.get_default()
        n = display.get_n_monitors()
        mon = display.get_monitor(self.args.monitor) if 0 <= self.args.monitor < n else display.get_primary_monitor()
        geo = mon.get_geometry()
        w, h = self.get_size()
        margin = 8
        self.move(geo.x + geo.width - w - margin, geo.y + geo.height - h - margin)

    def set_overlay_visible(self, on):
        if on:
            self.show()
            self.reposition()
        else:
            self.hide()

    def set_translate(self, on):
        self.translate_on = on
        cfg = load_config()
        cfg["translate"] = on
        save_config(cfg)
        if on:
            self.start_translation()
        self.refresh_line()

    # -- player (MPRIS)

    def find_player(self):
        """Primeiro player MPRIS tocando (Spotify tem prioridade). Retorna proxy ou None."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                  "ListNames", None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 500, None
                                  ).unpack()[0]
        except GLib.Error:
            return None
        cands = sorted((n for n in names if n.startswith(MPRIS_PREFIX)), key=lambda n: n != MPRIS_PREFIX + "spotify")
        for name in cands:
            try:
                proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                    name, MPRIS_PATH, "org.freedesktop.DBus.Properties", None)
                self.proxy = proxy
                if self.get_prop("PlaybackStatus") == "Playing":
                    return proxy
            except GLib.Error:
                continue
        self.proxy = None
        return None

    def get_prop(self, name):
        res = self.proxy.call_sync("Get", GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", name)),
                                   Gio.DBusCallFlags.NONE, 500, None)
        return res.unpack()[0]

    # -- texto

    def set_text(self, cur="", tr="", nxt=""):
        self.cur.set_text(cur)
        self.tr.set_text(tr)
        self.tr.set_visible(bool(tr) and self.translate_on)
        self.nxt.set_text(nxt)
        self.nxt.set_visible(bool(nxt))

    def refresh_line(self):
        """Reaplica a traducao da linha atual (chegou depois da linha na tela, ou o toggle mudou)."""
        if 0 <= self.idx < len(self.lines):
            tr = self.trans.get(self.lines[self.idx][1], "")
            self.tr.set_text(tr)
            self.tr.set_visible(bool(tr) and self.translate_on)
        else:
            self.tr.set_visible(False)
        return False

    # -- loop principal

    def tick(self):
        try:
            self.update()
        except GLib.Error:
            self.proxy = None
            self.track = None
            self.set_text("Nenhuma midia tocando")
        return True

    def update(self):
        # Player atual pausou/fechou -> procura outro que esteja tocando.
        if self.proxy and self.get_prop("PlaybackStatus") != "Playing":
            self.proxy = None
        if not self.proxy and not self.find_player():
            if self.track is None:
                self.set_text("Nenhuma midia tocando")
            return
        meta = self.get_prop("Metadata")
        title = meta.get("xesam:title", "")
        raw_artist = meta.get("xesam:artist", "")
        artist = ", ".join(raw_artist) if isinstance(raw_artist, list) else str(raw_artist)
        if not self.proxy.get_name().endswith(".spotify"):
            artist, title = clean_browser_meta(artist, title)
        album = meta.get("xesam:album", "")
        duration = meta.get("mpris:length", 0) / 1e6
        if (artist, title) != self.track:
            self.track = (artist, title)
            self.lines, self.times, self.trans, self.idx = [], [], {}, -2
            self.tr_gen += 1
            self.set_text("", "", f"{title} - {artist}\nbuscando letra...")
            threading.Thread(target=self.load_track, args=(artist, title, album, duration), daemon=True).start()
            return
        if not self.lines:
            return
        pos = self.get_prop("Position") / 1e6 + self.args.offset
        i = bisect.bisect_right(self.times, pos) - 1
        if i == self.idx:
            return
        self.idx = i
        cur = self.lines[i][1] if i >= 0 else ""
        nxt = self.lines[i + 1][1] if i + 1 < len(self.lines) else ""
        self.set_text(cur, self.trans.get(cur, ""), nxt)

    # -- letra e traducao em segundo plano

    def load_track(self, artist, title, album, duration):
        lines = fetch_lyrics(artist, title, album, duration)
        if self.track == (artist, title):
            GLib.idle_add(self.apply_lyrics, artist, title, lines)

    def apply_lyrics(self, artist, title, lines):
        if self.track != (artist, title):
            return
        if not lines:
            self.set_text("", "", f"{title} - {artist}\nletra sincronizada nao encontrada")
            return
        self.lines, self.times, self.idx = lines, [t for t, _ in lines], -2
        self.set_text("", "", lines[0][1])
        self.start_translation()

    def start_translation(self):
        if not (self.translate_on and self.track and self.lines):
            return
        self.tr_gen += 1
        threading.Thread(target=self.translate_all, args=(self.tr_gen, self.track, self.lines), daemon=True).start()

    def translate_all(self, gen, track, lines):
        def stale():
            return gen != self.tr_gen or not self.translate_on or self.track != track

        cache = CACHE_DIR / f"{track_key(*track)}.pt.json"
        if cache.exists():
            self.trans = json.loads(cache.read_text())
            GLib.idle_add(self.refresh_line)
            return
        # Comeca pela linha atual, para a tela ter traducao o quanto antes.
        start = max(self.idx, 0)
        out, fails = dict(self.trans), 0
        for _, text in lines[start:] + lines[:start]:
            if stale():
                return
            if not text or text in out:
                continue
            try:
                out[text] = translate(text)
                fails = 0
            except Exception:
                fails += 1
                if fails >= 3:  # provedor recusando (ex.: 429): desiste, sem cache parcial
                    return
                time.sleep(1)
                continue
            self.trans = out
            GLib.idle_add(self.refresh_line)
            time.sleep(0.05)
        if len(out) == len({t for _, t in lines if t}):
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(out, ensure_ascii=False))


# ---------------------------------------------------------------- bandeja do sistema

class Tray:
    """Icone na bandeja com menu: mostrar overlay, traduzir, sair."""

    def __init__(self, overlay, app):
        self.overlay = overlay
        menu = Gtk.Menu()

        self.show_item = Gtk.CheckMenuItem(label="Mostrar letra")
        self.show_item.set_active(True)
        self.show_item.connect("toggled", lambda i: overlay.set_overlay_visible(i.get_active()))
        menu.append(self.show_item)

        self.tr_item = Gtk.CheckMenuItem(label="Traduzir para português")
        self.tr_item.set_active(overlay.translate_on)
        self.tr_item.connect("toggled", lambda i: overlay.set_translate(i.get_active()))
        menu.append(self.tr_item)

        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Sair")
        quit_item.connect("activate", lambda *_: app.quit())
        menu.append(quit_item)
        menu.show_all()
        self.menu = menu

        if AppIndicator:
            ind = AppIndicator.Indicator.new("lyrics-overlay", "lyrics-overlay",
                                             AppIndicator.IndicatorCategory.APPLICATION_STATUS)
            ind.set_icon_theme_path(str(ICON_DIR))
            ind.set_title("Lyrics Overlay")
            ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            ind.set_menu(menu)
            self.indicator = ind
        else:  # fallback para desktops sem AppIndicator
            icon = Gtk.StatusIcon()
            icon.set_from_file(str(ICON_DIR / "lyrics-overlay.svg"))
            icon.set_tooltip_text("Lyrics Overlay")
            icon.connect("popup-menu", lambda _i, button, t: menu.popup(None, None, None, None, button, t))
            self.status_icon = icon


# ---------------------------------------------------------------- aplicacao (instancia unica)

class App(Gio.Application):
    def __init__(self, args):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.args = args
        self.overlay = None
        self.tray = None

    def do_activate(self):
        if self.overlay:  # segunda execucao: so traz o overlay de volta
            self.overlay.set_overlay_visible(True)
            self.tray.show_item.set_active(True)
            return
        self.hold()
        translate_on = load_config().get("translate", True) and not self.args.no_translate
        self.overlay = Overlay(self.args, translate_on)
        self.tray = Tray(self.overlay, self)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offset", type=float, default=0.0, help="ajuste de sincronia em segundos (+ adianta a letra)")
    ap.add_argument("--no-translate", action="store_true", help="inicia sem traducao (nesta execucao)")
    ap.add_argument("--monitor", type=int, default=-1, help="indice do monitor (default: primario)")
    args = ap.parse_args()
    app = App(args)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, app.quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, app.quit)
    app.run([])


if __name__ == "__main__":
    main()
