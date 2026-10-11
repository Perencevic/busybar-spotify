import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


class SpotifyError(Exception):
    pass


class RateLimited(SpotifyError):
    def __init__(self, seconds):
        self.seconds = seconds
        super().__init__("Spotify request limit reached")


class TokenStore:
    def __init__(self, directory):
        self.path = directory / "spotify.json"

    def read(self):
        if not self.path.exists():
            raise SpotifyError("Sign in first with busybar-spotify auth")
        return json.loads(self.path.read_text())

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle)
        os.replace(temporary, self.path)
        self.path.chmod(0o600)


def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authenticate(config):
    if not config.client_id:
        raise SpotifyError("Set SPOTIFY_CLIENT_ID in .env first")
    redirect = urlparse(config.redirect_uri)
    if redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or not redirect.port:
        raise SpotifyError("Use an http://127.0.0.1:PORT/callback redirect URI")
    verifier, challenge = pkce_pair()
    expected_state = secrets.token_urlsafe(32)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            request = urlparse(self.path)
            params = parse_qs(request.query)
            if request.path != redirect.path:
                self.send_error(404)
                return
            returned_state = params.get("state", [""])[0]
            if not hmac.compare_digest(returned_state, expected_state):
                self.send_error(400, "Invalid sign-in state")
                return
            if "error" in params or "code" not in params:
                result["error"] = True
                self.send_error(400, "Spotify sign-in was declined")
                return
            result["code"] = params["code"][0]
            content = b"<h1>Spotify connected</h1><p>You can close this tab and return to the app.</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    parameters = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri,
        "state": expected_state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "scope": "user-read-playback-state user-modify-playback-state",
    }
    with HTTPServer(("127.0.0.1", redirect.port), Callback) as server:
        server.timeout = 1
        url = "https://accounts.spotify.com/authorize?" + urlencode(parameters)
        if not webbrowser.open(url):
            raise SpotifyError("Could not open the browser for Spotify sign-in")
        print("Waiting for Spotify sign-in in your browser.", flush=True)
        deadline = time.monotonic() + 300
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if "code" not in result:
        raise SpotifyError("Spotify sign-in did not complete; run auth again")
    with httpx.Client(timeout=15) as client:
        response = client.post("https://accounts.spotify.com/api/token", data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": config.redirect_uri,
            "client_id": config.client_id,
            "code_verifier": verifier,
        })
    if response.status_code != 200:
        raise SpotifyError("Spotify token exchange failed; check the app settings and try again")
    data = response.json()
    data["expires_at"] = time.time() + data["expires_in"]
    data["client_id"] = config.client_id
    TokenStore(config.state_dir).write(data)
    print("Spotify connected. Tokens saved locally outside Git.", flush=True)


class Spotify:
    def __init__(self, config, client=None):
        self.config = config
        self.store = TokenStore(config.state_dir)
        self.tokens = self.store.read()
        if self.tokens.get("client_id") != config.client_id:
            raise SpotifyError("Client ID changed; sign in again")
        self.client = client or httpx.AsyncClient(timeout=12)
        self.token_lock = asyncio.Lock()
        self.retry_at = 0

    async def close(self):
        await self.client.aclose()

    async def refresh(self, force=False):
        async with self.token_lock:
            if not force and self.tokens.get("expires_at", 0) > time.time() + 60:
                return
            response = await self.client.post("https://accounts.spotify.com/api/token", data={
                "grant_type": "refresh_token",
                "refresh_token": self.tokens["refresh_token"],
                "client_id": self.config.client_id,
            })
            if response.status_code != 200:
                raise SpotifyError("Spotify session could not refresh; sign in again")
            self.tokens.update(response.json())
            self.tokens["expires_at"] = time.time() + self.tokens["expires_in"]
            self.store.write(self.tokens)

    async def request(self, method, path, **kwargs):
        if time.monotonic() < self.retry_at:
            raise RateLimited(self.retry_at - time.monotonic())
        for attempt in range(2):
            await self.refresh(force=attempt > 0)
            response = await self.client.request(method, "https://api.spotify.com/v1" + path,
                headers={"Authorization": "Bearer " + self.tokens["access_token"]}, **kwargs)
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code == 429:
                try:
                    delay = max(1, float(response.headers.get("Retry-After", "30")))
                except ValueError:
                    delay = 30
                self.retry_at = time.monotonic() + delay
                raise RateLimited(delay)
            if response.status_code == 403:
                raise SpotifyError("Spotify denied access; check Premium, app access, and the selected player")
            if response.status_code == 404:
                raise SpotifyError("No available Spotify player; select your speaker in Spotify and start a track")
            if response.is_error:
                raise SpotifyError(f"Spotify request failed (HTTP {response.status_code})")
            if response.status_code == 204 or method != "GET":
                return None
            try:
                return response.json()
            except ValueError:
                raise SpotifyError("Spotify returned an incomplete response; retrying") from None

    async def playback(self):
        return await self.request("GET", "/me/player")

    async def toggle(self, playback):
        path = "/me/player/pause" if playback.get("is_playing") else "/me/player/play"
        await self.request("PUT", path, params={"device_id": playback["device"]["id"]})

    async def volume(self, device_id, value):
        await self.request("PUT", "/me/player/volume", params={"device_id": device_id, "volume_percent": value})
