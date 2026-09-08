FROM node:22-bookworm

# Bookworm does not package Java 21. Use Trixie for the Java install and
# retain security updates; this also upgrades required system libraries.
RUN printf '%s\n' \
      'deb https://deb.debian.org/debian trixie main' \
      'deb https://security.debian.org/debian-security trixie-security main' \
      > /etc/apt/sources.list.d/java21.list \
    && printf '%s\n' \
      'Package: *' 'Pin: release n=trixie' 'Pin-Priority: 100' '' \
      'Package: *' 'Pin: release n=trixie-security' 'Pin-Priority: 990' \
      > /etc/apt/preferences.d/java21 \
    && apt-get update \
    && apt-get install -y --no-install-recommends -t trixie openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN npm install --global firebase-tools@latest \
    && npm cache clean --force

WORKDIR /app
COPY firebase.json firestore.rules database.rules.json storage.rules entrypoint.sh ./
RUN chmod +x /app/entrypoint.sh \
    && firebase setup:emulators:firestore \
    && firebase setup:emulators:database \
    && firebase setup:emulators:storage \
    && firebase setup:emulators:ui


# Firebase CLI replaces the export directory, which fails with EBUSY when
# /data is a mounted volume. Preserve the mount and commit metadata last.
# Fail the build if upstream changes this code, so upgrades require review.
RUN node <<'NODE'
const fs = require('node:fs');
const file = '/usr/local/lib/node_modules/firebase-tools/lib/emulator/hubExport.js';
const source = fs.readFileSync(file, 'utf8');
const original = `(0, node_fs_1.rmSync)(this.exportPath, { recursive: true });
        fse.moveSync(this.tmpDir, this.exportPath);`;
if (source.split(original).length !== 2) {
  throw new Error('Firebase export implementation changed; review the /data mount compatibility patch.');
}
const replacement = `if (require('node:path').resolve(this.exportPath) === '/data') {
            fse.emptyDirSync(this.exportPath);
            const names = fse.readdirSync(this.tmpDir)
                .filter(name => name !== HubExport.METADATA_FILE_NAME);
            names.push(HubExport.METADATA_FILE_NAME);
            for (const name of names) {
                fse.moveSync(require('node:path').join(this.tmpDir, name),
                    require('node:path').join(this.exportPath, name));
            }
            fse.removeSync(this.tmpDir);
        } else {
            ${original}
        }`;
fs.writeFileSync(file, source.replace(original, replacement));
NODE

ENV PORT=4000 \
    FIREBASE_PROJECT_ID=demo-railway \
    JAVA_TOOL_OPTIONS="-Xms256m -Xmx1024m"

EXPOSE 4000 4400 8080 9000 9099 9199
ENTRYPOINT ["/app/entrypoint.sh"]
