import asyncio
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from busylib.features.input_events import ButtonEvent, EncoderEvent
from PIL import Image

from busybar_spotify.app import Player
from busybar_spotify.bar import progress_elements, safe_text, thumbnail, track_elements, volume_elements
from busybar_spotify.config import Config
from busybar_spotify.spotify import RateLimited, Spotify, SpotifyError, TokenStore, pkce_pair
from busybar_spotify.launcher import Bridge, launcher_store


class LayoutTests(unittest.TestCase):
    def test_album_art_and_text_do_not_overlap(self):
        elements = track_elements("A long song title", "Artist")
        cover = next(element for element in elements if element["id"] == "cover")
        self.assertEqual((cover["x"], cover["y"]), (0, 0))
        for element in elements:
            if element["type"] == "text":
                self.assertGreaterEqual(element["x"], 16)
                self.assertLessEqual(element["x"] + element["width"], 72)

    def test_progress_clamps_at_both_ends(self):
        self.assertEqual(progress_elements(-10, 100)[1]["width"], 1)
        self.assertEqual(progress_elements(150, 100)[1]["width"], 54)
        self.assertEqual(progress_elements(100, 0)[1]["width"], 1)

    def test_foreign_text_is_printable_ascii(self):
        self.assertEqual(safe_text("Beyoncé\n🎵"), "Beyonce")
        self.assertEqual(safe_text("🎵"), "Unknown")

    def test_thumbnail_is_rgb_and_exact_size(self):
        source = io.BytesIO()
        Image.new("RGBA", (300, 180), (210, 80, 120, 255)).save(source, format="PNG")
        result = Image.open(io.BytesIO(thumbnail(source.getvalue())))
        self.assertEqual(result.size, (16, 16))
        self.assertEqual(result.mode, "RGB")

    def test_zero_volume_has_no_green_pixel(self):
        level = next(element for element in volume_elements(0) if element["id"] == "volume-level")
        self.assertEqual(level["fill_colors"], ["#173322FF"])

    def test_pkce_challenge_matches_verifier(self):
        import base64
        import hashlib

        verifier, challenge = pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(challenge, expected)
        self.assertTrue(43 <= len(verifier) <= 128)


class SpotifyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = Config(client_id="test-app", state_dir=Path(self.directory.name))
        TokenStore(self.config.state_dir).write({"client_id": "test-app", "access_token": "test-access",
            "refresh_token": "test-refresh", "expires_at": time.time() + 3600})

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_refresh_retains_existing_refresh_token(self):
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.path == "/api/token":
                return httpx.Response(200, json={"access_token": "new-test-access", "expires_in": 3600})
            return httpx.Response(204)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            spotify = Spotify(self.config, client)
            spotify.tokens["expires_at"] = 0
            self.assertIsNone(await spotify.playback())
            self.assertEqual(spotify.tokens["refresh_token"], "test-refresh")
            self.assertEqual(requests[-1].headers["Authorization"], "Bearer new-test-access")
            saved = TokenStore(self.config.state_dir).read()
            self.assertEqual(saved["refresh_token"], "test-refresh")
            self.assertEqual(TokenStore(self.config.state_dir).path.stat().st_mode & 0o777, 0o600)

    async def test_rate_limit_prevents_further_requests_during_cooldown(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(429, headers={"Retry-After": "20"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            spotify = Spotify(self.config, client)
            for _ in range(2):
                with self.assertRaises(RateLimited):
                    await spotify.playback()
            self.assertEqual(len(calls), 1)

    async def test_toggle_targets_the_selected_device(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(204)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            spotify = Spotify(self.config, client)
            await spotify.toggle({"is_playing": True, "device": {"id": "speaker"}})
            self.assertEqual(requests[0].url.path, "/v1/me/player/pause")
            self.assertEqual(requests[0].url.params["device_id"], "speaker")

    async def test_accepted_command_does_not_require_json_body(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(202, text="202 Accepted"))) as client:
            spotify = Spotify(self.config, client)
            self.assertIsNone(await spotify.toggle({"is_playing": True, "device": {"id": "speaker"}}))

    async def test_incomplete_playback_response_is_retryable(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=""))) as client:
            spotify = Spotify(self.config, client)
            with self.assertRaises(SpotifyError):
                await spotify.playback()


class InputTests(unittest.IsolatedAsyncioTestCase):
    def player(self):
        player = Player.__new__(Player)
        player.config = Config()
        player.spotify = AsyncMock()
        player.demo = False
        player.playback = {"is_playing": True, "progress_ms": 1000, "item": {"duration_ms": 50000},
            "device": {"id": "speaker", "supports_volume": True}}
        player.fetched_at = time.monotonic()
        player.last_button = 0
        player.volume = 98
        player.volume_until = 0
        player.pending_volume = None
        player.error = ""
        player.menu_mode = False
        player.active_until = 0
        return player

    async def test_release_does_not_toggle_twice(self):
        player = self.player()
        await player.handle(ButtonEvent("start", "press"))
        await player.handle(ButtonEvent("start", "release"))
        player.spotify.toggle.assert_awaited_once()
        self.assertFalse(player.playback["is_playing"])

    async def test_wheel_clamps_and_coalesces_volume(self):
        player = self.player()
        await player.handle(EncoderEvent(10))
        self.assertEqual(player.pending_volume, ("speaker", 100))
        await player.handle(EncoderEvent(-60))
        self.assertEqual(player.pending_volume, ("speaker", 0))
        player.spotify.volume.assert_not_awaited()

    async def test_pinned_player_does_not_control_another_device(self):
        player = self.player()
        player.config.device_name = "Living room"
        player.playback["device"]["name"] = "Phone"
        await player.handle(ButtonEvent("start", "press"))
        player.spotify.toggle.assert_not_awaited()

    async def test_unsupported_volume_never_sends_command(self):
        player = self.player()
        player.playback["device"]["supports_volume"] = False
        await player.handle(EncoderEvent(1))
        self.assertIsNone(player.pending_volume)
        self.assertEqual(player.error, "NO VOLUME API")

    async def test_apps_menu_navigation_does_not_control_music(self):
        player = self.player()
        player.menu_mode = True
        await player.handle(ButtonEvent("start", "press"))
        await player.handle(EncoderEvent(2))
        player.spotify.toggle.assert_not_awaited()
        self.assertIsNone(player.pending_volume)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_authenticated_native_launcher_activates_display(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(state_dir=Path(directory))
            launcher_store(config).write({"host": "127.0.0.1", "port": 0, "token": "test-launcher"})
            player = SimpleNamespace(active_until=0)
            bridge = Bridge(config, player)
            bridge.start()
            try:
                address = "http://127.0.0.1:" + str(bridge.server.server_port) + "/activate"
                async with httpx.AsyncClient(trust_env=False) as client:
                    rejected = await client.post(address, content="{}")
                    self.assertEqual(rejected.status_code, 403)
                    self.assertEqual(player.active_until, 0)
                    accepted = await client.post(address, content="{}", headers={"Authorization": "Bearer test-launcher"})
                    self.assertEqual(accepted.status_code, 200)
                    self.assertGreater(player.active_until, time.monotonic())
            finally:
                await asyncio.to_thread(bridge.close)


if __name__ == "__main__":
    unittest.main()
