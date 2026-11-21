import os
import uuid
import time
import json
import threading
import subprocess
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from faster_whisper import WhisperModel
import yt_dlp


# =========================================================
# CONFIG
# =========================================================

BASE = Path("/tmp/ai_clipping_live")
BASE.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI Clipping Cloud + Live")
templates = Jinja2Templates(directory="app/templates")

_model = None
jobs = {}


# =========================================================
# JOB STORAGE
# =========================================================

def job_file(job_id):
    return BASE / job_id / "job.json"


def save_job(job_id):
    try:
        path = job_file(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(jobs[job_id], f, indent=2)

    except Exception:
        pass


def load_job(job_id):
    path = job_file(job_id)

    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            job = json.load(f)

        jobs[job_id] = job
        return job

    except Exception:
        return None


# =========================================================
# WHISPER
# =========================================================

def get_model():
    global _model

    if _model is None:
        _model = WhisperModel(
            "small",
            device="cpu",
            compute_type="int8"
        )

    return _model


# =========================================================
# HIGHLIGHT SCORING
# =========================================================

def highlight_score(text):
    t = text.lower()

    keys = [
        "wow",
        "no way",
        "what",
        "crazy",
        "insane",
        "bro",
        "wait",
        "oh my",
        "clutch",
        "funny",
        "fail",
        "win",
        "lost",
        "kill",
        "police",
        "chase",
        "secret",
        "money",
        "gta",
        "holy",
        "look at",
        "did you see"
    ]

    keyword_score = sum(t.count(k) for k in keys)

    length_score = min(len(text) / 100, 4)

    return keyword_score + length_score


# =========================================================
# VIDEO DURATION
# =========================================================

def get_video_duration(src):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(src)
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        return float(result.stdout.strip())
    except Exception:
        return 0


# =========================================================
# DOWNLOAD / RESOLVE VIDEO
# =========================================================

def download_vod(url, out):

    out.mkdir(parents=True, exist_ok=True)

    url = url.strip()

    # -----------------------------------------------------
    # DIRECT HLS / M3U8
    # -----------------------------------------------------

    if ".m3u8" in url.lower():

        target = out / "source.mp4"

        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                url,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(target)
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0 or not target.exists():
            raise RuntimeError(
                "Unable to read the public HLS stream."
            )

        return target

    # -----------------------------------------------------
    # DIRECT VIDEO
    # -----------------------------------------------------

    direct_extensions = (
        ".mp4",
        ".webm",
        ".mov",
        ".mkv",
        ".avi"
    )

    clean_url = url.lower().split("?")[0]

    if clean_url.endswith(direct_extensions):

        target = out / "source.mp4"

        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                url,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(target)
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0 or not target.exists():
            error = result.stderr[-1000:]

            raise RuntimeError(
                "Unable to download the direct video URL.\n"
                + error
            )

        return target

    # -----------------------------------------------------
    # YT-DLP PUBLIC SOURCES
    # -----------------------------------------------------

    output_template = str(out / "source.%(ext)s")

    opts = {
        "format": (
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080]/best"
        ),

        "outtmpl": output_template,

        "merge_output_format": "mp4",

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "retries": 3,

        "fragment_retries": 3,

        "hls_prefer_native": True,

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/157.0.0.0 Safari/537.36"
            )
        }
    }

    try:

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    except yt_dlp.utils.DownloadError as e:

        error_text = str(e)

        if "Sign in to confirm you're not a bot" in error_text:

            raise RuntimeError(
                "YouTube cannot be accessed from this cloud server. "
                "This video requires YouTube verification/authentication."
            )

        raise RuntimeError(
            "Unable to access this public source: "
            + error_text
        )

    # -----------------------------------------------------
    # FIND DOWNLOADED FILE
    # -----------------------------------------------------

    candidates = [
        p
        for p in out.glob("source.*")
        if p.is_file()
        and p.suffix.lower() not in {
            ".part",
            ".ytdl"
        }
    ]

    if not candidates:

        raise RuntimeError(
            "No playable video was obtained from this URL."
        )

    mp4_files = [
        p
        for p in candidates
        if p.suffix.lower() == ".mp4"
    ]

    if mp4_files:
        return mp4_files[0]

    return candidates[0]


# =========================================================
# RENDER SHORT CLIP
# =========================================================

def render_clip(src, start, end, out, index):

    duration = min(
        45,
        max(
            12,
            end - start
        )
    )

    start = max(
        0,
        start - 3
    )

    target = out / f"clip_{index}.mp4"

    vf = (
        "scale=1080:1920:"
        "force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    )

    subprocess.run(
        [
            "ffmpeg",
            "-y",

            "-ss",
            str(start),

            "-i",
            str(src),

            "-t",
            str(duration),

            "-vf",
            vf,

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "23",

            "-c:a",
            "aac",

            "-movflags",
            "+faststart",

            str(target)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return target


# =========================================================
# FALLBACK CLIPS
# =========================================================

def create_fallback_clips(src, out):

    duration = get_video_duration(src)

    if duration <= 0:
        return []

    clips = []

    # Create up to 5 evenly distributed clips.
    count = min(
        5,
        max(
            1,
            int(duration // 15)
        )
    )

    if count <= 0:
        return []

    segment_length = min(
        30,
        max(
            12,
            duration / count
        )
    )

    for i in range(count):

        start = i * (duration / count)

        end = min(
            duration,
            start + segment_length
        )

        if end - start < 5:
            continue

        try:

            clip = render_clip(
                src,
                start,
                end,
                out,
                i + 1
            )

            clips.append(str(clip))

        except Exception:
            continue

    return clips


# =========================================================
# VOD PROCESSING
# =========================================================

def process_vod(job_id, url):

    job = jobs[job_id]

    out = BASE / job_id

    out.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        # -------------------------------------------------
        # DOWNLOAD
        # -------------------------------------------------

        job["status"] = "Downloading video..."
        save_job(job_id)

        src = download_vod(
            url,
            out
        )

        # -------------------------------------------------
        # TRANSCRIBE
        # -------------------------------------------------

        job["status"] = "Transcribing..."
        save_job(job_id)

        segments, _ = get_model().transcribe(
            str(src),
            beam_size=5
        )

        rows = [
            (
                s.start,
                s.end,
                s.text.strip()
            )

            for s in segments

            if s.text.strip()
        ]

        # -------------------------------------------------
        # FIND HIGHLIGHTS
        # -------------------------------------------------

        candidates = []

        for i in range(len(rows)):

            end_i = min(
                len(rows),
                i + 4
            )

            start = rows[i][0]

            end = rows[end_i - 1][1]

            text = " ".join(
                x[2]
                for x in rows[i:end_i]
            )

            score = highlight_score(text)

            candidates.append(
                (
                    score,
                    start,
                    end
                )
            )

        candidates.sort(
            reverse=True
        )

        # -------------------------------------------------
        # SELECT NON-OVERLAPPING HIGHLIGHTS
        # -------------------------------------------------

        chosen = []

        for score, start, end in candidates:

            if all(
                end <= a or start >= b
                for _, a, b in chosen
            ):

                chosen.append(
                    (
                        score,
                        start,
                        end
                    )
                )

            if len(chosen) >= 5:
                break

        # -------------------------------------------------
        # RENDER
        # -------------------------------------------------

        job["status"] = "Rendering clips..."
        save_job(job_id)

        clips = []

        for i, (_, start, end) in enumerate(
            chosen
        ):

            try:

                clip = render_clip(
                    src,
                    start,
                    end,
                    out,
                    i + 1
                )

                clips.append(
                    str(clip)
                )

            except Exception:
                continue

        # -------------------------------------------------
        # FALLBACK
        # -------------------------------------------------

        if not clips:

            job["status"] = (
                "No strong spoken highlights found. "
                "Creating clips..."
            )

            save_job(job_id)

            clips = create_fallback_clips(
                src,
                out
            )

        # -------------------------------------------------
        # FINAL
        # -------------------------------------------------

        if not clips:

            raise RuntimeError(
                "Unable to create any clips from this video."
            )

        job["clips"] = clips

        job["status"] = "done"

        save_job(job_id)

    except Exception as e:

        job["status"] = (
            "error: "
            + str(e)
        )

        save_job(job_id)


# =========================================================
# LIVE WORKER
# =========================================================

def live_worker(job_id, url):

    job = jobs[job_id]

    out = BASE / job_id

    out.mkdir(
        parents=True,
        exist_ok=True
    )

    stream = out / "live.ts"

    ff = None
    proc = None

    try:

        job["status"] = "Resolving live stream..."
        save_job(job_id)

        with yt_dlp.YoutubeDL({
            "quiet": True,
            "format": "best[height<=1080]/best",
            "noplaylist": True
        }) as y:

            info = y.extract_info(
                url,
                download=False
            )

        stream_url = info.get("url")

        if not stream_url:

            raise RuntimeError(
                "Could not resolve a playable live stream URL."
            )

        ff = open(
            stream,
            "ab"
        )

        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                stream_url,
                "-c",
                "copy",
                "-f",
                "mpegts",
                "-"
            ],
            stdout=ff
        )

        job["status"] = "LIVE monitoring..."
        save_job(job_id)

        last_size = 0
        last_clip = 0
        clip_no = 0

        while not job.get("stop"):

            time.sleep(20)

            if not stream.exists():
                continue

            size = stream.stat().st_size

            if size < 2_000_000:
                continue

            if size == last_size:
                continue

            sample = out / "sample.ts"

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-sseof",
                    "-75",
                    "-i",
                    str(stream),
                    "-c",
                    "copy",
                    str(sample)
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            segments, _ = get_model().transcribe(
                str(sample),
                beam_size=3
            )

            rows = [
                (
                    s.start,
                    s.end,
                    s.text.strip()
                )

                for s in segments

                if s.text.strip()
            ]

            text = " ".join(
                x[2]
                for x in rows
            )

            score = highlight_score(text)

            now = time.time()

            if (
                score >= 3
                and now - last_clip > 45
                and rows
            ):

                clip_no += 1

                target = (
                    out
                    / f"live_clip_{clip_no}.mp4"
                )

                vf = (
                    "scale=1080:1920:"
                    "force_original_aspect_ratio=decrease,"
                    "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
                )

                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-sseof",
                        "-75",
                        "-i",
                        str(stream),
                        "-t",
                        "35",
                        "-vf",
                        vf,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "23",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                        str(target)
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )

                if target.exists():

                    job.setdefault(
                        "clips",
                        []
                    ).append(
                        str(target)
                    )

                    save_job(job_id)

                last_clip = now

            last_size = size

            if clip_no >= 10:

                job["status"] = (
                    "10 live clips created. "
                    "Monitoring paused."
                )

                save_job(job_id)

                break

        if job.get("stop"):

            job["status"] = "stopped"

            save_job(job_id)

    except Exception as e:

        job["status"] = (
            "error: "
            + str(e)
        )

        save_job(job_id)

    finally:

        if proc:

            proc.terminate()

        if ff:

            ff.close()


# =========================================================
# HOME
# =========================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def home(request: Request):

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request
        }
    )


# =========================================================
# GENERATE VIDEO
# =========================================================

@app.post("/generate")
def generate(
    url: str = Form(...)
):

    jid = uuid.uuid4().hex

    jobs[jid] = {
        "type": "video",
        "status": "queued",
        "clips": []
    }

    save_job(jid)

    threading.Thread(
        target=process_vod,
        args=(jid, url),
        daemon=True
    ).start()

    return RedirectResponse(
        f"/job/{jid}",
        status_code=303
    )


# =========================================================
# LIVE
# =========================================================

@app.post("/live")
def live(
    url: str = Form(...)
):

    jid = uuid.uuid4().hex

    jobs[jid] = {
        "type": "live",
        "status": "starting",
        "clips": [],
        "stop": False
    }

    save_job(jid)

    threading.Thread(
        target=live_worker,
        args=(jid, url),
        daemon=True
    ).start()

    return RedirectResponse(
        f"/job/{jid}",
        status_code=303
    )


# =========================================================
# JOB PAGE
# =========================================================

@app.get(
    "/job/{jid}",
    response_class=HTMLResponse
)
def job_page(
    request: Request,
    jid: str
):

    job = jobs.get(jid)

    if not job:

        job = load_job(jid)

    if not job:

        return HTMLResponse(
            "Job not found",
            status_code=404
        )

    return templates.TemplateResponse(
        "job.html",
        {
            "request": request,
            "jid": jid,
            "job": job
        }
    )


# =========================================================
# STOP LIVE
# =========================================================

@app.post("/job/{jid}/stop")
def stop(jid: str):

    job = jobs.get(jid)

    if not job:

        job = load_job(jid)

    if job:

        job["stop"] = True

        jobs[jid] = job

        save_job(jid)

    return RedirectResponse(
        f"/job/{jid}",
        status_code=303
    )


# =========================================================
# DOWNLOAD CLIP
# =========================================================

@app.get("/download")
def download(path: str):

    p = Path(path).resolve()

    base = BASE.resolve()

    try:

        p.relative_to(base)

    except ValueError:

        return HTMLResponse(
            "Invalid file",
            status_code=400
        )

    if not p.exists():

        return HTMLResponse(
            "File not found",
            status_code=404
        )

    return FileResponse(
        p,
        media_type="video/mp4",
        filename=p.name
    )
