#!/usr/bin/env bash
# Slice source videos (default: the videos/ folder) into short shots, shuffle them,
# add random transitions, and write a 1080x1920 9:16 Instagram Reel.
# Output is capped at 90 seconds. Files longer than 10s contribute excerpts
# instead of being diced from start to finish.
#
#   ./make_reel.sh                      # 90s reel from footage in videos/
#   ./make_reel.sh --duration 30        # typical Reels length
#   ./make_reel.sh --duration 90 --count 3
#   ./make_reel.sh --transition-chance 0.4 --seed 7
#
# Want it unhinged instead?  ./make_reel_insane.sh --duration 20
set -euo pipefail
cd "$(dirname "$0")"
exec python3 ./make_reel.py "$@"
