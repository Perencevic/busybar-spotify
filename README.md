# BUSY Bar Spotify

Album art, now playing, and physical Spotify controls on a BUSY Bar.

The front display shows a 16 × 16 album thumbnail, two scrolling lines for the song and artist, and a progress bar. Press the big START button to pause or resume. Turn the wheel to adjust volume in 2% steps; the volume bar appears briefly before returning to the track. Paused tracks keep their artwork with an amber artist label and progress bar.

The app runs on a computer that stays on while you listen. It controls your active Spotify player; audio continues playing on your phone, computer, or Spotify Connect speaker. Spotify Premium is required.

## Setup

Use Python 3.11 or later.

```sh
git clone https://github.com/Perencevic/busybar-spotify.git
cd busybar-spotify
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Create an app in the [Spotify developer dashboard](https://developer.spotify.com/dashboard). Select Web API and register this redirect URI exactly:

```text
http://127.0.0.1:8888/callback
```

Put your Client ID into `SPOTIFY_CLIENT_ID` in `.env`. A Client Secret is not required: sign-in uses Authorization Code with PKCE. Each person using this public repository should create their own Spotify developer app. Spotify development-mode account access and request quotas apply.

Connect the Bar by USB and confirm that `http://10.0.4.20` opens. The USB connection works as a network interface and does not require a BUSY token.

```sh
busybar-spotify auth
busybar-spotify run
```

## Open from the Bar's Apps menu

Firmware 1.2.4 includes a native JavaScript launcher. To add a Spotify entry:

```sh
busybar-spotify install
busybar-spotify run
```

The install command adds a small launcher, icons, and a manifest to `/ext/user_assets/app.spotify/` and enables the firmware's existing JavaScript app menu. Move the mode selector away from Apps and back to refresh the list. Press the select button to open the list, turn the wheel to highlight Spotify, then press select to launch it. If another app is open, use Back to return to the list.

The Mac companion stays running. The launcher activates the display over the local connection; it does not contain Spotify credentials. If the companion is unavailable, the Bar shows `START ON MAC`. Leaving Apps stops display updates and physical playback controls. While Spotify is open, the big START button toggles play/pause and the wheel adjusts volume when the player supports it.

The native launcher uses a random pairing token saved only in ignored `.state/launcher.json` and the installed script on your Bar. It binds the companion to the computer's interface used to reach the Bar on port 8890. If that interface's IP address changes, rerun `install` and restart the companion. Local Wi-Fi use requires the Bar and computer to reach each other; a firewall must permit the companion on that interface. Native JavaScript app support is still under development upstream; the launcher was tested on firmware 1.2.4.

The sign-in command opens your browser and waits up to five minutes. Sign in on the same computer running the command. Tokens are saved to `.state/spotify.json` with owner-only permissions and automatically refreshed.

Select your speaker in Spotify and play a track. For a WiiM Amp, selecting the Amp directly through Spotify Connect gives the app a player to control. AirPlay playback may not expose volume control through Spotify's API.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPOTIFY_CLIENT_ID` | Required | Your Spotify app Client ID |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8888/callback` | Must match your Spotify app settings |
| `BUSYBAR_HOST` | `10.0.4.20` | USB address or local Wi-Fi IP |
| `BUSYBAR_TOKEN` | Empty | Local API password for Wi-Fi |
| `SPOTIFY_DEVICE_NAME` | Empty | Optionally require a specific active player name |
| `SPOTIFY_POLL_SECONDS` | `5` | Playback polling interval; minimum 3 seconds |
| `VOLUME_STEP` | `2` | Percentage points per wheel step, from 1 to 10 |
| `PLAY_BUTTON` | `start` | `start`, `ok`, or `back` |

Wi-Fi requires enabling the Bar's local HTTP API and setting its password. Physical input streaming requires a local connection; BUSY cloud tokens alone cannot provide button events. The app retries interrupted connections and respects Spotify's rate-limit cooldown. Track changes are picked up on the next poll; the progress bar advances locally between polls.

By default, controls follow the current Spotify player. Set `SPOTIFY_DEVICE_NAME` to pin controls to a speaker's exact name. The app does not transfer playback automatically. If the active player cannot accept volume commands, the Bar shows `NO VOLUME API`.

Stop any native Bar timer before running. The app uses display priority 50 and does not stop or overwrite an active BUSY session. Closing the companion clears its display elements; if it disconnects unexpectedly, they expire within 15 seconds.

## Try the display without Spotify

```sh
busybar-spotify demo
```

Demo uses a sample track and placeholder artwork. The button and knob affect the demo only.

## Tests

```sh
python -m unittest discover -s tests -v
```

## Local credentials

`.env`, `.state/`, and `.venv/` are ignored by Git. Keep Client IDs, passwords, and OAuth tokens in local configuration. `.env.example` contains placeholders only. Avoid including local configuration, token files, or authorization URLs in issues and screenshots.
