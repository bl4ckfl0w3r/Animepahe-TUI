#!/usr/bin/env python3
import os
import sys

# Ensure discrete NVIDIA GPU is visible on hybrid graphics laptops
if "__NV_PRIME_RENDER_OFFLOAD" not in os.environ:
    os.environ["__NV_PRIME_RENDER_OFFLOAD"] = "1"
if "__GLX_VENDOR_LIBRARY_NAME" not in os.environ:
    os.environ["__GLX_VENDOR_LIBRARY_NAME"] = "nvidia"

import re
import json
import subprocess
import threading
import queue
import time
import curses
import requests
import glob

# Configuration
CONFIG_FILE = "config.json"
DEFAULT_RESOLUTION = "1080"
DEFAULT_AUDIO = "jpn"
DEFAULT_MODEL = "large-v3-turbo"
RESOLUTIONS = ["1080", "720", "480", "360"]
AUDIOS = ["jpn", "eng"]
MODELS = ["large-v3-turbo", "large-v3", "medium", "small", "base", "tiny"]

def load_config():
    """Load configuration from config.json, falling back to animepahe-dl config if needed."""
    cf, ua, audio, resolution, model = "", "", DEFAULT_AUDIO, DEFAULT_RESOLUTION, DEFAULT_MODEL
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                cf = data.get("cf", "")
                ua = data.get("ua", "")
                audio = data.get("audio", DEFAULT_AUDIO)
                resolution = data.get("resolution", DEFAULT_RESOLUTION)
                model = data.get("model", DEFAULT_MODEL)
                return cf, ua, audio, resolution, model
        except Exception:
            pass

    # Fallback to animepahe-dl/config.json if it exists
    fallback_path = os.path.join("animepahe-dl", "config.json")
    if os.path.exists(fallback_path):
        try:
            with open(fallback_path, "r") as f:
                data = json.load(f)
                cf = data.get("cf", "")
                ua = data.get("ua", "")
        except Exception:
            pass
            
    return cf, ua, audio, resolution, model

def save_config(cf, ua, audio, resolution, model):
    """Save configuration to config.json."""
    data = {
        "cf": cf,
        "ua": ua,
        "audio": audio,
        "resolution": resolution,
        "model": model
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

def clean_filename(title):
    """Clean anime title to make it safe for filenames."""
    cleaned = re.sub(r'[^a-zA-Z0-9\s\(\)\[\]\-\_\.]', '_', title)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    cleaned = re.sub(r'_+', '_', cleaned)
    return cleaned.strip()

def check_local_episode_files(download_dir, anime_title, episode_num, resolution, audio):
    """Check if the episode has been downloaded locally and if it has subtitles."""
    cleaned_title = clean_filename(anime_title)
    
    # Exact match filename pattern
    target_video_filename = f"{cleaned_title}_-_Ep_{episode_num:02d}_{resolution}p_{audio}.mp4"
    target_video_path = os.path.join(download_dir, target_video_filename)
    
    target_srt_filename = f"{cleaned_title}_-_Ep_{episode_num:02d}_{resolution}p_{audio}.srt"
    target_srt_path = os.path.join(download_dir, target_srt_filename)
    
    # Look for any resolution/audio of this episode
    any_video_pattern = os.path.join(download_dir, f"{cleaned_title}_-_Ep_{episode_num:02d}_*.mp4")
    any_video_files = glob.glob(any_video_pattern)
    
    # Check if target file exists
    has_target_video = os.path.exists(target_video_path)
    has_target_srt = os.path.exists(target_srt_path)
    
    # If no zero-padded, check that too
    if not has_target_video:
        target_video_filename_alt = f"{cleaned_title}_-_Ep_{episode_num}_{resolution}p_{audio}.mp4"
        target_video_path_alt = os.path.join(download_dir, target_video_filename_alt)
        if os.path.exists(target_video_path_alt):
            target_video_path = target_video_path_alt
            has_target_video = True
            
            target_srt_filename_alt = f"{cleaned_title}_-_Ep_{episode_num}_{resolution}p_{audio}.srt"
            target_srt_path = os.path.join(download_dir, target_srt_filename_alt)
            if os.path.exists(target_srt_path):
                target_srt_path = target_srt_path_alt
                has_target_srt = True

    return {
        'target_video': target_video_path if has_target_video else None,
        'target_srt': target_srt_path if has_target_srt else None,
        'has_any_video': len(any_video_files) > 0,
        'any_video_files': any_video_files
    }

def select_best_button(buttons, preferred_audio, preferred_resolution):
    """Select the best link candidate from available play buttons."""
    # 1. Filter by data-av1="0" (non-AV1 as standard in animepahe-dl)
    candidates = [b for b in buttons if b.get('data-av1') == '0']
    if not candidates:
        candidates = buttons  # Fallback to all if none are av1="0"

    # 2. Filter by audio language
    audio_candidates = [b for b in candidates if b.get('data-audio') == preferred_audio]
    if not audio_candidates:
        # Fallback to other available audio language
        fallback_audio = "jpn" if preferred_audio == "eng" else "eng"
        audio_candidates = [b for b in candidates if b.get('data-audio') == fallback_audio]
        if not audio_candidates:
            audio_candidates = candidates

    # 3. Filter by resolution
    res_candidates = [b for b in audio_candidates if b.get('data-resolution') == preferred_resolution]
    if not res_candidates:
        # Sort audio candidates by resolution descending and pick the highest
        def get_res(b):
            try:
                return int(b.get('data-resolution', 0))
            except ValueError:
                return 0
        audio_candidates.sort(key=get_res, reverse=True)
        return audio_candidates[0] if audio_candidates else None

    return res_candidates[0]


class AnimePaheClient:
    """API and scraping client for AnimePahe."""
    def __init__(self, cf_clearance, user_agent):
        self.cf_clearance = cf_clearance
        self.user_agent = user_agent

    def _get(self, url, headers=None, timeout=10):
        """Perform a GET request using curl to bypass Cloudflare's JA3 fingerprint block."""
        cmd = [
            "curl", "-s", "-L",
            "-A", self.user_agent,
            "-b", f"cf_clearance={self.cf_clearance}",
            "--connect-timeout", str(timeout),
            "--max-time", str(timeout * 2),
            "-w", "\n---HTTP_CODE---:%{http_code}",
            "--compressed"
        ]
        
        referer = 'https://animepahe.pw/'
        if headers and 'Referer' in headers:
            referer = headers['Referer']
        cmd += ["-H", f"Referer: {referer}"]
        
        if headers:
            for k, v in headers.items():
                if k.lower() not in ('user-agent', 'referer'):
                    cmd += ["-H", f"{k}: {v}"]
                    
        cmd.append(url)
        
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise requests.exceptions.RequestException(f"curl failed with exit code {res.returncode}. Stderr: {res.stderr.strip()}")
            
        out = res.stdout
        marker = "\n---HTTP_CODE---:"
        idx = out.rfind(marker)
        if idx != -1:
            body = out[:idx]
            try:
                status_code = int(out[idx + len(marker):].strip())
            except ValueError:
                status_code = 200
        else:
            body = out
            status_code = 200
            
        class MockResponse:
            def __init__(self, text, status_code):
                self.text = text
                self.status_code = status_code
                
            def json(self):
                return json.loads(self.text)
                
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.exceptions.HTTPError(f"HTTP Error {self.status_code}", response=self)
                    
        return MockResponse(body, status_code)

    def search_anime(self, query):
        """Search anime by name using the API."""
        url = f"https://animepahe.pw/api?m=search&q={requests.utils.quote(query)}"
        r = self._get(url, timeout=10)
        r.raise_for_status()
        return r.json()

    def get_all_episodes(self, anime_slug):
        """Fetch all pages of episodes for a given anime slug."""
        episodes = []
        page = 1
        while True:
            url = f"https://animepahe.pw/api?m=release&id={anime_slug}&sort=episode_asc&page={page}"
            r = self._get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            episodes.extend(data.get('data', []))
            if page >= data.get('last_page', 1):
                break
            page += 1
        return episodes

    def get_play_buttons(self, anime_slug, episode_session):
        """Fetch play page and parse available download stream buttons."""
        url = f"https://animepahe.pw/play/{anime_slug}/{episode_session}"
        r = self._get(url, timeout=10)
        r.raise_for_status()
        
        html = r.text
        buttons = []
        # Find all <button ...> tags and parse attributes
        for match in re.finditer(r'<button\s+([^>]+)>', html, re.IGNORECASE):
            attrs_str = match.group(1)
            attrs = {}
            for attr_match in re.finditer(r'([\w\-]+)=["\']([^"\']*)["\']', attrs_str):
                attrs[attr_match.group(1)] = attr_match.group(2)
            if 'data-src' in attrs:
                buttons.append(attrs)
        return buttons

    def decrypt_kwik(self, kwik_url):
        """Decrypt kwik.cx page using Node.js subprocess to evaluate the packer javascript."""
        headers = {
            'Referer': 'https://animepahe.pw/',
            'User-Agent': self.user_agent
        }
        r = self._get(kwik_url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None, f"HTTP Error {r.status_code}"
            
        html = r.text
        # Find packed js script eval(function(p,a,c,k,e,d)...)
        match = re.search(r'<script[^>]*>\s*(eval\(function\(p,a,c,k,e,d\).+?)\s*</script>', html, re.DOTALL)
        if not match:
            # Fallback to matching packer syntax explicitly up to split('|'),0,{}))
            match = re.search(r'eval\(function\(p,a,c,k,e,d\).+?split\([\'\"]\|[\'\"]\)\s*,\s*0\s*,\s*\{\}\)\)', html, re.DOTALL)
            if not match:
                # Even more relaxed check
                match = re.search(r'eval\(function\(p,a,c,k,e,d\).+?\}\)\)', html, re.DOTALL)
                if not match:
                    return None, "Dean Edwards packed script not found on kwik page"
            js_code = match.group(0)
        else:
            js_code = match.group(1)
            
        # Adapt for Node.js execution
        modified_js = js_code.replace("document", "process") \
                             .replace("querySelector", "exit") \
                             .replace("eval(", "console.log(")
                             
        try:
            res = subprocess.run(["node", "-e", modified_js], capture_output=True, text=True, timeout=10)
            if res.returncode != 0:
                return None, f"Node failed: {res.stderr}"
            
            output = res.stdout
            m = re.search(r"const\s+source\s*=\s*['\"]([^'\"]+\.m3u8)['\"]", output)
            if m:
                return m.group(1), None
                
            # Fallback search for any m3u8
            m2 = re.search(r"https?://[^\s'\"]+\.m3u8", output)
            if m2:
                return m2.group(0), None
                
            return None, f"m3u8 URL not found in Node output: {output[:100]}"
        except Exception as e:
            return None, f"Node execution error: {str(e)}"


class TaskManager:
    """Manages background download and transcription worker threads and queues."""
    def __init__(self, download_dir="."):
        self.download_dir = download_dir
        self.download_queue = queue.Queue()
        self.transcribe_queue = queue.Queue()
        
        self.download_thread = None
        self.transcribe_thread = None
        
        self.active_download = None
        self.active_transcribe = None
        
        self.logs = []
        self.max_logs = 500
        self.log_lock = threading.Lock()
        
        self.running = False
        self.client = None
        
    def add_log(self, message):
        timestamp = time.strftime("[%H:%M:%S]")
        with self.log_lock:
            self.logs.append(f"{timestamp} {message}")
            if len(self.logs) > self.max_logs:
                self.logs.pop(0)
                
    def start(self, client):
        self.client = client
        if self.running:
            return
        self.running = True
        
        self.download_thread = threading.Thread(target=self._download_loop, daemon=True)
        self.transcribe_thread = threading.Thread(target=self._transcribe_loop, daemon=True)
        
        self.download_thread.start()
        self.transcribe_thread.start()
        
    def stop(self):
        self.running = False
        self.download_queue.put(None)
        self.transcribe_queue.put(None)
        
    def queue_download(self, anime_title, anime_slug, episode_num, episode_session, resolution, audio, transcribe_on_complete=False, model="large-v3-turbo"):
        # Check if already queued
        for task in list(self.download_queue.queue):
            if task and task['anime_slug'] == anime_slug and task['episode_num'] == episode_num:
                return False
        if self.active_download and self.active_download['anime_slug'] == anime_slug and self.active_download['episode_num'] == episode_num:
            return False
            
        task = {
            'anime_title': anime_title,
            'anime_slug': anime_slug,
            'episode_num': episode_num,
            'episode_session': episode_session,
            'resolution': resolution,
            'audio': audio,
            'transcribe_on_complete': transcribe_on_complete,
            'model': model,
            'status': 'Queued'
        }
        self.download_queue.put(task)
        self.add_log(f"Queued download: {anime_title} - Ep {episode_num}")
        return True
        
    def queue_transcribe(self, anime_title, episode_num, filepath, audio="eng", model="large-v3-turbo"):
        # Check if already queued
        for task in list(self.transcribe_queue.queue):
            if task and task['filepath'] == filepath:
                return False
        if self.active_transcribe and self.active_transcribe['filepath'] == filepath:
            return False
            
        task = {
            'anime_title': anime_title,
            'episode_num': episode_num,
            'filepath': filepath,
            'audio': audio,
            'model': model,
            'status': 'Queued',
            'current_time': ''
        }
        self.transcribe_queue.put(task)
        self.add_log(f"Queued Whisper ({model}): {os.path.basename(filepath)} ({audio.upper()})")
        return True
        
    def get_status(self, anime_title, episode_num, resolution, audio):
        """Get the real-time status of a specific episode."""
        # Check active download
        if self.active_download and self.active_download['anime_title'] == anime_title and self.active_download['episode_num'] == episode_num:
            return "Downloading"
            
        # Check active transcribe
        if self.active_transcribe and self.active_transcribe['anime_title'] == anime_title and self.active_transcribe['episode_num'] == episode_num:
            t = self.active_transcribe.get('current_time', '')
            return f"Transcribing ({t})" if t else "Transcribing"
            
        # Check download queue
        for task in list(self.download_queue.queue):
            if task and task['anime_title'] == anime_title and task['episode_num'] == episode_num:
                if task.get('transcribe_on_complete'):
                    return "Queued (DL + Sub)"
                return "Queued (Download)"
                
        # Check transcribe queue
        for task in list(self.transcribe_queue.queue):
            if task and task['anime_title'] == anime_title and task['episode_num'] == episode_num:
                return "Queued (Whisper)"
                
        # Check local files
        local = check_local_episode_files(self.download_dir, anime_title, episode_num, resolution, audio)
        if local['target_video']:
            if local['target_srt']:
                return "Completed (Subbed)"
            return "Downloaded"
        elif local['has_any_video']:
            # Found in another resolution/audio
            other_names = [os.path.basename(f) for f in local['any_video_files']]
            # Just extract resolution & audio from filenames
            res_audios = []
            for f in other_names:
                m = re.search(r"_(\d+p)_(eng|jpn)\.mp4$", f)
                if m:
                    res_audios.append(f"{m.group(1)}/{m.group(2).upper()}")
            if res_audios:
                return f"Downloaded ({', '.join(res_audios)})"
            return "Downloaded (Alt)"
            
        return "Not Downloaded"

    def _download_loop(self):
        while self.running:
            task = self.download_queue.get()
            if task is None:
                break
                
            self.active_download = task
            self.add_log(f"Starting download: {task['anime_title']} - Ep {task['episode_num']}")
            
            try:
                # 1. Fetch play buttons
                buttons = self.client.get_play_buttons(task['anime_slug'], task['episode_session'])
                # 2. Select best button based on criteria
                best_button = select_best_button(buttons, task['audio'], task['resolution'])
                if not best_button:
                    raise Exception("No suitable links found on page")
                    
                actual_res = best_button.get('data-resolution', 'unknown')
                actual_audio = best_button.get('data-audio', 'unknown')
                
                self.add_log(f"Link selected: {actual_res}p - {actual_audio.upper()}")
                
                # 3. Decrypt kwik to m3u8
                kwik_url = best_button['data-src']
                m3u8_url, err = self.client.decrypt_kwik(kwik_url)
                if err:
                    raise Exception(f"Decryption failed: {err}")
                    
                # 4. Run FFmpeg download
                cleaned_title = clean_filename(task['anime_title'])
                filename = f"{cleaned_title}_-_Ep_{task['episode_num']:02d}_{actual_res}p_{actual_audio}.mp4"
                output_path = os.path.join(self.download_dir, filename)
                
                self.add_log(f"Downloading stream via yt-dlp to {filename}...")
                
                # Setup yt-dlp process dynamically (check local .venv first)
                script_dir = os.path.dirname(os.path.abspath(__file__))
                yt_dlp_bin = os.path.join(script_dir, ".venv", "bin", "yt-dlp")
                if not os.path.exists(yt_dlp_bin):
                    python_dir = os.path.dirname(sys.executable)
                    yt_dlp_bin = os.path.join(python_dir, "yt-dlp")
                    if not os.path.exists(yt_dlp_bin):
                        yt_dlp_bin = "yt-dlp"
                cmd = [
                    yt_dlp_bin,
                    "--add-header", "Referer: https://kwik.cx/",
                    "--impersonate", "firefox",
                    "-o", output_path,
                    m3u8_url
                ]
                    
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                
                # Stream logs
                for line in process.stdout:
                    if not self.running:
                        process.kill()
                        break
                    self.add_log(f"[yt-dlp] {line.strip()}")
                    
                process.wait()
                
                if process.returncode == 0 and os.path.exists(output_path):
                    self.add_log(f"Download completed: {filename}")
                    
                    if task['transcribe_on_complete']:
                        self.queue_transcribe(task['anime_title'], task['episode_num'], output_path, task['audio'], model=task.get('model', 'large-v3-turbo'))
                else:
                    raise Exception(f"FFmpeg exited with code {process.returncode}")
                    
            except Exception as e:
                self.add_log(f"Download failed for Ep {task['episode_num']}: {str(e)}")
                
            self.active_download = None
            self.download_queue.task_done()
            
    def _transcribe_loop(self):
        while self.running:
            task = self.transcribe_queue.get()
            if task is None:
                break
                
            self.active_transcribe = task
            self.add_log(f"Starting Whisper ({task.get('model', 'large-v3-turbo')}) transcription for: {os.path.basename(task['filepath'])}")
            
            try:
                # Build stable-ts command dynamically (check local .venv first)
                script_dir = os.path.dirname(os.path.abspath(__file__))
                stable_ts_bin = os.path.join(script_dir, ".venv", "bin", "stable-ts")
                if not os.path.exists(stable_ts_bin):
                    python_dir = os.path.dirname(sys.executable)
                    stable_ts_bin = os.path.join(python_dir, "stable-ts")
                    if not os.path.exists(stable_ts_bin):
                        stable_ts_bin = "stable-ts"
                # Common transcription options to prevent loops and skipping dialogue
                extra_opts = [
                    "--condition_on_previous_text", "False",
                    "--no_speech_threshold", "0.8"
                ]
                
                audio_lang = task.get('audio', 'eng')
                if audio_lang == "jpn":
                    cmd_opts = ["--language", "ja", "--task", "translate"] + extra_opts
                else:
                    cmd_opts = ["--language", "en", "--task", "transcribe"] + extra_opts
                    
                # Check GPU capability in a clean subprocess to ensure environment variables are inherited at start
                try:
                    python_bin = os.path.join(script_dir, ".venv", "bin", "python")
                    if not os.path.exists(python_bin):
                        python_bin = sys.executable
                    
                    res = subprocess.run(
                        [python_bin, "-c", "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"],
                        capture_output=True, text=True, timeout=5
                    )
                    if res.returncode == 0:
                        lines = res.stdout.strip().split('\n')
                        has_cuda = (lines[0] == "True") if lines else False
                        gpu_name = lines[1] if len(lines) > 1 else ""
                    else:
                        has_cuda = False
                        gpu_name = ""
                        self.add_log(f"[Whisper] CUDA check process returned non-zero code {res.returncode}. Stderr: {res.stderr.strip()}")
                except Exception as e:
                    has_cuda = False
                    gpu_name = ""
                    self.add_log(f"[Whisper] Failed to run CUDA check: {str(e)}")
                
                device = "cuda" if has_cuda else "cpu"
                if device == "cpu":
                    self.add_log("[Whisper] WARNING: CUDA/GPU not detected by PyTorch! Falling back to CPU. This may use massive system RAM and fail.")
                else:
                    self.add_log(f"[Whisper] Running on GPU: {gpu_name}")

                cmd = [
                    stable_ts_bin,
                    task['filepath'],
                    "-m", task.get('model', 'large-v3-turbo'),
                    "--device", device,
                    "--word_timestamps", "True",
                    "--output_format", "srt",
                    "--word_level", "False",
                    "-y"
                ] + cmd_opts
                
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                
                for line in process.stdout:
                    if not self.running:
                        process.kill()
                        break
                        
                    self.add_log(f"[Whisper] {line.strip()}")
                    # Detect progress percentage or timestamp
                    m = re.search(r"(\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}\.\d{3})", line)
                    if m:
                        task['current_time'] = m.group(1)
                        
                process.wait()
                
                if process.returncode == 0:
                    self.add_log(f"Whisper completed: {os.path.basename(task['filepath'])}")
                else:
                    raise Exception(f"stable-ts exited with code {process.returncode}")
                    
            except Exception as e:
                self.add_log(f"Transcription failed: {str(e)}")
                
            self.active_transcribe = None
            self.transcribe_queue.task_done()


# Curses Input Helpers
def curses_input(stdscr, y, x, prompt, initial_text="", max_len=4096):
    stdscr.nodelay(False)
    curses.curs_set(1)
    text = list(initial_text)
    
    while True:
        max_y, max_x = stdscr.getmaxyx()
        # Calculate available width to avoid line wrapping and curses crashes
        available_width = max_x - x - len(prompt) - 2
        if available_width < 10:
            available_width = 10
            
        stdscr.move(y, x)
        stdscr.clrtoeol()
        
        text_str = "".join(text)
        if len(text_str) > available_width:
            # Scroll to show the end of the text where the cursor is
            visible_text = text_str[-available_width:]
        else:
            visible_text = text_str
            
        stdscr.addstr(y, x, prompt + visible_text)
        stdscr.refresh()
        
        ch = stdscr.getch()
        if ch in (10, 13, curses.KEY_ENTER):
            break
        elif ch == 27:  # ESC
            text = None
            break
        elif ch in (8, 127, curses.KEY_BACKSPACE):
            if text:
                text.pop()
        elif 32 <= ch <= 126:
            if len(text) < max_len:
                text.append(chr(ch))
                
    curses.curs_set(0)
    stdscr.nodelay(True)
    return "".join(text) if text is not None else None

def draw_header(stdscr, preferred_audio, preferred_resolution, preferred_model, active_anime=None):
    max_y, max_x = stdscr.getmaxyx()
    
    # Border-like line at top
    stdscr.attron(curses.color_pair(2))
    stdscr.addstr(0, 0, "╔" + "═"*(max_x-2) + "╗")
    
    # Title & Config
    title = " Animepahe-TUI - Downloader & Subtitle Generator "
    config_str = f" [Language: {preferred_audio.upper()}] [Resolution: {preferred_resolution}p] [Model: {preferred_model}] "
    
    stdscr.addstr(0, 2, title, curses.color_pair(2) | curses.A_BOLD)
    stdscr.addstr(0, max_x - len(config_str) - 2, config_str, curses.color_pair(4))
    
    # Line 2: Info
    stdscr.move(1, 0)
    stdscr.clrtoeol()
    stdscr.addstr(1, 0, "║", curses.color_pair(2))
    
    if active_anime:
        anime_info = f" Active Anime: {active_anime['title']} ({active_anime.get('status', 'Airing')}) "
        stdscr.addstr(1, 2, anime_info[:max_x-3], curses.color_pair(3) | curses.A_BOLD)
    else:
        stdscr.addstr(1, 2, " Enter search terms or configure settings to begin.", curses.color_pair(1))
        
    stdscr.addstr(1, max_x-1, "║", curses.color_pair(2))
    stdscr.addstr(2, 0, "╚" + "═"*(max_x-2) + "╝", curses.color_pair(2))
    stdscr.attroff(curses.color_pair(2))

def draw_footer(stdscr, legend):
    max_y, max_x = stdscr.getmaxyx()
    y = max_y - 2
    
    # Border
    stdscr.attron(curses.color_pair(2))
    stdscr.addstr(y, 0, "═"*max_x)
    stdscr.attroff(curses.color_pair(2))
    
    # Legend text
    stdscr.move(y+1, 0)
    stdscr.clrtoeol()
    stdscr.addstr(y+1, 1, legend[:max_x-2], curses.color_pair(4))


# Main Curses App Loop
def main_tui(stdscr):
    # Setup curses settings
    stdscr.nodelay(True)
    stdscr.keypad(True)
    curses.curs_set(0)
    
    # Initialize color pairs
    if curses.has_colors():
        try:
            curses.use_default_colors()
            bg = -1
        except Exception:
            bg = curses.COLOR_BLACK
            
        curses.init_pair(1, curses.COLOR_WHITE, bg)       # White/Default
        curses.init_pair(2, curses.COLOR_CYAN, bg)        # Borders
        curses.init_pair(3, curses.COLOR_GREEN, bg)       # Success/Done
        curses.init_pair(4, curses.COLOR_YELLOW, bg)      # Warning/Config
        curses.init_pair(5, curses.COLOR_RED, bg)         # Error
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)    # Highlight Cursor
        curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_GREEN)   # Active/Downloading
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # Highlight Alt Selector
        
    # State management
    STATE_SETUP = 0
    STATE_SEARCH = 1
    STATE_EPISODES = 2
    STATE_LOGS = 3
    
    current_state = STATE_SEARCH
    
    # Load config
    cf, ua, audio, resolution, model = load_config()
    
    # If config missing, force setup
    if not cf or not ua:
        current_state = STATE_SETUP
        
    # App variables
    search_query = ""
    search_results = []
    selected_search_idx = 0
    
    active_anime = None
    episodes = []
    selected_episode_idx = 0
    episode_selections = set()  # set of episode numbers
    
    task_manager = TaskManager(download_dir=".")
    client = None
    
    if cf and ua:
        client = AnimePaheClient(cf, ua)
        task_manager.start(client)
        
    # Logs scroll position
    log_scroll_offset = 0
    
    # Setup inputs indices
    setup_field_idx = 0
    cf_input = cf
    ua_input = ua
    
    last_refresh_time = 0
    
    while True:
        max_y, max_x = stdscr.getmaxyx()
        
        # Screen size check
        if max_y < 20 or max_x < 80:
            stdscr.clear()
            stdscr.addstr(0, 0, f"Terminal size ({max_x}x{max_y}) is too small. Need at least 80x20.", curses.color_pair(5))
            stdscr.refresh()
            time.sleep(0.2)
            # Drain input buffer
            stdscr.getch()
            continue
            
        # Draw base headers and footers depending on state
        stdscr.erase()
        
        # Draw setup screen
        if current_state == STATE_SETUP:
            draw_header(stdscr, audio, resolution, model)
            
            # Display instructions
            inst_y = 4
            stdscr.addstr(inst_y, 2, "┌── Cloudflare & User-Agent Setup ──────────────────────────────────────────┐", curses.color_pair(2))
            stdscr.addstr(inst_y+1, 2, "│ 1. Open your web browser.                                                 │", curses.color_pair(1))
            stdscr.addstr(inst_y+2, 2, "│ 2. Visit https://animepahe.pw and solve the Cloudflare verification.      │", curses.color_pair(1))
            stdscr.addstr(inst_y+3, 2, "│ 3. Open Developer Tools (F12) -> Network tab.                             │", curses.color_pair(1))
            stdscr.addstr(inst_y+4, 2, "│ 4. Refresh page, click any request, copy User-Agent and cf_clearance.     │", curses.color_pair(1))
            stdscr.addstr(inst_y+5, 2, "└───────────────────────────────────────────────────────────────────────────┘", curses.color_pair(2))
            
            # Draw input boxes
            box_y = inst_y + 7
            
            # CF field
            cf_label = "cf_clearance: "
            stdscr.addstr(box_y, 2, cf_label, curses.color_pair(1))
            cf_disp = cf_input if cf_input else "<Not Set>"
            cf_color = curses.color_pair(6) if setup_field_idx == 0 else curses.color_pair(4)
            stdscr.addnstr(box_y, 2 + len(cf_label), cf_disp, max_x - len(cf_label) - 5, cf_color)
            
            # UA field
            ua_label = "User-Agent:   "
            stdscr.addstr(box_y + 2, 2, ua_label, curses.color_pair(1))
            ua_disp = ua_input if ua_input else "<Not Set>"
            ua_color = curses.color_pair(6) if setup_field_idx == 1 else curses.color_pair(4)
            stdscr.addnstr(box_y + 2, 2 + len(ua_label), ua_disp, max_x - len(ua_label) - 5, ua_color)
            
            # Help instructions
            stdscr.addstr(box_y + 5, 2, "Press [Enter] to Edit Field. Press [S] to Save and start.", curses.color_pair(3) | curses.A_BOLD)
            if cf_input and ua_input:
                stdscr.addstr(box_y + 6, 2, "Config ready! Press [S] to launch.", curses.color_pair(3))
            else:
                stdscr.addstr(box_y + 6, 2, "Configuration is incomplete.", curses.color_pair(5))
                
            draw_footer(stdscr, "Tab/Arrows: Select Field | Enter: Edit | S: Save & Close | Q: Quit")
            
        # Draw Search screen
        elif current_state == STATE_SEARCH:
            draw_header(stdscr, audio, resolution, model, active_anime)
            
            # Draw search bar
            stdscr.addstr(4, 2, "Search Anime: ", curses.color_pair(2) | curses.A_BOLD)
            q_disp = search_query if search_query else "(Press / to type...)"
            q_color = curses.color_pair(4) if search_query else curses.color_pair(1)
            stdscr.addstr(4, 16, q_disp, q_color)
            
            # Draw results border
            stdscr.addstr(6, 2, "┌" + "─"*(max_x-6) + "┐", curses.color_pair(2))
            
            # Draw list
            list_height = max_y - 10
            for i in range(list_height):
                y = 7 + i
                stdscr.move(y, 2)
                stdscr.clrtoeol()
                stdscr.addstr(y, 2, "│", curses.color_pair(2))
                
                idx = i
                if idx < len(search_results):
                    res = search_results[idx]
                    text = f" {res['title']} ({res.get('type', 'TV')}) - {res.get('episodes', '?')} Ep - Score: {res.get('score', '?')}"
                    color = curses.color_pair(6) if idx == selected_search_idx else curses.color_pair(1)
                    stdscr.addnstr(y, 3, text, max_x - 7, color)
                
                stdscr.addstr(y, max_x-3, "│", curses.color_pair(2))
                
            stdscr.addstr(7 + list_height, 2, "└" + "─"*(max_x-6) + "┘", curses.color_pair(2))
            
            # Status or error
            if not search_results and search_query:
                stdscr.addstr(8, 5, "No results found. Press / to search again.", curses.color_pair(5))
            elif not search_query:
                stdscr.addstr(8, 5, "Press [/] or [Enter] to enter search terms...", curses.color_pair(4))
                
            draw_footer(stdscr, "/: Search | Enter: View Episodes | O: Settings | V: Logs | Q: Quit")
            
        # Draw Episode list screen
        elif current_state == STATE_EPISODES:
            draw_header(stdscr, audio, resolution, model, active_anime)
            
            # Split screen into left (episode list) and right (details and queues)
            col_width = int(max_x * 0.6)
            
            # Draw headers for list
            stdscr.addstr(4, 2, "┌" + "─"*(col_width-4) + "┐", curses.color_pair(2))
            stdscr.addstr(5, 2, "│  [ ]  Episode  -  Status", curses.color_pair(2) | curses.A_BOLD)
            stdscr.addstr(5, col_width-2, "│", curses.color_pair(2))
            stdscr.addstr(6, 2, "├" + "─"*(col_width-4) + "┤", curses.color_pair(2))
            
            # Draw episode list
            list_height = max_y - 11
            start_idx = 0
            if selected_episode_idx >= list_height:
                start_idx = selected_episode_idx - list_height + 1
                
            for i in range(list_height):
                y = 7 + i
                stdscr.move(y, 0)
                stdscr.clrtoeol()
                stdscr.addstr(y, 2, "│", curses.color_pair(2))
                
                ep_idx = start_idx + i
                if ep_idx < len(episodes):
                    ep = episodes[ep_idx]
                    ep_num = ep.get('episode', ep_idx+1)
                    
                    # Check status
                    status = task_manager.get_status(active_anime['title'], ep_num, resolution, audio)
                    
                    # Selection marker
                    sel_char = "X" if ep_num in episode_selections else " "
                    cursor_char = ">" if ep_idx == selected_episode_idx else " "
                    
                    text = f" {cursor_char} [{sel_char}]  Ep {ep_num:<4d}  -  {status}"
                    
                    # Color coding status
                    color = curses.color_pair(1)
                    if status.startswith("Downloading"):
                        color = curses.color_pair(4) | curses.A_BOLD
                    elif status.startswith("Transcribing"):
                        color = curses.color_pair(4) | curses.A_BOLD
                    elif "Completed" in status:
                        color = curses.color_pair(3)
                    elif "Downloaded" in status:
                        color = curses.color_pair(3) | curses.A_BOLD
                    elif "Failed" in status:
                        color = curses.color_pair(5)
                    elif "Queued" in status:
                        color = curses.color_pair(2)
                        
                    # Highlight selected row
                    if ep_idx == selected_episode_idx:
                        color = curses.color_pair(6)
                        
                    stdscr.addnstr(y, 3, text, col_width - 6, color)
                    
                stdscr.addstr(y, col_width-2, "│", curses.color_pair(2))
                
            stdscr.addstr(7 + list_height, 2, "└" + "─"*(col_width-4) + "┘", curses.color_pair(2))
            
            # Draw right side information (Queues & Instructions)
            right_x = col_width
            right_width = max_x - col_width - 2
            
            # Border & details
            stdscr.attron(curses.color_pair(2))
            for y in range(4, max_y-2):
                stdscr.addstr(y, right_x, "│")
            stdscr.attroff(curses.color_pair(2))
            
            # Active tasks info
            stdscr.addstr(4, right_x+2, "══ Queue Status ══", curses.color_pair(2) | curses.A_BOLD)
            
            # Active download
            stdscr.addstr(6, right_x+2, "Downloading:", curses.color_pair(2))
            if task_manager.active_download:
                dl = task_manager.active_download
                stdscr.addnstr(7, right_x+4, f"Ep {dl['episode_num']} ({dl['resolution']}p)", right_width-4, curses.color_pair(4) | curses.A_BOLD)
            else:
                stdscr.addstr(7, right_x+4, "Idle", curses.color_pair(1))
                
            # Active Whisper
            stdscr.addstr(9, right_x+2, "Whisper TS:", curses.color_pair(2))
            if task_manager.active_transcribe:
                tr = task_manager.active_transcribe
                t_disp = f"Ep {tr['episode_num']}"
                if tr.get('current_time'):
                    t_disp += f" @ {tr['current_time']}"
                stdscr.addnstr(10, right_x+4, t_disp, right_width-4, curses.color_pair(4) | curses.A_BOLD)
            else:
                stdscr.addstr(10, right_x+4, "Idle", curses.color_pair(1))
                
            # Queue lengths
            dl_len = task_manager.download_queue.qsize()
            tr_len = task_manager.transcribe_queue.qsize()
            stdscr.addstr(12, right_x+2, f"Download Queue: {dl_len}", curses.color_pair(1))
            stdscr.addstr(13, right_x+2, f"Whisper Queue:  {tr_len}", curses.color_pair(1))
            
            # Selection count
            stdscr.addstr(15, right_x+2, f"Selected: {len(episode_selections)} episodes", curses.color_pair(4) | curses.A_BOLD)
            
            # Help prompt
            legend = "Space: Toggle | A: Select All | C: Clear | L: Lang | R: Res | M: Model | D: Download | T: Subtitle | P: Play | S: Search | V: Logs | Q: Quit"
            draw_footer(stdscr, legend)
            
        # Draw logs screen
        elif current_state == STATE_LOGS:
            draw_header(stdscr, audio, resolution, model, active_anime)
            
            stdscr.addstr(4, 2, "══ Background Logs (Ffmpeg / Whisper) ══ (Press [Any Key] to Close)", curses.color_pair(2) | curses.A_BOLD)
            
            # Draw log box
            log_box_height = max_y - 8
            
            with task_manager.log_lock:
                log_lines = list(task_manager.logs)
                
            # Scroll handling
            visible_lines = log_lines[-log_box_height:] if len(log_lines) >= log_box_height else log_lines
            
            for i, line in enumerate(visible_lines):
                y = 6 + i
                stdscr.move(y, 2)
                stdscr.clrtoeol()
                
                # Check for warnings/errors or system messages
                color = curses.color_pair(1)
                if "[FFmpeg]" in line:
                    color = curses.color_pair(2)
                elif "[Whisper]" in line:
                    color = curses.color_pair(4)
                elif "failed" in line.lower() or "error" in line.lower():
                    color = curses.color_pair(5)
                elif "completed" in line.lower() or "finished" in line.lower():
                    color = curses.color_pair(3)
                    
                stdscr.addnstr(y, 2, line, max_x-4, color)
                
            draw_footer(stdscr, "Press any key to return to list...")
            
        # Refresh screen
        stdscr.refresh()
        
        # Non-blocking key poll
        ch = stdscr.getch()
        
        # Global hotkey checking
        if ch == ord('q') or ch == ord('Q'):
            break
            
        # Handle state transitions
        if current_state == STATE_SETUP:
            if ch in (curses.KEY_UP, curses.KEY_DOWN, 9):  # Arrows or Tab
                setup_field_idx = 1 - setup_field_idx
            elif ch in (10, 13, curses.KEY_ENTER):
                # Edit selected field
                if setup_field_idx == 0:
                    val = curses_input(stdscr, box_y, 2 + len(cf_label), cf_label, cf_input)
                    if val is not None:
                        cf_input = val.strip()
                else:
                    val = curses_input(stdscr, box_y + 2, 2 + len(ua_label), ua_label, ua_input)
                    if val is not None:
                        ua_input = val.strip()
            elif ch in (ord('s'), ord('S')):
                if cf_input and ua_input:
                    cf, ua = cf_input, ua_input
                    save_config(cf, ua, audio, resolution, model)
                    # Start / reinitialize client and tasks
                    client = AnimePaheClient(cf, ua)
                    task_manager.start(client)
                    current_state = STATE_SEARCH
                else:
                    # Show error alert
                    stdscr.addstr(box_y + 8, 2, "Please fill in both fields before starting!", curses.color_pair(5) | curses.A_BOLD)
                    stdscr.refresh()
                    time.sleep(1.5)
                    
        elif current_state == STATE_SEARCH:
            if (ch in (10, 13, curses.KEY_ENTER) or ch == ord('e') or ch == ord('E')) and search_results:
                active_anime = search_results[selected_search_idx]
                try:
                    stdscr.addstr(5, 2, f"Loading episodes for {active_anime['title']}...", curses.color_pair(4))
                    stdscr.refresh()
                    episodes = client.get_all_episodes(active_anime['session'])
                    selected_episode_idx = 0
                    episode_selections.clear()
                    current_state = STATE_EPISODES
                except Exception as e:
                    stdscr.addstr(5, 2, f"Failed to load episodes: {str(e)}", curses.color_pair(5))
                    stdscr.refresh()
                    time.sleep(2)
            elif ch == ord('/') or (ch in (10, 13, curses.KEY_ENTER) and not search_results):
                # Run search input
                query = curses_input(stdscr, 4, 16, "Search Anime: ", search_query)
                if query is not None:
                    search_query = query.strip()
                    if search_query:
                        try:
                            stdscr.addstr(5, 2, "Searching AnimePahe...", curses.color_pair(4))
                            stdscr.refresh()
                            data = client.search_anime(search_query)
                            search_results = data.get('data', [])
                            selected_search_idx = 0
                        except Exception as e:
                            stdscr.addstr(5, 2, f"Search failed: {str(e)}", curses.color_pair(5))
                            stdscr.refresh()
                            time.sleep(2)
                            
            elif ch == curses.KEY_UP:
                if selected_search_idx > 0:
                    selected_search_idx -= 1
            elif ch == curses.KEY_DOWN:
                if selected_search_idx < len(search_results) - 1:
                    selected_search_idx += 1
            elif ch in (ord('o'), ord('O')):
                current_state = STATE_SETUP
            elif ch in (ord('v'), ord('V')):
                current_state = STATE_LOGS
                
        elif current_state == STATE_EPISODES:
            if ch == curses.KEY_UP:
                if selected_episode_idx > 0:
                    selected_episode_idx -= 1
            elif ch == curses.KEY_DOWN:
                if selected_episode_idx < len(episodes) - 1:
                    selected_episode_idx += 1
            elif ch == ord(' '):
                # Toggle select current episode
                if episodes:
                    ep_num = episodes[selected_episode_idx].get('episode', selected_episode_idx+1)
                    if ep_num in episode_selections:
                        episode_selections.remove(ep_num)
                    else:
                        episode_selections.add(ep_num)
            elif ch in (ord('a'), ord('A')):
                # Select all
                for ep in episodes:
                    ep_num = ep.get('episode')
                    if ep_num:
                        episode_selections.add(ep_num)
            elif ch in (ord('c'), ord('C')):
                # Clear all selections
                episode_selections.clear()
            elif ch in (ord('l'), ord('L')):
                # Toggle language
                audio = "eng" if audio == "jpn" else "jpn"
                save_config(cf, ua, audio, resolution, model)
            elif ch in (ord('r'), ord('R')):
                # Toggle resolution
                curr_idx = RESOLUTIONS.index(resolution)
                resolution = RESOLUTIONS[(curr_idx + 1) % len(RESOLUTIONS)]
                save_config(cf, ua, audio, resolution, model)
            elif ch in (ord('m'), ord('M')):
                # Cycle Whisper model
                curr_idx = MODELS.index(model)
                model = MODELS[(curr_idx + 1) % len(MODELS)]
                save_config(cf, ua, audio, resolution, model)
            elif ch in (ord('d'), ord('D')):
                # Download selected
                if not episode_selections:
                    # Auto-select current episode if none are checked
                    ep_num = episodes[selected_episode_idx].get('episode')
                    if ep_num:
                        episode_selections.add(ep_num)
                
                # Add to queue
                for ep_num in sorted(episode_selections):
                    # Find episode object
                    ep_obj = next((e for e in episodes if e.get('episode') == ep_num), None)
                    if ep_obj:
                        task_manager.queue_download(
                            anime_title=active_anime['title'],
                            anime_slug=active_anime['session'],
                            episode_num=ep_num,
                            episode_session=ep_obj['session'],
                            resolution=resolution,
                            audio=audio,
                            model=model
                        )
                # Clear selections after queuing
                episode_selections.clear()
                
            elif ch in (ord('t'), ord('T')):
                # Transcribe selected (Whisper)
                # If the file is not downloaded, it downloads it first and then transcribes it automatically!
                if not episode_selections:
                    ep_num = episodes[selected_episode_idx].get('episode')
                    if ep_num:
                        episode_selections.add(ep_num)
                        
                for ep_num in sorted(episode_selections):
                    # Check if already downloaded
                    local = check_local_episode_files(".", active_anime['title'], ep_num, resolution, audio)
                    ep_obj = next((e for e in episodes if e.get('episode') == ep_num), None)
                    
                    if local['target_video']:
                        # Add direct to transcribe queue (detect audio language from filename)
                        local_audio = "eng" if "_eng.mp4" in local['target_video'] else "jpn"
                        task_manager.queue_transcribe(active_anime['title'], ep_num, local['target_video'], local_audio, model=model)
                    elif ep_obj:
                        # Queue download with transcribe_on_complete=True
                        task_manager.queue_download(
                            anime_title=active_anime['title'],
                            anime_slug=active_anime['session'],
                            episode_num=ep_num,
                            episode_session=ep_obj['session'],
                            resolution=resolution,
                            audio=audio,
                            transcribe_on_complete=True,
                            model=model
                        )
                        task_manager.add_log(f"Queued download with auto-subtitle trigger for Ep {ep_num}")
                
                episode_selections.clear()
                
            elif ch in (ord('p'), ord('P')):
                # Play selected (celluloid)
                play_list = []
                if not episode_selections:
                    # Try to play current
                    ep_num = episodes[selected_episode_idx].get('episode')
                    if ep_num:
                        local = check_local_episode_files(".", active_anime['title'], ep_num, resolution, audio)
                        if local['target_video']:
                            play_list.append(local['target_video'])
                        elif local['any_video_files']:
                            play_list.append(local['any_video_files'][0])
                else:
                    for ep_num in sorted(episode_selections):
                        local = check_local_episode_files(".", active_anime['title'], ep_num, resolution, audio)
                        if local['target_video']:
                            play_list.append(local['target_video'])
                        elif local['any_video_files']:
                            play_list.append(local['any_video_files'][0])
                            
                if play_list:
                    task_manager.add_log(f"Launching Celluloid to play {len(play_list)} videos...")
                    # Launch celluloid in background
                    try:
                        subprocess.Popen(["celluloid"] + play_list, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception as e:
                        task_manager.add_log(f"Failed to launch Celluloid: {str(e)}")
                else:
                    stdscr.addstr(5, 2, "No downloaded videos found for selection! Download them first.", curses.color_pair(5) | curses.A_BOLD)
                    stdscr.refresh()
                    time.sleep(1.5)
                    
            elif ch in (ord('s'), ord('S')):
                current_state = STATE_SEARCH
            elif ch in (ord('v'), ord('V')):
                current_state = STATE_LOGS
                
        elif current_state == STATE_LOGS:
            # Any key exits logs
            if ch != -1:
                current_state = STATE_EPISODES
                
        # If no key pressed, sleep briefly to prevent 100% CPU usage
        if ch == -1:
            time.sleep(0.05)
            
    # Cleanup task manager
    task_manager.stop()

if __name__ == "__main__":
    try:
        curses.wrapper(main_tui)
    except KeyboardInterrupt:
        pass
