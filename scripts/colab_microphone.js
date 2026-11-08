// Notebook capture only: no server, tunnel, upload form or model implementation.
(async () => {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)
    return {ok:false, code:'BROWSER_UNSUPPORTED', message:'Use a supported secure browser with microphone access.'};
  const box=document.createElement('div');
  const start=document.createElement('button'), stop=document.createElement('button'), cancel=document.createElement('button');
  const status=document.createElement('span');
  start.textContent='Start microphone'; stop.textContent='Stop'; cancel.textContent='Cancel';
  stop.disabled=true; status.textContent=' Click Start, allow microphone permission, then speak. Maximum 30 seconds.';
  box.append(start,stop,cancel,status); document.body.append(box);
  let stream=null, recorder=null, limit=null, countdown=null, watchdog=null;
  let cancelled=false, settled=false, chunks=[], size=0, began=0;
  let finish;
  const answer=new Promise(resolve => {finish=value => {if (!settled) {settled=true; resolve(value);}};});
  const stopTracks=()=>stream?.getTracks().forEach(track=>track.stop());
  cancel.onclick=()=>{
    cancelled=true; stopTracks();
    if (recorder?.state==='recording') recorder.stop();
    finish({ok:false,code:'RECORDING_CANCELLED',message:'Recording cancelled.'});
  };
  start.onclick=async()=>{
    start.disabled=true; status.textContent=' Waiting for browser microphone permission…';
    try {
      stream=await navigator.mediaDevices.getUserMedia({audio:true,video:false});
      if (cancelled || settled) {stopTracks(); return;}
      const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(type=>MediaRecorder.isTypeSupported(type));
      if (!mime) throw new Error('No supported recording codec.');
      recorder=new MediaRecorder(stream,{mimeType:mime});
      recorder.ondataavailable=event=>{
        if (event.data.size) {chunks.push(event.data); size+=event.data.size;}
        if (size>25*1024*1024) {stopTracks(); if(recorder.state==='recording') recorder.stop(); finish({ok:false,code:'AUDIO_TOO_LARGE',message:'Capture size limit reached.'});}
      };
      recorder.onerror=()=>{stopTracks(); finish({ok:false,code:'AUDIO_DEVICE_ERROR',message:'Browser recording failed.'});};
      recorder.onstop=async()=>{
        stopTracks(); clearTimeout(limit); clearInterval(countdown);
        if (cancelled || settled) return;
        const elapsed=performance.now()-began;
        if (!size || elapsed<200) {finish({ok:false,code:'EMPTY_RECORDING',message:'No recording captured.'}); return;}
        const blob=new Blob(chunks,{type:recorder.mimeType});
        const reader=new FileReader();
        reader.onerror=()=>finish({ok:false,code:'AUDIO_READ_ERROR',message:'Could not transfer capture to Python.'});
        reader.onloadend=()=>{
          if (typeof reader.result!=='string') return;
          finish({ok:true,mime:recorder.mimeType,duration_ms:elapsed,data:reader.result});
        };
        reader.readAsDataURL(blob);
      };
      recorder.start(250); began=performance.now(); stop.disabled=false;
      const halt=()=>{if (recorder.state==='recording') recorder.stop();};
      stop.onclick=halt;
      limit=setTimeout(halt,30000);
      countdown=setInterval(()=>{status.textContent=' Recording… '+Math.min(30,(performance.now()-began)/1000).toFixed(1)+' / 30 seconds';},200);
    } catch(error) {
      stopTracks();
      const codes={NotAllowedError:'MICROPHONE_PERMISSION_DENIED',NotFoundError:'NO_MICROPHONE',NotReadableError:'AUDIO_DEVICE_BUSY'};
      finish({ok:false,code:codes[error.name]||'AUDIO_DEVICE_ERROR',message:String(error.message)});
    }
  };
  // Bounds waiting for Start/permission as well as capture. No dangling tracks.
  watchdog=setTimeout(()=>{cancelled=true;stopTracks(); if(recorder?.state==='recording') recorder.stop(); finish({ok:false,code:'CAPTURE_TIMEOUT',message:'Capture session timed out. Run the cell again.'});},150000);
  try {return await answer;}
  finally {
    clearTimeout(limit);clearTimeout(watchdog);clearInterval(countdown);stopTracks();
    start.disabled=stop.disabled=cancel.disabled=true;
    status.textContent=' Capture session ended. Run the cell again for another recording.';
    chunks=[];
  }
})()
