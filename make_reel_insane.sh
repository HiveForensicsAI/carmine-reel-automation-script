#!/usr/bin/env bash
# Insane fast-cut 9:16 1080p Reel — punch zooms, glitches, flashes, smash wipes.
#
#   ./make_reel_insane.sh --duration 20
#   ./make_reel_insane.sh --duration 30 --count 2
#   ./make_reel_insane.sh --flash-chance 0.25 --transition-chance 0.6
#
# Pair with ./make_reel.sh when you want the cleaner original.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 ./make_reel_insane.py "$@"
