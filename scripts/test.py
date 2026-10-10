#!/usr/bin/env python3
"""Run the complete deterministic contributor suite; no agent credentials needed."""
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
LEGACY = 'f0e10422aa0c79d942b2031a3b221f3e65558afb'
PAIRED_LEGACY = '36cf2322b00c5d81d372888864e15b946e7e0fb7'


def main():
    if not __debug__:
        raise SystemExit('Run without python -O: the integration tests use assertions.')
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('The complete suite requires the supported macOS ARM64/APFS host.')
    env = os.environ.copy()
    for key in ('FORKLIFT_FAILPOINT', 'FORKLIFT_PAIR_TEST_FAILPOINT',
                'FORKLIFT_PROJECT_TEST_FAILPOINT', 'FORKLIFT_SUPABASE_TEST_FAILPOINT', 'FORKLIFT_MANAGED_TEST_FAILPOINT', 'FORKLIFT_BIN', 'PYTHONOPTIMIZE'):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONUNBUFFERED'] = '1'
    pg = Path(env.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin')).resolve()
    for tool in ('cargo', 'git', 'tar'):
        if not shutil.which(tool):
            raise SystemExit(f'{tool} must be on PATH; see CONTRIBUTING.md.')
    for tool in ('postgres', 'initdb', 'pg_ctl', 'psql', 'pg_config',
                 'pg_controldata', 'pg_checksums', 'pg_dump', 'pg_restore'):
        if not os.access(pg/tool, os.X_OK):
            raise SystemExit(f'Missing executable {pg/tool}; set PG_BIN to PostgreSQL 17 binaries.')
    env['PG_BIN'] = str(pg)
    for revision, path in ((LEGACY, 'Cargo.toml'), (PAIRED_LEGACY, 'scripts/paired.py')):
        result = subprocess.run(['git', 'cat-file', '-e', f'{revision}:{path}'],
                                cwd=REPO, capture_output=True)
        if result.returncode:
            raise SystemExit(f'Missing historical commit {revision}; use a full Git clone '
                             '(git fetch --unshallow for a shallow clone).')

    parent = REPO/'benchmark-data/test-runs'
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    summary = {'status': 'running', 'postgres_bin': str(pg), 'steps': []}
    print(f'Test logs: {root}', flush=True)

    def run(label, argv, *, cwd=REPO, binary=None):
        argv = list(map(str, argv))
        current_env = env.copy()
        if binary is not None:
            current_env['FORKLIFT_BIN'] = str(binary)
        print(f'\n[{label}] {shlex.join(argv)}', flush=True)
        start = time.monotonic()
        log = root/f'{len(summary["steps"]):02d}-{label}.log'
        with log.open('w') as output:
            process = subprocess.Popen(argv, cwd=cwd, env=current_env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                print(line, end='', flush=True)
                output.write(line)
                output.flush()
            code = process.wait()
        summary['steps'].append({'name': label, 'argv': argv, 'exit': code,
                                 'seconds': round(time.monotonic()-start, 3), 'log': log.name})
        (root/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        if code:
            raise RuntimeError(f'{label} failed (exit {code}); see {log}')
        return log.read_text()

    try:
        run('format', ['cargo', 'fmt', '--check'])
        run('unit', ['cargo', 'test', '--locked', '--all-targets'])
        run('clippy', ['cargo', 'clippy', '--locked', '--all-targets', '--all-features', '--', '-D', 'warnings'])
        targets = REPO/'target/acceptance'
        fault_target = targets/'failpoints'
        release_target = targets/'release'
        run('build-failpoints', ['cargo', 'build', '--locked', '--release', '--bin', 'forklift',
                                '--features', 'test-failpoints', '--target-dir', fault_target])
        fault_binary = fault_target/'release/forklift'
        # Exercise subprocess/loopback orchestration early on fresh CI runners.
        managed_output = run('managed-app', [sys.executable, REPO/'scripts/test_managed.py'], binary=fault_binary)
        managed = Path(managed_output.strip().splitlines()[-1])
        for suite in ('lifecycle', 'objects', 'extensions', 'cache', 'cleanup', 'retention', 'paired', 'paired_legacy'):
            run(suite, [sys.executable, REPO/f'scripts/test_{suite}.py'], binary=fault_binary)
        run('project-onboarding', [sys.executable, REPO/'scripts/test_project_onboarding.py'], binary=fault_binary)
        if env.get('SUPABASE_BIN'):
            run('supabase-native', [sys.executable, REPO/'scripts/test_supabase.py'], binary=fault_binary)
        if env.get('SUPABASE_BIN'):
            run('managed-app-native', [sys.executable, REPO/'scripts/test_managed.py', '--supabase'], binary=fault_binary)
        project_output = run('project', [sys.executable, REPO/'scripts/test_project.py'], binary=fault_binary)
        project = Path(project_output.strip().splitlines()[-1])
        if not project.is_relative_to(REPO/'benchmark-data') or not (project/'SUCCESS').is_file():
            raise RuntimeError('Project suite did not return a successful disposable fixture.')

        # Separate target directories prevent accidentally shipping a failure-injection build.
        run('build-release', ['cargo', 'build', '--locked', '--release', '--bin', 'forklift',
                             '--target-dir', release_target])
        release_binary = release_target/'release/forklift'
        run('project-cleanup', [release_binary, '--json', '--home', project/'store',
                                'cleanup', '--yes'])
        run('project-release', [sys.executable, REPO/'scripts/test_project_release.py', project],
            binary=release_binary)
        run('managed-release', [sys.executable, REPO/'scripts/test_managed_release.py', managed],
            binary=release_binary)
        run('quickstart', [sys.executable, REPO/'scripts/quickstart.py', root/'quickstart'],
            binary=release_binary)

        if env.get('BOOKLIST_ROOT') and env.get('POSTGREST_BIN'):
            run('booklist', [sys.executable, REPO/'scripts/test_booklist.py'], binary=release_binary)
        elif env.get('POSTGREST_BIN') or (env.get('BOOKLIST_ROOT') and not env.get('SUPABASE_BIN')):
            raise RuntimeError('Set both BOOKLIST_ROOT and POSTGREST_BIN for the optional Booklist check.')

        # Build the actual old format writer without moving the contributor's checkout.
        legacy = root/'legacy-source'
        legacy.mkdir()
        archive = root/'legacy.tar'
        with archive.open('wb') as out:
            subprocess.run(['git', 'archive', LEGACY, 'Cargo.toml', 'Cargo.lock',
                            'rust-toolchain.toml', 'src'], cwd=REPO, stdout=out, check=True)
        subprocess.run(['tar', '-xf', archive, '-C', legacy], check=True)
        legacy_target = targets/'legacy'
        run('build-legacy', ['cargo', 'build', '--locked', '--release', '--bin', 'forklift',
                            '--target-dir', legacy_target], cwd=legacy)
        run('legacy', [sys.executable, REPO/'scripts/test_legacy.py', legacy_target/'release/forklift'],
            binary=release_binary)
        summary['status'] = 'passed'
        print('\nAll deterministic checks passed. Test stores are retained with servers stopped.', flush=True)
    except BaseException:
        summary['status'] = 'failed'
        raise
    finally:
        (root/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(f'Run summary: {root/"summary.json"}', flush=True)


if __name__ == '__main__':
    main()
