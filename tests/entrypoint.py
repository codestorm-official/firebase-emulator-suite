#!/usr/bin/env python3
"""Exercise wrapper signals and failure recovery in isolated temporary directories."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

SOURCE = Path(__file__).resolve().parents[1] / 'entrypoint.sh'
METADATA = 'firebase-export-metadata.json'


def snapshot(root, value):
    root.mkdir(parents=True, exist_ok=True)
    metadata = {'version': '15.29.0'}
    for service in ['auth', 'firestore', 'database', 'storage']:
        name = service + '_export'
        (root / name).mkdir(exist_ok=True)
        (root / name / 'value').write_text(value)
        metadata[service] = {'path': name}
    metadata['firestore']['metadata_file'] = 'firestore_export/value'
    (root / METADATA).write_text(json.dumps(metadata))


FAKE = '''import json, os, pathlib, signal, sys, time
args = sys.argv[1:]
root = pathlib.Path(os.environ['TEST_ROOT'])
mode = os.environ.get('MODE', 'ok')
if mode == 'startup-failure': sys.exit(7)
def stop(*unused):
    time.sleep(0.15)
    if mode not in ('no-export', 'startup-failure'):
        target = pathlib.Path(next(a.split('=',1)[1] for a in args if a.startswith('--export-on-exit=')))
        target.mkdir(parents=True, exist_ok=True)
        metadata = {'version':'15.29.0'}
        for service in ['auth','firestore','database','storage']:
            folder = target / (service+'_export')
            folder.mkdir(exist_ok=True)
            (folder/'value').write_text('new')
            metadata[service] = {'path':folder.name}
        metadata['firestore']['metadata_file'] = 'firestore_export/value'
        (target/'firebase-export-metadata.json').write_text(json.dumps(metadata))
    sys.exit(9 if mode == 'nonzero-export' else 0)
signal.signal(signal.SIGTERM, stop)
(root/'ready.json').write_text(json.dumps(args))
while True: time.sleep(0.05)
'''


def run_case(name, prepare=None, mode='ok', stop=signal.SIGTERM, expected=0,
             project=None, fail_copy=False, repeat_signal=False):
    with tempfile.TemporaryDirectory(prefix='firebase-wrapper-') as temp:
        root = Path(temp)
        data, export = root / 'data', root / 'export'
        data.mkdir()
        inode = data.stat().st_ino
        if prepare:
            prepare(data, export)
        (root / 'entrypoint.sh').write_text(SOURCE.read_text().replace('/tmp/firebase-export', str(export)).replace('/data', str(data)))
        fake = root / 'firebase'
        fake.write_text('#!' + sys.executable + '\n' + FAKE)
        fake.chmod(0o755)
        # Keep wrapper tests fast; the real container smoke test uses actual sync.
        (root / 'sync').symlink_to(shutil.which('true'))
        if fail_copy:
            real_cp = shutil.which('cp')
            cp = root / 'cp'
            cp.write_text('#!' + sys.executable + '\nimport os,sys\n'
                          + f'if {str(export / "auth_export")!r} in sys.argv: sys.exit(2)\n'
                          + f'os.execv({real_cp!r}, [{real_cp!r}, *sys.argv[1:]])\n')
            cp.chmod(0o755)
        env = {**os.environ, 'PATH': str(root) + ':' + os.environ['PATH'], 'TEST_ROOT': str(root), 'MODE': mode}
        env.pop('FIREBASE_PROJECT_ID', None)
        if project is not None:
            env['FIREBASE_PROJECT_ID'] = project
        with (root / 'log').open('w+') as log:
            process = subprocess.Popen(['sh', str(root / 'entrypoint.sh')], env=env, stdout=log, stderr=log, start_new_session=True)
            try:
                deadline = time.monotonic() + 10
                while not (root / 'ready.json').exists() and process.poll() is None:
                    if time.monotonic() > deadline:
                        raise AssertionError('Wrapper did not start fake Firebase')
                    time.sleep(0.02)
                if process.poll() is None:
                    args = json.loads((root / 'ready.json').read_text())
                    assert args[args.index('--project') + 1] == (project or 'demo-railway')
                    assert '--export-on-exit=' + str(export) in args
                    if (data / METADATA).exists():
                        assert '--import=' + str(data) in args
                    else:
                        assert not any(a.startswith('--import=') for a in args)
                    process.send_signal(stop)
                    if repeat_signal:
                        time.sleep(0.03)
                        process.send_signal(stop)
                code = process.wait(timeout=10)
                log.seek(0)
                output = log.read()
                assert code == expected, (name, code, output)
                assert data.stat().st_ino == inode, 'Mount root was replaced'
                if expected == 0:
                    assert (data / 'auth_export/value').read_text() == 'new'
                    assert not (data / '.firebase-sync-in-progress').exists()
                elif (data / 'auth_export/value').exists():
                    assert (data / 'auth_export/value').read_text() == 'old', output
                print(name + ': passed', flush=True)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def previous(data, export):
    snapshot(data, 'old')


def interrupted(data, export):
    snapshot(data / '.firebase-previous', 'old')
    snapshot(data, 'partial')
    (data / '.firebase-sync-in-progress').touch()


def stale(data, export):
    snapshot(data, 'old')
    snapshot(export, 'stale')


def invalid(data, export):
    (data / METADATA).write_text('{broken')


if __name__ == '__main__':
    run_case('Fresh boot and repeated SIGTERM', repeat_signal=True)
    run_case('Existing snapshot, custom project, SIGINT', previous, project='demo-custom', stop=signal.SIGINT)
    run_case('Empty project falls back', project='')
    run_case('Firebase failure status preserved', previous, mode='startup-failure', expected=7)
    run_case('Failed export preserves saved snapshot', previous, mode='no-export', expected=1)
    run_case('Nonzero exit never commits export', previous, mode='nonzero-export', expected=9)
    run_case('Copy failure rolls back', previous, fail_copy=True, expected=1)
    run_case('Interrupted synchronization recovers on boot', interrupted, mode='no-export', expected=1)
    run_case('Stale temporary export is rejected', stale, mode='no-export', expected=1)
    run_case('Invalid metadata fails closed', invalid, expected=1)
