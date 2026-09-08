#!/bin/sh
set -e

mkdir -p /data
PROJECT_ID="${FIREBASE_PROJECT_ID:-demo-railway}"

set -- emulators:start \
  --project "$PROJECT_ID" \
  --only auth,firestore,database,storage \
  --export-on-exit=/data

if [ -f /data/firebase-export-metadata.json ]; then
  set -- "$@" --import=/data
fi

exec firebase "$@"
