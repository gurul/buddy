#!/bin/sh
# Build Buddy Link for the owner's iPhone, and BuddyProbe for the Mac bench. Install the app with
#   sh ios/BuddyLink/build.sh --install
set -eu
cd "$(dirname "$0")"
xcodegen generate --quiet
DERIVED="${DERIVED:-$PWD/build}"
xcodebuild -project BuddyLink.xcodeproj -scheme BuddyLink -destination 'generic/platform=iOS' \
  -derivedDataPath "$DERIVED" -allowProvisioningUpdates -quiet build
xcodebuild -project BuddyLink.xcodeproj -scheme BuddyProbe -destination 'platform=macOS,arch=arm64' \
  -derivedDataPath "$DERIVED" -allowProvisioningUpdates -quiet build
echo "built: $DERIVED/Build/Products/Debug-iphoneos/BuddyLink.app and $DERIVED/Build/Products/Debug/BuddyProbe.app"
if [ "${1:-}" = "--install" ]; then
  DEVICE="${BUDDY_IPHONE:-$(xcrun devicectl list devices 2>/dev/null | awk '/physical/ && /iPhone/ && /available/ {for (i=1;i<=NF;i++) if ($i ~ /^[0-9A-F]{8}-[0-9A-F]{16}$/) print $i; exit}')}"
  [ -n "$DEVICE" ] || { echo "no paired iPhone available (unlock it, same network or cable)" >&2; exit 1; }
  xcrun devicectl device install app --device "$DEVICE" "$DERIVED/Build/Products/Debug-iphoneos/BuddyLink.app"
fi
