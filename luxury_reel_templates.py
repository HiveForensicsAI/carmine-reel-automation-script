#!/usr/bin/env python3
"""Shared engine for five black-and-gold luxury Reel templates.

The small ``reel_*.py`` entry points select a named art direction from this
module.  Source clips are discovered in ``--input-dir``, cut on a retention
curve (very fast opening, slower middle, fast payoff), shuffled without
placing the same source back-to-back, graded, and exported at 1080x1920.

FFmpeg and ffprobe must be installed.  No third-party Python package is used.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import make_reel as core


@dataclass(frozen=True)
class Template:
    slug: str
    name: str
    tagline: str
    hook: str
    payoff: str
    min_cut: float
    max_cut: float
    transition_chance: float
    transitions: tuple[str, ...]
    grade: tuple[str, ...]
    motion: str
    grain: int
    vignette: float


TEMPLATES = {
    "obsidian_noir": Template(
        "obsidian_noir", "Obsidian Noir", "Silence looks expensive.",
        "THEY NEVER SAW THIS COMING", "SILENCE LOOKS EXPENSIVE",
        .38, .82, .24, ("fadeblack", "dissolve", "hblur"),
        ("colorbalance=bs=.06:bm=.035:rs=-.025", "eq=brightness=-.055:contrast=1.24:saturation=.72:gamma=.91"),
        "creep", 5, .82,
    ),
    "gilded_venom": Template(
        "gilded_venom", "Gilded Venom", "Pretty. Poisonous. Precise.",
        "LUXURY HAS A DARK SIDE", "PRETTY. POISONOUS. PRECISE.",
        .24, .62, .46, ("fadewhite", "fadeblack", "hblur", "wipeleft"),
        ("colorbalance=rs=.12:gs=.045:bs=-.12:rh=.16:gh=.07:bh=-.16", "eq=brightness=-.025:contrast=1.30:saturation=1.05:gamma=.94"),
        "strike", 7, .72,
    ),
    "midnight_tuxedo": Template(
        "midnight_tuxedo", "Midnight Tuxedo", "Bond tailoring over trap drums.",
        "DRESS CODE: DANGEROUS", "MIDNIGHT. NO RESERVATIONS.",
        .42, .92, .34, ("fadeblack", "smoothleft", "circleclose", "dissolve"),
        ("colorbalance=bs=.08:bm=.045:rh=.08:gh=.035:bh=-.06", "eq=brightness=-.045:contrast=1.22:saturation=.80:gamma=.93"),
        "glide", 4, .88,
    ),
    "velvet_afterhours": Template(
        "velvet_afterhours", "Velvet Afterhours", "Trap soul after midnight.",
        "THIS IS WHAT 2 A.M. FEELS LIKE", "DON'T WAKE ME YET",
        .52, 1.08, .42, ("dissolve", "fadeblack", "hblur", "smoothdown"),
        ("colorbalance=rs=.09:bs=.035:rm=.055:bm=.05:rh=.12:bh=-.04", "eq=brightness=-.035:contrast=1.15:saturation=.88:gamma=.96"),
        "float", 8, .94,
    ),
    "golden_gun": Template(
        "golden_gun", "The Golden Gun", "One shot. No warning.",
        "YOU GET ONE SHOT", "NO WARNING. JUST IMPACT.",
        .18, .48, .58, ("fadewhite", "fadeblack", "zoomin", "hblur", "wipeleft", "radial"),
        ("colorbalance=rs=.18:gs=.07:bs=-.16:rh=.22:gh=.11:bh=-.20", "eq=brightness=-.01:contrast=1.36:saturation=1.12:gamma=.92"),
        "impact", 6, .68,
    ),
}


def retention_slice(src: core.Source, rng: random.Random, cfg: Template) -> list[core.Shot]:
    """Cut a source with pattern interrupts concentrated in the first seconds."""
    shots: list[core.Shot] = []
    t = min(.06, src.duration * .02)
    end = src.duration - t
    index = 0
    while end - t >= .14:
        if index < 3:
            lo, hi = .16, min(.42, cfg.max_cut)
        elif index % 7 == 6:
            lo, hi = .14, min(.30, cfg.max_cut)
        else:
            lo, hi = cfg.min_cut, cfg.max_cut
        remaining = end - t
        duration = core.quantize_frames(min(remaining, rng.uniform(lo, max(lo, hi))))
        if duration < .14:
            break
        shots.append(core.Shot(src.path, t, duration))
        t += duration
        index += 1
    return shots


def effect_filter(cfg: Template, rng: random.Random, shot_index: int) -> str:
    zoom = rng.uniform(1.08, 1.19)
    x, y = rng.uniform(.18, .82), rng.uniform(.20, .72)
    chain = [
        f"scale={core.WIDTH}:{core.HEIGHT}:force_original_aspect_ratio=increase:force_divisible_by=2",
        f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}",
    ]
    if cfg.motion == "strike":
        x = rng.choice((.12, .88))
    elif cfg.motion == "glide":
        x = .18 if shot_index % 2 else .72
    elif cfg.motion == "impact":
        zoom = rng.uniform(1.24, 1.42)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
    elif cfg.motion == "float":
        y = rng.uniform(.34, .58)
    chain += [
        f"crop={core.WIDTH}:{core.HEIGHT}:(iw-{core.WIDTH})*{x:.3f}:(ih-{core.HEIGHT})*{y:.3f}",
        "setsar=1", f"fps={core.FPS}", *cfg.grade,
    ]
    if cfg.motion == "impact" and shot_index % 4 == 0:
        chain.append("eq=brightness=.10:enable='lt(n,2)'")
    if cfg.motion == "strike" and shot_index % 5 == 0:
        chain.append("chromashift=cbh=3:crh=-3")
    chain += [
        f"vignette={cfg.vignette:.2f}*PI/4",
        "unsharp=5:5:.45:5:5:0",
        f"noise=alls={cfg.grain}:allf=t+u",
        "format=yuv420p", "setpts=PTS-STARTPTS",
    ]
    return ",".join(chain)


def encode_shot(shot: core.Shot, dest: Path, cfg: Template, seed: int, index: int,
                preset: str, crf: int) -> core.Shot:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{shot.start:.4f}",
           "-i", str(shot.source), "-t", f"{shot.duration:.4f}", "-an", "-vf",
           effect_filter(cfg, random.Random(seed), index), *core.x264_args(preset, crf), str(dest)]
    core.run(cmd)
    return core.Shot(shot.source, shot.start, core.probe_frames(dest) / core.FPS, dest)


def escape_drawtext(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")


def finish_reel(video: Path, dest: Path, cfg: Template, hook: str, payoff: str,
                audio: Path | None, duration: float, preset: str, crf: int) -> None:
    actual = min(core.probe_duration(video), duration) if duration else core.probe_duration(video)
    hook_end = min(1.65, actual * .24)
    payoff_start = max(hook_end + .2, actual - 1.7)
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    gold = "F2CF73"
    vf = (
        f"drawbox=x=70:y=220:w=10:h=170:color=0x{gold}@.95:t=fill:enable='between(t,0,{hook_end:.2f})',"
        f"drawtext=fontfile={font}:text='{escape_drawtext(hook)}':fontcolor=white:fontsize=54:"
        f"x=105:y=250:box=1:boxcolor=black@.48:boxborderw=22:enable='between(t,0,{hook_end:.2f})',"
        f"drawtext=fontfile={font}:text='{escape_drawtext(payoff)}':fontcolor=0x{gold}:fontsize=48:"
        f"x=(w-text_w)/2:y=h-330:box=1:boxcolor=black@.58:boxborderw=20:"
        f"enable='between(t,{payoff_start:.2f},{actual:.2f})'"
    )
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
    if audio:
        cmd += ["-stream_loop", "-1", "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-af", "afade=t=in:d=.08,afade=t=out:st=%s:d=.7" % max(0, actual - .7)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000", "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-vf", vf, "-t", f"{actual:.4f}", *core.x264_args(preset, crf), "-c:a", "aac", "-b:a", "192k", "-shortest", str(dest)]
    core.run(cmd)


def parser_for(cfg: Template) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"{cfg.name}: {cfg.tagline}")
    p.add_argument("--input-dir", type=Path, default=Path("."))
    p.add_argument("--out-dir", type=Path, default=Path("reels_out"))
    p.add_argument("--audio", type=Path, help="Optional song/beat; looped and faded to fit")
    p.add_argument("--duration", type=float, default=15.0)
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--hook", default=cfg.hook, help="Opening retention hook")
    p.add_argument("--payoff", default=cfg.payoff, help="Closing line or call to action")
    p.add_argument("--seed", type=int)
    p.add_argument("--jobs", type=int, default=max(1, min(8, os.cpu_count() or 4)))
    p.add_argument("--max-sources", type=int, default=0)
    p.add_argument("--draft", action="store_true")
    p.add_argument("--keep-work", action="store_true")
    return p


def run_template(slug: str) -> int:
    cfg = TEMPLATES[slug]
    args = parser_for(cfg).parse_args()
    if args.duration <= 0 or args.count <= 0:
        print("--duration and --count must be positive", file=sys.stderr)
        return 2
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe are required", file=sys.stderr)
        return 2
    root = args.input_dir.resolve()
    paths = core.find_sources(root)
    if args.max_sources:
        paths = paths[:args.max_sources]
    sources = [source for path in paths if (source := core.ffprobe_media(path))]
    if not sources:
        print(f"No readable source videos found in {root}", file=sys.stderr)
        return 1
    audio = args.audio.resolve() if args.audio else None
    if audio and not audio.is_file():
        print(f"Audio file not found: {audio}", file=sys.stderr)
        return 2
    seed = args.seed if args.seed is not None else random.SystemRandom().randint(0, 2**31 - 1)
    out_dir = args.out_dir if args.out_dir.is_absolute() else root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    work = root / f".{cfg.slug}_build"
    shutil.rmtree(work, ignore_errors=True)
    (work / "clips").mkdir(parents=True)
    preset, final_preset, crf = ("ultrafast", "ultrafast", 25) if args.draft else ("veryfast", "medium", 19)
    print(f"{cfg.name} — {len(sources)} sources — seed {seed}", flush=True)
    try:
        rng = random.Random(seed)
        planned = [shot for source in sources for shot in retention_slice(source, rng, cfg)]
        if len(planned) < 2:
            print("Need enough source footage to create at least two cuts", file=sys.stderr)
            return 1
        encoded: list[core.Shot] = []
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(encode_shot, shot, work / "clips" / f"shot_{i:04d}.mp4", cfg,
                            seed + i * 37, i, preset, crf): i
                for i, shot in enumerate(planned)
            }
            for future in as_completed(futures):
                try:
                    encoded.append(future.result())
                except subprocess.CalledProcessError as exc:
                    print(f"shot {futures[future]} failed: {(exc.stderr or '')[-300:]}", file=sys.stderr)
        if len(encoded) < 2:
            return 1
        encoded.sort(key=lambda shot: shot.path.name if shot.path else "")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for number in range(args.count):
            reel_rng = random.Random(seed + 1009 * (number + 1))
            order = core.smart_shuffle(encoded, reel_rng)
            chosen, total = [], 0.0
            while total < args.duration + 2 and order:
                shot = order.pop(0)
                chosen.append(shot)
                total += shot.duration
            if len(chosen) < 2:
                chosen = encoded[:2]
            junctions = core.pick_junctions(len(chosen), reel_rng, cfg.transition_chance,
                                             [s.duration for s in chosen], .10, .22)
            junctions = [(reel_rng.choice(cfg.transitions), value[1]) if value else None for value in junctions]
            reel_work = work / f"reel_{number:02d}"
            reel_work.mkdir()
            assembled = reel_work / "assembled.mp4"
            core.assemble_all(chosen, junctions, reel_work, assembled, final_preset, crf)
            output = out_dir / f"{cfg.slug}_{stamp}_{number + 1:02d}.mp4"
            finish_reel(assembled, output, cfg, args.hook, args.payoff, audio, args.duration, final_preset, crf)
            print(f"wrote {output} ({core.probe_duration(output):.2f}s)", flush=True)
        return 0
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)

