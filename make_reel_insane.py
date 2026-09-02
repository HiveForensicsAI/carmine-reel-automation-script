#!/usr/bin/env python3
"""Insane 9:16 1080p Instagram Reel builder.

Wilder sibling of make_reel.py: faster cuts, punch zooms, handheld shake,
RGB splits, reverse/speed ramps, mirror splits, invert flashes, film grades,
and smash transitions (pixelize, squeeze, circlecrop, white flashes).

Usage:
  python3 make_reel_insane.py --duration 20
  python3 make_reel_insane.py --duration 30 --count 2 --seed 9
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


WIDTH = 1080
HEIGHT = 1920
FPS = 30
MIN_CUT = 0.28
MAX_CUT = 0.72

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}

# The gnarly xfade set on purpose. The original script avoids these.
TRANSITIONS = (
    "pixelize",
    "squeezeh",
    "squeezev",
    "circlecrop",
    "rectcrop",
    "distance",
    "radial",
    "hblur",
    "fadewhite",
    "fadeblack",
    "fadegrays",
    "dissolve",
    "circleopen",
    "circleclose",
    "horzopen",
    "vertopen",
    "vertclose",
    "horzclose",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "diagtl",
    "diagtr",
    "hlslice",
    "hrslice",
    "vuslice",
    "vdslice",
    "wipetl",
    "wipebr",
)

FLASH_COLORS = (
    "0xFFFFFF",
    "0x000000",
    "0xFFE7C2",
    "0xFF2D2D",
    "0x00F0FF",
    "0xC8FF00",
    "0xFF7A18",
)


@dataclass
class Source:
    path: Path
    duration: float
    width: int
    height: int
    has_audio: bool


@dataclass
class Shot:
    source: Path
    start: float
    duration: float
    path: Path | None = None
    kind: str = "video"  # video | flash
    tags: list[str] = field(default_factory=list)


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def log(msg: str) -> None:
    print(msg, flush=True)


def ffprobe_media(path: Path) -> Source | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = run(cmd)
    except subprocess.CalledProcessError:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        return None
    audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        return None
    return Source(
        path=path,
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        has_audio=audio,
    )


def probe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(path),
    ]
    proc = run(cmd)
    return float(proc.stdout.strip())


def probe_frames(path: Path) -> int:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=nb_frames",
        "-of",
        "csv=p=0",
        str(path),
    ]
    proc = run(cmd)
    raw = (proc.stdout or "").strip().split(",")[0].strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, int(round(probe_duration(path) * FPS)))


def find_sources(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXTS:
            continue
        name = p.name
        if name.startswith("reel_") or name.startswith("reel_insane_"):
            continue
        files.append(p)
    return files


def quantize_frames(seconds: float, fps: int = FPS) -> float:
    frames = max(1, int(round(seconds * fps)))
    return frames / fps


def slice_source(src: Source, rng: random.Random, min_cut: float, max_cut: float) -> list[Shot]:
    pad = min(0.06, src.duration * 0.02)
    t = pad
    end = src.duration - pad
    shots: list[Shot] = []
    abs_min = quantize_frames(0.12)

    while True:
        remaining = end - t
        if remaining < abs_min - 1e-6:
            break
        if rng.random() < 0.16:
            lo, hi = 0.12, 0.24
        else:
            lo, hi = min_cut, max_cut
        lo_q = quantize_frames(lo)
        hi_q = quantize_frames(hi)
        if remaining <= hi_q + 1e-6:
            dur = quantize_frames(remaining)
            if dur >= abs_min - 1e-6:
                shots.append(Shot(source=src.path, start=t, duration=dur))
            break
        leave = abs_min
        cap = min(hi_q, remaining - leave)
        if cap < lo_q:
            dur = quantize_frames(remaining)
            if dur >= abs_min - 1e-6:
                shots.append(Shot(source=src.path, start=t, duration=dur))
            break
        dur = quantize_frames(rng.uniform(lo_q, cap))
        shots.append(Shot(source=src.path, start=t, duration=dur))
        t += dur
    return shots


def _cover() -> list[str]:
    return [
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase:force_divisible_by=2",
        "setsar=1",
    ]


def _grade(rng: random.Random) -> tuple[str, list[str]]:
    pick = rng.choice(
        [
            "fire",
            "ice",
            "bleach",
            "candy",
            "teal",
            "crush",
            "vintage",
            "night",
            "gold",
        ]
    )
    grades = {
        "fire": [
            "hue=h=18:s=1.45",
            "eq=contrast=1.18:saturation=1.28:gamma=1.08:brightness=0.02",
        ],
        "ice": [
            "hue=h=-22:s=0.88",
            "eq=contrast=1.12:saturation=0.92:gamma=0.94:brightness=0.03",
        ],
        "bleach": [
            "eq=contrast=1.28:saturation=0.32:brightness=0.07:gamma=1.05",
        ],
        "candy": [
            "hue=h=32:s=1.7",
            "eq=contrast=1.16:saturation=1.35:brightness=0.02",
        ],
        "teal": [
            "colorbalance=rs=0.22:bs=-0.22:rm=0.08:bm=-0.12:rh=0.1:bh=-0.08",
            "eq=contrast=1.16:saturation=1.22",
        ],
        "crush": [
            "eq=contrast=1.35:gamma=0.88:saturation=1.15:brightness=-0.02",
        ],
        "vintage": [
            "curves=vintage",
            "eq=contrast=1.08:saturation=0.9",
        ],
        "night": [
            "hue=h=-12:s=1.15",
            "eq=brightness=-0.06:gamma=0.86:contrast=1.2:saturation=1.18",
        ],
        "gold": [
            "hue=h=12:s=1.2",
            "eq=gamma=1.12:contrast=1.1:saturation=1.18:brightness=0.04",
        ],
    }
    return pick, grades[pick]


def build_effect_chain(rng: random.Random, n_frames: int) -> tuple[str | None, str | None, list[str]]:
    """Return (vf, filter_complex, tags). Exactly one of vf/fc is set."""
    tags: list[str] = []
    n = max(6, n_frames)

    motion_names = ["punch", "shake", "dutch", "whip", "speed", "reverse", "stutter", "hold"]
    motion_weights = [18, 16, 10, 10, 12, 10, 8, 16]
    if n_frames < 10:
        motion_names = ["punch", "shake", "dutch", "whip", "hold"]
        motion_weights = [22, 22, 12, 16, 28]
    motion = rng.choices(motion_names, weights=motion_weights, k=1)[0]
    optical = rng.choices(
        ["none", "chroma", "rgb", "smear", "invert", "lens", "flashin"],
        weights=[28, 16, 10, 12, 10, 12, 12],
        k=1,
    )[0]
    layout = rng.choices(["none", "hflip", "mirror"], weights=[70, 18, 12], k=1)[0]
    grade_name, grade_filters = _grade(rng)
    tags.extend([motion, optical, layout, grade_name])

    # Mirror needs a graph. Keep the rest of the look simpler on that path.
    if layout == "mirror":
        fc = (
            f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase:force_divisible_by=2,"
            f"crop={WIDTH}:{HEIGHT},split[l][r];"
            f"[l]crop=540:{HEIGHT}:0:0[lc];"
            f"[r]crop=540:{HEIGHT}:540:0,hflip[rc];"
            f"[lc][rc]hstack=inputs=2,"
            f"{','.join(grade_filters)},"
            f"eq=contrast=1.08,"
            f"chromashift=cbh=5:crh=-6,"
            f"noise=alls=6:allf=t+u,"
            f"fps={FPS},format=yuv420p,setpts=PTS-STARTPTS[vout]"
        )
        return None, fc, tags

    chain = _cover()
    extra_zoom = rng.uniform(1.12, 1.28)

    if motion == "punch":
        extra_zoom = rng.uniform(1.38, 1.55)
        chain.append(f"scale=iw*{extra_zoom:.4f}:ih*{extra_zoom:.4f}")
        chain.append(
            f"scale=w='iw*(1.0+0.22*n/{n})':h='ih*(1.0+0.22*n/{n})':eval=frame"
        )
        chain.append(f"crop={WIDTH}:{HEIGHT}")
    elif motion == "shake":
        extra_zoom = rng.uniform(1.18, 1.32)
        amp_x = rng.randint(12, 22)
        amp_y = rng.randint(9, 18)
        sx = rng.uniform(1.6, 2.6)
        sy = rng.uniform(1.4, 2.4)
        chain.append(f"scale=iw*{extra_zoom:.4f}:ih*{extra_zoom:.4f}")
        chain.append(
            f"crop={WIDTH}:{HEIGHT}:"
            f"'min(iw-{WIDTH},max(0,(iw-{WIDTH})/2+{amp_x}*sin(n/{sx:.2f})))':"
            f"'min(ih-{HEIGHT},max(0,(ih-{HEIGHT})/2+{amp_y}*cos(n/{sy:.2f})))'"
        )
    elif motion == "dutch":
        ang = rng.choice([-1, 1]) * rng.uniform(0.06, 0.14)
        extra_zoom = rng.uniform(1.22, 1.36)
        chain.append(f"scale=iw*{extra_zoom:.4f}:ih*{extra_zoom:.4f}")
        chain.append(f"rotate={ang:.4f}:fillcolor=black")
        chain.append(f"crop={WIDTH}:{HEIGHT}")
    elif motion == "whip":
        extra_zoom = rng.uniform(1.2, 1.34)
        chain.append(f"scale=iw*{extra_zoom:.4f}:ih*{extra_zoom:.4f}")
        chain.append(
            f"crop={WIDTH}:{HEIGHT}:"
            f"'(iw-{WIDTH})/2+28*sin(n/1.3)':"
            f"'(ih-{HEIGHT})/2'"
        )
        chain.append("boxblur=lr=10:lp=1:enable='lt(n,4)'")
    else:
        pan_x = rng.uniform(0.2, 0.8)
        pan_y = rng.uniform(0.2, 0.8)
        chain.append(f"scale=iw*{extra_zoom:.4f}:ih*{extra_zoom:.4f}")
        chain.append(
            f"crop={WIDTH}:{HEIGHT}:(iw-{WIDTH})*{pan_x:.3f}:(ih-{HEIGHT})*{pan_y:.3f}"
        )

    if layout == "hflip":
        chain.append("hflip")

    chain.append(f"fps={FPS}")

    if motion == "reverse":
        chain.append("reverse")
        chain.append("setpts=PTS-STARTPTS")
    elif motion == "speed":
        # 0.72 = slow-mo, 1.45 = slam. Bias slightly fast.
        speed = rng.choice([0.78, 0.85, 1.18, 1.28, 1.38, 1.5])
        chain.append(f"setpts={1.0 / speed:.4f}*PTS")
        tags.append(f"spd{speed:.2f}")
    elif motion == "stutter":
        start = max(1, min(max(1, n - 4), rng.randint(2, 5)))
        chain.append(f"loop=loop=2:size=2:start={start}")
        chain.append(f"setpts=N/{FPS}/TB")

    chain.extend(grade_filters)

    if optical == "chroma":
        chain.append(
            f"chromashift=cbh={rng.randint(6, 14)}:crh={-rng.randint(6, 14)}:"
            f"cbv={-rng.randint(3, 8)}:crv={rng.randint(3, 8)}"
        )
    elif optical == "rgb":
        chain.append("format=rgba")
        chain.append(
            f"rgbashift=rh={rng.randint(6, 14)}:bh={-rng.randint(6, 14)}:"
            f"rv={rng.randint(2, 6)}:bv={-rng.randint(2, 6)}"
        )
    elif optical == "smear":
        chain.append("tmix=frames=5:weights='1 2 3 2 1'")
    elif optical == "invert":
        chain.append("negate=enable='lt(mod(n\\,9)\\,2)'")
    elif optical == "lens":
        k1 = -rng.uniform(0.12, 0.22)
        chain.append(f"lenscorrection=k1={k1:.3f}:k2=0.04")
    elif optical == "flashin":
        color = rng.choice(["white", "black", "0xFFCC88"])
        chain.append(f"fade=t=in:st=0:d=0.07:color={color}")

    if rng.random() < 0.55:
        chain.append(f"vignette={rng.uniform(0.55, 0.95):.2f}*PI/4")
    chain.append(f"unsharp=5:5:{rng.uniform(0.4, 0.9):.2f}:5:5:0.0")
    chain.append(f"noise=alls={rng.randint(4, 9)}:allf=t+u")
    chain.append("format=yuv420p")
    chain.append("setpts=PTS-STARTPTS")
    return ",".join(chain), None, tags


def encode_shot(shot: Shot, dest: Path, rng_seed: int, preset: str, crf: int) -> Shot:
    rng = random.Random(rng_seed)
    n_frames = max(4, int(round(shot.duration * FPS)))
    vf, fc, tags = build_effect_chain(rng, n_frames)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(shot.source),
        "-ss",
        f"{shot.start:.4f}",
        "-t",
        f"{shot.duration:.4f}",
        "-an",
    ]
    if fc:
        cmd.extend(["-filter_complex", fc, "-map", "[vout]"])
    else:
        cmd.extend(["-vf", vf or "null"])
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-r",
            str(FPS),
            "-g",
            str(FPS),
            "-keyint_min",
            str(FPS),
            "-sc_threshold",
            "0",
            "-video_track_timescale",
            "30000",
            "-vsync",
            "cfr",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    run(cmd)
    frames = probe_frames(dest)
    return Shot(
        source=shot.source,
        start=shot.start,
        duration=frames / FPS,
        path=dest,
        kind="video",
        tags=tags,
    )


def encode_flash(dest: Path, color: str, frames: int, preset: str, crf: int) -> Shot:
    dur = frames / FPS
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={WIDTH}x{HEIGHT}:d={dur:.4f}:r={FPS}",
        "-vf",
        f"noise=alls=4:allf=t,format=yuv420p,setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-vsync",
        "cfr",
        "-video_track_timescale",
        "30000",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    run(cmd)
    actual = probe_frames(dest) / FPS
    return Shot(
        source=dest,
        start=0.0,
        duration=actual,
        path=dest,
        kind="flash",
        tags=["flash", color],
    )


def smart_shuffle(shots: list[Shot], rng: random.Random) -> list[Shot]:
    items = list(shots)
    rng.shuffle(items)
    for i in range(1, len(items)):
        if items[i].source == items[i - 1].source:
            for j in range(i + 1, len(items)):
                if items[j].source != items[i - 1].source:
                    items[i], items[j] = items[j], items[i]
                    break
    return items


def inject_flashes(shots: list[Shot], flashes: list[Shot], rng: random.Random, chance: float) -> list[Shot]:
    if not flashes:
        return list(shots)
    out: list[Shot] = []
    for i, shot in enumerate(shots):
        out.append(shot)
        if i < len(shots) - 1 and rng.random() < chance:
            out.append(rng.choice(flashes))
    return out


def pick_junctions(
    shots: list[Shot],
    rng: random.Random,
    chance: float,
    trans_min: float,
    trans_max: float,
) -> list[tuple[str, float] | None]:
    junctions: list[tuple[str, float] | None] = []
    for i in range(len(shots) - 1):
        a, b = shots[i], shots[i + 1]
        if a.kind == "flash" or b.kind == "flash":
            junctions.append(None)
            continue
        max_allowed = min(a.duration, b.duration) * 0.42
        if max_allowed < trans_min or rng.random() > chance:
            junctions.append(None)
            continue
        td = min(quantize_frames(rng.uniform(trans_min, trans_max)), quantize_frames(max_allowed))
        if td < trans_min * 0.75:
            junctions.append(None)
            continue
        junctions.append((rng.choice(TRANSITIONS), td))
    return junctions


def x264_args(preset: str, crf: int) -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "4.2",
        "-r",
        str(FPS),
        "-vsync",
        "cfr",
        "-video_track_timescale",
        "30000",
        "-maxrate",
        "12M",
        "-bufsize",
        "24M",
        "-movflags",
        "+faststart",
    ]


def assemble_xfade_chain(
    items: list[tuple[Path, float]],
    junctions: list[tuple[str, float] | None],
    dest: Path,
    preset: str,
    crf: int,
) -> None:
    if len(items) == 1:
        shutil.copy2(items[0][0], dest)
        return

    filter_lines: list[str] = []
    for i, (_path, _dur) in enumerate(items):
        filter_lines.append(
            f"[{i}:v]fps=fps={FPS},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[v{i}]"
        )

    current = "v0"
    running = items[0][1]
    for i in range(1, len(items)):
        nxt = f"v{i}"
        out = f"vx{i}"
        spec = junctions[i - 1]
        if spec is None:
            filter_lines.append(
                f"[{current}][{nxt}]concat=n=2:v=1:a=0,fps=fps={FPS},setpts=PTS-STARTPTS[{out}]"
            )
            running += items[i][1]
        else:
            name, td = spec
            offset = max(0.0, running - td)
            filter_lines.append(
                f"[{current}][{nxt}]xfade=transition={name}:duration={td:.4f}:offset={offset:.4f},fps=fps={FPS}[{out}]"
            )
            running = running + items[i][1] - td
        current = out

    script_path = dest.with_suffix(".filter.txt")
    script_path.write_text(";\n".join(filter_lines) + "\n", encoding="utf-8")

    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path, _dur in items:
        cmd.extend(["-i", str(path)])
    cmd.extend(
        [
            "-filter_complex_script",
            str(script_path),
            "-map",
            f"[{current}]",
            "-an",
            *x264_args(preset, crf),
            str(dest),
        ]
    )
    run(cmd)


def chunk_shots(
    shots: list[Shot],
    junctions: list[tuple[str, float] | None],
    max_shots: int = 10,
) -> list[tuple[list[Shot], list[tuple[str, float] | None]]]:
    chunks: list[tuple[list[Shot], list[tuple[str, float] | None]]] = []
    start = 0
    n = len(shots)
    for i in range(n):
        length = i - start + 1
        last = i == n - 1
        hard_after = i < n - 1 and junctions[i] is None
        if last or (length >= max_shots and hard_after) or (length >= max_shots * 2):
            sl = shots[start : i + 1]
            jn = junctions[start:i]
            chunks.append((sl, jn))
            start = i + 1
    return chunks


def assemble_all(
    shots: list[Shot],
    junctions: list[tuple[str, float] | None],
    work: Path,
    dest: Path,
    preset: str,
    crf: int,
) -> None:
    chunks = chunk_shots(shots, junctions)
    pieces: list[tuple[Path, float]] = []
    for idx, (sl, jn) in enumerate(chunks):
        piece = work / f"piece_{idx:04d}.mp4"
        items = [(s.path, s.duration) for s in sl if s.path]
        assemble_xfade_chain(items, jn, piece, preset, crf)
        pieces.append((piece, probe_frames(piece) / FPS))

    if len(pieces) == 1:
        shutil.copy2(pieces[0][0], dest)
        return
    assemble_xfade_chain(pieces, [None] * (len(pieces) - 1), dest, preset, crf)


def mux_silent_audio(video: Path, dest: Path) -> None:
    duration = probe_duration(video)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-f",
        "lavfi",
        "-t",
        f"{duration:.4f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        str(dest),
    ]
    run(cmd)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Insane fast-cut 9:16 1080p Instagram Reel (effects + smash transitions)."
    )
    p.add_argument("--input-dir", type=Path, default=None, help="Source folder (default: ./videos if present)")
    p.add_argument("--out-dir", type=Path, default=Path("reels_out"))
    p.add_argument("--duration", type=float, default=0.0, help="Target seconds. 0 = use every shot")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--min-cut", type=float, default=MIN_CUT)
    p.add_argument("--max-cut", type=float, default=MAX_CUT)
    p.add_argument("--transition-chance", type=float, default=0.52)
    p.add_argument("--flash-chance", type=float, default=0.18, help="Insert 2–4 frame color flashes")
    p.add_argument("--trans-min", type=float, default=0.08)
    p.add_argument("--trans-max", type=float, default=0.20)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--jobs", type=int, default=max(1, min(12, os.cpu_count() or 4)))
    p.add_argument("--max-sources", type=int, default=0)
    p.add_argument("--preset", default="veryfast")
    p.add_argument("--final-preset", default="medium")
    p.add_argument("--crf", type=int, default=19)
    p.add_argument("--keep-work", action="store_true")
    p.add_argument("--draft", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_cut <= 0 or args.max_cut < args.min_cut:
        log("Invalid cut range")
        return 2
    if args.draft:
        args.preset = "ultrafast"
        args.final_preset = "ultrafast"
        args.crf = 23

    if args.input_dir is None:
        here = Path(__file__).resolve().parent
        bundled = here / "videos"
        args.input_dir = bundled if bundled.is_dir() else here
    root = args.input_dir.resolve()
    out_dir = args.out_dir if args.out_dir.is_absolute() else (root / args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = find_sources(root)
    if args.max_sources:
        paths = paths[: args.max_sources]
    if not paths:
        log(f"No source videos found in {root}")
        return 1

    log(f"INSANE mode. Found {len(paths)} source video(s)")
    sources: list[Source] = []
    for path in paths:
        src = ffprobe_media(path)
        if src is None:
            log(f"  skip unreadable: {path.name}")
            continue
        log(f"  {path.name}  {src.duration:.2f}s  {src.width}x{src.height}")
        sources.append(src)
    if not sources:
        log("No readable video streams")
        return 1

    seed = args.seed if args.seed is not None else random.SystemRandom().randint(0, 2**31 - 1)
    log(f"Base seed {seed}")

    work_root = root / ".reel_insane_build"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)

    try:
        slice_rng = random.Random(seed)
        planned: list[Shot] = []
        for src in sources:
            planned.extend(slice_source(src, slice_rng, args.min_cut, args.max_cut))
        if not planned:
            log("No shots produced")
            return 1
        log(f"Sliced into {len(planned)} shots ({args.min_cut:.2f}–{args.max_cut:.2f}s + micro-cuts)")

        clips_dir = work_root / "clips"
        clips_dir.mkdir()
        encoded: list[Shot] = []
        log(f"Encoding insane shots with {args.jobs} workers…")
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = {}
            for i, shot in enumerate(planned):
                dest = clips_dir / f"shot_{i:04d}.mp4"
                fut = pool.submit(encode_shot, shot, dest, seed + 31 * i + 3, args.preset, args.crf)
                futs[fut] = i
            done = 0
            failures = 0
            for fut in as_completed(futs):
                done += 1
                try:
                    encoded.append(fut.result())
                except subprocess.CalledProcessError as exc:
                    failures += 1
                    err = (exc.stderr or "")[-500:]
                    log(f"  shot {futs[fut]:04d} failed: {err}")
                if done % 10 == 0 or done == len(futs):
                    log(f"  {done}/{len(futs)} shots encoded ({failures} failed)")
        if not encoded:
            log("Every shot encode failed")
            return 1
        encoded.sort(key=lambda s: s.path.name if s.path else "")
        encoded = [s for s in encoded if s.duration >= 0.10]
        if len(encoded) < 2:
            log("Not enough usable shots after dropping tiny frames")
            return 1

        counts = Counter(tag for s in encoded for tag in s.tags)
        top = ", ".join(f"{k}={v}" for k, v in counts.most_common(12))
        log(f"Effect mix: {top}")

        flash_dir = work_root / "flashes"
        flashes: list[Shot] = []
        for i, color in enumerate(FLASH_COLORS):
            for frames in (2, 3, 4):
                dest = flash_dir / f"flash_{i}_{frames}.mp4"
                try:
                    flashes.append(encode_flash(dest, color, frames, args.preset, args.crf))
                except subprocess.CalledProcessError as exc:
                    log(f"  flash {color} {frames}f failed: {(exc.stderr or '')[-200:]}")
        log(f"Built {len(flashes)} flash frames")

        durs = [s.duration for s in encoded]
        log(
            f"Shot duration range {min(durs):.2f}–{max(durs):.2f}s  "
            f"total pool {sum(durs):.1f}s"
        )

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rendered = 0
        for n in range(args.count):
            rng = random.Random(seed + 2027 * (n + 1))
            order = smart_shuffle(encoded, rng)
            if args.duration and args.duration > 0:
                # Short shots + smash xfades eat a lot of timeline. Overshoot
                # then trim so --duration actually lands on the number asked.
                limit = args.duration * 1.85
                picked: list[Shot] = []
                acc = 0.0
                for shot in order:
                    if acc >= limit:
                        break
                    picked.append(shot)
                    acc += shot.duration
                order = picked
            order = inject_flashes(order, flashes, rng, args.flash_chance)
            if len(order) < 2:
                log("Need at least 2 shots after duration cap")
                return 1

            junctions = pick_junctions(order, rng, args.transition_chance, args.trans_min, args.trans_max)
            n_xfade = sum(1 for j in junctions if j is not None)
            n_flash = sum(1 for s in order if s.kind == "flash")
            log(
                f"Reel {n + 1}/{args.count}: {len(order)} items "
                f"({n_flash} flashes, {n_xfade} smash transitions), "
                f"~{sum(s.duration for s in order):.1f}s before xfades"
            )

            reel_work = work_root / f"reel_{n:02d}"
            reel_work.mkdir()
            assembled = reel_work / "assembled.mp4"
            try:
                assemble_all(order, junctions, reel_work, assembled, args.final_preset, args.crf)
            except subprocess.CalledProcessError as exc:
                log(f"Assemble failed:\n{(exc.stderr or '')[-800:]}")
                return 1

            out_path = out_dir / f"reel_insane_{stamp}_{n + 1:02d}.mp4"
            try:
                mux_silent_audio(assembled, out_path)
            except subprocess.CalledProcessError as exc:
                log(f"Final mux failed:\n{(exc.stderr or '')[-800:]}")
                return 1
            if args.duration and args.duration > 0:
                actual = probe_duration(out_path)
                if actual > args.duration + 0.05:
                    trimmed = reel_work / "trimmed.mp4"
                    run(
                        [
                            "ffmpeg",
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            str(out_path),
                            "-t",
                            f"{args.duration:.4f}",
                            "-c",
                            "copy",
                            "-movflags",
                            "+faststart",
                            str(trimmed),
                        ]
                    )
                    shutil.move(str(trimmed), str(out_path))

            info = ffprobe_media(out_path)
            if info:
                ratio = info.height / info.width if info.width else 0
                log(
                    f"  wrote {out_path.name}  {info.duration:.2f}s  "
                    f"{info.width}x{info.height}  ar={ratio:.3f}  audio={'yes' if info.has_audio else 'no'}"
                )
                if info.width != WIDTH or info.height != HEIGHT:
                    log("  WARNING: output is not 1080x1920")
            rendered += 1

        log(f"Done. {rendered} insane reel(s) in {out_dir}")
        return 0
    finally:
        if not args.keep_work and work_root.exists():
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
