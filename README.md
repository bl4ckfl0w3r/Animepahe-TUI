# Animepahe-TUI

A lightweight, elegant terminal user interface (TUI) for searching, downloading, transcribing, and playing anime from AnimePahe.

It is built in Python using the standard `curses` library (no external UI dependencies needed) and utilizes `yt-dlp` for multi-threaded downloads, `Node.js` for stream decryption, and `stable-ts` (Whisper) for high-quality English subtitle generation.

---

## Features

- **Interactive Search & Browse**: Live database search directly from the terminal.
- **High-Speed HLS Downloads**: Downloads encrypted AES-128 streams using `yt-dlp` and `pycryptodomex` with **parallel threads**, bypassing sequential download throttling.
- **Flexible Batch Selection**: Options to select and download episodes individually, in custom batches, or an entire season at once.
- **Automatic Whisper Transcription**: Integrates `stable-ts` (OpenAI Whisper wrapper) to generate clean English subtitles (`.srt` format).
  - *Model Selection*: Cycle through different Whisper models (such as `large-v3-turbo` (default), `large-v3`, `medium`, `small`, `base`, `tiny`) dynamically to trade speed for accuracy.
  - *Smart Chaining*: Queuing transcription on a non-downloaded episode automatically downloads the video first and fires transcription as soon as it's completed.
- **Integrated Background Media Player**: Starts playback of selected episodes (as a single file or sequential playlist) in `celluloid` asynchronously.
- **Live Logs Viewer**: Built-in console logs panel to monitor background download (`yt-dlp`) and transcription (`stable-ts`) stdout/stderr in real-time.
- **No Complex UI Packages**: Runs natively in any standard terminal using standard `curses` color schemes.

---

## Prerequisites

Ensure the following dependencies are installed globally on your system:
- **Python 3.8+**
- **Node.js** (required for evaluating the kwik.cx packer decryption script)
- **FFmpeg** (required for video merging/segment operations)
- **Celluloid** (default media player, optional)

---

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/Animepahe-TUI.git
   cd Animepahe-TUI
   ```

2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

4. Make the script executable:
   ```bash
   chmod +x animepahe_tui.py
   ```

---

## Setup & Configuration

Due to Cloudflare protection on AnimePahe, you must provide your browser's clearance cookie and User-Agent:

1. Visit animepahe website in your browser and verify you are not a bot.
2. Open Developer Tools (`F12`) -> Go to the **Network** tab.
3. Refresh the page and inspect any request header to copy:
   - Your browser's **User-Agent** string.
   - The value of the **`cf_clearance`** cookie.
4. Fill these in. You can either:
   - Run `./animepahe_tui.py` directly, which will launch the interactive setup panel.
   - Copy `config.json.example` to `config.json` and paste your tokens there:
     ```bash
      cp config.json.example config.json
     ```

---

## Keyboard Controls

### Setup Screen
- `Tab` / `Up/Down Arrow`: Toggle focus between input fields.
- `Enter`: Edit field text (supports horizontal scrolling for long values).
- `S`: Save settings and launch app.
- `Q`: Quit.

### Search Screen
- `/` or `Enter`: Type search queries.
- `Up/Down Arrow`: Move cursor.
- `Enter` (on a result): Load episode list.
- `O`: Open Settings screen.
- `V`: Open background log viewer.
- `Q`: Quit.

### Episode List Screen
- `Up/Down Arrow`: Move cursor.
- `Space`: Select/unselect episode.
- `A`: Select all episodes.
- `C`: Clear selection.
- `L`: Toggle preferred audio language (`JPN` / `ENG`).
- `R`: Toggle preferred resolution (`1080p` / `720p` / `480p` / `360p`).
- `M`: Cycle preferred Whisper model (`large-v3-turbo` / `large-v3` / `medium` / `small` / `base` / `tiny`).
- `D`: Queue selected episodes for downloading.
- `T`: Queue selected episodes for Whisper subtitle transcription (downloads first if missing).
- `P`: Play selected downloaded video(s) in Celluloid.
- `S`: Go back to Search screen.
- `V`: Switch to background log viewer.
- `Q`: Quit.

---

## Disclaimer

This tool is designed for downloading and transcribing anime episodes for offline, personal viewing. Please use this tool responsibly and respect the terms of service of the content host.
