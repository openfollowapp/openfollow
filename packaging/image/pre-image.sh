#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
#
# SRCROOT pre-image hook. Runs after the image layout generated genimage.cfg
# and before genimage executes (bin/runner pre-image phase, SRCROOT last).
# Chains openfollow-root-ref.sh onto each partition's exec-pre so that right
# after the layout's setup.sh writes the default by-slot root/boot references
# they are rewritten to filesystem UUIDs. See openfollow-root-ref.sh for why.
#
# Args (from bin/runner pre-image phase): <target_path> <image_outputdir>
set -eu

OUTDIR="${2:?image outputdir not provided}"

CFG="$OUTDIR/genimage.cfg"
if [ ! -f "$CFG" ]; then
   CFG=$(find "$OUTDIR" -maxdepth 2 -name genimage.cfg 2>/dev/null | head -n1 || true)
fi
[ -n "${CFG:-}" ] && [ -f "$CFG" ] || {
   echo "pre-image: genimage.cfg not found under $OUTDIR" >&2
   exit 1
}

# Absolute path to our patch script (sits next to this hook in SRCROOT).
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PATCH="$HERE/openfollow-root-ref.sh"
[ -x "$PATCH" ] || {
   echo "pre-image: $PATCH missing or not executable" >&2
   exit 1
}

# Idempotent: never chain twice.
if grep -q "openfollow-root-ref.sh" "$CFG"; then
   exit 0
fi

# Append the UUID rewrite after each setup.sh exec-pre, passing the outputdir so
# the patch can read img_uuids without relying on env propagation into genimage.
# Temp-file edit keeps this portable across GNU and BSD sed (no -i).
TMP=$(mktemp)
sed \
   -e "s| BOOT\"| BOOT \&\& '$PATCH' BOOT '$OUTDIR'\"|" \
   -e "s| ROOT\"| ROOT \&\& '$PATCH' ROOT '$OUTDIR'\"|" \
   "$CFG" >"$TMP"
mv "$TMP" "$CFG"

# Fail loudly if the exec-pre format changed and the chain did not land - better
# a red build than silently shipping a by-slot image that boots to a shell.
for lbl in BOOT ROOT; do
   grep -q "openfollow-root-ref.sh' $lbl" "$CFG" || {
      echo "pre-image: could not chain root-ref patch for $lbl (genimage.cfg exec-pre format changed?)" >&2
      exit 1
   }
done
