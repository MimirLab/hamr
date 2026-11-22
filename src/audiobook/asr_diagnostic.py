"""Compare local, unprompted transcripts without changing a take or a job."""
from difflib import SequenceMatcher
from pathlib import Path
from shutil import copyfile
import subprocess
from tempfile import TemporaryDirectory

from . import jobs, performance, qa, qa_cache
from .checks_setup import observer
from .core import Error, RATE, filehash, key
from .speech_compare import comparison_tokens, notation_observation


def snapshot_take(store, job_id, utterance, path):
    with store.lock():
        job = store.get(job_id)
        unit = next((u for u in job['plan']['utterances'] if u['id']==utterance),None)
        if unit is None:
            raise Error('unknown_utterance','Choose an utterance in the saved plan.')
        clip = jobs.clip_key(unit,job)
        receipt = jobs.valid_clip(store,clip)
        if not receipt:
            raise Error('qa_missing_clip','This passage has no committed verified audio.')
        copyfile(store.root/'clips'/clip/'audio.pcm',path)
        if filehash(path)!=receipt['sha256']:
            raise Error('qa_clip_changed','The committed audio changed while it was being read.')
    return unit, clip, receipt, job['revision']


def audio_file(source, path):
    source = Path(source).expanduser().absolute()
    if not source.is_file():
        raise Error('asr_audio_missing',f'Audio file not found: {source}')
    try:
        subprocess.run(['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe',
                        '-i',str(source),'-map','0:a:0','-ac','1','-ar',str(RATE),'-t','126',
                        '-f','s16le',str(path)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,check=True,timeout=60)
    except (OSError,subprocess.SubprocessError) as exc:
        raise Error('asr_audio_decode','Could not decode this local audio file.',
                    'Use a short WAV or an excerpt from the book.') from exc
    size = path.stat().st_size
    if not 0<size<=RATE*250 or size%2:
        raise Error('asr_audio_duration','Choose a nonempty audio excerpt up to 125 seconds.')
    return {'sha256':filehash(path),'samples':size//2,'seconds':size/(2*RATE)}


def token_disagreements(first, second):
    a,b = map(comparison_tokens,(first,second))
    return [{'change':tag,'primary':a[i:j],'secondary':b[k:l],
             'primary_token_span':[i,j],'secondary_token_span':[k,l]}
            for tag,i,j,k,l in SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if tag!='equal']


def digital_silence(path):
    """Exact zero runs are signal facts, independent of how the line should sound."""
    import numpy as np

    silent = np.fromfile(path,dtype='<i2') == 0
    edges = np.flatnonzero(np.diff(np.pad(silent,(1,1))))
    return [{'start_seconds':start/RATE,'end_seconds':end/RATE}
            for start,end in zip(edges[::2],edges[1::2]) if end-start>=RATE//4]


def additional_observation(store, path, receipt, unit, baseline):
    from . import asr_fluid

    try:
        profile = asr_fluid.configured(store)
        if profile is None:
            return None
        if receipt['seconds']>30:
            return {'status':'unsupported_duration','profile':asr_fluid.PROFILE,'max_seconds':30}
        result = asr_fluid.observe(profile,path,receipt)
    except Error as exc:
        return {'status':'unavailable','profile':asr_fluid.PROFILE,'error':exc.code,'message':exc.message}
    transcript = result['text']
    if unit:
        transcript = notation_observation(unit['text'],performance.lexical_observation(unit,transcript))
        result['word_edits'] = qa.distance(comparison_tokens(unit['text']),comparison_tokens(transcript))
        if sound := performance.authored_sound_observation(unit,result['text'],transcript):
            result['authored_sound_observation'] = sound
    return {**result,'comparison_text':transcript,
            'disagreements_with_primary':token_disagreements(baseline,transcript)}


def transcribe(store, source, *, utterance=None, python=None, model=None, second=None):
    python, model, second = observer(store,python,model,second)
    worker = Path(qa_cache.__file__).with_name('qa_worker.py')
    with TemporaryDirectory(prefix='hamr-transcribe-') as scratch:
        path = Path(scratch)/'audio.pcm'
        unit = clip = None
        target = {'file':str(Path(source).expanduser().absolute())}
        if utterance:
            unit, clip, receipt, revision = snapshot_take(store,source,utterance,path)
            target = {'job':source,'revision':revision,'utterance':utterance,'expected':unit['text']}
        else:
            receipt = audio_file(source,path)
        observations = []
        primary = None
        duplicate = False
        for role, selected in (('primary',model),('secondary',second)):
            if selected is None:
                continue
            identity = qa_cache.observer_identity(python,selected,worker)
            weights = qa_cache.model_fingerprint(identity)
            if role=='secondary' and primary and weights==primary:
                duplicate = True
                continue
            primary = weights if role=='primary' else primary
            raw = qa_cache.read_observation(store,clip,receipt,identity) if clip else None
            reused = raw is not None
            if raw is None:
                raw, = qa_cache.recognize(python,selected,[{'utterance':utterance or 'audio','path':str(path)}])
                if qa_cache.observer_identity(python,selected,worker)!=identity:
                    raise Error('asr_environment_changed','Speech-check assets changed during transcription.')
            aligned = qa_cache.audible_text(raw,receipt['seconds'])
            name = identity.get('profile') or Path(selected).name
            reading = {'role':role,'observer_sha256':key(identity),'model':name,
                       'model_sha256':weights,'device':identity.get('device'),'compute_type':identity.get('compute_type'),
                       'cache_reused':reused,'timing_extends_past_audio':any(s['end']>receipt['seconds']+1
                                                                           for s in raw['segments']),
                       'raw':raw,'comparison_text':aligned}
            if unit:
                compared = notation_observation(unit['text'],performance.lexical_observation(unit,aligned))
                reading['comparison_text'] = compared
                reading['word_edits'] = qa.distance(comparison_tokens(unit['text']),comparison_tokens(compared))
                if sound := performance.authored_sound_observation(unit,aligned,compared):
                    reading['authored_sound_observation'] = sound
            observations.append(reading)
        challenger = None
        if observations[0]['model']!='fluid-int8':
            challenger = additional_observation(store,path,receipt,unit,observations[0]['comparison_text'])
        silence = digital_silence(path)
        if filehash(path)!=receipt['sha256']:
            raise Error('qa_clip_changed','The PCM changed during transcription.')
    differences = []
    if len(observations)==2:
        differences = token_disagreements(*(row['comparison_text'] for row in observations))
    return {'schema_version':1,'target':target,'pcm_sha256':receipt['sha256'],'samples':receipt['samples'],
            'audio_seconds':receipt['seconds'],'sample_rate':RATE,'channels':1,'prompt':None,
            'observations':observations,'disagreements':differences,'challenger':challenger,'digital_silence':silence,
            'comparison_status':('recognizers_disagree' if differences else 'recognizers_agree') if len(observations)==2
                                else ('duplicate_model' if duplicate else 'one_observer'),
            'assessment':'Transcription evidence only. Acting, sound quality, and pronunciation require listening.'}
