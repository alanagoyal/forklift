#!/usr/bin/env python3
"""Independent observations and CLI adapters for paired integration tests/demos.
No production capture, restore, locking, or recovery implementation lives here.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

BIN = Path(os.environ.get('FORKLIFT_BIN', 'target/release/forklift')).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin')).resolve()


def pair_command(root, *args):
    return [str(BIN), '--pg-bin', str(PG), 'pair', str(root), *map(str, args)]


def pair_json(root, *args):
    p = subprocess.run([str(BIN), '--pg-bin', str(PG), '--json', 'pair', str(root), *map(str, args)], capture_output=True, text=True, timeout=180)
    if p.returncode:
        raise RuntimeError(p.stdout + p.stderr)
    return json.loads(p.stdout)['result']


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


class Workspace:
    """Read metadata directly so assertions do not trust the implementation's view."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.store = Path(json.loads((self.root/'workspace.json').read_text())['store'])

    def current(self):
        return json.loads((self.root/'current.json').read_text())

    def cli(self, *args):
        p = subprocess.run([str(BIN), '--home', str(self.store), '--pg-bin', str(PG), '--json', *args], capture_output=True, text=True, timeout=180)
        if p.returncode:
            raise RuntimeError(p.stdout + p.stderr)
        return json.loads(p.stdout)['result']

    def recover(self):
        return pair_json(self.root, 'recover')


def initialize(root, source, store, checkpoint):
    pair_json(root, 'init', '--source', source, '--store', store, '--checkpoint', checkpoint)
    return Workspace(root)
