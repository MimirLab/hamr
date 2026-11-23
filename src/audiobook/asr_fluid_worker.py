"""Isolated full-take Fluid CPU worker. No prompt, VAD or clean-mode graph."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import onnx_asr
import onnxruntime


def read_take(request, ffmpeg, model):
    pcm = Path(request['pcm']).read_bytes()
    sha256 = hashlib.sha256(pcm).hexdigest()
    if len(pcm)!=2*request['samples'] or sha256!=request['pcm_sha256'] or not 0<len(pcm)<=6_000_000:
        raise ValueError('PCM did not match the supplied take')
    raw = subprocess.run([ffmpeg,'-v','error','-f','s16le','-ar','24000','-ac','1','-i','pipe:0',
                          '-ar','16000','-f','f32le','pipe:1'],input=pcm,stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,check=True,timeout=30).stdout
    audio = np.frombuffer(raw,dtype='<f4')
    if abs(len(audio)-round(request['samples']*2/3))>1:
        raise ValueError('Resampled audio length differs from the requested take')
    started = time.monotonic()
    result = model.recognize(audio,sample_rate=16000)
    transcript = ({'text':result} if isinstance(result,str) else
                  {'text':result.text,'tokens':result.tokens,'timestamps':result.timestamps})
    return {'pcm_sha256':sha256,'processed_samples':request['samples'],**transcript,
            'seconds':time.monotonic()-started,'input_frames_16khz':len(audio)}


def main():
    request = json.load(sys.stdin)
    session = onnxruntime.SessionOptions()
    session.intra_op_num_threads = 2
    session.inter_op_num_threads = 1
    model = onnx_asr.load_model('nemo-conformer-tdt',request['model'],quantization='int8',
                                providers=['CPUExecutionProvider'],sess_options=session)
    for graph in (model.asr._encoder,model.asr._decoder_joint):
        if graph.get_providers()!=['CPUExecutionProvider']:
            raise RuntimeError('Unexpected ONNX execution provider')
    if request.get('timestamps'):
        model = model.with_timestamps()
    metadata = {'schema_version':1,'provider':'CPUExecutionProvider','compute_type':'int8-weights',
                'language_policy':'auto','runtime':{name:version(name) for name in ('onnx-asr','onnxruntime','numpy')}}
    if 'clips' in request:
        rows = [read_take(take,request['ffmpeg'],model) for take in request['clips']]
        output = {**metadata,'rows':rows}
    else:
        output = {**metadata,**read_take(request,request['ffmpeg'],model)}
    print(json.dumps(output))


if __name__=='__main__':
    main()
