import io
import unicodedata

import httpx
from busylib import AsyncBusyBar
from busylib.features.input_events import input_events
from PIL import Image, ImageOps


APP_NAME = "app.spotify"
ACCENT = "#1ED760FF"


def safe_text(value):
    clean = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join("".join(char for char in clean if 32 <= ord(char) <= 126).split()) or "Unknown"


def rectangle(identifier, x, y, width, height, color, z=0):
    return {"id": identifier, "type": "rectangle", "display": "front", "x": x, "y": y,
        "width": max(1, width), "height": height, "fill": "solid", "fill_colors": [color],
        "border_width": 0, "z_index": z, "timeout": 15}


def text(identifier, value, x, y, width, color=ACCENT):
    return {"id": identifier, "type": "text", "display": "front", "text": safe_text(value),
        "font": "tiny", "color": color, "x": x, "y": y, "width": width, "align": "top_left",
        "scroll_rate": 600, "scroll_start_delay": 1800, "scroll_repeat_delay": 1800,
        "z_index": 2, "timeout": 15}


def track_elements(title, artist, playing=True):
    return [rectangle("background", 0, 0, 72, 16, "#000000FF"),
        {"id": "cover", "type": "image", "display": "front", "path": "cover.png", "x": 0, "y": 0,
            "opacity": 100 if playing else 55, "z_index": 1, "timeout": 15},
        text("title", title, 18, 0, 54, "#FFFFFFFF"),
        text("artist", artist, 18, 7, 54, ACCENT if playing else "#E8B85CFF")]


def progress_elements(progress, duration, playing=True):
    width = int(54 * max(0, min(1, progress / duration))) if duration else 0
    return [rectangle("progress-track", 18, 15, 54, 1, "#173322FF", 3),
        rectangle("progress", 18, 15, max(1, width), 1,
            (ACCENT if playing else "#E8B85CFF") if width else "#173322FF", 4)]


def volume_elements(value):
    return [rectangle("background", 0, 0, 72, 16, "#000000FF"),
        text("volume-text", f"VOL {value}%", 18, 2, 54),
        rectangle("volume-track", 18, 11, 54, 3, "#173322FF", 1),
        rectangle("volume-level", 18, 11, max(1, round(54 * value / 100)), 3,
            ACCENT if value else "#173322FF", 2)]


def thumbnail(data):
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.fit(image.convert("RGB"), (16, 16), method=Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def placeholder():
    image = Image.new("RGB", (16, 16), "#12301d")
    for x in range(3, 13):
        for y in range(4, 12):
            if y in {4, 7, 10} and x < 12 - (y - 4) // 3:
                image.putpixel((x, y), (30, 215, 96))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class BarError(Exception):
    pass


class Bar:
    def __init__(self, config):
        self.config = config
        self.base_url = f"http://{config.host}/api"
        headers = {"X-API-Token": config.bar_token} if config.bar_token else {}
        self.client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=8, trust_env=False)
        self.stream = AsyncBusyBar(config.host, token=config.bar_token or None)

    async def close(self):
        await self.client.aclose()
        await self.stream.aclose()

    async def request(self, method, path, **kwargs):
        response = await self.client.request(method, self.base_url + path, **kwargs)
        if response.status_code == 409:
            raise BarError("Bar is busy; stop its timer or close its menu to show Spotify")
        if response.is_error:
            raise BarError(f"Bar request failed (HTTP {response.status_code})")
        return response

    async def upload(self, data):
        await self.request("POST", "/assets/upload", params={"application_name": APP_NAME, "file": "cover.png"},
            content=data, headers={"Content-Type": "application/octet-stream"})

    async def draw(self, elements):
        await self.request("POST", "/display/draw", json={"application_name": APP_NAME, "priority": 50, "elements": elements})

    async def clear(self):
        await self.request("DELETE", "/display/draw", params={"application_name": APP_NAME})

    async def events(self):
        async for message in self.stream.stream_status_ws():
            if isinstance(message, dict):
                for event in input_events(message):
                    yield event
