"""Colab-only isolated PostgreSQL setup and existing controlled execution.

No laptop connections, tunnels, model changes or alternative SQL executor.
Credentials and one-shot execution requests remain private in the runtime.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path('/content/clearquery-private')
CONFIG = PRIVATE/'synthetic-db.json'
PHRASE = 'RUN APPROVED QUERY'


def require_colab():
    if sys.platform != 'linux' or ROOT != Path('/content/ClearQuery-Solo-Demo') or not Path('/content').is_dir():
        raise RuntimeError('Database provisioning/execution is restricted to the hosted Colab source directory.')


def run(argv, **kwargs):
    # Never print environment variables or password-bearing commands.
    value = subprocess.run([str(x) for x in argv], capture_output=True, text=True,
        timeout=kwargs.pop('timeout',120), **kwargs)
    if value.returncode:
        raise RuntimeError(f'{Path(str(argv[0])).name} failed ({value.returncode}): {value.stderr or value.stdout}')
    return value.stdout


def settings(config):
    from clearquery_poc.executor import DbSettings
    if config.get('source_root') != str(ROOT) or config.get('kind') != 'ISOLATED_COLAB_SYNTHETIC':
        raise RuntimeError('Colab database provenance rejected.')
    return DbSettings(host='127.0.0.1',port=55432,dbname='clearquery_demo',
        user='clearquery_app_ro',password=config['app_password'])


def verify(config):
    from clearquery.executor.safe_demo import connect_demo, preflight
    db = settings(config)
    with connect_demo(db) as conn:
        try:return preflight(conn,db)
        finally:conn.rollback()


def install_postgres():
    """Install distro packages without creating/starting a default cluster."""
    require_colab()
    if os.geteuid() != 0:raise RuntimeError('Fresh hosted runtime root privileges are needed for package setup.')
    env={**os.environ,'DEBIAN_FRONTEND':'noninteractive'}
    print(run(['apt-get','update'],env=env,timeout=240),flush=True)
    print(run(['apt-get','install','-y','--no-install-recommends','postgresql-common'],env=env,timeout=240),flush=True)
    config=Path('/etc/postgresql-common/createcluster.conf')
    previous=config.read_bytes() if config.exists() else None
    try:
        # postgresql-common's supported switch prevents package-triggered clusters.
        with config.open('a') as stream:stream.write('\ncreate_main_cluster = false\n')
        print(run(['apt-get','install','-y','--no-install-recommends','postgresql'],env=env,timeout=300),flush=True)
    finally:
        if previous is not None:config.write_bytes(previous)
        else:config.unlink(missing_ok=True)


def setup():
    require_colab()
    if CONFIG.exists():
        config=json.loads(CONFIG.read_text())
        verify(config)
        print('Existing isolated synthetic database passed the full read-only preflight.')
        return
    import pwd
    import socket
    if os.geteuid()!=0:raise RuntimeError('Use a fresh hosted Colab runtime with root package setup.')
    # Refuse collisions before installing, starting, or connecting to any server.
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',55432))
        except OSError as exc:raise RuntimeError('Port 55432 is already in use; select a fresh runtime.') from exc
    base=ROOT/'.pgdemo';data=base/'data'
    if base.exists():raise RuntimeError('Unverified prior cluster exists; use a fresh runtime. It will not be overwritten.')
    if not list(Path('/usr/lib/postgresql').glob('*/bin/initdb')):install_postgres()
    binaries=sorted(Path('/usr/lib/postgresql').glob('*/bin/initdb'),key=lambda p:int(p.parents[1].name))
    if not binaries:raise RuntimeError('PostgreSQL server binaries were not installed.')
    bindir=binaries[-1].parent
    owner=pwd.getpwnam('postgres')
    base.mkdir(mode=0o700);os.chown(base,owner.pw_uid,owner.pw_gid)
    # Source bundle is public code; postgres needs traversal to its private child.
    if not (ROOT.stat().st_mode & 0o001):raise RuntimeError('Source directory must permit traversal; do not use a private Drive mount.')
    admin_password,app_password=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    pw=base/'init-password';pw.write_text(admin_password);os.chmod(pw,0o600);os.chown(pw,owner.pw_uid,owner.pw_gid)
    def pg(*args):return run(['runuser','-u','postgres','--',*args],cwd='/tmp',timeout=60)
    started=False
    try:
        try:pg(bindir/'initdb','-D',data,'-U','clearquery_demo_admin','--pwfile',pw,
            '--auth=scram-sha-256','--encoding=UTF8','--locale=C','--no-instructions')
        finally:pw.unlink(missing_ok=True)
        with (data/'postgresql.conf').open('a') as stream:
            stream.write("\nlisten_addresses='127.0.0.1'\nport=55432\nunix_socket_directories=''\npassword_encryption='scram-sha-256'\n")
        (data/'pg_hba.conf').write_text('host all all 127.0.0.1/32 scram-sha-256\n')
        pg(bindir/'pg_ctl','-D',data,'-l',base/'server.log','-w','-t','30','start');started=True
        env={**os.environ,'CLEARQUERY_DB_HOST':'127.0.0.1','CLEARQUERY_DB_PORT':'55432',
            'CLEARQUERY_DB_NAME':'clearquery_demo','CLEARQUERY_ADMIN_USER':'clearquery_demo_admin',
            'CLEARQUERY_ADMIN_PASSWORD':admin_password,'CLEARQUERY_APP_USER':'clearquery_app_ro',
            'CLEARQUERY_APP_PASSWORD':app_password}
        print(run([sys.executable,ROOT/'scripts/build_demo_db.py'],env=env,cwd=ROOT),flush=True)
        # Verify bind configuration as admin, then discard admin credentials.
        import psycopg
        with psycopg.connect(host='127.0.0.1',port=55432,dbname='clearquery_demo',user='clearquery_demo_admin',password=admin_password,connect_timeout=5) as conn:
            if conn.execute('SHOW listen_addresses').fetchone()[0]!='127.0.0.1':raise RuntimeError('Non-loopback listener rejected.')
        config={'kind':'ISOLATED_COLAB_SYNTHETIC','source_root':str(ROOT),'app_password':app_password}
        verify(config)
        PRIVATE.mkdir(mode=0o700,exist_ok=True);os.chmod(PRIVATE,0o700)
        fd=os.open(CONFIG,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(config,stream)
        print('READY: isolated synthetic data, loopback only, read-only roles/fixtures/limits verified.')
    except Exception:
        if started:pg(bindir/'pg_ctl','-D',data,'-m','fast','-w','-t','30','stop')
        raise


def approved_inputs(result):
    from clearquery.contracts import BIR, SqlCandidate, ValidationResult, Decision
    if result.get('stop_reason')!='REVIEWED_SQL_EXECUTION_DISABLED' or result.get('executed') is not False:
        raise RuntimeError('Pipeline did not finish at approved, unexecuted SQL.')
    stages=result.get('stages',[])
    def one(name,cls):
        found=[s['result'] for s in stages if s['stage']==name]
        if len(found)!=1:raise RuntimeError('Missing or duplicate stage: '+name)
        value=cls.model_validate(found[0])
        if value.trace_id!=result['trace_id']:raise RuntimeError('Trace mismatch.')
        return value
    from clearquery.contracts import ConfirmationResult, TranslationConsistencyResult
    if not one('confirmation',ConfirmationResult).user_confirmed:raise RuntimeError('Arabic confirmation missing.')
    if one('decision',Decision).decision!='ANSWER':raise RuntimeError('Decision cannot execute.')
    if one('semantic_gate',TranslationConsistencyResult).status!='PASS':raise RuntimeError('Translation Gate cannot execute.')
    bir=one('bir',BIR);candidate=one('sql_candidate',SqlCandidate);guard=one('sql_guard',ValidationResult)
    if not guard.validated or guard.reviewed_sql!=candidate.candidate_sql:raise RuntimeError('Guard cannot execute.')
    return candidate,bir


def consume_request(path, result_path, *, now=None):
    """Atomic one-shot, hash-bound request; complements ControlledExecutor TTL."""
    path,result_path=Path(path),Path(result_path)
    if path.parent.resolve()!=PRIVATE.resolve() or not path.name.endswith('-authorization.json'):
        raise RuntimeError('Authorization path rejected.')
    # Exclusive claim: concurrent/replayed calls cannot both rename the source.
    claimed=path.with_suffix('.consumed')
    fd=os.open(claimed,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.close(fd)
    # Consumed marker is deliberately retained until the runtime is discarded.
    request=json.loads(path.read_text())
    path.unlink()
    if request.get('authorization')!=PHRASE:raise RuntimeError('Explicit execution authorization missing.')
    age=(monotonic() if now is None else now)-request['issued_monotonic']
    if not 0<=age<=300:raise RuntimeError('Execution authorization expired.')
    raw=result_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=request.get('result_sha256'):raise RuntimeError('Approved result changed.')
    result=json.loads(raw)
    if result.get('trace_id')!=request.get('trace_id'):raise RuntimeError('Approval trace mismatch.')
    approved_inputs(result)
    return result


def execute(result_path, request_path):
    require_colab()
    config=json.loads(CONFIG.read_text())
    result=consume_request(request_path,result_path)
    candidate,bir=approved_inputs(result)
    from clearquery.executor.safe_demo import ControlledExecutor
    executor=ControlledExecutor()
    # Current Guard and full same-transaction database preflight run inside this.
    approval=executor.approve(candidate,bir)
    return asdict(executor.execute(approval,settings(config)))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['setup','verify','execute'])
    parser.add_argument('--result',type=Path);parser.add_argument('--authorization',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    require_colab()
    if args.action=='setup':setup();return
    if args.action=='verify':verify(json.loads(CONFIG.read_text()));print('Synthetic read-only database preflight PASS.');return
    if not all((args.result,args.authorization,args.output)):parser.error('Execution needs result, authorization and output paths.')
    if args.output.exists():parser.error('Refusing to overwrite prior database results.')
    value=execute(args.result,args.authorization)
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(value,stream,default=str,ensure_ascii=False)
    print('Read-only query completed.' if value['executed'] and not value['error'] else 'Read-only query stopped; see the displayed error.')


if __name__=='__main__':main()
