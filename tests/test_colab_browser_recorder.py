"""JS event tests with TEST_STUB browser objects; never real microphone claims."""
import json
from pathlib import Path
import shutil
import subprocess
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/colab_microphone.js'
HARNESS=r'''
import {readFileSync} from 'node:fs';
const mode=process.argv[2],buttons=[],timers=[];
let stopped=0,now=1000;
globalThis.document={createElement:tag=>{const e={tag,append(){},disabled:false};if(tag==='button')buttons.push(e);return e;},body:{append(){}}};
Object.defineProperty(globalThis,'navigator',{value:{mediaDevices:{getUserMedia:async()=>{
  if(mode==='denied'){const e=new Error('TEST_STUB denied');e.name='NotAllowedError';throw e;}
  return {getTracks:()=>[{stop(){stopped++;}}]};
}}},configurable:true});
Object.defineProperty(globalThis,'performance',{value:{now:()=>now},configurable:true});
globalThis.setTimeout=(fn,ms)=>{timers.push({fn,ms});return timers.length;};
globalThis.clearTimeout=()=>{};globalThis.setInterval=()=>1;globalThis.clearInterval=()=>{};
class Recorder{
  static isTypeSupported(){return true;}
  constructor(stream,options){this.mimeType=options.mimeType;this.state='inactive';}
  start(){this.state='recording';}
  stop(){this.state='inactive';this.ondataavailable({data:new Blob(['TEST_STUB'])});this.onstop();}
}
globalThis.window={MediaRecorder:Recorder};globalThis.MediaRecorder=Recorder;
globalThis.FileReader=class{readAsDataURL(){this.result='data:audio/webm;base64,VEVTVF9TVFVC';this.onloadend();}};
if(mode==='unsupported')navigator.mediaDevices=null;
const result=eval(readFileSync(process.argv[1],'utf8'));
if(mode==='cancel')buttons[2].onclick();
else if(mode!=='unsupported'){
  await buttons[0].onclick();
  if(mode!=='denied'){
    now=mode==='empty'?1000:2000;
    if(mode==='limit')timers.find(t=>t.ms===30000).fn();
    else buttons[1].onclick();
  }
}
const value=await result;
console.log(JSON.stringify({value,stopped,limit:timers.some(t=>t.ms===30000)}));
'''


@pytest.mark.parametrize('mode,expected',[
    ('enter',True),('limit',True),('denied','MICROPHONE_PERMISSION_DENIED'),
    ('cancel','RECORDING_CANCELLED'),('unsupported','BROWSER_UNSUPPORTED'),('empty','EMPTY_RECORDING'),
])
def test_recorder_event_paths(mode,expected):
    if not shutil.which('node'):pytest.skip('Node unavailable for isolated JS event checks')
    result=subprocess.run(['node','--input-type=module','-e',HARNESS,str(SCRIPT),mode],
        capture_output=True,text=True,encoding='utf-8',timeout=10,check=True)
    parsed=json.loads(result.stdout)
    if expected is True:
        assert parsed['value']['ok'] is True and parsed['stopped']>0 and parsed['limit']
    else:assert parsed['value']['code']==expected
