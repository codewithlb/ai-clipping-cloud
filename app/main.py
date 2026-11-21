
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

_worker_lock = threading.Lock()
_running_jobs = set()


# =========================================================
# MODEL
# =========================================================

def get_model():
    global _model

    if _model is None:
        print("[AI] Loading Whisper model...", flush=True)

        _model = WhisperModel(
            "small",
            device="cpu",
            compute_type="int8"
        )

        print("[AI] Whisper model loaded.", flush=True)

    return _model


# =========================================================
# JOB STORAGE
# =========================================================

def job_dir(job_id):
    path = BASE / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_file(job_id):
    return job_dir(job_id) / "job.json"


def save_job(job_id):
    try:
        path = job_file(job_id)

        temp = path.with_suffix(".tmp")

        with open(temp, "w", encoding="utf-8") as f:
            json.dump(
                jobs[job_id],
                f,
                indent=2
            )

        temp.replace(path)

    except Exception as e:
        print(f"[JOB] Save failed: {e}", flush=True)


def load_job(job_id):
    path = job_file(job_id)

    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(f"[JOB] Load failed: {e}", flush=True)
        return None


def update_job(job_id, **values):
    if job_id not in jobs:
        return

    jobs[job_id].update(values)
    save_job(job_id)


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

    keyword_score = sum(
        t.count(k)
        for k in keys
    )

    length_score = min(
        len(text) / 100,
        4
    )

    return keyword_score + length_score


# =========================================================
# VIDEO DOWNLOAD
# =========================================================

def download_vod(url, out):
    out.mkdir(
        parents=True,
        exist_ok=True
    )

    print(f"[DOWNLOAD] URL: {url}", flush=True)

    # -----------------------------------------------------
    # DIRECT M3U8
    # -----------------------------------------------------

    if ".m3u8" in url.lower():

        target = out / "source.mp4"

        print(
            "[DOWNLOAD] Public M3U8 detected.",
            flush=True
        )

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

        if (
            result.returncode != 0
            or not target.exists()
            or target.stat().st_size == 0
        ):
            print(
                result.stderr[-3000:],
                flush=True
            )

            raise RuntimeError(
                "Unable to read the public HLS stream."
            )

        print(
            f"[DOWNLOAD] Complete: {target}",
            flush=True
        )

        return target

    # -----------------------------------------------------
    # DIRECT VIDEO FILE
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

        print(
            "[DOWNLOAD] Direct video URL detected.",
            flush=True
        )

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

        if (
            result.returncode != 0
            or not target.exists()
            or target.stat().st_size == 0
        ):
            print(
                result.stderr[-3000:],
                flush=True
            )

            raise RuntimeError(
                "Unable to download the direct video URL."
            )

        print(
            f"[DOWNLOAD] Complete: {target}",
            flush=True
        )

        return target

    # -----------------------------------------------------
    # YT-DLP
    # -----------------------------------------------------

    print(
        "[DOWNLOAD] Trying yt-dlp...",
        flush=True
    )

    output_template = str(
        out / "source.%(ext)s"
    )

    opts = {
        "format": (
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080]/best"
        ),

        "outtmpl": output_template,

        "merge_output_format": "mp4",

        "noplaylist": True,

        "quiet": False,

        "no_warnings": False,

        "retries": 3,

        "fragment_retries": 3,

        "hls_prefer_native": True,

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/157.0.0.0 "
                "Safari/537.36"
            )
        }
    }

    try:

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    except yt_dlp.utils.DownloadError as e:

        error_text = str(e)

        print(
            f"[DOWNLOAD ERROR] {error_text}",
            flush=True
        )

        if (
            "Sign in to confirm you're not a bot"
            in error_text
        ):
            raise RuntimeError(
                "This YouTube video cannot be accessed "
                "from the cloud server because YouTube "
                "requires authentication or verification."
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
        if (
            p.is_file()
            and p.suffix.lower()
            not in {
                ".part",
                ".ytdl"
            }
        )
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

    result = (
        mp4_files[0]
        if mp4_files
        else candidates[0]
    )

    print(
        f"[DOWNLOAD] Complete: {result}",
        flush=True
    )

    return result


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

    if result.returncode != 0:
        return 0

    try:
        return float(result.stdout.strip())

    except Exception:
        return 0


# =========================================================
# CLIP RENDER
# =========================================================

def render_clip(
    src,
    start,
    end,
    out,
    index
):

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

    target = (
        out / f"clip_{index}.mp4"
    )

    vf = (
        "scale=1080:1920:"
        "force_original_aspect_ratio=decrease,"
        "pad=1080:1920:"
        "(ow-iw)/2:(oh-ih)/2"
    )

    print(
        f"[CLIP] Creating clip {index}: "
        f"{start:.1f}s - {end:.1f}s",
        flush=True
    )

    result = subprocess.run(
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if (
        result.returncode != 0
        or not target.exists()
        or target.stat().st_size == 0
    ):

        print(
            result.stderr[-3000:],
            flush=True
        )

        raise RuntimeError(
            f"Failed to render clip {index}"
        )

    print(
        f"[CLIP] Created: {target}",
        flush=True
    )

    return target


# =========================================================
# FALLBACK CLIPS
# =========================================================

def create_fallback_clips(
    src,
    out,
    duration
):

    clips = []

    if duration <= 0:
        return clips

    clip_length = min(
        30,
        max(
            12,
            duration / 5
        )
    )

    max_clips = min(
        5,
        max(
            1,
            int(duration // clip_length)
        )
    )

    if duration < 12:
        max_clips = 1
        clip_length = duration

    for i in range(max_clips):

        start = (
            i *
            max(
                1,
                (duration - clip_length)
                / max(1, max_clips - 1)
            )
        )

        end = min(
            duration,
            start + clip_length
        )

        clips.append(
            str(
                render_clip(
                    src,
                    start,
                    end,
                    out,
                    i + 1
                )
            )
        )

    return clips


# =========================================================
# VIDEO PROCESSING
# =========================================================

def process_vod(
    job_id,
    url
):

    with _worker_lock:

        if job_id in _running_jobs:
            print(
                f"[WORKER] Job already running: {job_id}",
                flush=True
            )
            return

        _running_jobs.add(job_id)

    try:

        if job_id not in jobs:
            loaded = load_job(job_id)

            if loaded:
                jobs[job_id] = loaded

        if job_id not in jobs:
            print(
                f"[WORKER] Job missing: {job_id}",
                flush=True
            )
            return

        job = jobs[job_id]

        out = job_dir(job_id)

        update_job(
            job_id,
            status="Downloading video..."
        )

        print(
            f"[WORKER] Starting job {job_id}",
            flush=True
        )

        # -------------------------------------------------
        # DOWNLOAD
        # -------------------------------------------------

        src = download_vod(
            url,
            out
        )

        update_job(
            job_id,
            source=str(src),
            status="Transcribing..."
        )

        print(
            "[AI] Starting transcription...",
            flush=True
        )

        # -------------------------------------------------
        # TRANSCRIPTION
        # -------------------------------------------------

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

        print(
            f"[AI] Transcript segments: {len(rows)}",
            flush=True
        )

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

            score = highlight_score(
                text
            )

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

        chosen = []

        for score, start, end in candidates:

            if all(
                end <= a
                or start >= b
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
        # FALLBACK
        # -------------------------------------------------

        if not chosen:

            print(
                "[AI] No strong highlights found.",
                flush=True
            )

            duration = get_video_duration(
                src
            )

            update_job(
                job_id,
                status="Creating clips..."
            )

            clips = create_fallback_clips(
                src,
                out,
                duration
            )

        else:

            update_job(
                job_id,
                status="Rendering clips..."
            )

            clips = []

            for i, (_, start, end) in enumerate(
                chosen
            ):

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

        # -------------------------------------------------
        # DONE
        # -------------------------------------------------

        update_job(
            job_id,
            clips=clips,
            status="done"
        )

        print(
            f"[WORKER] DONE: {job_id}",
            flush=True
        )

    except Exception as e:

        print(
            f"[WORKER ERROR] {job_id}: {e}",
            flush=True
        )

        update_job(
            job_id,
            status="error: " + str(e)
        )

    finally:

        with _worker_lock:
            _running_jobs.discard(job_id)


# =========================================================
# RECOVER UNFINISHED JOBS
# =========================================================

def recover_jobs():

    print(
        "[RECOVERY] Checking unfinished jobs...",
        flush=True
    )

    if not BASE.exists():
        return

    for folder in BASE.iterdir():

        if not folder.is_dir():
            continue

        job_id = folder.name

        data = load_job(job_id)

        if not data:
            continue

        jobs[job_id] = data

        status = data.get(
            "status",
            ""
        )

        job_type = data.get(
            "type"
        )

        url = data.get(
            "url"
        )

        # Only retry unfinished video jobs
        if (
            job_type == "video"
            and url
            and status not in {
                "done",
                "stopped"
            }
            and not status.startswith(
                "error:"
            )
        ):

            print(
                f"[RECOVERY] Resuming job: {job_id}",
                flush=True
            )

            threading.Thread(
                target=process_vod,
                args=(
                    job_id,
                    url
                ),
                daemon=True
            ).start()


# =========================================================
# LIVE WORKER
# =========================================================

def live_worker(
    job_id,
    url
):

    with _worker_lock:

        if job_id in _running_jobs:
            return

        _running_jobs.add(job_id)

    job = jobs[job_id]

    out = job_dir(job_id)

    stream = (
        out / "live.ts"
    )

    ff = None
    proc = None

    try:

        update_job(
            job_id,
            status="Resolving live stream..."
        )

        with yt_dlp.YoutubeDL({
            "quiet": True,
            "format": "best[height<=1080]/best",
            "noplaylist": True
        }) as y:

            info = y.extract_info(
                url,
                download=False
            )

        stream_url = info.get(
            "url"
        )

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

        update_job(
            job_id,
            status="LIVE monitoring..."
        )

        last_size = 0
        last_clip = 0
        clip_no = 0

        while not job.get("stop"):

            time.sleep(20)

            if not stream.exists():
                continue

            size = stream.stat().st_size

            if (
                size < 2_000_000
                or size == last_size
            ):
                continue

            sample = (
                out / "sample.ts"
            )

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

            score = highlight_score(
                text
            )

            now = time.time()

            if (
                score >= 3
                and now - last_clip > 45
                and rows
            ):

                clip_no += 1

                target = (
                    out /
                    f"live_clip_{clip_no}.mp4"
                )

                vf = (
                    "scale=1080:1920:"
                    "force_original_aspect_ratio=decrease,"
                    "pad=1080:1920:"
                    "(ow-iw)/2:(oh-ih)/2"
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

                    save_job(
                        job_id
                    )

                last_clip = now

            last_size = size

            if clip_no >= 10:

                update_job(
                    job_id,
                    status="10 live clips created. Monitoring paused."
                )

                break

        if job.get("stop"):

            update_job(
                job_id,
                status="stopped"
            )

    except Exception as e:

        update_job(
            job_id,
            status="error: " + str(e)
        )

    finally:

        if proc:
            proc.terminate()

        if ff:
            ff.close()

        with _worker_lock:
            _running_jobs.discard(job_id)


# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
def startup_event():

    print(
        "[STARTUP] AI Clipping Cloud started.",
        flush=True
    )

    # Small delay so FastAPI can finish startup
    threading.Thread(
        target=lambda: (
            time.sleep(2),
            recover_jobs()
        ),
        daemon=True
    ).start()


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
        "url": url,
        "status": "queued",
        "clips": []
    }

    save_job(jid)

    print(
        f"[JOB] Created video job: {jid}",
        flush=True
    )

    threading.Thread(
        target=process_vod,
        args=(
            jid,
            url
        ),
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
        "url": url,
        "status": "starting",
        "clips": [],
        "stop": False
    }

    save_job(jid)

    threading.Thread(
        target=live_worker,
        args=(
            jid,
            url
        ),
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

    # If not in memory, try job.json
    if not job:

        job = load_job(jid)

        if job:
            jobs[jid] = job

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
# STOP
# =========================================================

@app.post("/job/{jid}/stop")
def stop(jid: str):

    if jid in jobs:

        jobs[jid]["stop"] = True

        save_job(jid)

    return RedirectResponse(
        f"/job/{jid}",
        status_code=303
    )


# =========================================================
# DOWNLOAD
# =========================================================

@app.get("/download")
def download(
    path: str
):

    try:

        p = Path(path).resolve()
        base = BASE.resolve()

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
