#!/usr/bin/env bash
# Build the signed LOCKE//LINK APK (docs/phone-app/APK.md).
#   scripts/build-apk.sh                       # default origin
#   LINK_ORIGIN=https://host.tailnet.ts.net scripts/build-apk.sh
#   LINK_VERSION_NAME=1.1.0 LINK_VERSION_CODE=2 scripts/build-apk.sh
# Output: android/dist/LOCKE-LINK.apk (+ ~/Downloads/LOCKE-LINK.apk), sha256, aapt badging.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"

export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"
export JAVA_HOME="${LINK_JAVA_HOME:-$(ls -d "$HOME"/.local/share/jdk-21* 2>/dev/null | head -1)}"
[ -x "$JAVA_HOME/bin/javac" ] || { echo "need a JDK 17/21: set LINK_JAVA_HOME" >&2; exit 1; }
export PATH="$JAVA_HOME/bin:$PATH"
BT="$(ls -d "$ANDROID_HOME"/build-tools/*/ | sort -V | tail -1)"
ORIGIN="${LINK_ORIGIN:-https://ripjk.tailea4e7f.ts.net}"
HOST="${ORIGIN#https://}"; HOST="${HOST%%/*}"
KEYDIR="$HOME/.jarvis/android"
KS="$KEYDIR/locke-link.keystore"
PASSF="$KEYDIR/keystore.pass"

# 1. signing key: created once, kept OUTSIDE the repo. Back it up (APK.md).
if [ ! -f "$KS" ]; then
  mkdir -p "$KEYDIR"; chmod 700 "$KEYDIR"
  (umask 177; head -c 24 /dev/urandom | base64 | tr -d '/+=\n' > "$PASSF")
  keytool -genkeypair -keystore "$KS" -storetype PKCS12 -alias locke-link -keyalg RSA -keysize 4096 \
    -validity 10000 -storepass:file "$PASSF" -keypass:file "$PASSF" \
    -dname "CN=LOCKE//LINK, O=Jarvis, C=US"
  chmod 600 "$KS" "$PASSF"
  echo "created signing key $KS (BACK IT UP)"
fi

# Public Digital Asset Links metadata must use this signing certificate.
uv run python scripts/android-assetlinks.py

# 2. web bundle built for the configured origin (CSP connect-src), copied into the app assets
LINK_ORIGIN_HOST="$HOST" pnpm exec vite build
rm -rf android/app/src/main/assets/www
mkdir -p android/app/src/main/assets/www
cp -r dist/. android/app/src/main/assets/www/
rm -f android/app/src/main/assets/www/sw.js   # the shell serves the bundle itself; no service worker

# 3. icons (committed; regenerate only when the SVGs change): scripts/android-icons.sh

# 4. gradle
echo "sdk.dir=$ANDROID_HOME" > android/local.properties
cd android
LINK_KEYSTORE="$KS" LINK_KEYSTORE_PASS="$(cat "$PASSF")" ./gradlew --no-daemon -q assembleRelease \
  -PlinkOrigin="$ORIGIN" -PlinkVersionName="${LINK_VERSION_NAME:-1.0.0}" -PlinkVersionCode="${LINK_VERSION_CODE:-1}"
cd ..

# 5. collect + verify
mkdir -p android/dist
cp android/app/build/outputs/apk/release/app-release.apk android/dist/LOCKE-LINK.apk
"$BT/apksigner" verify --verbose --print-certs android/dist/LOCKE-LINK.apk > android/dist/apksigner.txt; head -8 android/dist/apksigner.txt
sha256sum android/dist/LOCKE-LINK.apk | tee android/dist/LOCKE-LINK.apk.sha256
"$BT/aapt" dump badging android/dist/LOCKE-LINK.apk | grep -E "^(package|sdkVersion|targetSdkVersion|uses-permission|application-label:|launchable)" | tee android/dist/badging.txt
mkdir -p "$HOME/Downloads"; cp android/dist/LOCKE-LINK.apk "$HOME/Downloads/LOCKE-LINK.apk"
ls -l android/dist/LOCKE-LINK.apk "$HOME/Downloads/LOCKE-LINK.apk"
