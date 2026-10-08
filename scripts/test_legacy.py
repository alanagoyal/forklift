#!/usr/bin/env python3
"""Upgrade a real milestone-two store; pass the old forklift executable as argv[1]."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

OLD = Path(sys.argv[1]).resolve()
NEW = Path(os.environ.get('FORKLIFT_BIN', 'target/release/forklift')).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
ROOT = Path(tempfile.mkdtemp(prefix='legacy-',dir='benchmark-data')).resolve()
HOME = ROOT/'store'
records = []


def cli(binary, *args, home=HOME, code=None):
    p = subprocess.run([str(binary),'--home',str(home),'--pg-bin',str(PG),'--json',*args],capture_output=True,text=True,timeout=90)
    obj = json.loads(p.stdout)
    if code:
        assert not obj['ok'] and obj['error']['code']==code, obj
    else:
        assert p.returncode==0 and obj['ok'], (obj,p.stderr)
    records.append({'binary':'old' if binary==OLD else 'new','args':args,'response':obj})
    return obj.get('result')


def value(binary,name,home=HOME):
    conn = cli(binary,'connection',name,home=home)['connection']
    return subprocess.check_output([str(PG/'psql'),conn,'-XAt','-v','ON_ERROR_STOP=1','-c','SELECT value FROM retained'],text=True).strip()


try:
    cli(OLD,'init','demo');cli(OLD,'start','demo')
    conn = cli(OLD,'connection','demo')['connection']
    subprocess.run([str(PG/'psql'),conn,'-XAt','-v','ON_ERROR_STOP=1','-c',"CREATE TABLE retained(value text); INSERT INTO retained VALUES('legacy survives')"],check=True,capture_output=True)
    cli(OLD,'checkpoint','demo','--name','legacy')
    cli(OLD,'stop','demo')
    assert json.loads((HOME/'catalog.json').read_text())['format']==1
    cli(NEW,'list')
    assert json.loads((HOME/'catalog.json').read_text())['format']==3
    cli(OLD,'list',code='incompatible')
    cli(NEW,'export','legacy','--output',str(ROOT/'legacy.forklift'),code='legacy_checkpoint')
    cli(NEW,'branch','--from','legacy','--name','branch')
    cli(NEW,'start','branch');assert value(NEW,'branch')=='legacy survives'
    cli(NEW,'reset','branch','--to','legacy','--yes');assert value(NEW,'branch')=='legacy survives'
    cli(NEW,'checkpoint','branch','--name','objects')
    cli(NEW,'export','objects','--output',str(ROOT/'objects.forklift'))
    cli(NEW,'restore',str(ROOT/'objects.forklift'),'--name','restored',home=ROOT/'destination')
    cli(NEW,'start','restored',home=ROOT/'destination')
    assert value(NEW,'restored',ROOT/'destination')=='legacy survives'
    cli(NEW,'stop','restored',home=ROOT/'destination');cli(NEW,'stop','branch')
    (ROOT/'SUCCESS').write_text('real milestone-two catalog migration and legacy checkpoint compatibility passed\n')
    print('PASS: '+str(ROOT))
finally:
    for store in (HOME,ROOT/'destination'):
        for data in (store/'generations').glob('*/data'):
            pid = data/'postmaster.pid'
            if pid.exists() and pid.read_text().splitlines()[1]==str(data):
                subprocess.run([str(PG/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],capture_output=True)
    (ROOT/'test-results.json').write_text(json.dumps(records,indent=2)+'\n')
