#!/usr/bin/env python3
"""Bounded, cooperative application checkpoints over the Rust Forklift engine.
All app/agent activity must use `run`; no detached children or outside writers.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import uuid

BIN = Path(os.environ.get('FORKLIFT_BIN', 'target/release/forklift')).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))


def syncdir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(path, value):
    tmp = path.with_name('.tmp-' + uuid.uuid4().hex)
    with tmp.open('x') as f:
        json.dump(value, f, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    syncdir(path.parent)


def read(path):
    return json.loads(path.read_text())


def fault(stage):
    # Explicit test-only coordinator injection; Rust injection requires its feature.
    if os.environ.get('FORKLIFT_PAIR_TEST_FAILPOINT') == stage:
        os._exit(99)


def inventory(root):
    result = {}
    for p in sorted(root.rglob('*')):
        if len(result) >= 10000:
            raise ValueError('source exceeds 10000 entries')
        s = p.lstat()
        if stat.S_ISLNK(s.st_mode) or not (stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode)):
            raise ValueError('source contains a link or special file')
        if p.name == '.git':
            raise ValueError('use a dedicated source directory without .git metadata')
        if p.is_file():
            if s.st_nlink != 1 or s.st_size > 16 * 1024 * 1024:
                raise ValueError('source hardlink or file exceeds 16 MiB')
            result[str(p.relative_to(root))] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'mode': stat.S_IMODE(s.st_mode)}
        else:
            result[str(p.relative_to(root))] = {'directory': True}
    if len(result) > 10000:
        raise ValueError('source exceeds 10000 entries')
    return result


def copy_source(source, dest):
    before = inventory(source)
    shutil.copytree(source, dest)
    if inventory(dest) != before or inventory(source) != before:
        raise ValueError('source changed while paused')
    for p in dest.rglob('*'):
        if p.is_file():
            with p.open('rb') as f:
                os.fsync(f.fileno())
    for p in sorted((p for p in dest.rglob('*') if p.is_dir()), reverse=True):
        syncdir(p)
    syncdir(dest)
    syncdir(dest.parent)
    return before


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.config = read(self.root / 'workspace.json')
        self.store = Path(self.config['store'])

    def cli(self, *args):
        cmd = [str(BIN), '--home', str(self.store), '--pg-bin', str(PG), '--json', *args]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if p.returncode:
            raise RuntimeError(p.stdout + p.stderr)
        return json.loads(p.stdout)['result']

    def current(self):
        return read(self.root / 'current.json')

    def pending(self):
        return self.root / 'pending.json'

    def finish(self):
        self.pending().unlink()
        syncdir(self.root)

    def recover(self):
        self.cli('list')  # Let the Rust catalog settle interrupted DB operations first.
        if not self.pending().exists():
            return
        j = read(self.pending())
        if j['kind'] == 'restore':
            current = self.current()
            discard = j['old']['branch'] if current == j['new'] else j['new']['branch']
            view = self.cli('list')
            if any(b['name'] == discard and b['state'] != 'failed' for b in view['branches']):
                self.cli('stop', discard)
        # Capture never edits current source/database. An unpublished capture is orphaned.
        self.finish()

    def capture(self, name):
        if not name.isidentifier() or not name.isascii() or len(name) > 40:
            raise ValueError('pair name must be an ASCII identifier, at most 40 characters')
        pair = self.root / 'pairs' / (name + '.json')
        if pair.exists():
            raise ValueError('pair already exists')
        current = self.current()
        token = uuid.uuid4().hex[:16]
        checkpoint = 'pair-' + token
        write(self.pending(), {'kind': 'capture', 'checkpoint': checkpoint})
        manifest = copy_source(self.root / current['code'], self.root / 'saved' / token)
        fault('capture_code')
        result = self.cli('checkpoint', current['branch'], '--name', checkpoint)
        fault('capture_database')
        write(pair, {'format': 1, 'code': 'saved/' + token, 'files': manifest, 'checkpoint': checkpoint, 'database_receipt': result})
        fault('capture_published')
        self.finish()
        return read(pair)

    def restore(self, name):
        # Name cannot address arbitrary files.
        if not name.isidentifier() or not name.isascii():
            raise ValueError('invalid pair name')
        pair = read(self.root / 'pairs' / (name + '.json'))
        source = self.root / pair['code']
        if inventory(source) != pair['files']:
            raise ValueError('saved source checksum mismatch')
        token = uuid.uuid4().hex[:16]
        new = {'code': 'code/' + token, 'branch': 'rewind-' + token}
        write(self.pending(), {'kind': 'restore', 'old': self.current(), 'new': new})
        copy_source(source, self.root / new['code'])
        fault('restore_code')
        self.cli('branch', '--from', pair['checkpoint'], '--name', new['branch'])
        fault('restore_database')
        self.cli('start', new['branch'])
        fault('restore_started')
        # The pair reference switches only after both independent preparations succeed.
        write(self.root / 'current.json', new)
        fault('restore_published')
        self.recover()
        return new

    def run(self, command, gate_fd):
        current = self.current()
        self.cli('start', current['branch'])
        env = os.environ.copy()
        env['DATABASE_URL'] = self.cli('connection', current['branch'])['connection']
        env['PG_BIN'] = str(PG)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        return subprocess.call(command, cwd=self.root / current['code'], env=env, pass_fds=(gate_fd,))


def initialize(root, source, store, checkpoint):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    for name in ('code', 'saved', 'pairs'):
        (root / name).mkdir()
    copy_source(Path(source).resolve(), root / 'code' / 'initial')
    write(root / 'workspace.json', {'format': 1, 'store': str(Path(store).resolve())})
    branch = 'app-' + uuid.uuid4().hex[:16]
    w = Workspace(root)
    w.cli('branch', '--from', checkpoint, '--name', branch)
    write(root / 'current.json', {'code': 'code/initial', 'branch': branch})
    return w


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('workspace')
    sub = p.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init')
    for arg in ('source', 'store', 'checkpoint'):
        init.add_argument('--' + arg, required=True)
    for action in ('capture', 'restore'):
        sub.add_parser(action).add_argument('name')
    sub.add_parser('recover')
    sub.add_parser('inspect')
    sub.add_parser('stop')
    sub.add_parser('run').add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    if a.action == 'init':
        initialize(a.workspace, a.source, a.store, a.checkpoint)
        return
    w = Workspace(a.workspace)
    with (w.root / 'gate.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.action != 'recover' and w.pending().exists():
            raise RuntimeError('workspace paused after interruption; run recover')
        if a.action == 'run':
            command = a.command[1:] if a.command[:1] == ['--'] else a.command
            raise SystemExit(w.run(command, lock.fileno()))
        if a.action == 'inspect':
            print(json.dumps({'current': w.current(), 'files': inventory(w.root / w.current()['code']), 'pairs': [read(p) for p in sorted((w.root / 'pairs').glob('*.json'))]}, indent=2))
        elif a.action == 'stop':
            w.cli('stop', w.current()['branch'])
        elif a.action == 'recover':
            w.recover()
        else:
            print(json.dumps(getattr(w, a.action)(a.name), indent=2))


if __name__ == '__main__':
    main()
