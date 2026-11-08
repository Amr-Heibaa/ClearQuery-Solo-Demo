"""Run ONE native diagnostic child with an external process-tree timeout."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from time import monotonic
from clearquery.voice.native_process import NativeProcessGuard


def run_probe(command, *, seconds, output):
    output=Path(output)
    if output.exists():raise ValueError('Refusing to overwrite existing probe evidence.')
    if not 0<seconds<=120:raise ValueError('Probe deadline must be between 0 and 120 seconds.')
    started=monotonic();lines=[];lock=threading.Lock()
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    guard=None
    try:
        guard=NativeProcessGuard(process,seconds)
        def read():
            for line in process.stdout:
                record={'elapsed_seconds':round(monotonic()-started,3),'line':line.rstrip()}
                with lock:lines.append(record)
                print(json.dumps(record,ensure_ascii=True),flush=True)
        reader=threading.Thread(target=read,daemon=True);reader.start()
        try:code=process.wait(timeout=seconds+3)
        except subprocess.TimeoutExpired:
            guard.terminate();process.kill();code=process.wait(timeout=3)
        guard.close();reader.join(timeout=2)
        if not reader.is_alive():process.stdout.close()
        summary={'exit_code':code,'timed_out':guard.expired.is_set(),
            'elapsed_seconds':round(monotonic()-started,3),'external_deadline_seconds':seconds,'stages':lines}
        with output.open('x',encoding='utf-8') as stream:json.dump(summary,stream,indent=2)
        return summary
    finally:
        if guard:guard.close()
        if process.poll() is None:process.kill();process.wait(timeout=3)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=25)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    command=args.command[1:] if args.command and args.command[0]=='--' else args.command
    result=run_probe(command,seconds=args.seconds,output=args.output)
    raise SystemExit(124 if result['timed_out'] else result['exit_code'])
