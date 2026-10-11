import argparse
import asyncio
import sys

import httpx

from .app import Player
from .bar import BarError
from .config import Config
from .spotify import SpotifyError, authenticate
from .launcher import install


def main():
    parser = argparse.ArgumentParser(description="Spotify display and controls for BUSY Bar")
    parser.add_argument("command", nargs="?", choices=["run", "auth", "demo", "install"], default="run")
    args = parser.parse_args()
    try:
        config = Config.load()
        if args.command == "auth":
            authenticate(config)
        elif args.command == "install":
            asyncio.run(install(config))
        else:
            asyncio.run(Player(config, demo=args.command == "demo").run())
    except KeyboardInterrupt:
        pass
    except (SpotifyError, BarError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPError:
        print("Connection failed. Check the Bar connection and internet access.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
