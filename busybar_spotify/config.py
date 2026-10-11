import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    client_id: str = ""
    redirect_uri: str = "http://127.0.0.1:8888/callback"
    host: str = "10.0.4.20"
    bar_token: str = ""
    device_name: str = ""
    poll_seconds: float = 5
    volume_step: int = 2
    play_button: str = "start"
    state_dir: Path = Path(".state")

    @classmethod
    def load(cls):
        load_dotenv()
        config = cls(
            client_id=os.getenv("SPOTIFY_CLIENT_ID", ""),
            redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", cls.redirect_uri),
            host=os.getenv("BUSYBAR_HOST", cls.host),
            bar_token=os.getenv("BUSYBAR_TOKEN", ""),
            device_name=os.getenv("SPOTIFY_DEVICE_NAME", ""),
            poll_seconds=max(3, float(os.getenv("SPOTIFY_POLL_SECONDS", "5"))),
            volume_step=int(os.getenv("VOLUME_STEP", "2")),
            play_button=os.getenv("PLAY_BUTTON", "start"),
            state_dir=Path(os.getenv("STATE_DIR", ".state")),
        )
        if config.play_button not in {"start", "ok", "back"}:
            raise ValueError("PLAY_BUTTON must be start, ok, or back")
        if not 1 <= config.volume_step <= 10:
            raise ValueError("VOLUME_STEP must be between 1 and 10")
        return config
