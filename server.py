import json
import base64
import urllib.request
import urllib.parse
import re
import subprocess
import tempfile
import os
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Local clips storage directory
CLIPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'clips')
if not os.path.exists(CLIPS_DIR):
    os.makedirs(CLIPS_DIR)

def datetime_filename():
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

def process_clip_transcode(ts_bytes, params):
    # Check if local ffmpeg.exe exists, otherwise look in system PATH
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_ffmpeg = os.path.join(script_dir, 'ffmpeg.exe')
    
    ffmpeg_bin = 'ffmpeg'
    if os.path.exists(local_ffmpeg):
        ffmpeg_bin = local_ffmpeg
    else:
        try:
            subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            print("\n" + "!"*60)
            print("WARNING: FFmpeg is not found in PATH or local directory.")
            print("Advanced filters (crop, split-screen, scaling) are disabled.")
            print("To enable advanced filters, please place ffmpeg.exe in the same folder as server.py.")
            print("!"*60 + "\n")
            
            # Simple fallback: Save raw TS directly as clip name
            date_str = datetime_filename()
            out_filename = f"kick_clip_{date_str}.mp4"
            filepath = os.path.join(CLIPS_DIR, out_filename)
            with open(filepath, 'wb') as f:
                f.write(ts_bytes)
            return ts_bytes, out_filename

    # Create temporary file for input TS bytes
    with tempfile.NamedTemporaryFile(suffix='.ts', delete=False) as temp_in:
        temp_in.write(ts_bytes)
        temp_in_name = temp_in.name

    date_str = datetime_filename()
    out_filename = f"kick_clip_{date_str}.mp4"
    temp_out_name = os.path.join(CLIPS_DIR, out_filename)

    try:
        layout_mode = params.get('layout_mode', 'widescreen')
        crop_offset_pct = params.get('crop_offset_pct', 50)
        watermark_text = params.get('watermark_text', '').strip()
        audio_volume = params.get('audio_volume', 100)
        resolution = params.get('resolution', 'source')

        is_widescreen = (layout_mode == 'widescreen')
        has_no_watermark = (not watermark_text)
        is_source_res = (resolution == 'source')
        is_normal_vol = (audio_volume == 100)

        # 1. Check if we can perform a 100% copy transcode (instant!)
        if is_widescreen and has_no_watermark and is_source_res and is_normal_vol:
            cmd = [
                ffmpeg_bin, '-y',
                '-i', temp_in_name,
                '-c', 'copy',
                '-movflags', '+faststart',  # Enable mobile/faststart optimization
                temp_out_name
            ]
            print(f"Executing instant stream-copy command:\n{' '.join(cmd)}")
        
        # 2. Check if we can copy video and only transcode audio (extremely fast!)
        elif is_widescreen and has_no_watermark and is_source_res:
            volume_factor = audio_volume / 100.0
            cmd = [
                ffmpeg_bin, '-y',
                '-i', temp_in_name,
                '-c:v', 'copy',
                '-af', f"volume={volume_factor:.2f}",
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',  # Enable mobile/faststart optimization
                temp_out_name
            ]
            print(f"Executing fast video-copy + audio-transcode command:\n{' '.join(cmd)}")
        
        # 3. Otherwise, build full filter complex for crop, watermark, or scale
        else:
            filter_complex = []
            video_output_label = "[v]"
            audio_output_label = "[a]"
            
            # Apply Layout Crop filter
            if layout_mode == 'vertical_crop':
                filter_complex.append(f"[0:v]crop=2*trunc(ih*9/32):2*trunc(ih/2):(iw-2*trunc(ih*9/32))*{crop_offset_pct}/100:0[layoutv]")
                video_input_label = "[layoutv]"
            elif layout_mode == 'split_screen':
                fx_pct = params.get('facecam_x_pct', 10)
                fy_pct = params.get('facecam_y_pct', 10)
                fw_pct = params.get('facecam_w_pct', 25)
                fh_pct = params.get('facecam_h_pct', 25)
                
                # 1. Crop facecam at coordinates
                filter_complex.append(f"[0:v]crop=iw*{fw_pct}/100:ih*{fh_pct}/100:iw*{fx_pct}/100:ih*{fy_pct}/100[facecam_raw]")
                
                # 2. Crop gameplay using even constraints (65% of screen height)
                filter_complex.append(f"[0:v]crop=2*trunc(ih*9/32):2*trunc(ih*0.65/2):(iw-2*trunc(ih*9/32))*{crop_offset_pct}/100:2*trunc(ih*0.35/4)[gameplay]")
                
                # 3. Use scale2ref to dynamically match facecam width to gameplay width (iw) and scale height proportionally (even)
                filter_complex.append("[facecam_raw][gameplay]scale2ref=w=iw:h=-2[facecam][gameplay]")
                
                # 4. Stack them vertically
                filter_complex.append("[facecam][gameplay]vstack=inputs=2[layoutv]")
                video_input_label = "[layoutv]"
            else:
                video_input_label = "[0:v]"

            # Burn Custom text watermark
            if watermark_text:
                escaped_text = watermark_text.replace("'", "'\\\\''")
                wpos = params.get('watermark_pos', 'top_right')
                if wpos == 'top_left':
                    coord_str = "x=12:y=12"
                elif wpos == 'bottom_center':
                    coord_str = "x=(w-tw)/2:y=h-th-12"
                else: # top_right
                    coord_str = "x=w-tw-12:y=12"
                filter_complex.append(f"{video_input_label}drawtext=text='{escaped_text}':{coord_str}:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.4[watermarkv]")
                video_input_label = "[watermarkv]"

            # Output Scaling
            if resolution != 'source':
                target_h = int(resolution)
                filter_complex.append(f"{video_input_label}scale=-2:{target_h}[scaledv]")
                video_input_label = "[scaledv]"

            # Attach video null label
            filter_complex.append(f"{video_input_label}null{video_output_label}")

            # Audio volume filter
            volume_factor = audio_volume / 100.0
            filter_complex.append(f"[0:a]volume={volume_factor:.2f}{audio_output_label}")

            cmd = [
                ffmpeg_bin, '-y',
                '-i', temp_in_name,
                '-filter_complex', '; '.join(filter_complex),
                '-map', video_output_label,
                '-map', audio_output_label,
                '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p',      # Force YUV420p color space for universal mobile decoding
                '-preset', 'superfast',
                '-crf', '22',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',  # Move moov atom to the front for progressive phone playback
                temp_out_name
            ]
            print(f"Executing advanced filter transcode command:\n{' '.join(cmd)}")

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if result.returncode == 0 and os.path.exists(temp_out_name):
            print(f"FFmpeg transcode successful. Clip saved to: {temp_out_name}")
            with open(temp_out_name, 'rb') as f:
                output_bytes = f.read()
            return output_bytes, out_filename
        else:
            stderr_log = result.stderr.decode('utf-8', errors='ignore')
            print(f"FFmpeg transcoding failed with code {result.returncode}.\nStderr: {stderr_log}")
            # Fallback
            with open(temp_out_name, 'wb') as f:
                f.write(ts_bytes)
            return ts_bytes, out_filename
            
    except Exception as e:
        print(f"Error during transcode execution: {e}")
        # Save raw fallback
        with open(temp_out_name, 'wb') as f:
            f.write(ts_bytes)
        return ts_bytes, out_filename
    finally:
        # Clean up temporary input file
        try:
            if os.path.exists(temp_in_name):
                os.remove(temp_in_name)
        except Exception as cleanup_err:
            print(f"Temporary file cleanup warning: {cleanup_err}")

def fetch_and_slice_hls(m3u8_url, start_offset, duration_seconds):
    print(f"Fetching manifest from: {m3u8_url}")
    req = urllib.request.Request(m3u8_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        content = response.read().decode('utf-8')
    
    lines = content.split('\n')
    
    # Check for variant playlists
    variants = []
    current_variant_info = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXT-X-STREAM-INF:'):
            current_variant_info = line
        elif not line.startswith('#'):
            if current_variant_info:
                variants.append((current_variant_info, line))
                current_variant_info = None
    
    if variants:
        def get_bandwidth(variant):
            match = re.search(r'BANDWIDTH=(\d+)', variant[0])
            return int(match.group(1)) if match else 0
        variants.sort(key=get_bandwidth, reverse=True)
        best_playlist_rel = variants[0][1]
        m3u8_url = urllib.parse.urljoin(m3u8_url, best_playlist_rel)
        print(f"Following highest quality stream playlist: {m3u8_url}")
        
        req = urllib.request.Request(m3u8_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            content = response.read().decode('utf-8')
        lines = content.split('\n')

    segments = []
    current_duration = 0.0
    segment_duration = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXTINF:'):
            match = re.search(r'#EXTINF:([0-9.]+)', line)
            if match:
                segment_duration = float(match.group(1))
        elif not line.startswith('#'):
            if segment_duration is not None:
                segments.append({
                    'url': urllib.parse.urljoin(m3u8_url, line),
                    'duration': segment_duration,
                    'start_time': current_duration
                })
                current_duration += segment_duration
                segment_duration = None

    end_offset = start_offset + duration_seconds
    target_segments = []
    
    for seg in segments:
        seg_start = seg['start_time']
        seg_end = seg_start + seg['duration']
        if seg_end > start_offset and seg_start < end_offset:
            target_segments.append(seg)
            
    print(f"Parsed {len(segments)} total segments (Duration: {current_duration:.2f}s).")
    print(f"Downloading {len(target_segments)} segments for requested clip [{start_offset}s - {end_offset}s]...")
    
    video_bytes = bytearray()
    for i, seg in enumerate(target_segments):
        print(f"Downloading segment {i+1}/{len(target_segments)}: {seg['url']}")
        try:
            req_seg = urllib.request.Request(seg['url'], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_seg) as response_seg:
                video_bytes.extend(response_seg.read())
        except Exception as e:
            print(f"Failed to download segment {seg['url']}: {e}")
            
    return bytes(video_bytes)

class ClipperMockHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        # Handle CORS preflight requests from Kick.com
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        # 1. Stream static clips in gallery
        if self.path.startswith('/clips/'):
            filename = urllib.parse.unquote(self.path.split('/')[-1])
            filename = os.path.basename(filename) # Path traversal guard
            filepath = os.path.join(CLIPS_DIR, filename)
            
            if os.path.exists(filepath):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "video/mp4")
                
                with open(filepath, 'rb') as f:
                    content_bytes = f.read()
                
                self.send_header("Content-Length", str(len(content_bytes)))
                self.end_headers()
                self.wfile.write(content_bytes)
                return
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Clip Not Found")
                return

        # 2. List all clips
        elif self.path == '/list-clips':
            files = []
            for f in os.listdir(CLIPS_DIR):
                if f.endswith('.mp4'):
                    path = os.path.join(CLIPS_DIR, f)
                    stat = os.stat(path)
                    files.append({
                        'filename': f,
                        'size': stat.st_size,
                        'created': stat.st_mtime
                    })
            # Sort by newest first
            files.sort(key=lambda x: x['created'], reverse=True)
            
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(files).encode('utf-8'))
            return

        # 3. Delete clip
        elif self.path.startswith('/delete-clip'):
            from urllib.parse import urlparse, parse_qs
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            filename = query_params.get('filename', [None])[0]
            
            if filename:
                filename = os.path.basename(filename)
                filepath = os.path.join(CLIPS_DIR, filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
                    self.send_response(200)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
                    print(f"Deleted clip file: {filename}")
                    return
            
            self.send_response(400)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"Invalid parameters")
            return

        # 4. Generate clip request
        elif self.path.startswith('/create-clip'):
            from urllib.parse import urlparse, parse_qs
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            
            stream_url = query_params.get('stream_url', [None])[0]
            start_offset = query_params.get('start_offset', [None])[0]
            duration_seconds = query_params.get('duration_seconds', [None])[0]
            
            # Format parameters
            layout_mode = query_params.get('layout_mode', ['widescreen'])[0]
            crop_offset_pct = int(query_params.get('crop_offset_pct', ['50'])[0])
            facecam_x_pct = float(query_params.get('facecam_x_pct', ['10'])[0])
            facecam_y_pct = float(query_params.get('facecam_y_pct', ['10'])[0])
            facecam_w_pct = float(query_params.get('facecam_w_pct', ['25'])[0])
            facecam_h_pct = float(query_params.get('facecam_h_pct', ['25'])[0])
            watermark_text = query_params.get('watermark_text', [''])[0]
            watermark_pos = query_params.get('watermark_pos', ['top_right'])[0]
            audio_volume = int(query_params.get('audio_volume', ['100'])[0])
            resolution = query_params.get('resolution', ['source'])[0]
            
            if stream_url and start_offset and duration_seconds:
                try:
                    start_offset = int(start_offset)
                    duration_seconds = int(duration_seconds)
                    
                    print("\n" + "="*50)
                    print("CLIP GENERATION GET REQUEST RECEIVED:")
                    print(f"Stream playlist URL: {stream_url}")
                    print(f"Start Offset:        {start_offset} seconds")
                    print(f"Duration:            {duration_seconds} seconds")
                    print(f"Layout Mode:         {layout_mode}")
                    print(f"Crop Offset Pct:     {crop_offset_pct}%")
                    print(f"Facecam Coordinates: X={facecam_x_pct}%, Y={facecam_y_pct}%, W={facecam_w_pct}%, H={facecam_h_pct}%")
                    print(f"Watermark:           '{watermark_text}' ({watermark_pos})")
                    print(f"Audio Volume:        {audio_volume}%")
                    print(f"Resolution:          {resolution}")
                    print("="*50 + "\n")

                    # Download HLS segments
                    ts_bytes = fetch_and_slice_hls(stream_url, start_offset, duration_seconds)

                    # Build params bundle
                    params = {
                        'layout_mode': layout_mode,
                        'crop_offset_pct': crop_offset_pct,
                        'facecam_x_pct': facecam_x_pct,
                        'facecam_y_pct': facecam_y_pct,
                        'facecam_w_pct': facecam_w_pct,
                        'facecam_h_pct': facecam_h_pct,
                        'watermark_text': watermark_text,
                        'watermark_pos': watermark_pos,
                        'audio_volume': audio_volume,
                        'resolution': resolution
                    }

                    # Transcode and save
                    clip_bytes, out_filename = process_clip_transcode(ts_bytes, params)

                    # Send success response with clip bytes
                    self.send_response(200)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Type", "video/mp4")
                    self.end_headers()
                    
                    # Write video bytes to response
                    self.wfile.write(clip_bytes)
                    print(f"Successfully returned clip {out_filename} ({len(clip_bytes)} bytes).")
                    return
                except Exception as e:
                    self.send_response(400)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    error_response = json.dumps({"error": f"Invalid query parameters or transcode error: {str(e)}"})
                    self.wfile.write(error_response.encode('utf-8'))
                    print(f"GET transcode request error: {e}")
                    return
            
            self.send_response(400)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"Missing parameters")
        else:
            self.send_response(404)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        if self.path == '/create-clip':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            try:
                payload = json.loads(post_data.decode('utf-8'))
                stream_url = payload.get('stream_url')
                start_offset = payload.get('start_offset')
                duration_seconds = payload.get('duration_seconds')
                
                layout_mode = payload.get('layout_mode', 'widescreen')
                crop_offset_pct = int(payload.get('crop_offset_pct', 50))
                facecam_x_pct = float(payload.get('facecam_x_pct', 10))
                facecam_y_pct = float(payload.get('facecam_y_pct', 10))
                facecam_w_pct = float(payload.get('facecam_w_pct', 25))
                facecam_h_pct = float(payload.get('facecam_h_pct', 25))
                watermark_text = payload.get('watermark_text', '')
                watermark_pos = payload.get('watermark_pos', 'top_right')
                audio_volume = int(payload.get('audio_volume', 100))
                resolution = payload.get('resolution', 'source')

                print("\n" + "="*50)
                print("CLIP GENERATION POST REQUEST RECEIVED:")
                print(f"Stream playlist URL: {stream_url}")
                print(f"Start Offset:        {start_offset} seconds")
                print(f"Duration:            {duration_seconds} seconds")
                print(f"Layout Mode:         {layout_mode}")
                print(f"Crop Offset Pct:     {crop_offset_pct}%")
                print(f"Facecam Coordinates: X={facecam_x_pct}%, Y={facecam_y_pct}%, W={facecam_w_pct}%, H={facecam_h_pct}%")
                print(f"Watermark:           '{watermark_text}' ({watermark_pos})")
                print(f"Audio Volume:        {audio_volume}%")
                print(f"Resolution:          {resolution}")
                print("="*50 + "\n")

                # Download HLS segments
                ts_bytes = fetch_and_slice_hls(stream_url, start_offset, duration_seconds)

                params = {
                    'layout_mode': layout_mode,
                    'crop_offset_pct': crop_offset_pct,
                    'facecam_x_pct': facecam_x_pct,
                    'facecam_y_pct': facecam_y_pct,
                    'facecam_w_pct': facecam_w_pct,
                    'facecam_h_pct': facecam_h_pct,
                    'watermark_text': watermark_text,
                    'watermark_pos': watermark_pos,
                    'audio_volume': audio_volume,
                    'resolution': resolution
                }

                # Transcode and save
                clip_bytes, out_filename = process_clip_transcode(ts_bytes, params)

                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "video/mp4")
                self.end_headers()
                
                # Write video bytes to response
                self.wfile.write(clip_bytes)
                print(f"Successfully returned clip {out_filename} ({len(clip_bytes)} bytes).")

            except Exception as e:
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                error_response = json.dumps({"error": f"Invalid request or server error: {str(e)}"})
                self.wfile.write(error_response.encode('utf-8'))
                print(f"Error handling POST request: {e}")
        else:
            self.send_response(404)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"Not Found")

def run(server_class=HTTPServer, handler_class=ClipperMockHandler, port=8000):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Mock/Live Clipper API server running at http://localhost:{port}...")
    print("Press Ctrl+C to terminate the server.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()

if __name__ == '__main__':
    run()
