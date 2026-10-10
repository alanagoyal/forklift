#!/usr/bin/env python3
"""Recheck retained real-agent states; no agents or feature migrations are simulated."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from demo_parallel import Demo, save
ROOT=Path(__file__).resolve().parents[1]
ORIGINAL=ROOT/'results/2026-10-09-parallel'
OUT=ROOT/'demo-video/verification'
setup=json.loads((ORIGINAL/'setup.json').read_text())
d=Demo(Path(setup['root']), OUT)
read=lambda n:json.loads((ORIGINAL/n).read_text())
states={}
try:
    for name in ('soft','move'): d.project(name,'restore','baseline')
    states['baseline']={n:d.observe(n) for n in ('soft','move')}
    for n in ('soft','move'): assert states['baseline'][n]==read(f'{n}-baseline.json')
    for name in ('soft','move'): d.project(name,'restore','experiment')
    states['experiment']={n:d.observe(n) for n in ('soft','move')}
    for n in ('soft','move'): assert states['experiment'][n]==read(f'{n}-modified.json')
    d.project('soft','restore','baseline')
    states['rewound']={n:d.observe(n) for n in ('soft','move')}
    assert states['rewound']['soft']==states['baseline']['soft']
    assert states['rewound']['move']==states['experiment']['move']
    assert d.control()==read('control-before.json')
    save(OUT/'states.json',states)
    save(ROOT/'demo-video/data.json', {'states':states,'provenance':{
        'kind':'condensed visual replay of an authentic parallel-agent run',
        'agent_evidence':'results/2026-10-09-parallel',
        'live_recheck':'saved real-agent experiments restored and independently observed; one rewound; sibling and seed unchanged',
        'agents_rerun_for_video':False,
        'durability':'fsync, full_page_writes, synchronous_commit, data_checksums on'}})
finally:
    d.cleanup()
print('Verified baseline → retained agent experiments → rewind A; B and seed unchanged. All servers stopped.')
