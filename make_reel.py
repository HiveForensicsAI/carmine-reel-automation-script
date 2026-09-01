#!/usr/bin/env python3
"""Build a 9:16 1080p Instagram Reel from every video in the current directory.

Each source (typically 6–10s) is sliced into 0.5–1.0s shots, the shots are
shuffled, some junctions get a random xfade, and the result is encoded as
1080x1920 H.264 + silent AAC.

Usage:
  python3 make_reel.py
  python3 make_reel.py --duration 30 --count 3
  python3 make_reel.py --transition-chance 0.35 --seed 7
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


WIDTH = 1080
HEIGHT = 1920
FPS = 30
MIN_CUT = 0.5
MAX_CUT = 1.0

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
SKIP_DIR_NAMES = {".reel_build", "reels_out", "__pycache__"}

# Transitions that read well on vertical Reels. Ugly ones (pixelize, squeeze,
# circlecrop) are left out on purpose.
TRANSITIONS = (
    "fade",
    "fadeblack",
    "fadewhite",
    "dissolve",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "distance",
    "radial",
    "circleopen",
    "circleclose",
    "horzopen",
    "horzclose",
    "vertopen",
    "vertclose",
    "diagtl",
    "diagtr",
    "diagbl",
    "diagbr",
    "hblur",
    "zoomin",
    "wipetl",
    "wipetr",
    "wipebl",
    "wipebr",
    "fadegrays",
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
    path: Path | None = None  # filled after encode


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


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
    # Fallback: duration * fps
    return max(1, int(round(probe_duration(path) * FPS)))


def find_sources(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXTS:
            continue
        if p.name.startswith("reel_"):
            continue
        files.append(p)
    return files


def quantize_frames(seconds: float, fps: int = FPS) -> float:
    frames = max(1, int(round(seconds * fps)))
    return frames / fps


def slice_source(src: Source, rng: random.Random, min_cut: float, max_cut: float) -> list[Shot]:
    """Cover the usable timeline with non-overlapping 0.5–1.0s shots."""
    # Drop a few frames at each end — generated clips often have a still/settle.
    pad = min(0.08, src.duration * 0.02)
    t = pad
    end = src.duration - pad
    shots: list[Shot] = []
    min_f = quantize_frames(min_cut)
    max_f = quantize_frames(max_cut)

    while True:
        remaining = end - t
        if remaining < min_f - 1e-6:
            break
        if remaining <= max_f + 1e-6:
            dur = quantize_frames(remaining)
            if dur >= min_f - 1e-6:
                shots.append(Shot(source=src.path, start=t, duration=dur))
            break
        # Leave at least min_cut for the following shot.
        lo = min_f
        hi = min(max_f, remaining - min_f)
        if hi < lo:
            dur = quantize_frames(remaining)
            if dur >= min_f - 1e-6:
                shots.append(Shot(source=src.path, start=t, duration=dur))
            break
        dur = quantize_frames(rng.uniform(lo, hi))
        shots.append(Shot(source=src.path, start=t, duration=dur))
        t += dur
    return shots


def clip_filter(rng: random.Random) -> str:
    """Scale/crop to 1080x1920 plus a light per-shot look change."""
    zoom = rng.uniform(1.04, 1.14)
    pan_x = rng.uniform(0.25, 0.75)
    pan_y = rng.uniform(0.25, 0.75)
    brightness = rng.uniform(-0.045, 0.045)
    contrast = rng.uniform(0.96, 1.07)
    saturation = rng.uniform(0.94, 1.14)
    gamma = rng.uniform(0.96, 1.05)
    grain = rng.randint(3, 7)
    sharpen = rng.uniform(0.25, 0.55)

    filters = [
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase:force_divisible_by=2",
        f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}",
        f"crop={WIDTH}:{HEIGHT}:(iw-{WIDTH})*{pan_x:.3f}:(ih-{HEIGHT})*{pan_y:.3f}",
        "setsar=1",
        f"fps={FPS}",
    ]
    if rng.random() < 0.22:
        filters.append("hflip")
    filters.append(
        f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:"
        f"saturation={saturation:.3f}:gamma={gamma:.3f}"
    )
    filters.append(f"unsharp=5:5:{sharpen:.2f}:5:5:0.0")
    filters.append(f"noise=alls={grain}:allf=t+u")
    filters.append("format=yuv420p")
    filters.append("setpts=PTS-STARTPTS")
    return ",".join(filters)


def encode_shot(shot: Shot, dest: Path, rng_seed: int, preset: str, crf: int) -> Shot:
    rng = random.Random(rng_seed)
    vf = clip_filter(rng)
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
        "-vf",
        vf,
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
    run(cmd)
    frames = probe_frames(dest)
    out = Shot(source=shot.source, start=shot.start, duration=frames / FPS, path=dest)
    return out


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


def pick_junctions(
    n_shots: int,
    rng: random.Random,
    chance: float,
    durations: list[float],
    trans_min: float,
    trans_max: float,
) -> list[tuple[str, float] | None]:
    """One entry per junction: (transition, duration) or None for a hard cut."""
    junctions: list[tuple[str, float] | None] = []
    for i in range(n_shots - 1):
        max_allowed = min(durations[i], durations[i + 1]) * 0.45
        if max_allowed < trans_min or rng.random() > chance:
            junctions.append(None)
            continue
        td = min(quantize_frames(rng.uniform(trans_min, trans_max)), quantize_frames(max_allowed))
        if td < trans_min * 0.8:
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
        "10M",
        "-bufsize",
        "20M",
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
    """Assemble a short chain with mixed hard cuts and xfades, then re-encode."""
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
    max_shots: int = 12,
) -> list[tuple[list[Shot], list[tuple[str, float] | None]]]:
    """Split into bounded chains, preferring to break on hard cuts."""
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

    # Hard-cut the chunks together through the concat filter so fps stays 30/1.
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


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Slice, shuffle, and assemble videos into a 9:16 1080p Instagram Reel."
    )
    p.add_argument("--input-dir", type=Path, default=Path("."), help="Directory of source videos")
    p.add_argument("--out-dir", type=Path, default=Path("reels_out"), help="Output directory")
    p.add_argument("--duration", type=float, default=0.0, help="Target length in seconds. 0 = use every shot")
    p.add_argument("--count", type=int, default=1, help="How many shuffled reels to render")
    p.add_argument("--min-cut", type=float, default=MIN_CUT)
    p.add_argument("--max-cut", type=float, default=MAX_CUT)
    p.add_argument("--transition-chance", type=float, default=0.32, help="Probability a cut becomes an xfade")
    p.add_argument("--trans-min", type=float, default=0.12)
    p.add_argument("--trans-max", type=float, default=0.28)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--jobs", type=int, default=max(1, min(12, os.cpu_count() or 4)))
    p.add_argument("--max-sources", type=int, default=0, help="Use only the first N sources (0 = all)")
    p.add_argument("--preset", default="veryfast", help="x264 preset for shot encodes")
    p.add_argument("--final-preset", default="medium")
    p.add_argument("--crf", type=int, default=19)
    p.add_argument("--keep-work", action="store_true")
    p.add_argument("--draft", action="store_true", help="Faster/lower-quality encode for a smoke test")
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

    root = args.input_dir.resolve()
    out_dir = args.out_dir if args.out_dir.is_absolute() else (root / args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = find_sources(root)
    if args.max_sources:
        paths = paths[: args.max_sources]
    if not paths:
        log(f"No source videos found in {root}")
        return 1

    log(f"Found {len(paths)} source video(s) in {root}")
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

    work_root = root / ".reel_build"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)

    try:
        # Slice + encode shots once, then shuffle per output reel.
        slice_rng = random.Random(seed)
        planned: list[Shot] = []
        for src in sources:
            planned.extend(slice_source(src, slice_rng, args.min_cut, args.max_cut))
        if not planned:
            log("No shots produced — sources may be shorter than --min-cut")
            return 1
        log(f"Sliced into {len(planned)} shots ({args.min_cut:.2f}–{args.max_cut:.2f}s)")

        clips_dir = work_root / "clips"
        clips_dir.mkdir()
        encoded: list[Shot] = []
        log(f"Encoding shots with {args.jobs} workers…")
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = {}
            for i, shot in enumerate(planned):
                dest = clips_dir / f"shot_{i:04d}.mp4"
                fut = pool.submit(encode_shot, shot, dest, seed + 17 * i + 1, args.preset, args.crf)
                futs[fut] = i
            done = 0
            failures = 0
            for fut in as_completed(futs):
                done += 1
                try:
                    encoded.append(fut.result())
                except subprocess.CalledProcessError as exc:
                    failures += 1
                    err = (exc.stderr or "")[-400:]
                    log(f"  shot {futs[fut]:04d} failed: {err}")
                if done % 10 == 0 or done == len(futs):
                    log(f"  {done}/{len(futs)} shots encoded ({failures} failed)")
        if not encoded:
            log("Every shot encode failed")
            return 1
        encoded.sort(key=lambda s: s.path.name if s.path else "")

        durs = [s.duration for s in encoded]
        log(
            f"Shot duration range {min(durs):.2f}–{max(durs):.2f}s  "
            f"total pool {sum(durs):.1f}s"
        )

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rendered = 0
        for n in range(args.count):
            rng = random.Random(seed + 1009 * (n + 1))
            order = smart_shuffle(encoded, rng)
            if args.duration and args.duration > 0:
                # xfades overlap and concat rounding shorten the timeline, so
                # pick extra shots and trim to the requested length at the end.
                avg_trans = (args.trans_min + args.trans_max) / 2
                extra = args.duration * args.transition_chance * avg_trans / 0.75
                extra += 2.0
                limit = args.duration + extra
                picked: list[Shot] = []
                acc = 0.0
                for shot in order:
                    if acc >= limit:
                        break
                    picked.append(shot)
                    acc += shot.duration
                order = picked
            if len(order) < 2:
                log("Need at least 2 shots after duration cap")
                return 1

            junctions = pick_junctions(
                len(order),
                rng,
                args.transition_chance,
                [s.duration for s in order],
                args.trans_min,
                args.trans_max,
            )
            n_xfade = sum(1 for j in junctions if j is not None)
            log(
                f"Reel {n + 1}/{args.count}: {len(order)} shots, "
                f"{n_xfade} transitions, ~{sum(s.duration for s in order):.1f}s before xfades"
            )

            reel_work = work_root / f"reel_{n:02d}"
            reel_work.mkdir()
            assembled = reel_work / "assembled.mp4"
            try:
                assemble_all(order, junctions, reel_work, assembled, args.final_preset, args.crf)
            except subprocess.CalledProcessError as exc:
                log(f"Assemble failed:\n{(exc.stderr or '')[-800:]}")
                return 1

            out_path = out_dir / f"reel_{stamp}_{n + 1:02d}.mp4"
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

        log(f"Done. {rendered} reel(s) in {out_dir}")
        return 0
    finally:
        if not args.keep_work and work_root.exists():
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
