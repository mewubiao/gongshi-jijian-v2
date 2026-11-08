#!/usr/bin/env python3
"""Upgrade only the existing macOS local demo app; never stop PostgreSQL.

Usage: upgrade-local.py upgrade bin/workshop
       upgrade-local.py rollback <backup-directory-name>
The runtime's MULTICA_CLI_PATH must be set for every process lifecycle change.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path.home() / '.local/share/gongshi-jijian-go-mvp'
SOURCE = Path(__file__).resolve().parents[1]
LABEL = 'com.multica.gongshi-jijian-go-mvp.app'
DOMAIN = f'gui/{os.getuid()}'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
BINARY = ROOT / 'bin/workshop'
PSQL = '/opt/homebrew/bin/psql'


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def config():
    return plistlib.loads(PLIST.read_bytes())


def dsn():
    return config()['EnvironmentVariables']['DATABASE_URL']


def snapshot():
    query = "SELECT jsonb_object_agg(tab, rows) FROM (" + ' UNION ALL '.join(
        "SELECT '%s' AS tab, coalesce(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb) AS rows FROM %s t" % (t, t)
        for t in ('centers', 'employees', 'products', 'operations', 'versions', 'orders', 'snapshots', 'dispatches', 'members', 'batches', 'spans', 'allocations', 'revisions', 'resolutions', 'trims', 'audits', 'requests')
    ) + ') AS data'
    return json.loads(subprocess.check_output([PSQL, dsn(), '-XAt', '-c', query]))


def stop_app():
    cli = os.environ.get('MULTICA_CLI_PATH')
    if not cli:
        raise RuntimeError('MULTICA_CLI_PATH is required; refusing to stop an unverified process')
    daemon = json.loads(subprocess.check_output([cli, 'daemon', 'status', '--output', 'json']))['pid']
    job = subprocess.run(['/bin/launchctl', 'print', DOMAIN + '/' + LABEL], capture_output=True, text=True)
    if job.returncode:
        return
    match = re.search(r'^\s*pid = (\d+)', job.stdout, re.M)
    if match:
        pid = int(match.group(1))
        command = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'command='], text=True).strip()
        if pid == daemon or command.split(' -', 1)[0] != str(BINARY):
            raise RuntimeError('Unexpected app PID/command; refusing to stop it')
    run(['/bin/launchctl', 'bootout', DOMAIN + '/' + LABEL])


def start_app():
    run(['/bin/launchctl', 'bootstrap', DOMAIN, str(PLIST)])
    for _ in range(40):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=1) as response:
                if response.status == 200 and response.read() == b'ok':
                    return response.headers.get('X-Workshop-Version', 'legacy-interval-v1')
        except OSError:
            pass
        time.sleep(.25)
    raise RuntimeError('App readiness failed; inspect private local logs')


def atomic_binary(source):
    target = BINARY.with_name('workshop.next')
    shutil.copy2(source, target)
    target.chmod(0o755)
    os.replace(target, BINARY)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    path.chmod(0o600)


def restore(backup):
    # A legacy binary cannot understand new v2 business facts. Never restore a
    # stale database dump over work recorded since the upgrade.
    if any(b.get('algorithm') == 'interval-v2' for b in snapshot()['batches']):
        raise RuntimeError('v2 reports exist: keep the current service and repair forward; legacy rollback would corrupt accounting')
    stop_app()
    atomic_binary(backup / 'workshop.previous')
    previous = backup / 'source.previous'
    if previous.exists():
        (ROOT / 'source').rename(backup / ('source.replaced-' + str(time.time_ns())))
        previous.rename(ROOT / 'source')
    version = start_app()
    write_json(ROOT / 'run/version.json', {'version':version, 'rolled_back_from':str(backup), 'at':datetime.datetime.now().isoformat()})
    return version


def upgrade(candidate):
    if not candidate.is_file() or candidate.resolve() == BINARY.resolve():
        raise RuntimeError('Supply a separate, verified candidate binary')
    backup = ROOT / 'backups' / ('wb5-upgrade-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(BINARY, backup / 'workshop.previous')
    (backup / 'workshop.previous').chmod(0o700)
    shutil.copy2(PLIST, backup / 'app.plist')
    (backup / 'app.plist').chmod(0o600)
    staged = ROOT / ('source.next-' + str(time.time_ns()))
    shutil.copytree(SOURCE, staged, ignore=shutil.ignore_patterns('.pg*', 'node_modules', 'bin', 'artifacts', '.git', '*.log', '__pycache__'))
    stopped = False
    ready = False
    try:
        stop_app()
        stopped = True
        before = snapshot()
        write_json(backup / 'state-before.json', before)
        run(['/opt/homebrew/bin/pg_dump', '-Fc', '--dbname', dsn(), '--file', str(backup / 'before.dump')])
        (backup / 'before.dump').chmod(0o600)
        run([str(candidate.resolve()), '-migrate', '-once'], env=dict(os.environ, DATABASE_URL=dsn()), timeout=30)
        after = snapshot()
        old_shape = json.loads(json.dumps(after))
        for batch in old_shape['batches']:
            if 'algorithm' not in next(b for b in before['batches'] if b['id'] == batch['id']):
                if batch.pop('algorithm') != 'interval-v1':
                    raise RuntimeError('Legacy batch received an incorrect version')
        if before != old_shape:
            raise RuntimeError('Migration changed existing business data')
        atomic_binary(candidate)
        version = start_app()
        if version == 'legacy-interval-v1':
            raise RuntimeError('Candidate did not identify its version')
        ready = True
        # Every source copy is retained privately; the executable embeds assets.
        (ROOT / 'source').rename(backup / 'source.previous')
        staged.rename(ROOT / 'source')
        current = snapshot()
        for table, rows in before.items():
            if not {r['id'] for r in rows}.issubset({r['id'] for r in current[table]}):
                raise RuntimeError('Existing records are missing after startup: ' + table)
        approved = {b['id'] for b in before['batches'] if b['status'] == 'approved'}
        old_allocs = [a for a in before['allocations'] if a['batch_id'] in approved]
        if old_allocs != [a for a in current['allocations'] if a['batch_id'] in approved]:
            raise RuntimeError('Approved allocations changed after startup')
        manifest = {'version':version, 'binary_sha256':hashlib.sha256(BINARY.read_bytes()).hexdigest(), 'backup':str(backup), 'url':'http://127.0.0.1:8080', 'at':datetime.datetime.now().isoformat(), 'migration_business_data_unchanged':True, 'approved_allocations_unchanged':True, 'counts':{t:len(rows) for t, rows in current.items()}}
        write_json(ROOT / 'run/version.json', manifest)
        write_json(backup / 'upgrade-result.json', manifest)
        print(json.dumps({'version':version,'backup':backup.name,'url':manifest['url'],'migration_business_data_unchanged':True,'approved_allocations_unchanged':True},ensure_ascii=False,indent=2))
    except Exception:
        if stopped and not ready:
            print('Startup failed; restoring the previous app without restoring/deleting business data.', file=sys.stderr)
            print('Restored version: ' + restore(backup), file=sys.stderr)
        raise


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('upgrade', 'rollback'):
        raise SystemExit(__doc__)
    if sys.argv[1] == 'upgrade':
        upgrade(Path(sys.argv[2]))
    else:
        backup = (ROOT / 'backups' / sys.argv[2]).resolve()
        if backup.parent != (ROOT / 'backups').resolve():
            raise RuntimeError('Backup must be inside the private service backups directory')
        print('Restored version: ' + restore(backup))


if __name__ == '__main__':
    main()
