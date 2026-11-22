#!/bin/sh
# Bootstrap a toolchain hotfix on an installation that predates loment-hotupdate.
#
#   1. Compare this script and loment-hotupdate-linux-x64 with the digests published on
#      the release page (out of band -- do not trust a digest inside the archive).
#   2. sh bootstrap-hotfix.sh PREFIX RELEASE_DIR
#
# The script only creates the two generation slots, installs the updater + pinned key +
# baseline, and runs the updater. The updater verifies the Ed25519 signature and every
# file digest before writing anything.
set -eu
prefix=${1:?usage: bootstrap-hotfix.sh PREFIX RELEASE_DIR}
rel=${2:?usage: bootstrap-hotfix.sh PREFIX RELEASE_DIR}
shift 2

mkdir -p "$prefix/bin" \
         "$prefix/share/loment/hot/gen/a/bin" \
         "$prefix/share/loment/hot/gen/b/bin"
cp "$rel/loment-hotupdate-linux-x64" "$prefix/bin/loment-hotupdate"
chmod 755 "$prefix/bin/loment-hotupdate"
cp "$rel/release-key.ed25519" "$prefix/share/loment/hot/release-key.ed25519"
cp "$rel/baseline.txt" "$prefix/share/loment/hot/baseline.txt"
# A stock 0.1.5.1 launcher only runs bin/; it does not know about generations. Install the
# launcher that resolves share/loment/hot/current, otherwise the payload lands on disk and
# nothing uses it.
cp "$rel/loment-launcher.sh" "$prefix/bin/loment"
chmod 755 "$prefix/bin/loment"
cp "$rel/loment-launcher.cmd" "$prefix/bin/loment.cmd"

exec "$prefix/bin/loment-hotupdate" \
    --prefix "$prefix" \
    --payload "$rel/payload-linux-x64.hot" \
    --sig "$rel/payload-linux-x64.hot.sig" "$@"
