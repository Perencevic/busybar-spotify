import hmac
import io
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from PIL import Image

from .bar import APP_NAME, Bar, BarError, placeholder
from .spotify import TokenStore


def launcher_store(config):
    store = TokenStore(config.state_dir)
    store.path = config.state_dir / "launcher.json"
    return store


class Bridge:
    def __init__(self, config, player):
        self.settings = launcher_store(config).read()
        token = self.settings["token"]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                expected = "Bearer " + token
                if self.path != "/activate" or not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                    self.send_error(403)
                    return
                self.connection.settimeout(2)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 256:
                    self.send_error(413)
                    return
                self.rfile.read(length)
                player.active_until = time.monotonic() + 3
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer((self.settings["host"], self.settings["port"]), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


async def install(config):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect((config.host, 80))
        local_address = connection.getsockname()[0]
    store = launcher_store(config)
    settings = store.read() if store.path.exists() else {"token": secrets.token_urlsafe(32), "port": 8890}
    settings["host"] = local_address
    native = files("busybar_spotify").joinpath("native")
    script = native.joinpath("main.js").read_text()
    script = script.replace("__BRIDGE_URL__", json.dumps(f"http://{local_address}:{settings['port']}/activate"))
    script = script.replace("__BRIDGE_TOKEN__", json.dumps(settings["token"]))
    bar = Bar(config)
    root = "/ext/user_assets/" + APP_NAME

    async def mkdir(path):
        response = await bar.client.post(bar.base_url + "/storage/mkdir", params={"path": path})
        if response.is_error:
            check = await bar.client.get(bar.base_url + "/storage/list", params={"path": path})
            if check.is_error:
                raise BarError("Could not create the native app directory")

    async def write(path, content):
        await bar.request("POST", "/storage/write", params={"path": path}, content=content,
            headers={"Content-Type": "application/octet-stream"})

    try:
        for path in [root, root + "/appmeta", root + "/scripts"]:
            await mkdir(path)
        await write(root + "/appmeta/manifest.json", native.joinpath("manifest.json").read_bytes())
        await write(root + "/scripts/main.js", script.encode())
        for size, name in [(8, "icon_front_8x8.png"), (11, "icon_back_11x11.png")]:
            image = Image.open(io.BytesIO(placeholder())).resize((size, size), Image.Resampling.NEAREST)
            output = io.BytesIO()
            image.save(output, format="PNG")
            await write(root + "/appmeta/" + name, output.getvalue())
        await write("/ext/apps_data/apps_menu/js_apps_enabled", b"1")
        store.write(settings)
    finally:
        await bar.close()
    print("Spotify installed in Apps. Move the selector away from Apps, then back to refresh the menu.", flush=True)
