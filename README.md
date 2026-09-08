# Firebase Emulator Suite on Railway

A reusable, self-hosted Firebase development and testing environment. Firebase
Emulator Suite provides local implementations of Firebase services and a browser
UI for inspecting test data. This is **not production Firebase hosting**: the
emulators and deliberately open security rules are for development/testing only.
Never copy these rules to production or store sensitive data here. The UI has no
authentication; restrict access through your network when needed.

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
Functions, Hosting, Pub/Sub, and Eventarc are not enabled. No external database,
Firebase login, real Firebase credentials, or `GOOGLE_APPLICATION_CREDENTIALS`
is required. Use the same project ID in every connected application; the default
`demo-railway` is a demo project, so it needs no real Firebase project.

## Deploy on Railway

1. Push these files to a Git repository and create a Railway service from it.
   Railway builds the root Dockerfile. Keep its default entrypoint/start command.
2. Attach a Railway Volume at **`/data`**. Run one instance against this volume.
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

## Persistence and runtime

The POSIX entrypoint creates `/data` and runs the CLI using `exec`, so it receives
shutdown signals directly. It always uses `--export-on-exit=/data`, and adds
`--import=/data` only when `/data/firebase-export-metadata.json` exists. The CLI
restores exported Auth, Firestore, Database, and Storage data. An invalid export
fails startup rather than silently discarding data.

The Dockerfile includes a guarded compatibility patch for Firebase CLI's export
implementation (verified against 15.29.0). Upstream deletes and replaces the
export directory, which fails with `EBUSY` at a mounted `/data`. The patch keeps
the mount point, replaces its contents, and moves metadata last. It applies only
to exports targeting `/data`; a changed upstream implementation fails the build
and requires reviewing the patch. Dedicate this volume to emulator exports: its
contents are replaced on export.

The volume stores **shutdown snapshots**, not continuous durable writes. Crashes,
out-of-memory kills, or insufficient shutdown time can lose changes since the
last successful export. Back up successful exports separately, and keep the same
project ID when restoring. See [Firebase CLI persistence options](https://firebase.google.com/docs/emulator-suite/install_and_configure#startup_options).

The image starts from Node.js 22 on Debian Bookworm. Java 21 JRE is required
internally by the Java-based emulators. Because Bookworm does not provide
`openjdk-21-jre-headless`, the Dockerfile selects Debian Trixie for the Java
installation and prioritizes Trixie security updates. This
upgrades some runtime libraries; it is not a pure Bookworm package set.

`firebase-tools@latest` installs the stable npm release at build time. Emulator
artifacts are downloaded during the build, requiring internet access then.
Use `docker build --pull --no-cache` to refresh cached CLI/runtime layers and
validate saved exports after updates. Retain a tested image digest for rollback.
`JAVA_TOOL_OPTIONS` applies to each Java process, not to the whole container;
allow memory for multiple JVMs, Node.js, and the UI (start with 4 GiB and monitor).
