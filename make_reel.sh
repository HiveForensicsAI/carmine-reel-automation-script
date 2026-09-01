#!/usr/bin/env bash
# Slice every video in this folder into 0.5–1s shots, shuffle them, add
# random transitions, and write a 1080x1920 9:16 Instagram Reel.
#
#   ./make_reel.sh                      # use every shot (can exceed 90s)
#   ./make_reel.sh --duration 30        # typical Reels length
#   ./make_reel.sh --duration 90 --count 3
#   ./make_reel.sh --transition-chance 0.4 --seed 7
#
# Want it unhinged instead?  ./make_reel_insane.sh --duration 20
set -euo pipefail
cd "$(dirname "$0")"
exec python3 ./make_reel.py "$@"
