# Firebase Emulator Suite on Railway

A reusable, self-hosted Firebase development and testing environment. Firebase
Emulator Suite provides local implementations of Firebase services and a browser
UI for inspecting test data. This is **not production Firebase hosting**: the
emulators and deliberately open security rules are for development/testing only.
Never copy these rules to production or store sensitive data here. The UI has no
authentication; restrict access through your network when needed.
No real Firebase account or Google credentials are required.

## Services

| Service | Port |
| --- | --- |
| Emulator Suite web UI | 4000 |
| Authentication | 9099 |
| Cloud Firestore | 8080 |
| Realtime Database | 9000 |
| Cloud Storage | 9199 |
| Emulator Hub (internal discovery/control) | 4400 |

All configured listeners bind to `0.0.0.0`. `singleProjectMode` is enabled.
The CLI also uses internal UI logging on port 4500 and Firestore UI WebSocket
traffic on port 9150. These are UI support endpoints, not additional Firebase services.
Functions, Hosting, Pub/Sub, and Eventarc are not enabled. No external database,
Firebase login, `FIREBASE_TOKEN`, service accounts, or `GOOGLE_APPLICATION_CREDENTIALS`
is required. Use the same project ID in every connected application; the default
`demo-railway` is a demo project, so it needs no real Firebase project.

## Deploy on Railway

1. Push these files to a Git repository and create a Railway service from it.
   Railway builds the root Dockerfile. Keep its default entrypoint/start command.
2. Attach a Railway Volume at **`/data`**. Run one instance against this volume.
   Dedicate it to this service; do not put unrelated files or backups inside it.
3. Set these service variables:

   ```env
   PORT="4000"
   FIREBASE_PROJECT_ID="demo-railway"
   JAVA_TOOL_OPTIONS="-Xms256m -Xmx1024m"
   ```

4. Set `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=120` in Railway so the CLI has time
   to export on shutdown. Increase this for larger datasets and verify export
   completion in logs. Railway's default draining time is zero; see
   [deployment teardown](https://docs.railway.com/deployments/deployment-teardown).
5. Generate a public domain and set its **target port to `4000`**. The web UI
   is served on that port. `PORT` describes this public HTTP target; changing it
   does not change the ports in `firebase.json`.
6. Configure Railway's HTTP healthcheck path as `/` with a 300-second startup
   timeout. The Docker healthcheck also probes the UI, but configure Railway's
   own deployment healthcheck explicitly. UI health is not a durability check.

The other emulators are best accessed through Railway private networking by
applications in the same Railway project/environment. Use this service's actual
private hostname; no domain is hardcoded here. IPv4 connectivity is required by
the requested `0.0.0.0` binding.

Publishing port 4000 serves the UI, but does not forward the other ports. The UI
also makes browser-side requests to emulator endpoints. A browser outside Railway
cannot reach private hostnames: full remote UI functionality and public SDK access
may require separate networking configuration or secure tunnels for the Hub and
emulator ports (including any auxiliary endpoints advertised by the CLI).
An HTTPS UI also needs browser-compatible secure endpoint access. Exposing a
Docker port alone does not publish it through Railway. Keep test endpoints private
unless public access is intentional.

## Connect an application

Example **server/Admin SDK** environment variables (substitute the actual private
hostname for `EMULATOR_PRIVATE_HOST` below):

```env
GCLOUD_PROJECT=demo-railway
FIREBASE_AUTH_EMULATOR_HOST=EMULATOR_PRIVATE_HOST:9099
FIRESTORE_EMULATOR_HOST=EMULATOR_PRIVATE_HOST:8080
FIREBASE_DATABASE_EMULATOR_HOST=EMULATOR_PRIVATE_HOST:9000
FIREBASE_STORAGE_EMULATOR_HOST=EMULATOR_PRIVATE_HOST:9199
```

These host variables have **no `http://` prefix**. For local Docker usage, replace
the hostname with `127.0.0.1`. Initialize the SDK with project ID `demo-railway`,
database URL `https://demo-railway-default-rtdb.firebaseio.com`, and a consistent
test bucket such as `demo-railway.appspot.com`; the emulator variables redirect
supported Admin SDKs to the emulators. Match these values to your configured
project ID. Do not initialize with a production service account.

Browser/client SDKs do not automatically read these server environment variables.
Use their `connectAuthEmulator`, `connectFirestoreEmulator`,
`connectDatabaseEmulator`, and `connectStorageEmulator` APIs before using the
services, with hostnames reachable from the client. See the official
[Firebase connection guides](https://firebase.google.com/docs/emulator-suite/connect_and_prototype).

## Run locally

```sh
docker build -t firebase-emulator-suite .
docker run --name firebase-emulator-suite --stop-timeout 120 \
  -p 127.0.0.1:4000:4000 -p 127.0.0.1:4400:4400 \
  -p 127.0.0.1:8080:8080 -p 127.0.0.1:9000:9000 \
  -p 127.0.0.1:9099:9099 -p 127.0.0.1:9199:9199 \
  -v firebase-emulator-data:/data \
  -e FIREBASE_PROJECT_ID=demo-railway \
  firebase-emulator-suite
```

Open <http://localhost:4000>. In another terminal, stop gracefully with
`docker stop --time 120 firebase-emulator-suite`; restart with
`docker start -a firebase-emulator-suite`. No Docker socket is mounted or used
inside the deployment.

## Persistence and shutdown

The volume at `/data` stores the latest complete emulator snapshot. An empty
volume starts fresh. When `/data/firebase-export-metadata.json` exists, the
entrypoint validates its JSON and referenced export paths and starts Firebase
with `--import=/data`. Invalid snapshots or data without metadata fail startup
instead of being silently overwritten. Existing exports from the previous image
layout remain compatible; back up your volume before upgrading.

A small POSIX shell wrapper stays as PID 1 so it can finish persistence after
Firebase exits. It explicitly handles both SIGTERM and SIGINT, forwards one
SIGTERM to Firebase, and waits for its actual exit status even when a signal
interrupts `wait`. Additional graceful-stop signals do not interrupt synchronization.
Firebase receives `--export-on-exit=/tmp/firebase-export`. This is an ordinary
container directory that Firebase can replace safely; **Firebase never exports
directly over the `/data` mount point**. No Firebase source files are patched.

After Firebase stops successfully:

1. Validate the fresh temporary export. Missing or invalid exports cause a
   nonzero exit and preserve the previous snapshot.
2. Copy the previous volume contents into `/data/.firebase-previous`, flush them,
   and create `/data/.firebase-sync-in-progress` before changing the saved data.
3. Replace the contents of `/data` using standard file operations, copy
   `firebase-export-metadata.json` last, validate and flush the new snapshot.
4. Remove the transaction marker and rollback copy. The mount root itself is
   never removed, renamed, or replaced. `lost+found` is left alone.

A copy failure attempts to restore the previous snapshot and exits nonzero.
If the container dies during synchronization, the marker causes restoration of
that rollback copy at the next boot. Do not manually remove the hidden recovery
files during a failed deployment. Snapshot copying requires volume capacity for
both old and new data (roughly twice the larger snapshot) plus ephemeral disk
space for Firebase's temporary export. Recovery is designed for interrupted file
copies, not as a substitute for volume backups or protection against disk corruption.

Data is held by the emulators while running; this is **not continuous durable
storage**. SIGKILL, an out-of-memory kill, host failure, or a shutdown deadline
can lose changes since the last successful snapshot. Allow enough time for both
Firebase export and the volume copy. On Railway set
`RAILWAY_DEPLOYMENT_DRAINING_SECONDS=120`, increase it for larger datasets, and
verify the `Firebase snapshot persisted to /data.` log message. Graceful restarts
and redeploys import the saved volume data. Keep the project ID consistent across
restarts. See [Firebase's documented persistence flags](https://firebase.google.com/docs/emulator-suite/install_and_configure#startup_options).

## Pinned runtime and updates

- **Node.js 22.23.2**, copied from the official Node image.
- **Eclipse Temurin Java 21.0.12+8 JRE**, using its official Ubuntu Noble image.
  Java is required internally by the Firestore, Database, and Storage rules runtimes.
- **Firebase CLI 15.29.0**, declared in `package.json`; `package-lock.json` locks
  its dependency tree and integrity hashes. These files install tooling only;
  there is no application framework. The runtime does not include npm.

Both base-image releases are pinned by manifest digest in `Dockerfile`. Only the
Node binary and CLI dependencies cross build stages; no Debian repositories or
Debian system libraries are added to the Ubuntu runtime. Emulator artifacts are
prefetched at image build time with the pinned CLI, avoiding startup downloads.
Builds require network access. Dependency versions and base images are fixed;
this does not promise byte-for-byte identical image timestamps or perpetual
availability of upstream download servers.

To update intentionally, change the image tags **and digests** in `Dockerfile`.
On a machine with Node/npm, run
`npm install --package-lock-only --save-exact firebase-tools@<version>` and commit
both package files. Rebuild, run the tests below, and test restoration of a copy
of your existing export before publishing the new image. Keep a tested image
digest and volume backup for rollback. Pinned dependencies need periodic security
reviews and updates; they do not update automatically.

Dependency audit on 2026-09-08: `npm audit --omit=dev --package-lock-only`
reported **9 moderate findings and no high/critical findings** in the locked npm
dependency tree. Compatible automatic fixes made no changes. Findings include
upstream `qs`, `stream-json`, OpenTelemetry, and `uuid` advisories and their parent
dependency chains; this is not nine independent issues. For example, see the
[qs denial-of-service advisory](https://github.com/advisories/GHSA-4mjr-xmp4-gh2g)
and [stream-json denial-of-service advisory](https://github.com/advisories/GHSA-528h-pc64-c93x).
Review current advisories and their reachability before exposing this service;
this template does not claim to be vulnerability-free. The npm audit does not
cover operating-system packages or the downloaded Java emulator artifacts.

`JAVA_TOOL_OPTIONS` remains configurable and defaults to
`-Xms256m -Xmx1024m`. This limit applies to each JVM, not the whole container.
Allow memory for multiple JVMs, Node.js, the UI, and export work (start with 4 GiB
and monitor). The Docker healthcheck requests `http://127.0.0.1:4000/` and checks
for HTTP 200; it cannot guarantee successful future exports.

## Validation before publishing

```sh
docker build -t firebase-emulator-suite .
python3 tests/entrypoint.py
python3 tests/smoke.py firebase-emulator-suite
```

The tests require Python 3, local Node.js for wrapper tests, and Docker for the
container smoke test. They use disposable data and containers, never production
Firebase credentials. They verify signal handling, failure/recovery behavior,
all four service writes, and persistence across container replacement.

The public UI is unauthenticated, the rules are intentionally open, and browser
access to private emulator endpoints requires separate connectivity. Review those
access choices, the volume attachment, healthcheck, shutdown grace period, and
resource limits in your Railway template settings before publishing. The image
is portable to ordinary Docker hosts and uses no Docker socket, external database,
reverse proxy, or unrelated service at runtime.
