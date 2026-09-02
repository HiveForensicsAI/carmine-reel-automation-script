#!/usr/bin/env python3
"""Shared engine for black-and-gold luxury Reel templates.

The small ``reel_*.py`` entry points select a named art direction from this
module.  You can also run ``python3 luxury_reel_templates.py --list`` or
``python3 luxury_reel_templates.py <slug>``.  Source clips are discovered in
``--input-dir``, cut on a retention curve (very fast opening, slower middle,
fast payoff), shuffled without placing the same source back-to-back, graded,
and exported at 1080x1920.

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
        .38, .82, .24, ("fadeblack", "dissolve", "hblur"),
        ("colorbalance=bs=.06:bm=.035:rs=-.025", "eq=brightness=-.055:contrast=1.24:saturation=.72:gamma=.91"),
        "creep", 5, .82,
    ),
    "gilded_venom": Template(
        "gilded_venom", "Gilded Venom", "Pretty. Poisonous. Precise.",
        .24, .62, .46, ("fadewhite", "fadeblack", "hblur", "wipeleft"),
        ("colorbalance=rs=.12:gs=.045:bs=-.12:rh=.16:gh=.07:bh=-.16", "eq=brightness=-.025:contrast=1.30:saturation=1.05:gamma=.94"),
        "strike", 7, .72,
    ),
    "midnight_tuxedo": Template(
        "midnight_tuxedo", "Midnight Tuxedo", "Bond tailoring over trap drums.",
        .42, .92, .34, ("fadeblack", "smoothleft", "circleclose", "dissolve"),
        ("colorbalance=bs=.08:bm=.045:rh=.08:gh=.035:bh=-.06", "eq=brightness=-.045:contrast=1.22:saturation=.80:gamma=.93"),
        "glide", 4, .88,
    ),
    "velvet_afterhours": Template(
        "velvet_afterhours", "Velvet Afterhours", "Trap soul after midnight.",
        .52, 1.08, .42, ("dissolve", "fadeblack", "hblur", "smoothdown"),
        ("colorbalance=rs=.09:bs=.035:rm=.055:bm=.05:rh=.12:bh=-.04", "eq=brightness=-.035:contrast=1.15:saturation=.88:gamma=.96"),
        "float", 8, .94,
    ),
    "golden_gun": Template(
        "golden_gun", "The Golden Gun", "One shot. No warning.",
        .18, .48, .58, ("fadewhite", "fadeblack", "circleopen", "hblur", "wipeleft", "radial"),
        ("colorbalance=rs=.18:gs=.07:bs=-.16:rh=.22:gh=.11:bh=-.20", "eq=brightness=-.01:contrast=1.36:saturation=1.12:gamma=.92"),
        "impact", 6, .68,
    ),
    "black_mamba": Template(
        "black_mamba", "Black Mamba", "Don't blink. Don't breathe.",
        .16, .40, .64, ("wipeleft", "wiperight", "slideleft", "hblur", "fadeblack", "horzopen"),
        ("colorbalance=gs=.10:bs=-.12:rs=.05:gh=.12:bh=-.14:rh=.07", "eq=brightness=-.045:contrast=1.33:saturation=.90:gamma=.89"),
        "slash", 6, .70,
    ),
    "blood_diamond": Template(
        "blood_diamond", "Blood Diamond", "Red on black. Paid in full.",
        .22, .56, .54, ("fadeblack", "radial", "circleopen", "wipeup", "dissolve", "diagtl"),
        ("colorbalance=rs=.18:gs=-.07:bs=-.12:rh=.24:gh=-.09:bh=-.14:rm=.10", "eq=brightness=-.03:contrast=1.30:saturation=1.10:gamma=.92"),
        "impact", 7, .76,
    ),
    "chrome_reaper": Template(
        "chrome_reaper", "Chrome Reaper", "Cold steel. No soul.",
        .26, .68, .40, ("fadegrays", "hblur", "horzopen", "fadeblack", "distance", "vertclose"),
        ("colorbalance=bs=.16:bm=.09:rs=-.10:gs=-.05:bh=.14:rh=-.12", "eq=brightness=-.07:contrast=1.36:saturation=.38:gamma=.87"),
        "reaper", 5, .90,
    ),
    "kingpin": Template(
        "kingpin", "Kingpin", "The room goes quiet when he sits.",
        .60, 1.22, .26, ("fadeblack", "dissolve", "smoothdown", "circleclose", "smoothleft"),
        ("colorbalance=rs=.11:gs=.045:bs=.06:rh=.15:gh=.06:bh=-.05", "eq=brightness=-.055:contrast=1.18:saturation=.76:gamma=.95"),
        "heave", 9, .93,
    ),
    "ghost_protocol": Template(
        "ghost_protocol", "Ghost Protocol", "You never saw him. He saw everything.",
        .18, .50, .50, ("fadeblack", "hblur", "fadegrays", "vertopen", "distance", "dissolve"),
        ("colorbalance=bs=.07:rs=-.05:gs=-.03:bm=.04", "eq=brightness=-.09:contrast=1.28:saturation=.32:gamma=.90"),
        "ghost", 4, .86,
    ),
    "warlord": Template(
        "warlord", "Warlord", "Crowns are taken, not given.",
        .18, .48, .58, ("fadeblack", "wipeleft", "radial", "hblur", "diagtl", "fadewhite", "wipetl"),
        ("colorbalance=rs=.15:gs=.05:bs=-.11:rh=.17:gh=.04:bh=-.13", "eq=brightness=-.02:contrast=1.35:saturation=1.04:gamma=.90"),
        "war", 12, .62,
    ),
    "onyx_cartel": Template(
        "onyx_cartel", "Onyx Cartel", "The table is already set.",
        .30, .76, .42, ("fadeblack", "smoothleft", "wipeleft", "dissolve", "hblur", "circleclose"),
        ("colorbalance=rs=.12:gs=.03:bs=.05:rh=.16:bh=-.09:bm=.03", "eq=brightness=-.075:contrast=1.27:saturation=.68:gamma=.91"),
        "hunt", 6, .85,
    ),
    "coffin_nails": Template(
        "coffin_nails", "Coffin Nails", "Last smoke. Last look.",
        .48, 1.04, .38, ("dissolve", "fadeblack", "smoothup", "hblur", "fadegrays"),
        ("colorbalance=rs=.09:gs=.03:bs=.02:rh=.11:bm=.05:rm=.04", "eq=brightness=-.065:contrast=1.14:saturation=.58:gamma=.97"),
        "smoke", 11, .96,
    ),
    "silk_assassin": Template(
        "silk_assassin", "Silk Assassin", "Soft hands. Hard ending.",
        .40, .96, .32, ("dissolve", "fadeblack", "smoothleft", "circleclose", "hblur"),
        ("colorbalance=rs=.08:gs=.045:bs=-.04:rh=.12:gh=.08:bh=-.06", "eq=brightness=-.02:contrast=1.16:saturation=.82:gamma=.96"),
        "silk", 3, .80,
    ),
    "neon_heist": Template(
        "neon_heist", "Neon Heist", "The city already picked a side.",
        .22, .54, .56, ("fadeblack", "hblur", "wipeleft", "fadewhite", "radial", "horzopen"),
        ("colorbalance=bs=.18:rs=.08:gs=-.08:bh=.20:rh=.10:gh=-.10:bm=.10", "eq=brightness=-.02:contrast=1.28:saturation=1.18:gamma=.93"),
        "flicker", 8, .60,
    ),
    "ivory_mafia": Template(
        "ivory_mafia", "Ivory Mafia", "Old money doesn't raise its voice.",
        .50, 1.12, .28, ("dissolve", "fadeblack", "smoothright", "fadegrays", "circleclose"),
        ("colorbalance=rs=.04:gs=.03:bs=.02:rh=.08:gh=.06:bh=.02:rm=.04", "eq=brightness=.02:contrast=1.12:saturation=.70:gamma=1.02"),
        "drift", 4, .78,
    ),
    "black_mass": Template(
        "black_mass", "Black Mass", "Pray if you want. Pay if you must.",
        .44, .98, .36, ("fadeblack", "circleopen", "dissolve", "radial", "smoothup"),
        ("colorbalance=rs=.14:gs=.05:bs=-.06:rh=.20:gh=.08:bh=-.10", "eq=brightness=-.10:contrast=1.22:saturation=.74:gamma=.88"),
        "kneel", 6, .95,
    ),
    "copper_bullet": Template(
        "copper_bullet", "Copper Bullet", "Bronze. Fast. Final.",
        .20, .50, .60, ("diagtl", "diagtr", "wipeleft", "fadeblack", "hblur", "wipetr"),
        ("colorbalance=rs=.16:gs=.06:bs=-.14:rh=.18:gh=.08:bh=-.16:rm=.08", "eq=brightness=-.02:contrast=1.32:saturation=1.00:gamma=.92"),
        "ricochet", 7, .66,
    ),
    "red_room": Template(
        "red_room", "Red Room", "Nobody leaves the same.",
        .36, .80, .40, ("dissolve", "fadeblack", "circleclose", "hblur", "smoothdown"),
        ("colorbalance=rs=.14:gs=-.08:bs=.02:rh=.18:gh=-.10:bh=.04:rm=.16:bm=.06", "eq=brightness=-.05:contrast=1.20:saturation=.92:gamma=.94"),
        "close", 5, .88,
    ),
    "casino_royale": Template(
        "casino_royale", "Casino Royale", "The house always knows.",
        .24, .60, .48, ("wipeleft", "wiperight", "fadeblack", "slideleft", "radial", "hblur"),
        ("colorbalance=gs=.10:rs=.08:bs=-.10:gh=.12:rh=.10:bh=-.12", "eq=brightness=-.03:contrast=1.26:saturation=.95:gamma=.93"),
        "deal", 5, .70,
    ),
    "platinum_hit": Template(
        "platinum_hit", "Platinum Hit", "Clean. White. Lethal.",
        .18, .46, .52, ("fadewhite", "fadegrays", "horzopen", "fadeblack", "distance", "hblur"),
        ("colorbalance=bs=.08:rs=-.04:gs=-.02:bh=.06:rh=-.02", "eq=brightness=.03:contrast=1.34:saturation=.48:gamma=.90"),
        "hit", 3, .58,
    ),
}


def retention_slice(src: core.Source, rng: random.Random, cfg: Template) -> list[core.Shot]:
    """Cut a source with pattern interrupts concentrated in the first seconds.

    Files longer than 10s contribute a few scattered excerpts instead of being
    sliced from start to finish (a 3-minute clip would otherwise become hundreds
    of 0.3s shots).
    """
    shots: list[core.Shot] = []
    index = 0
    for start, end in core.usable_windows(src, rng):
        t = start
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
    elif cfg.motion == "slash":
        zoom = rng.uniform(1.30, 1.50)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.choice((.04, .96))
        y = rng.uniform(.26, .64)
    elif cfg.motion == "hunt":
        zoom = rng.uniform(1.16, 1.30)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.uniform(.34, .66)
        y = rng.uniform(.06, .38)
    elif cfg.motion == "reaper":
        zoom = rng.uniform(1.18, 1.34)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = .50 + rng.uniform(-.14, .14)
        y = rng.uniform(.20, .48)
    elif cfg.motion == "heave":
        zoom = rng.uniform(1.06, 1.14)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x, y = rng.uniform(.30, .70), rng.uniform(.28, .55)
    elif cfg.motion == "ghost":
        zoom = rng.uniform(1.10, 1.24)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.uniform(.22, .78)
        y = rng.uniform(.16, .54)
    elif cfg.motion == "war":
        zoom = rng.uniform(1.22, 1.42)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.uniform(.06, .94)
        y = rng.uniform(.10, .72)
    elif cfg.motion == "smoke":
        zoom = rng.uniform(1.08, 1.16)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.uniform(.28, .72)
        y = rng.uniform(.32, .62)
    elif cfg.motion == "silk":
        zoom = rng.uniform(1.28, 1.40) if shot_index % 7 == 0 else rng.uniform(1.10, 1.20)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.uniform(.32, .68)
        y = rng.uniform(.28, .55)
    elif cfg.motion == "flicker":
        zoom = rng.uniform(1.12, 1.28)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.uniform(.16, .84)
        y = rng.uniform(.18, .62)
    elif cfg.motion == "drift":
        zoom = rng.uniform(1.08, 1.16)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = .22 if shot_index % 2 == 0 else .78
        y = rng.uniform(.30, .58)
    elif cfg.motion == "kneel":
        zoom = rng.uniform(1.14, 1.26)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.uniform(.30, .70)
        y = rng.uniform(.62, .92)
    elif cfg.motion == "ricochet":
        zoom = rng.uniform(1.22, 1.40)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.choice((.08, .22, .78, .92))
        y = rng.choice((.12, .28, .70, .88))
    elif cfg.motion == "close":
        zoom = rng.uniform(1.36, 1.56)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.uniform(.38, .62)
        y = rng.uniform(.28, .48)
    elif cfg.motion == "deal":
        zoom = rng.uniform(1.14, 1.26)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = .12 if shot_index % 2 else .88
        y = rng.uniform(.24, .60)
    elif cfg.motion == "hit":
        zoom = rng.uniform(1.20, 1.36)
        chain[-1] = f"scale=iw*{zoom:.4f}:ih*{zoom:.4f}"
        x = rng.uniform(.28, .72)
        y = rng.uniform(.22, .52)
    chain += [
        f"crop={core.WIDTH}:{core.HEIGHT}:(iw-{core.WIDTH})*{x:.3f}:(ih-{core.HEIGHT})*{y:.3f}",
        "setsar=1", f"fps={core.FPS}", *cfg.grade,
    ]
    if cfg.motion == "impact" and shot_index % 4 == 0:
        chain.append("eq=brightness=.10:enable='lt(n,2)'")
    if cfg.motion == "strike" and shot_index % 5 == 0:
        chain.append("chromashift=cbh=3:crh=-3")
    if cfg.motion == "slash" and shot_index % 3 == 0:
        chain.append("chromashift=cbh=5:crh=-4")
        chain.append("eq=contrast=1.08:brightness=.07:enable='lt(n,2)'")
    if cfg.motion == "hunt" and shot_index % 6 == 0:
        chain.append("eq=brightness=-.14:saturation=.65")
    if cfg.motion == "reaper":
        if shot_index % 7 == 0:
            chain.append("hue=s=0")
        if shot_index % 5 == 0:
            chain.append("eq=brightness=.16:enable='lt(n,1)'")
    if cfg.motion == "heave" and shot_index % 8 == 0:
        chain.append("gblur=sigma=0.8")
    if cfg.motion == "ghost":
        if shot_index % 4 == 0:
            chain.append("eq=brightness=-.20:saturation=.30")
        if shot_index % 6 == 0:
            chain.append("eq=brightness=.22:enable='lt(n,2)'")
    if cfg.motion == "war" and shot_index % 3 == 0:
        chain.append("eq=brightness=.12:contrast=1.12:enable='lt(n,2)'")
        chain.append("chromashift=cbh=2:crh=-2")
    if cfg.motion == "smoke":
        chain.append("gblur=sigma=1.35")
        if shot_index % 5 == 0:
            chain.append("eq=brightness=-.10:saturation=.5")
    if cfg.motion == "silk" and shot_index % 7 == 0:
        chain.append("eq=brightness=.08:enable='lt(n,2)'")
    if cfg.motion == "flicker":
        if shot_index % 2 == 0:
            chain.append("eq=brightness=.14:enable='lt(n,1)'")
        if shot_index % 5 == 0:
            chain.append("chromashift=cbh=4:crh=-5")
    if cfg.motion == "drift" and shot_index % 6 == 0:
        chain.append("gblur=sigma=0.6")
    if cfg.motion == "kneel" and shot_index % 5 == 0:
        chain.append("eq=brightness=-.10:gamma=.92")
    if cfg.motion == "ricochet" and shot_index % 4 == 0:
        chain.append("eq=brightness=.11:enable='lt(n,2)'")
        chain.append("chromashift=cbh=3:crh=-2")
    if cfg.motion == "close" and shot_index % 4 == 0:
        chain.append("eq=saturation=.85:brightness=-.04")
    if cfg.motion == "deal" and shot_index % 3 == 0:
        chain.append("eq=brightness=.09:enable='lt(n,1)'")
    if cfg.motion == "hit":
        if shot_index % 4 == 0:
            chain.append("eq=brightness=.18:saturation=.7:enable='lt(n,2)'")
        if shot_index % 6 == 0:
            chain.append("hue=s=.45")
    chain += [
        f"vignette={cfg.vignette:.2f}*PI/4",
        "unsharp=5:5:.45:5:5:0",
        f"noise=alls={cfg.grain}:allf=t+u",
        "format=yuv420p", "setpts=PTS-STARTPTS",
    ]
    return ",".join(chain)


def encode_shot(shot: core.Shot, dest: Path, cfg: Template, seed: int, index: int,
                preset: str, crf: int) -> core.Shot:
    nframes = max(1, int(round(shot.duration * core.FPS)))
    vf = (
        effect_filter(cfg, random.Random(seed), index)
        + f",trim=start_frame=0:end_frame={nframes},setpts=PTS-STARTPTS"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{shot.start:.4f}", "-i", str(shot.source),
        "-t", f"{shot.duration + 0.12:.4f}", "-an", "-vf", vf,
        *core.x264_args(preset, crf), str(dest),
    ]
    core.run(cmd)
    return core.Shot(shot.source, shot.start, core.probe_frames(dest) / core.FPS, dest)


def finish_reel(video: Path, dest: Path, audio: Path | None, duration: float) -> None:
    """Trim to target length and mux audio. No on-screen text."""
    actual = min(core.probe_duration(video), duration) if duration else core.probe_duration(video)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
    if audio:
        cmd += [
            "-stream_loop", "-1", "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-af", "afade=t=in:d=.08,afade=t=out:st=%s:d=.7" % max(0, actual - .7),
        ]
    else:
        cmd += [
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map", "0:v:0", "-map", "1:a:0",
        ]
    cmd += [
        "-t", f"{actual:.4f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(dest),
    ]
    core.run(cmd)


def parser_for(cfg: Template) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"{cfg.name}: {cfg.tagline}")
    p.add_argument("--input-dir", type=Path, default=None, help="Source folder (default: ./videos if present)")
    p.add_argument("--out-dir", type=Path, default=Path("reels_out"))
    p.add_argument("--audio", type=Path, help="Optional song/beat; looped and faded to fit")
    p.add_argument(
        "--duration",
        type=float,
        default=15.0,
        help=f"Target length in seconds (capped at {core.MAX_REEL_SECONDS:.0f})",
    )
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--seed", type=int)
    p.add_argument("--jobs", type=int, default=max(1, min(8, os.cpu_count() or 4)))
    p.add_argument("--max-sources", type=int, default=0)
    p.add_argument("--draft", action="store_true")
    p.add_argument("--keep-work", action="store_true")
    return p


def list_templates() -> None:
    width = max(len(t.slug) for t in TEMPLATES.values())
    print(f"{len(TEMPLATES)} luxury reel templates:\n")
    for t in TEMPLATES.values():
        print(f"  {t.slug:<{width}}  {t.name} — {t.tagline}")
    print("\nRun:  python3 reel_<slug>.py --duration 15")
    print("  or:  python3 luxury_reel_templates.py <slug> --duration 15")


def run_template(slug: str, argv: list[str] | None = None) -> int:
    if slug not in TEMPLATES:
        print(f"Unknown template {slug!r}.", file=sys.stderr)
        list_templates()
        return 2
    cfg = TEMPLATES[slug]
    args = parser_for(cfg).parse_args(argv)
    if args.count <= 0:
        print("--count must be positive", file=sys.stderr)
        return 2
    args.duration = core.cap_reel_duration(args.duration)
    if args.duration <= 0:
        print("--duration must be positive", file=sys.stderr)
        return 2
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe are required", file=sys.stderr)
        return 2
    if args.input_dir is None:
        args.input_dir = core.default_input_dir()
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
    transitions = core.safe_transitions(cfg.transitions)
    print(
        f"{cfg.name} — {len(sources)} sources — seed {seed} — "
        f"target {args.duration:.0f}s (max {core.MAX_REEL_SECONDS:.0f}s)",
        flush=True,
    )
    try:
        rng = random.Random(seed)
        planned: list[core.Shot] = []
        for source in sources:
            src_shots = retention_slice(source, rng, cfg)
            if source.duration > core.PORTION_AFTER_SECONDS:
                print(
                    f"  excerpts from {source.path.name} ({source.duration:.1f}s) "
                    f"-> {len(src_shots)} shots",
                    flush=True,
                )
            planned.extend(src_shots)
        if len(planned) < 2:
            print("Need enough source footage to create at least two cuts", file=sys.stderr)
            return 1
        planned = core.bound_shot_pool(planned, rng, args.duration)
        print(f"Shot pool: {len(planned)} cuts, {sum(s.duration for s in planned):.1f}s", flush=True)
        encoded: list[core.Shot] = []
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(
                    encode_shot, shot, work / "clips" / f"shot_{i:04d}.mp4",
                    cfg, seed + i * 37, i, preset, crf,
                ): i
                for i, shot in enumerate(planned)
            }
            done = 0
            for future in as_completed(futures):
                done += 1
                try:
                    encoded.append(future.result())
                except subprocess.CalledProcessError as exc:
                    print(f"shot {futures[future]} failed: {(exc.stderr or '')[-300:]}", file=sys.stderr)
                if done % 25 == 0 or done == len(futures):
                    print(f"  encoded {done}/{len(futures)}", flush=True)
        if len(encoded) < 2:
            print("Need at least two encoded shots", file=sys.stderr)
            return 1
        encoded.sort(key=lambda shot: shot.path.name if shot.path else "")
        print(
            f"Encoded {len(encoded)} shots, {sum(s.duration for s in encoded):.1f}s available",
            flush=True,
        )
        avg_cut = sum(s.duration for s in encoded) / len(encoded)
        kept_ratio = max(0.50, 1.0 - cfg.transition_chance * 0.16 / max(avg_cut, 0.18))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for number in range(args.count):
            reel_rng = random.Random(seed + 1009 * (number + 1))
            order = core.smart_shuffle(encoded, reel_rng)
            chosen, total = [], 0.0
            limit = args.duration / kept_ratio + 4.0
            while total < limit and order:
                shot = order.pop(0)
                chosen.append(shot)
                total += shot.duration
            if len(chosen) < 2:
                chosen = encoded[:2]
            junctions = core.pick_junctions(
                len(chosen), reel_rng, cfg.transition_chance,
                [s.duration for s in chosen], .10, .22,
            )
            junctions = [
                (reel_rng.choice(transitions), value[1]) if value else None
                for value in junctions
            ]
            reel_work = work / f"reel_{number:02d}"
            reel_work.mkdir()
            assembled = reel_work / "assembled.mp4"
            try:
                core.assemble_all(chosen, junctions, reel_work, assembled, final_preset, crf)
            except subprocess.CalledProcessError as exc:
                print(f"Assemble failed:\n{(exc.stderr or '')[-800:]}", file=sys.stderr)
                return 1
            output = out_dir / f"{cfg.slug}_{stamp}_{number + 1:02d}.mp4"
            try:
                finish_reel(assembled, output, audio, args.duration)
            except subprocess.CalledProcessError as exc:
                print(f"Finish failed:\n{(exc.stderr or '')[-800:]}", file=sys.stderr)
                return 1
            actual = core.probe_duration(output)
            if actual > core.MAX_REEL_SECONDS + 0.05:
                trimmed = reel_work / "trimmed.mp4"
                core.run([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(output), "-t", f"{core.MAX_REEL_SECONDS:.4f}",
                    "-c", "copy", "-movflags", "+faststart", str(trimmed),
                ])
                shutil.move(str(trimmed), str(output))
                actual = core.probe_duration(output)
            print(f"wrote {output} ({actual:.2f}s)", flush=True)
        return 0
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-l", "--list", "list", "-h", "--help"):
        list_templates()
        return 0
    return run_template(argv[0], argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

