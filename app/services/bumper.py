"""Attach a reusable intro/outro clip ("bumper") to a finished video.

The bumper is a video the user uploads once (for example a channel opening or
a "like and subscribe" ending). It is stored by file name under
``storage/bumpers`` and joined to the final video with a smooth crossfade on
both picture and sound.

Only bare file names inside the bumper directory are accepted. Task params
also come from the HTTP API, so accepting arbitrary paths would let a request
copy any local video file into a downloadable result.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from loguru import logger

from app.utils import utils

BUMPER_POSITIONS = ("none", "start", "end", "both")
BUMPER_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv", ".webm")
DEFAULT_TRANSITION_SECONDS = 1.0
_AUDIO_RATE = 44100
_INVALID_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


def bumper_dir(create: bool = False) -> str:
    return utils.storage_dir("bumpers", create=create)


def sanitize_bumper_name(file_name: str) -> str:
    """Turn an uploaded file name into a safe bare name, or "" if unusable."""
    name = os.path.basename(str(file_name or "").replace("\\", "/")).strip()
    name = _INVALID_NAME_CHARS.sub("_", name).strip(" .")
    if not name or os.path.splitext(name)[1].lower() not in BUMPER_EXTENSIONS:
        return ""
    return name[:200]


def list_bumpers() -> list[str]:
    directory = bumper_dir()
    if not os.path.isdir(directory):
        return []
    return sorted(
        name for name in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, name))
        and os.path.splitext(name)[1].lower() in BUMPER_EXTENSIONS
        and not name.startswith(".")
    )


def resolve_bumper(file_name: str) -> str | None:
    """Absolute path of a stored bumper, or None for empty/unsafe/missing names."""
    name = str(file_name or "").strip()
    if not name or sanitize_bumper_name(name) != name:
        return None
    directory = os.path.realpath(bumper_dir())
    candidate = os.path.realpath(os.path.join(directory, name))
    if os.path.dirname(candidate) != directory or not os.path.isfile(candidate):
        return None
    return candidate


def save_bumper(file_name: str, data: bytes) -> str:
    """Store uploaded bytes and return the bare name to put in task params."""
    name = sanitize_bumper_name(file_name)
    if not name:
        raise ValueError("unsupported bumper file")
    directory = bumper_dir(create=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".bumper-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, os.path.join(directory, name))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return name


@dataclass
class _MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _probe(path: str) -> _MediaInfo:
    from moviepy import VideoFileClip

    clip = VideoFileClip(path)
    try:
        width, height = clip.size
        return _MediaInfo(
            duration=float(clip.duration or 0),
            width=int(width),
            height=int(height),
            fps=float(clip.fps or 30),
            has_audio=clip.audio is not None,
        )
    finally:
        clip.close()


def _segments(position: str) -> list[str]:
    return {
        "start": ["bumper", "main"],
        "end": ["main", "bumper"],
        "both": ["bumper", "main", "bumper"],
    }[position]


def build_filter_graph(
    main: _MediaInfo,
    bumper: _MediaInfo,
    position: str,
    transition: float,
) -> tuple[str, float, list[str]]:
    """Return (filter_complex, crossfade seconds, extra lavfi inputs).

    Inputs: 0 = main video, 1 = bumper, then generated silence for any input
    without an audio track. Both clips are scaled into the main video's frame
    (letterboxed, never cropped), resampled to its frame rate and joined with
    xfade/acrossfade.
    """
    order = _segments(position)
    fps = round(main.fps) or 30
    fade = max(0.1, min(transition, bumper.duration / 2, main.duration / 2))
    w, h = main.width, main.height
    parts, extra_inputs = [], []
    silence_index = 2
    for name, index, info in (("main", 0, main), ("bumper", 1, bumper)):
        uses = order.count(name)
        parts.append(
            f"[{index}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},"
            f"format=yuv420p,settb=AVTB"
            + (f",split={uses}" if uses > 1 else "")
            + "".join(f"[v{name}{k}]" for k in range(uses))
        )
        if info.has_audio:
            source = f"[{index}:a]"
        else:
            source = f"[{silence_index}:a]"
            extra_inputs.append(
                f"anullsrc=r={_AUDIO_RATE}:cl=stereo:d={info.duration:.3f}"
            )
            silence_index += 1
        parts.append(
            f"{source}aformat=sample_fmts=fltp:sample_rates={_AUDIO_RATE}:"
            f"channel_layouts=stereo,atrim=0:{info.duration:.3f},asetpts=PTS-STARTPTS"
            + (f",asplit={uses}" if uses > 1 else "")
            + "".join(f"[a{name}{k}]" for k in range(uses))
        )

    used = {"main": 0, "bumper": 0}
    labels = []
    for name in order:
        k = used[name]; used[name] += 1
        labels.append((f"[v{name}{k}]", f"[a{name}{k}]", main if name == "main" else bumper))

    v_prev, a_prev, info0 = labels[0]
    offset = info0.duration - fade
    for step, (v_next, a_next, info) in enumerate(labels[1:], 1):
        v_out, a_out = f"[vx{step}]", f"[ax{step}]"
        parts.append(
            f"{v_prev}{v_next}xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}{v_out}"
        )
        parts.append(f"{a_prev}{a_next}acrossfade=d={fade:.3f}:c1=tri:c2=tri{a_out}")
        v_prev, a_prev = v_out, a_out
        offset += info.duration - fade
    parts.append(f"{v_prev}null[vout]")
    parts.append(f"{a_prev}anull[aout]")
    return ";".join(parts), fade, extra_inputs


def _codec_args(codec: str) -> list[str]:
    if codec == "libx264":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "19"]
    return ["-c:v", codec, "-b:v", "10M", "-maxrate", "14M", "-bufsize", "20M"]


def attach_bumper(
    video_path: str,
    bumper_name: str,
    position: str,
    transition: float = DEFAULT_TRANSITION_SECONDS,
) -> bool:
    """Join the stored bumper to ``video_path`` in place. Returns True on success.

    Failures are logged and leave the original video untouched, because the
    narrated video is the valuable part of the task.
    """
    if position not in BUMPER_POSITIONS or position == "none":
        return False
    bumper_path = resolve_bumper(bumper_name)
    if not bumper_path:
        logger.warning(f"bumper not found or not allowed: {bumper_name!r}")
        return False

    from app.services import video as video_service

    try:
        main_info, bumper_info = _probe(video_path), _probe(bumper_path)
    except Exception as exc:
        logger.warning(f"failed to read bumper or video: {exc}")
        return False
    if main_info.duration <= 0 or bumper_info.duration <= 0:
        logger.warning("bumper or video has no duration; skip bumper")
        return False

    graph, fade, extra_inputs = build_filter_graph(main_info, bumper_info, position, transition)
    output_dir = os.path.dirname(os.path.abspath(video_path))
    descriptor, temp_output = tempfile.mkstemp(prefix=".bumper-", suffix=".mp4", dir=output_dir)
    os.close(descriptor)
    ffmpeg = utils.get_ffmpeg_binary()
    preferred = video_service._get_effective_video_codec()
    codecs = [preferred] if preferred == "libx264" else [preferred, "libx264"]
    try:
        for codec in codecs:
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                       "-i", video_path, "-i", bumper_path]
            for source in extra_inputs:
                command += ["-f", "lavfi", "-i", source]
            command += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
                        *_codec_args(codec), "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", temp_output]
            logger.info(
                f"attaching bumper: position={position}, file={bumper_name}, "
                f"crossfade={fade:.2f}s, codec={codec}"
            )
            result = subprocess.run(command, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
            if result.returncode == 0 and os.path.getsize(temp_output) > 0:
                os.replace(temp_output, video_path)
                logger.success(f"bumper attached: {video_path}")
                return True
            logger.warning(f"bumper encode failed with {codec}: {result.stderr.strip()[-600:]}")
        return False
    finally:
        if os.path.exists(temp_output):
            os.unlink(temp_output)
