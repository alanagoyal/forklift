#!/usr/bin/env python3
"""Normal release must ignore managed failure-injection environment variables."""
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(sys.argv[1]).resolve()
assert FIXTURE.is_relative_to(REPO/'benchmark-data') and (FIXTURE/'SUCCESS').is_file()
assert json.loads((FIXTURE/'SUCCESS').read_text())['backend']=='postgres'
ROOT = FIXTURE/'one'
BIN = Path(os.environ['FORKLIFT_BIN']).resolve()

def project(*args, stage=''):
    env = dict(os.environ, FORKLIFT_MANAGED_TEST_FAILPOINT=stage)
    p = subprocess.run([str(BIN),'--json','project','--root',str(ROOT),*args],
        capture_output=True,text=True,env=env,timeout=180)
    assert p.returncode==0,(p.returncode,p.stdout,p.stderr)
    return json.loads(p.stdout)['result']

try:
    assert project('app-start',stage='started')['managed_app']=='running'
    assert project('capture','release_hooks',stage='paused')['managed_app']=='running'
    assert project('restore','release_hooks',stage='started')['managed_app']=='running'
    print('release ignores managed pause/start failpoints; real app capture/restore passes')
finally:
    project('recover')
    project('stop')
