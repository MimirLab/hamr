"""Runs only under an explicitly selected isolated faster-whisper Python."""
import json,subprocess,sys,time
from pathlib import Path
import numpy as np
from faster_whisper import WhisperModel

def main():
    req=json.load(sys.stdin)
    model=WhisperModel(req['model'],device='cpu',compute_type='int8',cpu_threads=2,local_files_only=True)
    rows=[]
    for item in req['clips']:
        started=time.monotonic()
        audio=np.frombuffer(subprocess.run(['ffmpeg','-v','error','-f','s16le','-ar','24000','-ac','1','-i',item['path'],'-ar','16000','-ac','1','-f','f32le','pipe:1'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout,dtype='<f4')
        segments,info=model.transcribe(audio,language='en',beam_size=5,condition_on_previous_text=False,initial_prompt=None,vad_filter=False)
        segs=[{'start':s.start,'end':s.end,'text':s.text,'avg_logprob':s.avg_logprob} for s in segments]
        rows.append({'utterance':item['utterance'],'text':' '.join(s['text'].strip() for s in segs),'segments':segs,'seconds':time.monotonic()-started})
    print(json.dumps({'rows':rows,'device':'cpu','compute_type':'int8','prompt':None}))
if __name__=='__main__': main()
