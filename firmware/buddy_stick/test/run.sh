#!/bin/sh
# Host tests of the stick's codec and playback ring, under address and undefined-behaviour sanitizers.
set -eu
cd "$(dirname "$0")/../../.."
OUT=$(mktemp -d)
c++ -std=c++17 -Wall -Wextra -Werror -g -fsanitize=address,undefined -fno-sanitize-recover=all \
  firmware/buddy_stick/test/host_test.cpp -o "$OUT/host_test"
"$OUT/host_test" tools/stick_link/vectors.txt
