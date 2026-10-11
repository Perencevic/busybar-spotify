import asyncio
import time

import httpx
from busylib.features.input_events import ButtonEvent, EncoderEvent, SelectorEvent

from .bar import Bar, BarError, placeholder, progress_elements, text, thumbnail, track_elements, volume_elements
from .spotify import RateLimited, Spotify, SpotifyError
from .launcher import Bridge, launcher_store


class Player:
    def __init__(self, config, demo=False):
        self.config = config
        self.demo = demo
        self.bar = Bar(config)
        self.spotify = None if demo else Spotify(config)
        self.playback = None
        self.fetched_at = time.monotonic()
        self.cover_url = None
        self.cover_ready = False
        self.cover_retry_at = 0
        self.volume = 45
        self.volume_until = 0
        self.pending_volume = None
        self.volume_changed = 0
        self.controls = asyncio.Queue()
        self.last_button = 0
        self.error = ""
        self.draw_signature = None
        self.draw_mode = None
        self.last_full_draw = 0
        self.last_progress = 0
        self.active_until = 0
        self.menu_mode = not demo and launcher_store(config).path.exists()
        self.bridge = None

    def active(self):
        return not self.menu_mode or time.monotonic() < self.active_until

    def selected_device(self):
        device = (self.playback or {}).get("device") or {}
        if self.config.device_name and device.get("name", "").casefold() != self.config.device_name.casefold():
            return {}
        return device

    async def poll(self):
        if self.demo:
            self.playback = {"is_playing": True, "progress_ms": 80000,
                "device": {"id": "demo", "name": "Demo", "volume_percent": 45, "supports_volume": True},
                "item": {"id": "demo", "name": "Nightcall", "duration_ms": 258000,
                    "artists": [{"name": "Kavinsky"}], "album": {"images": []}}}
            return
        failures = 0
        while True:
            if not self.active():
                await asyncio.sleep(0.5)
                continue
            delay = self.config.poll_seconds
            try:
                playback = await self.spotify.playback()
                self.playback = playback
                self.fetched_at = time.monotonic()
                device = self.selected_device()
                value = device.get("volume_percent")
                if value is not None and self.pending_volume is None and time.monotonic() >= self.volume_until:
                    self.volume = value
                self.error = "" if device or not playback else "SELECT PLAYER"
                failures = 0
            except RateLimited as exc:
                delay = max(delay, exc.seconds)
                self.error = "SPOTIFY LIMIT"
                print(f"Spotify requests paused for {delay:.0f} seconds.", flush=True)
            except (SpotifyError, httpx.HTTPError) as exc:
                failures += 1
                delay = min(60, self.config.poll_seconds * 2 ** min(failures, 4))
                self.error = "SPOTIFY OFFLINE"
                print(str(exc) if isinstance(exc, SpotifyError) else "Spotify connection unavailable; retrying.", flush=True)
            await asyncio.sleep(delay)

    async def read_inputs(self):
        while True:
            try:
                async for event in self.bar.events():
                    await self.controls.put(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                print("Bar input connection lost; reconnecting.", flush=True)
                await asyncio.sleep(3)

    async def handle(self, event):
        if isinstance(event, SelectorEvent):
            self.active_until = 0
            self.pending_volume = None
            return
        if not self.active():
            return
        device = self.selected_device()
        if not device.get("id") or device.get("is_restricted"):
            self.error = "SELECT PLAYER"
            return
        now = time.monotonic()
        if isinstance(event, ButtonEvent) and event.button == self.config.play_button and event.is_press:
            if now - self.last_button < 0.5:
                return
            self.last_button = now
            if not self.demo:
                await self.spotify.toggle(self.playback)
            progress = self.estimated_progress()
            self.playback["progress_ms"] = progress
            self.fetched_at = now
            self.playback["is_playing"] = not self.playback.get("is_playing")
            self.error = ""
        elif isinstance(event, EncoderEvent) and event.delta:
            if not device.get("supports_volume", False):
                self.error = "NO VOLUME API"
                return
            self.volume = max(0, min(100, self.volume + event.delta * self.config.volume_step))
            self.volume_until = now + 2
            self.volume_changed = now
            self.pending_volume = (device["id"], self.volume)
            self.error = ""

    async def control(self):
        while True:
            try:
                event = await asyncio.wait_for(self.controls.get(), timeout=0.1)
                await self.handle(event)
            except TimeoutError:
                pass
            except (SpotifyError, httpx.HTTPError) as exc:
                self.error = "CONTROL FAILED"
                print(str(exc) if isinstance(exc, SpotifyError) else "Spotify control connection failed.", flush=True)
            if self.pending_volume and time.monotonic() - self.volume_changed >= 0.2:
                device_id, value = self.pending_volume
                self.pending_volume = None
                if not self.active():
                    continue
                try:
                    if not self.demo:
                        if self.selected_device().get("id") != device_id:
                            continue
                        await self.spotify.volume(device_id, value)
                    self.playback["device"]["volume_percent"] = value
                except (SpotifyError, httpx.HTTPError) as exc:
                    self.error = "VOLUME FAILED"
                    print(str(exc) if isinstance(exc, SpotifyError) else "Volume connection failed.", flush=True)

    def estimated_progress(self):
        playback = self.playback or {}
        duration = (playback.get("item") or {}).get("duration_ms", 0)
        progress = playback.get("progress_ms") or 0
        if playback.get("is_playing"):
            progress += int((time.monotonic() - self.fetched_at) * 1000)
        return min(duration, progress)

    async def ensure_cover(self, item):
        album = item.get("album") or {}
        images = album.get("images") or item.get("images") or []
        url = images[-1]["url"] if images else ""
        if self.cover_ready and url == self.cover_url:
            return
        if self.cover_ready and time.monotonic() < self.cover_retry_at:
            return
        data = placeholder()
        if url and not self.demo:
            try:
                response = await self.spotify.client.get(url)
                response.raise_for_status()
                data = thumbnail(response.content)
            except (httpx.HTTPError, ValueError, OSError):
                print("Album cover unavailable; using a placeholder.", flush=True)
                url = None
                self.cover_retry_at = time.monotonic() + 60
        await self.bar.upload(data)
        self.cover_url = url
        self.cover_ready = True

    async def render(self):
        while True:
            try:
                await self.render_once()
            except (BarError, httpx.HTTPError) as exc:
                self.draw_signature = None
                print(str(exc) if isinstance(exc, BarError) else "Bar display unavailable; retrying.", flush=True)
                await asyncio.sleep(3)
            await asyncio.sleep(0.1)

    async def render_once(self):
        if not self.active():
            if self.draw_mode is not None:
                await self.bar.clear()
                self.draw_mode = None
                self.draw_signature = None
            return
        now = time.monotonic()
        playback = self.playback or {}
        item = playback.get("item") or {}
        playing = playback.get("is_playing", False)
        if self.error:
            mode = "error"
            signature = (mode, self.error)
            elements = [text("status", self.error, 2, 4, 68)]
        elif now < self.volume_until:
            mode = "volume"
            signature = (mode, self.volume)
            elements = volume_elements(self.volume)
        elif item:
            mode = "track"
            title = item.get("name", "Unknown")
            artists = ", ".join(artist["name"] for artist in item.get("artists", []))
            artist = artists or (item.get("show") or {}).get("name", "Podcast")
            signature = (mode, item.get("id"), title, artist, playing)
            await self.ensure_cover(item)
            elements = track_elements(title, artist, playing)
            elements += progress_elements(self.estimated_progress(), item.get("duration_ms", 0), playing)
        else:
            mode = "idle"
            signature = (mode,)
            elements = [text("status", "OPEN SPOTIFY", 2, 4, 68)]
        if mode != self.draw_mode:
            await self.bar.clear()
            self.draw_mode = mode
        if signature != self.draw_signature or now - self.last_full_draw > 10:
            await self.bar.draw(elements)
            self.draw_signature = signature
            self.last_full_draw = now
        elif mode == "track" and now - self.last_progress >= 1:
            await self.bar.draw(progress_elements(self.estimated_progress(), item.get("duration_ms", 0), playing))
            self.last_progress = now

    async def run(self):
        tasks = []
        try:
            await self.bar.request("GET", "/status/firmware")
            if self.menu_mode:
                self.bridge = Bridge(self.config, self)
                self.bridge.start()
            print("Demo on the Bar. Button toggles pause; knob changes the demo volume." if self.demo
                else "Spotify companion running. Open Spotify from the Bar's Apps menu." if self.menu_mode
                else "Spotify display running. Button: play/pause. Knob: volume. Ctrl+C to stop.", flush=True)
            if self.demo:
                await self.poll()
            else:
                tasks.append(asyncio.create_task(self.poll()))
            tasks += [asyncio.create_task(self.read_inputs()), asyncio.create_task(self.control()), asyncio.create_task(self.render())]
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.bar.clear()
            except Exception:
                pass
            await self.bar.close()
            if self.spotify:
                await self.spotify.close()
            if self.bridge:
                await asyncio.to_thread(self.bridge.close)
