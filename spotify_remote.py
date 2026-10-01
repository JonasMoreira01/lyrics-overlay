"""Spotify Web API como fonte de "tocando agora": celular ou qualquer aparelho da conta.

Autorizacao: Authorization Code + PKCE (sem client secret). O usuario cria o proprio app em
developer.spotify.com (a API exige Premium para o dono do app). Tokens ficam em
~/.config/lyrics-overlay/spotify.json (permissao 0600).

Sem dependencia de GTK, para ficar facil de testar.
"""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
SCOPES = "user-read-currently-playing user-modify-playback-state"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"  # o Spotify nao aceita "localhost"
POLL_PLAYING = 2.5   # segundos entre consultas enquanto toca
POLL_IDLE = 5.0      # ... e quando nao toca nada


class AuthError(Exception):
    """Falha na autorizacao (negada, estado invalido, tempo esgotado...)."""


class AuthRevoked(Exception):
    """O refresh token nao vale mais: precisa conectar de novo."""


class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__(f"429, tente em {retry_after}s")
        self.retry_after = retry_after


class Forbidden(Exception):
    """403: usuario fora da allowlist do app, ou endpoint indisponivel no modo de desenvolvimento."""


def token_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "lyrics-overlay" / "spotify.json"


# ---------------------------------------------------------------- autorizacao (PKCE)

def pkce_challenge(verifier):
    """S256: base64url(sha256(verifier)) sem padding (RFC 7636)."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def make_pkce():
    verifier = secrets.token_urlsafe(64)
    return verifier, pkce_challenge(verifier)


def authorize_url(client_id, challenge, state, redirect_uri=None):
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri or REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "scope": SCOPES,
    })


def wait_for_code(state, timeout=180, port=None, on_listening=None):
    """Sobe um servidor em 127.0.0.1 e espera o redirecionamento com ?code=. Retorna o code."""
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            if parts.path != "/callback":
                self.send_error(404)
                return
            qs = urllib.parse.parse_qs(parts.query)
            result.update({k: qs.get(k, [""])[0] for k in ("code", "state", "error")})
            body = ("<html><meta charset='utf-8'><body style='font-family:sans-serif'>"
                    "<h3>Pronto! Pode fechar esta aba e voltar ao Lyrics Overlay.</h3></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", REDIRECT_PORT if port is None else port), Handler)
    except OSError as e:
        raise AuthError(f"porta {REDIRECT_PORT} ocupada: {e}") from e
    server.timeout = 1
    try:
        if on_listening:
            on_listening()
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not result:
        raise AuthError("tempo esgotado esperando a autorizacao no navegador")
    if result.get("error"):
        raise AuthError("autorizacao negada: " + result["error"])
    if result.get("state") != state:
        raise AuthError("resposta com estado invalido (possivel redirecionamento forjado)")
    if not result.get("code"):
        raise AuthError("resposta sem codigo")
    return result["code"]


def _save(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def authorize(client_id, open_url, path=None, timeout=180, port=None):
    """Fluxo completo: abre o navegador (open_url), espera o code, troca por tokens e salva."""
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    redirect_uri = REDIRECT_URI if port is None else f"http://127.0.0.1:{port}/callback"
    url = authorize_url(client_id, challenge, state, redirect_uri)
    code = wait_for_code(state, timeout, port, on_listening=lambda: open_url(url))
    r = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": verifier}, timeout=10)
    if not r.ok:
        raise AuthError(f"o Spotify recusou a troca do codigo ({r.status_code}): {r.text[:120]}")
    tok = r.json()
    _save({"client_id": client_id, "access_token": tok["access_token"],
           "refresh_token": tok["refresh_token"], "expires_at": time.time() + tok.get("expires_in", 3600)},
          path or token_path())


# ---------------------------------------------------------------- consulta do que esta tocando

class SpotifyRemote:
    def __init__(self, path=None):
        self.path = path or token_path()
        self.tokens = {}
        try:
            self.tokens = json.loads(self.path.read_text())
        except (OSError, ValueError):
            pass
        self.latest = None   # dict da faixa tocando/pausada, ou None
        self.error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()  # acorda a consulta antes do intervalo (ex.: apos "proxima")
        self._thread = None

    @property
    def connected(self):
        return bool(self.tokens.get("refresh_token"))

    # -- tokens

    def _refresh(self):
        r = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"],
            "client_id": self.tokens["client_id"]}, timeout=10)
        if r.status_code in (400, 401):
            raise AuthRevoked()
        r.raise_for_status()
        tok = r.json()
        self.tokens.update(access_token=tok["access_token"], expires_at=time.time() + tok.get("expires_in", 3600))
        if tok.get("refresh_token"):  # o Spotify pode rotacionar o refresh token
            self.tokens["refresh_token"] = tok["refresh_token"]
        _save(self.tokens, self.path)

    def _request(self, method, endpoint, **kw):
        if time.time() >= self.tokens.get("expires_at", 0) - 30:
            self._refresh()
        for attempt in (1, 2):
            r = requests.request(method, API_URL + endpoint, timeout=8,
                                 headers={"Authorization": "Bearer " + self.tokens["access_token"]}, **kw)
            if r.status_code == 401 and attempt == 1:
                self._refresh()
                continue
            return r

    # -- estado

    def poll_once(self):
        """Estado atual (dict) ou None se nada toca / e anuncio / e podcast."""
        t0 = time.monotonic()
        r = self._request("GET", "/me/player/currently-playing", params={"additional_types": "track"})
        t1 = time.monotonic()
        if r.status_code == 204:
            return None
        if r.status_code == 429:
            raise RateLimited(int(r.headers.get("Retry-After", 5)))
        if r.status_code == 403:
            raise Forbidden()
        r.raise_for_status()
        j = r.json()
        item = j.get("item")
        if not item or j.get("currently_playing_type") != "track":
            return None
        return {
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "title": item.get("name", ""),
            "album": (item.get("album") or {}).get("name", ""),
            "duration": (item.get("duration_ms") or 0) / 1000,
            "progress": (j.get("progress_ms") or 0) / 1000,
            "is_playing": bool(j.get("is_playing")),
            "fetched": (t0 + t1) / 2,  # a posicao vale para o meio da requisicao
        }

    def position(self):
        """Posicao estimada agora (s), ou None."""
        with self._lock:
            s = dict(self.latest) if self.latest else None
        if not s:
            return None
        pos = s["progress"] + ((time.monotonic() - s["fetched"]) if s["is_playing"] else 0)
        return min(pos, s["duration"]) if s["duration"] else pos

    def snapshot(self):
        with self._lock:
            return dict(self.latest) if self.latest else None

    def _control(self, endpoint, playing):
        """pause/play (precisa de Premium). Melhor esforco: retorna True se aceito."""
        try:
            r = self._request("PUT", endpoint)
        except (requests.RequestException, AuthRevoked):
            return False
        if r.status_code not in (200, 202, 204):
            return False
        pos = self.position()
        with self._lock:
            if self.latest:
                self.latest.update(is_playing=playing, fetched=time.monotonic(),
                                   progress=pos if pos is not None else self.latest["progress"])
        return True

    def pause(self):
        return self._control("/me/player/pause", False)

    def play(self):
        return self._control("/me/player/play", True)

    def toggle(self):
        s = self.snapshot()
        return self.pause() if s and s["is_playing"] else self.play()

    def _skip(self, endpoint):
        try:
            r = self._request("POST", endpoint)
        except (requests.RequestException, AuthRevoked):
            return False
        if r.status_code not in (200, 202, 204):
            return False
        threading.Timer(0.5, self._wake.set).start()  # o Spotify leva um instante para trocar de faixa
        return True

    def next(self):
        return self._skip("/me/player/next")

    def previous(self):
        return self._skip("/me/player/previous")

    # -- thread de consulta

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def forget(self):
        """Desconectar: para a consulta e apaga os tokens."""
        self.stop()
        self.tokens = {}
        with self._lock:
            self.latest = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def _loop(self):
        fails = 0
        while not self._stop.is_set():
            delay = POLL_IDLE
            try:
                state = self.poll_once()
                with self._lock:
                    self.latest = state
                self.error, fails = "", 0
                delay = POLL_PLAYING if state else POLL_IDLE
            except RateLimited as e:
                delay = max(e.retry_after, POLL_IDLE)
            except AuthRevoked:
                self.error = "autorização revogada, conecte de novo"
                self.forget()
                return
            except Forbidden:
                self.error = "acesso negado (adicione seu e-mail em User Management no painel do app)"
                delay = 30
            except requests.RequestException:
                fails += 1
                if fails >= 3:
                    self.error = "sem conexão com o Spotify"
                    with self._lock:
                        self.latest = None
            self._wake.wait(delay)
            self._wake.clear()
