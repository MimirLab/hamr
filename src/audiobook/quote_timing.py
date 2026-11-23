"""Measure and verify direction within a single continuous speech take."""
from dataclasses import asdict, dataclass
from functools import lru_cache
import json
from math import isfinite
from pathlib import Path
import re
import subprocess

from . import performance
from .boundaries import sentence_spans
from .core import Error, RATE, atomic, digest, fault, filehash, key, loadjson, writejson


VERSION = 1
INSTRUCTION = ('Give only the middle quotation a quiet, breathy whisper. '
               'Use the usual narrator voice before and after the quotation.')
CONTINUING_INSTRUCTION = ('Give both quotations a quiet, breathy whisper. '
                          'Use the usual narrator voice in the narration and the reporting words between them.')
FRAME = performance.FRAME_GUIDANCE_CAPABILITY['frame_samples']
ANCHOR = {'start_frame': 0, 'end_frame': 1, 'scale': 0, 'base_scale': 0}
WORKER = Path(__file__).with_name('timing_worker.py')
MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')


def owned_whisper(text, context_version):
    """Return the complete quotation spans owned by one explicit whisper clause."""
    quoted = performance.quotation_text(text, context_version)
    sentences = list(sentence_spans(quoted))
    actor = performance.attribution_actor(context_version)
    found = []
    for quote in re.finditer(performance.QUOTATION, quoted):
        boundary = max((part['end'] for part in sentences if part['end'] <= quote.start()), default=0)
        start = len(quoted) - len(quoted[boundary:].lstrip())
        for part in sentences:
            if part['end'] < quote.end():
                continue
            turn = performance.dialogue_attribution(quoted[start:part['end']].rstrip(), 'whispered',
                actor=actor, allow_colon=True, context_version=context_version)
            if turn and not turn.groupdict().get('continuation'):
                offsets = tuple(start + offset for offset in turn.span('quote'))
                if offsets == quote.span():
                    continuation = performance.attributed_turn_continuation(
                        quoted[quote.start():], 'whispered', None, actor=actor, context_version=context_version)
                    if continuation and context_version >= 21:
                        following = tuple(quote.start()+offset for offset in continuation[1])
                        found.append((offsets,following))
                    elif not continuation:
                        found.append((offsets,))
                    break
    return found[0] if len(found) == 1 else None


def words(text):
    return re.findall(r"[a-z]+(?:'[a-z]+)?|\d+", text.lower().replace('’', "'"))


def scope(source, spoken, context_version):
    # Transform layout whitespace but establish ownership before using a spoken
    # offset. Rephrasing and numeric expansion can change that offset.
    if performance.normalize_space(source) != spoken:
        return None
    quoted = owned_whisper(spoken, context_version)
    if not quoted:
        return None
    if not words(spoken[:quoted[0][0]]) or any(not words(spoken[a+1:b-1]) for a,b in quoted):
        return None
    if len(quoted)>1:
        first, second = quoted
        if len(words(spoken[first[1]:second[0]]))<2:
            return None
        return {'quotations':[list(span) for span in quoted], 'version':2}
    (start, stop), = quoted
    if len(words(spoken[stop:]))<2:
        return None
    return {'quotation': [start,stop], 'version': VERSION}


@dataclass(frozen=True)
class Edges:
    before: int
    start: int
    end: int
    after: int

    def valid_start(self, start_frame):
        return self.before <= start_frame*FRAME <= self.start+FRAME

    def proposal(self, prior=None):
        start = max((self.before+FRAME-1)//FRAME, self.start//FRAME)
        if prior is not None and self.valid_start(prior['start_frame']):
            start = prior['start_frame']
        stop = (self.end+FRAME-1)//FRAME
        if start >= stop or not self.valid_start(start):
            return None
        # A sub-frame gap may still fit after the directed words change
        # length. Try the snapped interval and verify it on that take.
        available = max(0,self.after//FRAME-stop)
        return performance.validated_frame_guidance({
            'start_frame':start, 'end_frame':stop, 'scale':1.5,
            'release_frames':min(2,available),
        })

    def contains(self, schedule):
        hold = schedule['end_frame']*FRAME
        release = hold+schedule['release_frames']*FRAME
        return self.valid_start(schedule['start_frame']) and self.end <= hold and release <= self.after


def measurements(text, quotation, primary, secondary, *, samples=None):
    """Use one complete timed transcript and confirm the local edges independently."""
    left, right = quotation
    prefix, target, suffix = (words(part) for part in (text[:left],text[left+1:right-1],text[right:]))
    expected = prefix + target + suffix
    begin, end = len(prefix), len(prefix)+len(target)
    if [row['word'] for row in primary] != expected:
        return None

    labels = [row['word'] for row in secondary]
    if labels[:len(expected)] == expected:
        start = min(primary[begin]['start'],secondary[begin]['start'])
        before = max(primary[begin-1]['end'],secondary[begin-1]['end'])
        other_index = end
    else:
        tail = target[-3:]
        edge = tail+suffix[:2]
        candidates = [i+len(tail) for i in range(len(labels)) if labels[i:i+len(edge)] == edge]
        if len(candidates) != 1:
            return None
        (other_index,) = candidates
        before, start = primary[begin-1]['end'],primary[begin]['start']

    stop = max(primary[end-1]['end'],secondary[other_index-1]['end'])
    if suffix:
        after = min(primary[end]['start'],secondary[other_index]['start'])
    elif samples is not None:
        after = samples/RATE
    else:
        return None
    if not (before <= start < stop <= after and all(isfinite(v) for v in (before,start,stop,after))):
        return None
    return Edges(*(round(value*RATE) for value in (before,start,stop,after)))


@lru_cache(maxsize=32)
def cached_digest(path, size, modified, changed):
    return filehash(path)


def fingerprint(path):
    status = path.stat()
    return cached_digest(path,status.st_size,status.st_mtime_ns,status.st_ctime_ns)


def bind(store, job):
    """Pin the already installed timing models for an eligible new job."""
    if ('quote_timing_models' in job or not performance.supports_frame_guidance(job.get('backend') or {})
            or not (job.get('voice') or {}).get('reference_sha256')
            or not any(unit.get('quote_timing') for unit in job['plan']['utterances'])):
        return
    from .checks_setup import configured

    if saved:=configured(store):
        try:
            models = []
            for field in ('second_model', 'model'):
                directory = Path(saved[field])
                models.append({'path':str(directory), 'files':{name:fingerprint(directory/name) for name in MODEL_FILES}})
            if models[0]['files']['model.bin'] != models[1]['files']['model.bin']:
                job['quote_timing_models'] = {'worker':fingerprint(WORKER), 'python':saved['python'], 'models':models}
        except OSError:
            return


def active(job, unit):
    target = unit.get('quote_timing')
    return (target and job.get('quote_timing_models') and not unit.get('performance')
            and performance.supports_frame_guidance(job.get('backend') or {},multiple='quotations' in target)
            and (job.get('voice') or {}).get('reference_sha256'))


def owned_checkpoint(path):
    if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir()):
        raise Error('unsafe_path','The timing checkpoint must be an owned directory.')
    path.mkdir(mode=0o700,exist_ok=True)


def read_take(path, request):
    try:
        record = path/'receipt.json'
        if record.is_symlink():
            return None
        receipt = loadjson(record)
        pcm = path/'audio.pcm'
        if receipt['request'] != request or pcm.is_symlink() or pcm.stat().st_size > 2*RATE*125:
            return None
        data = pcm.read_bytes()
        if digest(data) == receipt['audio']['sha256'] and len(data) == 2*receipt['audio']['samples']:
            return data,receipt['audio']
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def observe(recipe, path, receipt):
    seconds = receipt['samples']/RATE
    observations = []
    for index, model in enumerate(recipe['models']):
        record = path/f'words-{index}.json'
        identity = key([receipt['sha256'],model,recipe['worker']])
        try:
            if record.is_symlink():
                raise ValueError('Timing observations must be regular files.')
            saved = loadjson(record)
            if saved['identity'] == identity and saved['word_sha256'] == key(saved['words']):
                observations.append(saved['words'])
                continue
        except (OSError, ValueError, TypeError, KeyError):
            pass
        request = {'pcm':str(path/'audio.pcm'),'model':model['path']}
        output = subprocess.check_output([recipe['python'],str(WORKER)],input=json.dumps(request).encode(),
                                         stderr=subprocess.PIPE,timeout=180)
        found = []
        for row in json.loads(output):
            start, end = row['start'],row['end']
            if not (isfinite(start) and isfinite(end) and 0<=start<=end<=seconds+0.12):
                return None
            found.extend({'word':word,'start':start,'end':end} for word in words(row['text']))
        writejson(record,{'identity':identity,'word_sha256':key(found),'words':found})
        observations.append(found)
    return observations


def verified_models(recipe):
    try:
        return (fingerprint(WORKER) == recipe['worker'] and Path(recipe['python']).is_file()
                and all(fingerprint(Path(model['path'])/name) == expected
                        for model in recipe['models'] for name,expected in model['files'].items()))
    except OSError:
        return False


def refine(measured, schedule):
    if len(measured)==1:
        return measured[0].proposal(schedule)
    previous = schedule['windows'] if schedule is not None else [None]*len(measured)
    windows = []
    for edges, prior in zip(measured,previous,strict=True):
        chosen = prior if prior and edges.contains(prior) else edges.proposal(prior)
        if chosen is None:
            return None
        windows.append({name:value for name,value in chosen.items() if name!='base_scale'})
    for left,right in zip(windows,windows[1:]):
        if left['end_frame'] > right['start_frame']:
            return None
        left['release_frames'] = min(left['release_frames'],right['start_frame']-left['end_frame'])
    return performance.validated_frame_guidance({'windows':windows})


def render(store, job, unit, clip, synthesize):
    """Reuse completed trials and adopt a direction only on measured speech."""
    recipe = job['quote_timing_models']
    target = unit['quote_timing']
    multiple = 'quotations' in target
    quotations = target['quotations'] if multiple else [target['quotation']]
    instruction = CONTINUING_INSTRUCTION if multiple else INSTRUCTION
    root = store.path(job)/'quote-timing'
    owned_checkpoint(root)
    checkpoint = root/clip
    owned_checkpoint(checkpoint)
    text, ordinary = performance.request(unit)
    history = []
    reason = 'timing_models_changed'
    schedule = ANCHOR
    if verified_models(recipe):
        for index in range(4 if multiple else 3):
            trial = checkpoint/str(index)
            owned_checkpoint(trial)
            options = {**ordinary, 'instruction':instruction,'frame_guidance':schedule}
            result = read_take(trial,options)
            reused = result is not None
            if result is None:
                try:
                    result = synthesize(text,options)
                except Error as exc:
                    if exc.code not in ('frame_guidance_unreached','frame_guidance_unavailable','frame_guidance_unconfirmed'):
                        raise
                    reason = exc.code
                    break
                data,receipt = result
                atomic(trial/'audio.pcm',data)
                writejson(trial/'receipt.json',{'request':options,'audio':receipt})
                fault('after_quote_anchor' if index == 0 else 'after_quote_candidate')
            data,receipt = result
            history.append({'pcm_sha256':receipt['sha256'],'schedule':schedule,'checkpoint_reused':reused})
            try:
                observations = observe(recipe,trial,receipt)
                measured = [measurements(text,span,*observations,samples=receipt['samples'])
                            for span in quotations] if observations else []
            except (OSError, ValueError, TypeError, KeyError, OverflowError, subprocess.SubprocessError):
                measured = []
            if not measured or not all(measured):
                reason = 'words_or_timing_uncertain'
                break
            if multiple:
                history[-1]['quote_edges_samples'] = [asdict(edges) for edges in measured]
            else:
                history[-1]['edges_samples'] = asdict(measured[0])
            if index:
                windows = schedule['windows'] if multiple else [schedule]
                if all(edges.contains(window) for edges,window in zip(measured,windows,strict=True)):
                    return data,{**receipt,'quote_timing':{**target,'selected':'directed','trials':history}}
            proposal = refine(measured,schedule if index else None)
            if proposal is None or any(trial['schedule']==proposal for trial in history):
                reason = 'no_clear_frame_window'
                break
            schedule = proposal
            reason = 'corrected_take_outside_quote'
    data,receipt = synthesize(text,ordinary)
    return data,{**receipt,'quote_timing':{**target,'selected':'ordinary','reason':reason,'trials':history}}
