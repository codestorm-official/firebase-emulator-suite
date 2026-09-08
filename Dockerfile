# Official runtime releases pinned by multi-platform manifest digest.
FROM node:22.23.2-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS node
WORKDIR /opt/firebase
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# Java and its OS libraries stay in their supported Ubuntu Noble image.
# Only Node's portable binary and the locked CLI dependencies are copied.
FROM eclipse-temurin:21.0.12_8-jre-noble@sha256:96975602e131485862eb8cd32927face8a06d7591a5e865944b634a701d9df72
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /opt/firebase /opt/firebase
ENV PATH="/opt/firebase/node_modules/.bin:${PATH}"
WORKDIR /app
RUN node --version && java -version \
    && firebase setup:emulators:firestore \
    && firebase setup:emulators:database \
    && firebase setup:emulators:storage \
    && firebase setup:emulators:ui
COPY firebase.json firestore.rules database.rules.json storage.rules entrypoint.sh ./
RUN chmod +x /app/entrypoint.sh

ENV PORT=4000 \
    FIREBASE_PROJECT_ID=demo-railway \
    JAVA_TOOL_OPTIONS="-Xms256m -Xmx1024m"
EXPOSE 4000 4400 8080 9000 9099 9199
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD node -e 'const r=require("node:http").get("http://127.0.0.1:4000/",s=>process.exit(s.statusCode===200?0:1));r.setTimeout(4000,()=>{r.destroy();process.exit(1)});r.on("error",()=>process.exit(1));'
STOPSIGNAL SIGTERM
ENTRYPOINT ["/app/entrypoint.sh"]
