#!/usr/bin/env python3
"""Real Docker volume test: fresh boot, all services, TERM/INT, container replacement."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

IMAGE = sys.argv[1] if len(sys.argv) > 1 else 'firebase-emulator-suite'
NAME = 'firebase-template-test-' + uuid.uuid4().hex[:10]
VOLUME = NAME + '-data'
ARTIFACTS = Path(tempfile.mkdtemp(prefix=NAME + '-'))
PORTS = {}
ROUND = 0


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.STDOUT).strip()


def state():
    return json.loads(docker('inspect', NAME))[0]


def request(port, path, method='GET', data=None, raw=False):
    headers = {}
    if data is not None:
        data = data if raw else json.dumps(data).encode()
        headers['Content-Type'] = 'application/octet-stream' if raw else 'application/json'
    host_port = PORTS[str(port) + '/tcp'][0]['HostPort']
    req = urllib.request.Request('http://127.0.0.1:' + host_port + path,
                                 data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        body = response.read()
        return body if raw else json.loads(body)


def start():
    global PORTS, ROUND
    ROUND += 1
    args = ['run', '-d', '--name', NAME, '--stop-timeout', '120', '-v', VOLUME + ':/data']
    for port in [4000, 4400, 8080, 9000, 9099, 9199]:
        args += ['-p', '127.0.0.1::' + str(port)]
    docker(*args, IMAGE)
    PORTS = state()['NetworkSettings']['Ports']
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        info = state()
        if not info['State']['Running']:
            raise AssertionError(docker('logs', NAME))
        try:
            mapping = request(4400, '/emulators')
            assert all(x in mapping for x in ['auth', 'firestore', 'database', 'storage', 'ui'])
            assert set(mapping) <= {'hub', 'logging', 'auth', 'firestore', 'database', 'storage', 'ui'}
            request(4000, '/', raw=True)
            if info['State']['Health']['Status'] == 'healthy':
                return docker('exec', NAME, 'stat', '-c', '%i', '/data')
        except (OSError, ValueError, AssertionError):
            pass
        time.sleep(1)
    raise AssertionError('Emulators and UI healthcheck did not become ready')


def stop(signal='SIGTERM'):
    if signal == 'SIGTERM':
        docker('stop', '--time', '120', NAME)
    else:
        docker('kill', '--signal', signal, NAME)
        deadline = time.monotonic() + 120
        while state()['State']['Running']:
            if time.monotonic() > deadline:
                raise AssertionError('Graceful shutdown timed out')
            time.sleep(1)
    info = state()
    logs = docker('logs', NAME)
    (ARTIFACTS / f'container-{ROUND}.log').write_text(logs)
    assert info['State']['ExitCode'] == 0, logs
    assert 'Firebase snapshot persisted to /data.' in logs, logs
    assert 'downloading ' not in logs.lower(), 'Unexpected runtime artifact download'
    docker('cp', NAME + ':/data/firebase-export-metadata.json', str(ARTIFACTS / f'metadata-{ROUND}.json'))
    metadata = json.loads((ARTIFACTS / f'metadata-{ROUND}.json').read_text())
    assert all(x in metadata for x in ['auth', 'firestore', 'database', 'storage'])
    docker('rm', NAME)


AUTH = '/identitytoolkit.googleapis.com/v1/'
DOC = '/v1/projects/demo-railway/databases/(default)/documents/smoke/persist'
DB = '/smoke.json?ns=demo-railway-default-rtdb'
BUCKET = '/v0/b/demo-railway.appspot.com/o'
USER = {'email': 'smoke@example.test', 'password': 'smoke-password', 'returnSecureToken': True}

try:
    docker('volume', 'create', VOLUME)
    inode = start()
    assert docker('exec', NAME, 'cat', '/app/entrypoint.sh') == (Path(__file__).resolve().parents[1] / 'entrypoint.sh').read_text().strip()
    assert docker('exec', NAME, 'node', '--version') == 'v22.23.2'
    assert '21.0.12' in docker('exec', NAME, 'java', '-version')
    assert docker('exec', NAME, 'firebase', '--version') == '15.29.0'
    request(9099, AUTH + 'accounts:signUp?key=demo-key', 'POST', USER)
    request(8080, DOC, 'PATCH', {'fields': {'value': {'stringValue': 'persisted'}}})
    request(9000, DB, 'PUT', {'value': 'persisted'})
    request(9199, BUCKET + '?uploadType=media&name=smoke.txt', 'POST', b'persisted', raw=True)
    print('Fresh volume: healthcheck and all four service writes passed', flush=True)
    stop()
    assert start() == inode, 'Volume mount root was replaced'
    request(9099, AUTH + 'accounts:signInWithPassword?key=demo-key', 'POST', USER)
    assert request(8080, DOC)['fields']['value']['stringValue'] == 'persisted'
    assert request(9000, DB) == {'value': 'persisted'}
    assert request(9199, BUCKET + '/smoke.txt?alt=media', raw=True) == b'persisted'
    print('SIGTERM and container replacement: all four services restored', flush=True)
    request(9000, DB, 'PUT', {'value': 'second-snapshot'})
    stop('SIGINT')
    assert start() == inode
    assert request(9000, DB) == {'value': 'second-snapshot'}
    print('SIGINT and another container replacement: latest snapshot restored', flush=True)
    stop()
finally:
    # Only remove resources with this test's unique names. Preserve diagnostic logs.
    try:
        info = state()
        if info['State']['Running']:
            docker('stop', '--time', '120', NAME)
        (ARTIFACTS / 'cleanup.log').write_text(docker('logs', NAME))
        docker('rm', NAME)
    except subprocess.CalledProcessError:
        pass
    docker('volume', 'rm', VOLUME)
    print('Test logs:', ARTIFACTS, flush=True)
