import sys
import json
import urllib.request
import urllib.parse
import re
import subprocess
import tempfile
import os
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Force UTF-8 output so Unicode characters in print() don't crash on Windows cp1252
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CLIPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'clips')
if not os.path.exists(CLIPS_DIR):
    os.makedirs(CLIPS_DIR)

# Map UI layout mode values to internal names.
# content.js sends: "16:9" | "9:16" | "split_screen"
# Internally we use: "widescreen" | "vertical_crop" | "split_screen"
LAYOUT_MODE_MAP = {
    '16:9':          'widescreen',
    '9:16':          'vertical_crop',
    'widescreen':    'widescreen',
    'vertical_crop': 'vertical_crop',
    'split_screen':  'split_screen',
}

def datetime_filename():
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

# ---------------------------------------------------------------------------
# FFprobe helper – detect source video resolution from a TS file
# ---------------------------------------------------------------------------
def probe_video_size(ffprobe_bin, filepath):
    """Return (width, height) of the video stream, or (1280, 720) as safe default."""
    try:
        result = subprocess.run(
            [
                ffprobe_bin, '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'csv=p=0',
                filepath
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )
        line = result.stdout.decode('utf-8', errors='ignore').strip().split('\n')[0]
        parts = line.split(',')
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception as e:
        print(f"ffprobe warning (using default 1280x720): {e}")
    return 1280, 720

# ---------------------------------------------------------------------------
# Clip transcode engine
# ---------------------------------------------------------------------------
def transcode_clip(ts_bytes, params):
    """
    Transcode raw HLS TS bytes to an MP4 clip file according to params.
    Returns (clip_bytes, out_filename).
    """
    # Locate ffmpeg / ffprobe executables
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_ffmpeg = os.path.join(script_dir, 'ffmpeg.exe')
    local_ffprobe = os.path.join(script_dir, 'ffprobe.exe')

    ffmpeg_bin = local_ffmpeg if os.path.exists(local_ffmpeg) else 'ffmpeg'
    ffprobe_bin = local_ffprobe if os.path.exists(local_ffprobe) else 'ffprobe'

    if not os.path.exists(local_ffmpeg):
        try:
            subprocess.run(['ffmpeg', '-version'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            print("\n" + "!"*60)
            print("WARNING: FFmpeg not found. Place ffmpeg.exe next to server.py.")
            print("!"*60 + "\n")
            date_str = datetime_filename()
            out_filename = f"kick_clip_{date_str}.ts"
            filepath = os.path.join(CLIPS_DIR, out_filename)
            with open(filepath, 'wb') as f:
                f.write(ts_bytes)
            return ts_bytes, out_filename

    # Write TS bytes to a temp file so ffmpeg can read them
    with tempfile.NamedTemporaryFile(suffix='.ts', delete=False) as temp_in:
        temp_in.write(ts_bytes)
        temp_in_name = temp_in.name

    date_str = datetime_filename()
    out_filename = f"kick_clip_{date_str}.mp4"
    temp_out_name = os.path.join(CLIPS_DIR, out_filename)

    try:
        # Normalise layout_mode (UI may send "16:9" / "9:16")
        raw_layout   = params.get('layout_mode', 'widescreen')
        layout_mode  = LAYOUT_MODE_MAP.get(raw_layout, 'widescreen')

        crop_offset_pct = int(params.get('crop_offset_pct', 50))
        watermark_text  = str(params.get('watermark_text', '')).strip()
        audio_volume    = int(params.get('audio_volume', 100))
        resolution      = str(params.get('resolution', 'source'))

        is_widescreen   = (layout_mode == 'widescreen')
        has_watermark   = bool(watermark_text)
        is_source_res   = (resolution == 'source')
        is_normal_vol   = (audio_volume == 100)

        # ==================================================================
        # Fast path 1 – complete stream copy (no re-encode needed)
        # ==================================================================
        if is_widescreen and not has_watermark and is_source_res and is_normal_vol:
            cmd = [
                ffmpeg_bin, '-y',
                '-i', temp_in_name,
                '-c', 'copy',
                '-movflags', '+faststart',
                temp_out_name
            ]
            print(f"[FAST COPY] {' '.join(cmd)}")

        # ==================================================================
        # Fast path 2 – copy video, only re-encode audio (volume change)
        # ==================================================================
        elif is_widescreen and not has_watermark and is_source_res:
            vol = audio_volume / 100.0
            cmd = [
                ffmpeg_bin, '-y',
                '-i', temp_in_name,
                '-c:v', 'copy',
                '-af', f'volume={vol:.4f}',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                temp_out_name
            ]
            print(f"[AUDIO TRANSCODE] {' '.join(cmd)}")

        # ==================================================================
        # Full transcode path – crops, split-screen, watermark, scaling
        # ==================================================================
        else:
            # Probe source dimensions so we can compute exact pixel crops
            src_w, src_h = probe_video_size(ffprobe_bin, temp_in_name)
            print(f"[PROBE] Source resolution: {src_w}x{src_h}")

            filters  = []   # filter_complex chain entries
            cur_v    = '[0:v]'   # current video label threading through filters

            # ------------------------------------------------------------------
            # Layout filters
            # ------------------------------------------------------------------
            if layout_mode == 'vertical_crop':
                # Crop a 9:16 vertical strip from the 16:9 source
                gp_w = 2 * int(src_h * 9 / 16 / 2)   # even width
                gp_h = src_h                            # full height
                max_x = src_w - gp_w
                gp_x = max(0, min(max_x, int(max_x * crop_offset_pct / 100)))
                gp_x = gp_x - (gp_x % 2)              # force even x

                filters.append(
                    f"{cur_v}crop={gp_w}:{gp_h}:{gp_x}:0[vcrop]"
                )
                cur_v = '[vcrop]'

            elif layout_mode == 'split_screen':
                fx_pct = float(params.get('facecam_x_pct', 10))
                fy_pct = float(params.get('facecam_y_pct', 10))
                fw_pct = float(params.get('facecam_w_pct', 25))
                fh_pct = float(params.get('facecam_h_pct', 25))

                # ── Gameplay crop (9:16 strip, ~65% of frame height) ──
                # Width = 9/16 × src_h  (gives 9:16 ratio)
                gp_w = 2 * int(src_h * 9 / 16 / 2)
                gp_h = 2 * int(src_h * 0.65 / 2)
                max_gp_x = src_w - gp_w
                gp_x = max(0, min(max_gp_x, int(max_gp_x * crop_offset_pct / 100)))
                gp_x = gp_x - (gp_x % 2)
                gp_y = 2 * int((src_h - gp_h) / 2 / 2)   # vertically centered, even

                filters.append(
                    f"[0:v]crop={gp_w}:{gp_h}:{gp_x}:{gp_y}[gameplay]"
                )

                # ── Facecam crop ──
                fc_w = 2 * int(src_w * fw_pct / 100 / 2)
                fc_h = 2 * int(src_h * fh_pct / 100 / 2)
                fc_x = 2 * int(src_w * fx_pct / 100 / 2)
                fc_y = 2 * int(src_h * fy_pct / 100 / 2)
                # Clamp so crop stays inside the source frame
                fc_w = max(2, min(fc_w, src_w - fc_x))
                fc_h = max(2, min(fc_h, src_h - fc_y))

                filters.append(
                    f"[0:v]crop={fc_w}:{fc_h}:{fc_x}:{fc_y}[facecam_raw]"
                )

                # ── Scale facecam to match gameplay width (gp_w is known in Python) ──
                # Target: facecam width = gp_w, height scaled proportionally (keep AR)
                fc_target_h = 2 * int(fc_h * gp_w / fc_w / 2) if fc_w > 0 else gp_w
                fc_target_h = max(2, fc_target_h)

                filters.append(
                    f"[facecam_raw]scale={gp_w}:{fc_target_h}[facecam]"
                )

                # ── Stack facecam ON TOP of gameplay ──
                filters.append(
                    "[facecam][gameplay]vstack=inputs=2[split_out]"
                )
                cur_v = '[split_out]'

            # ------------------------------------------------------------------
            # Watermark filter (drawtext)
            # ------------------------------------------------------------------
            if has_watermark:
                # Escape single quotes for the drawtext filter
                escaped = watermark_text.replace("'", r"'\''")
                wpos = str(params.get('watermark_pos', 'top_right'))
                pos_map = {
                    'top_left':     'x=12:y=12',
                    'bottom_center': 'x=(w-tw)/2:y=h-th-12',
                    'top_right':    'x=w-tw-12:y=12',
                }
                pos_str = pos_map.get(wpos, 'x=w-tw-12:y=12')
                filters.append(
                    f"{cur_v}drawtext=text='{escaped}':{pos_str}"
                    f":fontsize=24:fontcolor=white:box=1:boxcolor=black@0.4[wm]"
                )
                cur_v = '[wm]'

            # ------------------------------------------------------------------
            # Resolution scaling
            # ------------------------------------------------------------------
            if not is_source_res:
                target_h = int(resolution)
                filters.append(f"{cur_v}scale=-2:{target_h}[scaled]")
                cur_v = '[scaled]'

            # ------------------------------------------------------------------
            # Audio volume
            # ------------------------------------------------------------------
            vol = audio_volume / 100.0
            filters.append(f"[0:a]volume={vol:.4f}[aout]")

            # Build command
            cmd = [
                ffmpeg_bin, '-y',
                '-i', temp_in_name,
                '-filter_complex', '; '.join(filters),
                '-map', cur_v,
                '-map', '[aout]',
                '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p',   # universal mobile decode
                '-preset', 'superfast',
                '-crf', '22',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',  # progressive mobile playback
                temp_out_name
            ]
            print(f"[FULL TRANSCODE]\nFilter chain:\n  " +
                  '\n  '.join(filters) + f"\nCommand:\n  {' '.join(cmd)}")

        # ------------------------------------------------------------------
        # Execute the command
        # ------------------------------------------------------------------
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode == 0 and os.path.getsize(temp_out_name) > 0:
            print(f"FFmpeg transcode successful -> {temp_out_name}")
            with open(temp_out_name, 'rb') as f:
                return f.read(), out_filename
        else:
            stderr_text = result.stderr.decode('utf-8', errors='ignore')
            print(f"FFmpeg FAILED (exit {result.returncode}):\n{stderr_text}")
            # DO NOT silently return raw TS as MP4 – raise so the HTTP handler
            # returns a proper 500 error instead of a "working" fullscreen clip.
            raise RuntimeError(
                f"FFmpeg exited with code {result.returncode}. "
                f"See server log for FFmpeg stderr."
            )

    finally:
        # Always clean up the temporary input file
        try:
            if os.path.exists(temp_in_name):
                os.remove(temp_in_name)
        except Exception as e:
            print(f"Temp file cleanup warning: {e}")


# ---------------------------------------------------------------------------
# HLS segment fetcher
# ---------------------------------------------------------------------------
def fetch_and_slice_hls(m3u8_url, start_offset, duration_seconds):
    print(f"Fetching manifest from: {m3u8_url}")
    req = urllib.request.Request(m3u8_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as resp:
        content = resp.read().decode('utf-8')

    lines = content.split('\n')

    # Handle master/variant playlists
    variants = []
    cur_info = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXT-X-STREAM-INF:'):
            cur_info = line
        elif not line.startswith('#'):
            if cur_info:
                variants.append((cur_info, line))
                cur_info = None

    if variants:
        def bw(v):
            m = re.search(r'BANDWIDTH=(\d+)', v[0])
            return int(m.group(1)) if m else 0
        variants.sort(key=bw, reverse=True)
        best_rel = variants[0][1]
        m3u8_url = urllib.parse.urljoin(m3u8_url, best_rel)
        print(f"Following highest quality playlist: {m3u8_url}")
        req = urllib.request.Request(m3u8_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode('utf-8')
        lines = content.split('\n')

    # Parse media segments
    segments = []
    total_dur = 0.0
    seg_dur = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXTINF:'):
            m = re.search(r'#EXTINF:([0-9.]+)', line)
            if m:
                seg_dur = float(m.group(1))
        elif not line.startswith('#'):
            if seg_dur is not None:
                segments.append({
                    'url': urllib.parse.urljoin(m3u8_url, line),
                    'duration': seg_dur,
                    'start_time': total_dur,
                })
                total_dur += seg_dur
                seg_dur = None

    end_offset = start_offset + duration_seconds
    target = [
        s for s in segments
        if (s['start_time'] + s['duration']) > start_offset
        and s['start_time'] < end_offset
    ]

    print(f"Parsed {len(segments)} segments (total {total_dur:.2f}s). "
          f"Downloading {len(target)} for [{start_offset}s–{end_offset}s]...")

    video_bytes = bytearray()
    for i, seg in enumerate(target):
        print(f"  Segment {i+1}/{len(target)}: {seg['url']}")
        try:
            r = urllib.request.Request(seg['url'], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(r) as rs:
                video_bytes.extend(rs.read())
        except Exception as e:
            print(f"  WARNING: failed to download segment: {e}")

    return bytes(video_bytes)


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------
def _parse_clip_params(query_params):
    """Extract and type-coerce clip parameters from a query_params dict."""
    return {
        'layout_mode':    query_params.get('layout_mode', ['widescreen'])[0],
        'crop_offset_pct': int(query_params.get('crop_offset_pct', ['50'])[0]),
        'facecam_x_pct':  float(query_params.get('facecam_x_pct', ['10'])[0]),
        'facecam_y_pct':  float(query_params.get('facecam_y_pct', ['10'])[0]),
        'facecam_w_pct':  float(query_params.get('facecam_w_pct', ['25'])[0]),
        'facecam_h_pct':  float(query_params.get('facecam_h_pct', ['25'])[0]),
        'watermark_text': query_params.get('watermark_text', [''])[0],
        'watermark_pos':  query_params.get('watermark_pos', ['top_right'])[0],
        'audio_volume':   int(query_params.get('audio_volume', ['100'])[0]),
        'resolution':     query_params.get('resolution', ['source'])[0],
    }


class ClipperHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Keep default access logs
        print(f"[HTTP] {self.address_string()} {fmt % args}")

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        qp     = urllib.parse.parse_qs(parsed.query)

        # ── Serve a saved clip file ──────────────────────────────────────
        if path.startswith('/clips/'):
            filename = os.path.basename(urllib.parse.unquote(path))
            filepath = os.path.join(CLIPS_DIR, filename)
            if not os.path.exists(filepath):
                self.send_response(404); self.end_headers()
                self.wfile.write(b"Clip not found"); return
            with open(filepath, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # ── List saved clips ─────────────────────────────────────────────
        if path == '/list-clips':
            files = []
            for fname in os.listdir(CLIPS_DIR):
                if fname.endswith('.mp4'):
                    fp = os.path.join(CLIPS_DIR, fname)
                    st = os.stat(fp)
                    files.append({'filename': fname,
                                  'size': st.st_size,
                                  'created': st.st_mtime})
            files.sort(key=lambda x: x['created'], reverse=True)
            body = json.dumps(files).encode('utf-8')
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ── Delete a clip ────────────────────────────────────────────────
        if path.startswith('/delete-clip'):
            fname = qp.get('filename', [None])[0]
            if fname:
                fname = os.path.basename(fname)
                fp = os.path.join(CLIPS_DIR, fname)
                if os.path.exists(fp):
                    os.remove(fp)
                    body = json.dumps({"success": True}).encode('utf-8')
                    self.send_response(200)
                    self._cors_headers()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                    print(f"Deleted clip: {fname}")
                    return
            self.send_response(400)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"Invalid parameters")
            return

        # ── Generate clip (GET) ──────────────────────────────────────────
        if path.startswith('/create-clip'):
            stream_url      = qp.get('stream_url', [None])[0]
            start_offset    = qp.get('start_offset', [None])[0]
            duration_seconds = qp.get('duration_seconds', [None])[0]

            if not (stream_url and start_offset and duration_seconds):
                self.send_response(400)
                self._cors_headers()
                self.end_headers()
                self.wfile.write(b"Missing required parameters: stream_url, start_offset, duration_seconds")
                return

            params = _parse_clip_params(qp)
            self._run_clip_job(stream_url, int(start_offset), int(duration_seconds), params)
            return

        # ── 404 ──────────────────────────────────────────────────────────
        self.send_response(404)
        self._cors_headers()
        self.end_headers()
        self.wfile.write(b"Not Found")

    def do_POST(self):
        if self.path != '/create-clip':
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            self.send_response(400)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"Invalid JSON body")
            return

        stream_url       = payload.get('stream_url')
        start_offset     = payload.get('start_offset')
        duration_seconds = payload.get('duration_seconds')

        if not (stream_url and start_offset is not None and duration_seconds):
            self.send_response(400)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"Missing required parameters")
            return

        # Convert POST body keys to the same qp format _parse_clip_params expects
        qp_from_body = {k: [str(v)] for k, v in payload.items()}
        params = _parse_clip_params(qp_from_body)
        self._run_clip_job(stream_url, int(start_offset), int(duration_seconds), params)

    def _run_clip_job(self, stream_url, start_offset, duration_seconds, params):
        """Download HLS segments, transcode, and stream back to client."""
        print("\n" + "="*60)
        print("CLIP GENERATION REQUEST:")
        print(f"  URL:      {stream_url}")
        print(f"  Offset:   {start_offset}s,  Duration: {duration_seconds}s")
        print(f"  Layout:   {params['layout_mode']}")
        print(f"  Crop:     {params['crop_offset_pct']}%")
        print(f"  Facecam:  X={params['facecam_x_pct']}% Y={params['facecam_y_pct']}% "
              f"W={params['facecam_w_pct']}% H={params['facecam_h_pct']}%")
        print(f"  Watermark: '{params['watermark_text']}' @ {params['watermark_pos']}")
        print(f"  Volume:   {params['audio_volume']}%,  Resolution: {params['resolution']}")
        print("="*60 + "\n")

        try:
            ts_bytes = fetch_and_slice_hls(stream_url, start_offset, duration_seconds)
            clip_bytes, out_filename = transcode_clip(ts_bytes, params)

            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(clip_bytes)))
            self.end_headers()
            self.wfile.write(clip_bytes)
            print(f"SUCCESS: Returned {out_filename} ({len(clip_bytes):,} bytes)")

        except Exception as e:
            import traceback
            traceback.print_exc()
            error_body = json.dumps({"error": str(e)}).encode('utf-8')
            self.send_response(500)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_body)))
            self.end_headers()
            self.wfile.write(error_body)
            print(f"ERROR: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run(port=8000):
    server = HTTPServer(('', port), ClipperHandler)
    print(f"Kick Stream Clipper server running at http://localhost:{port}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()

if __name__ == '__main__':
    run()
