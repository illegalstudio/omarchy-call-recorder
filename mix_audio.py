"""Mix completed, timeline-aligned FLAC tracks without live audio deadlines."""

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


SAMPLE_RATE = 48000
BITRATE = "192k"
# FFmpeg 6.1's amix can discard queued samples from its first input at EOF.
# Pad both tracks to the same sample count, interleave them, then sum in float
# so neither the shorter track's tail nor overlapping peaks are lost.
MIX_FILTER = (
    "[0:a]aformat=sample_fmts=flt:channel_layouts=mono,apad=whole_len={samples}[mic];"
    "[1:a]aformat=sample_fmts=flt:channel_layouts=mono,apad=whole_len={samples}[desktop];"
    "[mic][desktop]amerge=inputs=2,pan=mono|c0=c0+c1,"
    "alimiter=limit=0.89:level=false:latency=true[out]"
)


def check_dependencies():
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise RuntimeError("Install the ffmpeg package before recording (missing " + ", ".join(missing) + ")")


def probe_audio(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if len(streams) != 1 or streams[0].get("codec_type") != "audio":
        raise RuntimeError(f"Expected one audio stream: {path}")
    stream = streams[0]
    duration = float(stream.get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"Empty or unfinished audio track: {path}")
    return stream


def mix_tracks(microphone, desktop, output, progress=None):
    """Publish an MP3 only after encoding and verification succeed.

    Inputs must already share the recording timeline. New recordings use
    audiorate before FLAC encoding; arbitrary legacy FLAC files may not align.
    Source files and any existing output are never overwritten.
    """
    check_dependencies()
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    sources = [probe_audio(path) for path in (microphone, desktop)]
    for source in sources:
        if (source["codec_name"] != "flac" or int(source["sample_rate"]) != SAMPLE_RATE
                or int(source["channels"]) != 1):
            raise RuntimeError("Mixing requires timeline-aligned, mono 48 kHz FLAC tracks")
    duration = max(float(source["duration"]) for source in sources)
    mix_filter = MIX_FILTER.format(samples=round(duration * SAMPLE_RATE))
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}-", suffix=".mp3", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-xerror", "-nostdin", "-y",
            "-i", str(microphone), "-i", str(desktop),
            "-filter_complex", mix_filter, "-map", "[out]",
            "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "libmp3lame", "-b:a", BITRATE,
            "-write_xing", "1", "-progress", "pipe:1", "-nostats", str(temporary),
        ]
        last_percent = -1
        with tempfile.TemporaryFile(mode="w+t") as errors:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True) as process:
                for line in process.stdout:
                    key, _, value = line.strip().partition("=")
                    if key == "out_time_us" and value.isdigit():
                        percent = min(99, int(int(value) / (duration * 10000)))
                        if progress and percent != last_percent:
                            progress(percent)
                            last_percent = percent
                returncode = process.wait()
            if returncode:
                errors.seek(0)
                raise RuntimeError("MP3 encoding failed: " + errors.read()[-4000:].strip())

        encoded = probe_audio(temporary)
        if encoded["codec_name"] != "mp3" or abs(float(encoded["duration"]) - duration) > 0.1:
            raise RuntimeError("Encoded MP3 is incomplete or has an unexpected duration")
        # Decode the whole result before announcing success, not just its header.
        subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(temporary), "-f", "null", "-"],
            check=True, capture_output=True,
        )
        # Hard-link publication is atomic and fails if a competing file appeared.
        # Both paths are on the same filesystem because mkstemp uses output.parent.
        os.link(temporary, output)
        if progress:
            progress(100)
        return {
            "method": "offline-flac-mix", "sample_rate": SAMPLE_RATE, "bitrate": BITRATE,
            "duration_seconds": duration, "filter": mix_filter,
            "source_duration_seconds": [float(source["duration"]) for source in sources],
        }
    finally:
        temporary.unlink(missing_ok=True)
