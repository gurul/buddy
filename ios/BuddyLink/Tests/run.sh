#!/bin/sh
# The app's codec on the Mac, against the vectors the Python reference wrote.
set -eu
cd "$(dirname "$0")/../../.."
OUT=$(mktemp -d)
swiftc -O -o "$OUT/codec_test" ios/BuddyLink/Shared/ADPCM.swift ios/BuddyLink/Tests/main.swift
"$OUT/codec_test" tools/stick_link/vectors.txt
