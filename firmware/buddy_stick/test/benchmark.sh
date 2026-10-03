#!/bin/sh
# Compare the previous algorithms to the production ring/queue with equal-output checks.
set -eu
cd "$(dirname "$0")/../../.."
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT HUP INT TERM
c++ -std=c++17 -O2 -Wall -Wextra -Werror firmware/buddy_stick/test/benchmark.cpp -o "$OUT/ring_bench"
"$OUT/ring_bench" 1200
# Swift executable top-level code is named main.swift when compiled alongside helper types.
cp ios/BuddyLink/Tests/benchmark.swift "$OUT/main.swift"
swiftc -O ios/BuddyLink/Shared/PacketQueue.swift "$OUT/main.swift" -o "$OUT/queue_bench"
"$OUT/queue_bench"
echo EMBEDDED_BENCHMARK_OK
