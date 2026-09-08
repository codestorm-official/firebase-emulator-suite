#!/bin/sh
set -eu

PROJECT_ID="${FIREBASE_PROJECT_ID:-demo-railway}"
EXPORT_DIR=/tmp/firebase-export
child_pid=
stop_requested=0
wait_interrupted=0

request_stop() {
  wait_interrupted=1
  if [ "$stop_requested" -eq 0 ]; then
    stop_requested=1
    if [ -n "$child_pid" ]; then
      # Normalize SIGINT to SIGTERM: background shell jobs can inherit ignored INT.
      kill -TERM "$child_pid" 2>/dev/null || :
    fi
  fi
}
trap request_stop TERM INT

fail() { echo "Persistence error: $*" >&2; exit 1; }

# Validate the documented export metadata and referenced paths, not CLI internals.
valid_export() {
  node - "$1" <<'NODE'
const fs = require('node:fs');
const path = require('node:path');
try {
  const root = path.resolve(process.argv[2]);
  const metadata = JSON.parse(fs.readFileSync(path.join(root, 'firebase-export-metadata.json'), 'utf8'));
  const inside = value => {
    if (typeof value !== 'string' || !value || path.isAbsolute(value)) throw Error('Invalid export path');
    const resolved = fs.realpathSync(path.resolve(root, value));
    if (!resolved.startsWith(root + path.sep)) throw Error('Export path escapes snapshot');
    return resolved;
  };
  for (const service of ['auth', 'firestore', 'database', 'storage']) {
    if (!fs.statSync(inside(metadata[service]?.path)).isDirectory()) throw Error('Missing ' + service + ' export');
  }
  if (!fs.statSync(inside(metadata.firestore.metadata_file)).isFile()) throw Error('Missing Firestore metadata');
} catch (error) {
  console.error('Invalid Firebase export: ' + error.message);
  process.exit(1);
}
NODE
}

# /data is dedicated to snapshots. Never remove or rename the mount root.
# Keep rollback bookkeeping and the filesystem's lost+found out of this loop.
current_files() {
  for item in /data/* /data/.[!.]* /data/..?*; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    case "$item" in
      /data/.firebase-previous|/data/.firebase-sync-in-progress|/data/lost+found) continue ;;
    esac
    case "$1" in
      backup) cp -a -- "$item" /data/.firebase-previous/ || return 1 ;;
      clear) rm -rf -- "$item" || return 1 ;;
      empty) return 1 ;;
    esac
  done
  return 0
}

recover_sync() {
  if [ -f /data/.firebase-sync-in-progress ]; then
    echo 'Recovering the previous snapshot after an interrupted synchronization.'
    [ -d /data/.firebase-previous ] || return 1
    current_files clear || return 1
    cp -a /data/.firebase-previous/. /data/ || return 1
    sync || return 1
    rm /data/.firebase-sync-in-progress || return 1
    sync || return 1
  fi
  rm -rf /data/.firebase-previous || return 1
}

persist_export() {
  # Copy the old snapshot before touching it. Keep it until the new copy commits.
  mkdir /data/.firebase-previous || return 1
  current_files backup || return 1
  sync || return 1
  touch /data/.firebase-sync-in-progress || return 1
  sync || return 1
  current_files clear || return 1
  for item in "$EXPORT_DIR"/* "$EXPORT_DIR"/.[!.]* "$EXPORT_DIR"/..?*; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    [ "$item" != "$EXPORT_DIR/firebase-export-metadata.json" ] || continue
    cp -a -- "$item" /data/ || return 1
  done
  # Metadata is the last file committed, so partial copies aren't valid exports.
  cp -a "$EXPORT_DIR/firebase-export-metadata.json" /data/ || return 1
  valid_export /data || return 1
  sync || return 1
  rm /data/.firebase-sync-in-progress || return 1
  sync || return 1
  rm -rf /data/.firebase-previous || return 1
}

mkdir -p /data
recover_sync || fail 'Could not recover the previous snapshot; volume left for inspection.'
set -- emulators:start --project "$PROJECT_ID" \
  --only auth,firestore,database,storage --export-on-exit="$EXPORT_DIR"
if [ -f /data/firebase-export-metadata.json ]; then
  valid_export /data || fail 'Existing snapshot is invalid; refusing to overwrite it.'
  set -- "$@" --import=/data
else
  current_files empty || fail 'Volume contains data without export metadata; refusing a fresh start.'
fi
# A restarted container can retain /tmp. Never mistake a stale export for a new one.
rm -rf "$EXPORT_DIR"
[ "$stop_requested" -eq 0 ] || exit 0
firebase "$@" &
child_pid=$!
# Cover a signal arriving between spawning Firebase and recording its PID.
[ "$stop_requested" -eq 0 ] || kill -TERM "$child_pid" 2>/dev/null || :

# POSIX wait is interrupted by trapped signals; wait again for Firebase's status.
while :; do
  wait_interrupted=0
  if wait "$child_pid"; then status=0; else status=$?; fi
  [ "$wait_interrupted" -ne 0 ] || break
done
child_pid=
# Finish the disk transaction even if another graceful-stop signal arrives.
trap '' TERM INT
[ "$status" -eq 0 ] || exit "$status"
valid_export "$EXPORT_DIR" || fail 'No complete shutdown export; previous snapshot retained.'
if ! persist_export; then
  recover_sync || fail 'Synchronization and rollback failed; retain the volume for recovery.'
  fail 'Synchronization failed; previous snapshot restored.'
fi
echo 'Firebase snapshot persisted to /data.'
exit "$status"
