"""Run under the existing local faster-whisper environment."""
import json
import subprocess
import sys

from faster_whisper import WhisperModel
import numpy as np


def main():
    request = json.load(sys.stdin)
    pcm = subprocess.run([
        'ffmpeg','-v','error','-f','s16le','-ar','24000','-ac','1','-i',request['pcm'],
        '-ar','16000','-ac','1','-f','f32le','pipe:1',
    ], stdin=subprocess.DEVNULL, capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm,dtype='<f4')
    model = WhisperModel(request['model'],device='cpu',compute_type='int8',cpu_threads=3,local_files_only=True)
    segments, _ = model.transcribe(audio,language='en',beam_size=5,initial_prompt=None,
                                   condition_on_previous_text=False,word_timestamps=True,vad_filter=False)
    print(json.dumps([{'text':word.word,'start':word.start,'end':word.end}
                      for segment in segments for word in (segment.words or ())],allow_nan=False))


if __name__ == '__main__':
    main()
