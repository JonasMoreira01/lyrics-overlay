#!/usr/bin/env python3
"""Lyrics Overlay: letra sincronizada (e traducao EN->PT) da musica que esta tocando, no canto da tela.

Musica atual: MPRIS via D-Bus (Spotify, Chrome/Chromium, Firefox...). Letra: LRCLIB.
Traducao: argostranslate (offline) se instalado; senao Google gtx (sujeito a 429).
Modo estudo: clique numa palavra para ver IPA, significado e salvar no vocabulario.
Controle pelo icone na bandeja do sistema. Linux/X11.
"""
import argparse
import bisect
import hashlib
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import gi
import requests

import study

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
UA = {"User-Agent": "lyrics-overlay/0.3 (https://github.com/JonasMoreira01/lyrics-overlay)"}
# Ruido comum em titulos de video: "(Official Video)", "[Lyric Video]", "(Remastered 2011)"...
TITLE_NOISE = re.compile(
    r"\s*[\(\[][^)\]]*(official|video|audio|lyric|visuali[sz]er|remaster|live|hd|4k)[^)\]]*[\)\]]", re.I)
CHANNEL_NOISE = re.compile(r"\s*(-\s*Topic|VEVO|Official)$", re.I)

# Aparencia
SIZES = {
    "small": {"cur": 13, "tr": 11, "nxt": 10, "width": 320, "chars": 34},
    "medium": {"cur": 15, "tr": 12, "nxt": 11, "width": 380, "chars": 40},
    "large": {"cur": 19, "tr": 15, "nxt": 13, "width": 480, "chars": 50},
}
CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")
MARGIN = 8
NUDGE = 0.5  # segundos por clique no ajuste de sincronia
DEFAULTS = {
    "translate": True,
    "size": "medium",
    "opacity": 0.6,
    "corner": "bottom-right",
    "monitor": -1,
    "autohide": False,
    "pause_on_lookup": True,
    "offsets": {},  # track_key -> segundos
}

# Modo demonstracao (--demo): frases proprias, sem player nem letra real.
DEMO_TRACK = ("Demo Artist", "Demo Song")
DEMO_LINES = [
    (0.0, "I walked along the river"),
    (4.0, "The water was cold and still"),
    (8.0, "And I thought about the summer"),
    (12.0, "When we had nothing to fear"),
    (16.0, ""),
    (18.0, "Now the city lights are fading"),
    (22.0, "But I still remember your voice"),
    (26.0, "Tomorrow I will find my way"),
    (30.0, "And leave the past behind"),
]
DEMO_LENGTH = 34.0


def build_css(size, opacity):
    s = SIZES[size]
    return f"""
window {{ background-color: transparent; }}
#box {{ background-color: rgba(0, 0, 0, {opacity}); border-radius: 10px; padding: 8px 14px; }}
#cur {{ color: #ffffff; font-size: {s['cur']}px; font-weight: bold; }}
#cur link {{ color: #9ecbff; }}
#tr  {{ color: #ffd166; font-size: {s['tr']}px; }}
#nxt {{ color: #9a9a9a; font-size: {s['nxt']}px; }}
#infobox {{ background-color: rgba(45, 45, 95, 0.90); border-radius: 8px; padding: 6px 10px; }}
#info {{ color: #ffffff; font-size: {s['tr']}px; }}
#infobox button {{ font-size: {s['nxt']}px; padding: 1px 8px; min-height: 0; min-width: 0; }}
""".encode()


# ---------------------------------------------------------------- config

def load_config():
    cfg = dict(DEFAULTS)
    try:
        stored = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    cfg.update({k: v for k, v in stored.items() if k in DEFAULTS})
    cfg["offsets"] = dict(cfg["offsets"])  # copia: nao mutar DEFAULTS
    if cfg["size"] not in SIZES:
        cfg["size"] = DEFAULTS["size"]
    if cfg["corner"] not in CORNERS:
        cfg["corner"] = DEFAULTS["corner"]
    return cfg


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


class OfflineUnavailable(Exception):
    """argostranslate ausente ou sem o modelo EN->PT."""


def translate_argos(text):
    try:
        import argostranslate.translate as t
        return t.translate(text, "en", "pt")
    except (ImportError, AttributeError) as e:  # AttributeError: modelo EN->PT nao instalado
        raise OfflineUnavailable(str(e)) from e


def translate(text):
    """Offline (argostranslate) se disponivel; senao Google gtx (sujeito a 429)."""
    try:
        return translate_argos(text)
    except OfflineUnavailable:
        return translate_google(text)


def split_stanzas(lines, max_len=8):
    """Agrupa linhas consecutivas (separadas por linhas vazias) em blocos de ate max_len linhas."""
    blocks, cur = [], []
    for _, text in lines:
        if text:
            cur.append(text)
            if len(cur) >= max_len:
                blocks.append(cur)
                cur = []
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def translate_block(block):
    """Traduz a estrofe inteira (o contexto melhora o resultado); se a contagem de linhas
    nao bater, volta para linha a linha. Retorna {linha_original: traducao}."""
    try:
        parts = translate("\n".join(block)).split("\n")
        if len(parts) == len(block):
            return {src: dst.strip() for src, dst in zip(block, parts)}
    except Exception:
        pass
    return {src: translate(src) for src in block}


def words_markup(text):
    """Texto da linha com cada palavra como link (<a href="word:...">), para o modo estudo."""
    esc = GLib.markup_escape_text
    out, pos = [], 0
    for m in study.WORD_RE.finditer(text):
        out.append(esc(text[pos:m.start()]))
        word = m.group(0)
        key = study.normalize(word)
        out.append('<a href="word:' + key + '" title="ver significado">' + esc(word) + "</a>")
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out)


# ---------------------------------------------------------------- overlay

class Overlay(Gtk.Window):
    def __init__(self, args, cfg):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.args = args
        self.cfg = cfg
        self.translate_on = cfg["translate"]
        self.study = False
        self.user_visible = True
        self.idle_since = None     # desde quando nada toca (para ocultar sozinho)
        self.tr_gen = 0            # invalida threads de traducao antigas
        self.lines = []            # [(t, texto)]
        self.times = []
        self.trans = {}            # texto -> traducao
        self.track = None          # (artist, title)
        self.idx = -2
        self.proxy = None
        self.last_player = None    # nome D-Bus do ultimo player tocando
        self.paused_by_us = False
        self.cur_text = ""
        self.lookup = None
        self.lookup_ticket = 0
        self.demo_t0 = time.monotonic()
        self.on_state_change = None  # a bandeja atualiza o rotulo de sincronia

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

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.box.set_name("box")
        self.cur, self.tr, self.nxt = (Gtk.Label() for _ in range(3))
        for name, lbl in (("cur", self.cur), ("tr", self.tr), ("nxt", self.nxt)):
            lbl.set_name(name)
            lbl.set_line_wrap(True)
            lbl.set_justify(Gtk.Justification.CENTER)
            self.box.pack_start(lbl, False, False, 0)
        self.cur.connect("activate-link", self.on_word_link)

        # painel de consulta de palavra (modo estudo)
        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.info_box.set_name("infobox")
        self.info = Gtk.Label()
        self.info.set_name("info")
        self.info.set_line_wrap(True)
        self.info.set_xalign(0)
        self.info_box.pack_start(self.info, False, False, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.btn_save = Gtk.Button(label="Salvar palavra")
        self.btn_save.connect("clicked", self.on_save_word)
        buttons.pack_start(self.btn_save, False, False, 0)
        if shutil.which("espeak-ng"):
            btn_say = Gtk.Button(label="Ouvir")
            btn_say.connect("clicked", self.on_say_word)
            buttons.pack_start(btn_say, False, False, 0)
        btn_close = Gtk.Button(label="Fechar")
        btn_close.connect("clicked", lambda *_: self.hide_info())
        buttons.pack_end(btn_close, False, False, 0)
        for b in buttons.get_children():
            b.set_can_focus(False)
        self.info_box.pack_start(buttons, False, False, 0)
        self.box.pack_start(self.info_box, False, False, 0)

        self.add(self.box)
        self.apply_style()
        self.connect("size-allocate", lambda *_: self.reposition())
        self.connect("realize", lambda *_: self.apply_input_shape())
        self.show_all()
        self.tr.set_visible(False)
        self.info_box.hide()

        GLib.timeout_add(150, self.tick)

    # -- janela e aparencia

    def apply_style(self):
        s = SIZES[self.cfg["size"]]
        self.css.load_from_data(build_css(self.cfg["size"], self.cfg["opacity"]))
        self.box.set_size_request(s["width"], -1)
        for lbl in (self.cur, self.tr, self.nxt, self.info):
            lbl.set_max_width_chars(s["chars"])
        self.resize(1, 1)

    def apply_input_shape(self):
        """Fora do modo estudo a janela ignora cliques; no modo estudo recebe."""
        win = self.get_window()
        if not win:
            return
        if hasattr(win, "set_pass_through"):  # GTK >= 3.18
            win.set_pass_through(not self.study)
        elif cairo and not self.study:
            win.input_shape_combine_region(cairo.Region(), 0, 0)

    def monitor_index(self):
        return self.args.monitor if self.args.monitor is not None else self.cfg["monitor"]

    def reposition(self):
        display = Gdk.Display.get_default()
        idx = self.monitor_index()
        mon = display.get_monitor(idx) if 0 <= idx < display.get_n_monitors() else display.get_primary_monitor()
        geo = mon.get_geometry()
        w, h = self.get_size()
        corner = self.cfg["corner"]
        x = geo.x + MARGIN if corner.endswith("left") else geo.x + geo.width - w - MARGIN
        y = geo.y + MARGIN if corner.startswith("top") else geo.y + geo.height - h - MARGIN
        self.move(x, y)

    def apply_visibility(self):
        idle = self.idle_since is not None and time.monotonic() - self.idle_since > 3
        auto_hidden = self.cfg["autohide"] and idle and not self.info_box.get_visible()
        want = self.user_visible and not auto_hidden
        if want != self.get_visible():
            if want:
                self.show()
                self.reposition()
            else:
                self.hide()

    def note_playing(self, playing):
        if playing:
            self.idle_since = None
        elif self.idle_since is None:
            self.idle_since = time.monotonic()
        self.apply_visibility()

    # -- opcoes (chamadas pela bandeja)

    def set_setting(self, key, value, restyle=False):
        self.cfg[key] = value
        save_config(self.cfg)
        if restyle:
            self.apply_style()
        self.reposition()
        self.apply_visibility()

    def set_overlay_visible(self, on):
        self.user_visible = on
        self.apply_visibility()

    def set_translate(self, on):
        self.translate_on = on
        self.cfg["translate"] = on
        save_config(self.cfg)
        if on:
            self.start_translation()
        self.refresh_line()

    def set_study(self, on):
        self.study = on
        self.apply_input_shape()
        self.render_cur()
        if not on:
            self.hide_info()

    # -- sincronia por musica

    def track_offset(self):
        return self.cfg["offsets"].get(track_key(*self.track), 0.0) if self.track else 0.0

    def nudge_offset(self, delta):
        if not self.track:
            return
        key = track_key(*self.track)
        value = 0.0 if delta is None else round(self.cfg["offsets"].get(key, 0.0) + delta, 2)
        if value == 0.0:
            self.cfg["offsets"].pop(key, None)
        else:
            self.cfg["offsets"][key] = value
        save_config(self.cfg)
        self.idx = -2  # forca reavaliar a linha atual
        self.notify_state()

    def notify_state(self):
        if self.on_state_change:
            self.on_state_change()

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

    def player_call(self, method):
        """Pause/Play no ultimo player tocando."""
        if self.args.demo or not self.last_player:
            return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(self.last_player, MPRIS_PATH, "org.mpris.MediaPlayer2.Player", method, None, None,
                          Gio.DBusCallFlags.NONE, 500, None)
        except GLib.Error:
            pass

    def read_player(self):
        """(artist, title, album, duration) do player tocando, ou None."""
        if self.proxy and self.get_prop("PlaybackStatus") != "Playing":
            self.proxy = None  # pausou/fechou -> procura outro que esteja tocando
        if not self.proxy and not self.find_player():
            return None
        self.last_player = self.proxy.get_name()
        meta = self.get_prop("Metadata")
        title = meta.get("xesam:title", "")
        raw_artist = meta.get("xesam:artist", "")
        artist = ", ".join(raw_artist) if isinstance(raw_artist, list) else str(raw_artist)
        if not self.last_player.endswith(".spotify"):
            artist, title = clean_browser_meta(artist, title)
        return artist, title, meta.get("xesam:album", ""), meta.get("mpris:length", 0) / 1e6

    # -- texto

    def set_text(self, cur="", tr="", nxt=""):
        self.cur_text = cur
        self.render_cur()
        self.tr.set_text(tr)
        self.tr.set_visible(bool(tr) and self.translate_on)
        self.nxt.set_text(nxt)
        self.nxt.set_visible(bool(nxt))

    def render_cur(self):
        if self.study and self.cur_text:
            self.cur.set_markup(words_markup(self.cur_text))
        else:
            self.cur.set_text(self.cur_text)

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
            self.note_playing(False)
            self.set_text("Nenhuma midia tocando")
        return True

    def update(self):
        meta = (DEMO_TRACK + ("", DEMO_LENGTH)) if self.args.demo else self.read_player()
        self.note_playing(meta is not None)
        if meta is None:
            if self.track is None:
                self.set_text("Nenhuma midia tocando")
            return
        artist, title, album, duration = meta
        if (artist, title) != self.track:
            self.track = (artist, title)
            self.lines, self.times, self.trans, self.idx = [], [], {}, -2
            self.tr_gen += 1
            self.hide_info(resume=False)
            self.notify_state()
            if self.args.demo:
                self.apply_lyrics(artist, title, DEMO_LINES)
            else:
                self.set_text("", "", f"{title} - {artist}\nbuscando letra...")
                threading.Thread(target=self.load_track, args=(artist, title, album, duration), daemon=True).start()
            return
        if not self.lines:
            return
        if self.args.demo:
            pos = (time.monotonic() - self.demo_t0) % DEMO_LENGTH
        else:
            pos = self.get_prop("Position") / 1e6
        pos += self.args.offset + self.track_offset()
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

        # sufixo .pt2: traducao por estrofe (o .pt.json antigo era linha a linha)
        cache = CACHE_DIR / f"{track_key(*track)}.pt2.json"
        if cache.exists():
            self.trans = json.loads(cache.read_text())
            GLib.idle_add(self.refresh_line)
            return
        blocks = split_stanzas(lines)
        # Comeca pela estrofe da linha atual, para a tela ter traducao o quanto antes.
        cur_text = lines[max(self.idx, 0)][1]
        k = next((i for i, b in enumerate(blocks) if cur_text in b), 0)
        out, fails = dict(self.trans), 0
        for block in blocks[k:] + blocks[:k]:
            if stale():
                return
            if all(text in out for text in block):
                continue
            try:
                out.update(translate_block(block))
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
        if all(text in out for block in blocks for text in block):
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(out, ensure_ascii=False))

    # -- modo estudo: consulta de palavra e vocabulario

    def on_word_link(self, _label, uri):
        if not uri.startswith("word:"):
            return False
        word = uri[len("word:"):]
        line = self.cur_text
        self.lookup_ticket += 1
        ticket = self.lookup_ticket
        self.lookup = None
        if self.cfg["pause_on_lookup"] and self.idle_since is None and not self.paused_by_us:
            self.player_call("Pause")
            self.paused_by_us = True
        self.info.set_markup("<b>" + GLib.markup_escape_text(word) + "</b>  buscando...")
        self.btn_save.set_label("Salvar palavra")
        self.btn_save.set_sensitive(False)
        self.info_box.show_all()
        self.apply_visibility()
        threading.Thread(target=self.do_lookup, args=(ticket, word, line), daemon=True).start()
        return True

    def do_lookup(self, ticket, word, line):
        info, tr = None, ""
        try:
            info = study.lookup(word, CACHE_DIR, UA)
        except requests.RequestException:
            pass
        try:
            tr = translate(word).strip().rstrip(".!")
            tr = tr[:1].lower() + tr[1:]
        except Exception:
            pass
        GLib.idle_add(self.show_lookup, ticket, word, info, tr, line)

    def show_lookup(self, ticket, word, info, tr, line):
        if ticket != self.lookup_ticket:
            return
        esc = GLib.markup_escape_text
        head = "<b>" + esc(word) + "</b>"
        if info and info["ipa"]:
            head += "  <i>/" + esc(info["ipa"]) + "/</i>"
        parts = [head]
        if tr:
            parts.append('<span foreground="#ffd166">' + esc(tr) + "</span>")
        definition = ""
        if info and info["defs"]:
            for d in info["defs"][:2]:
                text = d["text"] if len(d["text"]) <= 140 else d["text"][:137].rstrip() + "…"
                prefix = "(" + d["pos"] + ") " if d["pos"] else ""
                parts.append("<small>" + esc(prefix + text) + "</small>")
            first = info["defs"][0]
            definition = (("(" + first["pos"] + ") ") if first["pos"] else "") + first["text"]
        else:
            parts.append("<small>definição não encontrada</small>")
        self.info.set_markup("\n".join(parts))
        self.lookup = {"word": word, "ipa": info["ipa"] if info else "", "tr": tr, "defn": definition, "line": line}
        self.btn_save.set_sensitive(True)

    def on_save_word(self, btn):
        if not self.lookup:
            return
        lk = self.lookup
        track = " - ".join(self.track) if self.track else ""
        try:
            saved = study.save_word(lk["word"], lk["ipa"], lk["tr"], lk["defn"], lk["line"], track)
            btn.set_label("Salvo" if saved else "Já estava salvo")
        except OSError:
            btn.set_label("Erro ao salvar")
        btn.set_sensitive(False)

    def on_say_word(self, _btn):
        if self.lookup:
            subprocess.Popen(["espeak-ng", "-v", "en-us", "-s", "140", self.lookup["word"]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def hide_info(self, resume=True):
        self.lookup_ticket += 1
        self.lookup = None
        self.info_box.hide()
        self.resize(1, 1)
        if resume and self.paused_by_us:
            self.player_call("Play")
        self.paused_by_us = False

    def open_vocabulary(self):
        path = study.ensure_vocab_file()
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(str(path)).get_uri(), None)
        except GLib.Error:
            pass


# ---------------------------------------------------------------- bandeja do sistema

def radio_submenu(title, options, current, callback):
    """Submenu de escolha unica. options = [(valor, rotulo)]."""
    sub = Gtk.Menu()
    first = None
    for value, label in options:
        item = Gtk.RadioMenuItem.new_with_label_from_widget(first, label)
        first = first or item
        item.set_active(value == current)
        item.connect("toggled", lambda i, v=value: i.get_active() and callback(v))
        sub.append(item)
    root = Gtk.MenuItem(label=title)
    root.set_submenu(sub)
    return root


def check_item(label, active, callback):
    item = Gtk.CheckMenuItem(label=label)
    item.set_active(active)
    item.connect("toggled", lambda i: callback(i.get_active()))
    return item


def submenu(title, items):
    sub = Gtk.Menu()
    for it in items:
        sub.append(it)
    root = Gtk.MenuItem(label=title)
    root.set_submenu(sub)
    return root


def action_item(label, callback):
    item = Gtk.MenuItem(label=label)
    item.connect("activate", lambda *_: callback())
    return item


class Tray:
    """Icone na bandeja com o menu de controle."""

    def __init__(self, overlay, app):
        self.overlay = overlay
        cfg = overlay.cfg

        self.show_item = check_item("Mostrar letra", True, overlay.set_overlay_visible)
        self.offset_label = Gtk.MenuItem(label="")
        self.offset_label.set_sensitive(False)

        def set_opt(key, restyle=False):
            return lambda v: overlay.set_setting(key, v, restyle=restyle)

        display = Gdk.Display.get_default()
        monitors = [(-1, "Primário")]
        for i in range(display.get_n_monitors()):
            g = display.get_monitor(i).get_geometry()
            monitors.append((i, f"Monitor {i + 1} ({g.width}x{g.height})"))
        opacities = [(0.85, "Forte (85%)"), (0.6, "Média (60%)"), (0.35, "Leve (35%)")]
        nearest = min(opacities, key=lambda o: abs(o[0] - cfg["opacity"]))[0]

        menu = Gtk.Menu()
        for item in (
            self.show_item,
            check_item("Traduzir para português", overlay.translate_on, overlay.set_translate),
            check_item("Ocultar quando nada toca", cfg["autohide"], set_opt("autohide")),
            Gtk.SeparatorMenuItem(),
            submenu("Estudo", [
                check_item("Modo estudo (clicar nas palavras)", False, overlay.set_study),
                check_item("Pausar a música ao consultar", cfg["pause_on_lookup"], set_opt("pause_on_lookup")),
                action_item("Abrir lista de vocabulário", overlay.open_vocabulary),
            ]),
            submenu("Aparência", [
                radio_submenu("Tamanho", [("small", "Pequeno"), ("medium", "Médio"), ("large", "Grande")],
                              cfg["size"], set_opt("size", restyle=True)),
                radio_submenu("Fundo", opacities, nearest, set_opt("opacity", restyle=True)),
                radio_submenu("Posição", [("bottom-right", "Canto inferior direito"),
                                          ("bottom-left", "Canto inferior esquerdo"),
                                          ("top-right", "Canto superior direito"),
                                          ("top-left", "Canto superior esquerdo")],
                              cfg["corner"], set_opt("corner")),
                radio_submenu("Monitor", monitors, cfg["monitor"], set_opt("monitor")),
            ]),
            submenu("Sincronia da letra", [
                self.offset_label,
                action_item(f"Adiantar letra {NUDGE:.1f} s".replace(".", ","), lambda: overlay.nudge_offset(NUDGE)),
                action_item(f"Atrasar letra {NUDGE:.1f} s".replace(".", ","), lambda: overlay.nudge_offset(-NUDGE)),
                action_item("Zerar ajuste desta música", lambda: overlay.nudge_offset(None)),
            ]),
            Gtk.SeparatorMenuItem(),
            action_item("Sair", app.quit),
        ):
            menu.append(item)
        menu.show_all()
        self.menu = menu
        overlay.on_state_change = self.refresh
        self.refresh()

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

    def refresh(self):
        ov = self.overlay
        if ov.track:
            text = f"Ajuste desta música: {ov.track_offset():+.1f} s".replace(".", ",")
        else:
            text = "Nenhuma música tocando"
        self.offset_label.set_label(text)


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
        cfg = load_config()
        if self.args.no_translate:
            cfg["translate"] = False
        self.overlay = Overlay(self.args, cfg)
        self.tray = Tray(self.overlay, self)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offset", type=float, default=0.0,
                    help="ajuste global de sincronia em segundos (+ adianta a letra)")
    ap.add_argument("--no-translate", action="store_true", help="inicia sem traducao (nesta execucao)")
    ap.add_argument("--monitor", type=int, default=None, help="indice do monitor (sobrepoe a configuracao)")
    ap.add_argument("--demo", action="store_true", help="modo demonstracao: frases de exemplo, sem player")
    args = ap.parse_args()
    app = App(args)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, app.quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, app.quit)
    app.run([])


if __name__ == "__main__":
    main()
