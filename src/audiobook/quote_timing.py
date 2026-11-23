"""Measure and verify direction within a single continuous speech take."""
from dataclasses import asdict, dataclass
from functools import lru_cache
import json
from math import isfinite
from pathlib import Path
import re
import subprocess

from . import asr_fluid, performance
from .boundaries import sentence_spans
from .core import Error, RATE, atomic, digest, fault, filehash, key, loadjson, writejson


VERSION = 1
INSTRUCTION = ('Give only the middle quotation a quiet, breathy whisper. '
               'Use the usual narrator voice before and after the quotation.')
CONTINUING_INSTRUCTION = ('Give both quotations a quiet, breathy whisper. '
                          'Use the usual narrator voice in the narration and the reporting words between them.')
NARRATION_INSTRUCTION = 'Read in the original speaker’s natural, fully voiced audiobook narration.'
FRAME = performance.FRAME_GUIDANCE_CAPABILITY['frame_samples']
ANCHOR = {'start_frame': 0, 'end_frame': 1, 'scale': 0, 'base_scale': 0}
WORKER = Path(__file__).with_name('timing_worker.py')
MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')
WORD = re.compile(r"[a-z]+(?:'[a-z]+)?|\d+")
QUIET_QUOTE_POWER = 0.5


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
    return WORD.findall(text.lower().replace('’', "'"))


def source_spellings(decisions):
    """Index single-word spellings licensed by the compiler's expansion trace."""
    choices = []
    offset = 0
    for decision in decisions:
        audit = decision.get('abbreviations', {})
        compiled = words(decision['spoken'])
        edits = audit.get('readings', ())
        if edits:
            raw = (decision['source'] if audit['input_basis'] == 'block_source' else audit['input'])
            cursor = 0
            expanded = []
            found = []
            for edit in edits:
                start, stop = edit['input_start'], edit['input_end']
                if not cursor <= start < stop <= len(raw) or raw[start:stop] != edit['source']:
                    break
                printed = words(edit['source'])
                spoken = words(edit['spoken'])
                expanded.extend(words(raw[cursor:start]))
                if len(printed) == len(spoken) == 1 and printed != spoken:
                    found.append([offset+len(expanded), printed[0], spoken[0]])
                expanded.extend(spoken)
                cursor = stop
            else:
                expanded.extend(words(raw[cursor:]))
                if expanded == compiled:
                    choices.extend(found)
        offset += len(compiled)
    return choices


def observation_spellings(rows, expected, spellings, *, allow_trailing=False):
    """Apply a printed form only at a known word position in the complete take."""
    if not spellings or len(rows)<len(expected) or (len(rows)>len(expected) and not allow_trailing):
        return rows
    updated = list(rows)
    for index, printed, spoken in spellings:
        if 0<=index<len(expected) and rows[index]['word'] == printed and expected[index] == spoken:
            updated[index] = {**rows[index], 'word':spoken}
    if len(rows)>len(expected) and [row['word'] for row in updated[:len(expected)]]!=expected:
        return rows
    return updated


def primary_word_groups(parts, labels):
    groups = [words(part) for part in parts]
    if labels == [word for group in groups for word in group]:
        return groups

    # A compound may be printed with a syllable break and transcribed as one
    # word. Allow the observer to split or join only those source hyphens.
    patterns = []
    for part in parts:
        terms = re.findall(r"[a-z]+(?:'[a-z]+)?(?:[-‐‑][a-z]+(?:'[a-z]+)?)*|\d+",
                           part.lower().replace('’', "'"))
        compounds = [' ?'.join(re.escape(word) for word in words(term)) for term in terms]
        patterns.append(' '.join(compounds))
    if not any(' ?' in pattern for pattern in patterns):
        return None
    joined = ' '.join(f'({pattern})' for pattern in patterns if pattern)
    match = re.fullmatch(joined,' '.join(labels))
    if match:
        found = iter(match.groups())
        return [next(found).split() if pattern else [] for pattern in patterns]
    return None


def scope(source, spoken, context_version):
    source = performance.normalize_space(source)
    quoted = owned_whisper(spoken, context_version)
    if not quoted:
        return None
    if source != spoken:
        if context_version < 22:
            return None
        source_owned = owned_whisper(source, context_version)
        if not source_owned:
            return None
        # The spoken compiler can expand words anywhere in the paragraph.
        # Establish ownership on each text and compare quote order before
        # using the offsets of the version actually sent to Breeze.
        if quotation_indexes(source, source_owned, context_version) != quotation_indexes(spoken, quoted, context_version):
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


def quotation_indexes(text, owned, context_version):
    quoted = performance.quotation_text(text, context_version)
    ranges = [match.span() for match in re.finditer(performance.QUOTATION, quoted)]
    return len(ranges), tuple(index for index, span in enumerate(ranges) if span in owned)


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

    def contains(self, schedule, *, allow_tapered_tail=False):
        if schedule['end_frame']=='speech':
            return self.valid_start(schedule['start_frame'])
        hold = schedule['end_frame']*FRAME
        release = hold+schedule['release_frames']*FRAME
        # At 80 ms per frame, the last part of a quotation can fit inside a
        # taper even when no longer full-strength window fits before narration.
        covered = self.end <= hold or (allow_tapered_tail and self.end <= min(hold+FRAME,release))
        return self.valid_start(schedule['start_frame']) and covered and release <= self.after


@dataclass(frozen=True)
class ExitOverlap:
    before: int
    start: int
    last_end: int
    next_start: int

    def proposal(self):
        start = max((self.before+FRAME-1)//FRAME,self.start//FRAME)
        # Snap the midpoint of the two estimates to the nearest frame. The
        # next take must still establish an independent, measurable exit.
        stop = (self.last_end+self.next_start+FRAME)//(2*FRAME)
        if self.before <= start*FRAME <= self.start+FRAME and start < stop <= 1500:
            return performance.validated_frame_guidance({'start_frame':start,'end_frame':stop,'scale':1.5})
        return None


def exit_overlap(text, quotation, primary, secondary, samples):
    """Find a one-frame exit disagreement after two complete free transcripts."""
    left, right = quotation
    parts = text[:left],text[left+1:right-1],text[right:]
    intervals = []
    for rows in (primary,secondary):
        groups = primary_word_groups(parts,[row['word'] for row in rows])
        if groups is None:
            return None
        prefix, quoted, suffix = groups
        if not (prefix and quoted and suffix):
            return None
        begin, end = len(prefix), len(prefix)+len(quoted)
        times = (rows[begin-1]['end'],rows[begin]['start'],rows[end-1]['end'],rows[end]['start'])
        if not (all(isfinite(point) for point in times) and 0<=times[0]<=times[1]<times[2]<=times[3]<=samples/RATE):
            return None
        intervals.append(tuple(round(point*RATE) for point in times))
    first, second = intervals
    overlap = ExitOverlap(max(first[0],second[0]),min(first[1],second[1]),
                          max(first[2],second[2]),min(first[3],second[3]))
    if overlap.before<=overlap.start<overlap.next_start and 0<overlap.last_end-overlap.next_start<=FRAME:
        return overlap
    return None


def measurements(text, quotation, primary, secondary, *, samples=None):
    """Use one complete timed transcript and confirm the local edges independently."""
    left, right = quotation
    parts = text[:left],text[left+1:right-1],text[right:]
    groups = primary_word_groups(parts,[row['word'] for row in primary])
    if groups is None:
        return None
    prefix, target, suffix = groups
    expected = prefix + target + suffix
    begin, end = len(prefix), len(prefix)+len(target)

    labels = [row['word'] for row in secondary]
    if labels[:len(expected)] == expected:
        other_begin, other_end = begin, end
    elif other_groups := primary_word_groups(parts,labels):
        other_begin = len(other_groups[0])
        other_end = other_begin + len(other_groups[1])
    else:
        lead, head = prefix[-2:], target[:3]
        tail = target[-4:]
        first = lead + head
        last = tail + suffix[:2]
        begins = [i+len(lead) for i in range(len(labels)) if labels[i:i+len(first)] == first]
        if suffix:
            ends = [i+len(tail) for i in range(len(labels)) if labels[i:i+len(last)] == last]
        else:
            ends = [len(labels)] if labels[-len(tail):] == tail else []
        if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
            return None
        other_begin, other_end = begins[0], ends[0]

    before = max(primary[begin-1]['end'],secondary[other_begin-1]['end'])
    start = min(primary[begin]['start'],secondary[other_begin]['start'])
    stop = max(primary[end-1]['end'],secondary[other_end-1]['end'])
    if suffix:
        after = min(primary[end]['start'],secondary[other_end]['start'])
    elif samples is not None:
        after = samples/RATE
    else:
        return None
    if not (before <= start < stop <= after and all(isfinite(v) for v in (before,start,stop,after))):
        return None
    return Edges(*(round(value*RATE) for value in (before,start,stop,after)))


def emitted_words(observation):
    """Map token emission points to their words without inventing word ends."""
    tokens = [token.lower().replace('’', "'") for token in observation['tokens']]
    points = [time for token,time in zip(tokens,observation['timestamps'],strict=True) for _ in token]
    lexemes = list(WORD.finditer(''.join(tokens)))
    if [word[0] for word in lexemes] != words(observation['text']):
        return None
    return [{'word':word[0], 'first':points[word.start()], 'last':points[word.end()-1]}
            for word in lexemes]


def corroborated_edges(text, quotation, primary, independent, samples):
    """Require the full words and nearby token emissions before trusting an edge."""
    left, right = quotation
    parts = text[:left],text[left+1:right-1],text[right:]
    chunks = primary_word_groups(parts,[row['word'] for row in primary])
    other = primary_word_groups(parts,[row['word'] for row in independent])
    if chunks is None or other is None:
        return None
    begin, end = len(chunks[0]),len(chunks[0])+len(chunks[1])
    head, tail = len(other[0]),len(other[0])+len(other[1])
    pairs = [(primary[begin-1],independent[head-1]), (primary[begin],independent[head]),
             (primary[end-1],independent[tail-1])]
    if chunks[2]:
        pairs.append((primary[end],independent[tail]))
    # Fluid emits token positions on an 80 ms grid. Use them to corroborate
    # the approximate word intervals, retaining the other model's word ends.
    slop = 2*FRAME/RATE
    if any(not timed['start']-slop<=heard['first']<=heard['last']<=timed['end']+slop
           for timed,heard in pairs):
        return None
    before = max(primary[begin-1]['end'],independent[head-1]['last'])
    start = min(primary[begin]['start'],independent[head]['first'])
    stop = max(primary[end-1]['end'],independent[tail-1]['last'])
    after = min(primary[end]['start'],independent[tail]['first']) if chunks[2] else samples/RATE
    if not 0<=before<=start<stop<=after<=samples/RATE:
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
    from .checks_setup import configured, configured_fluid

    if saved:=configured(store):
        try:
            models = []
            for field in ('second_model', 'model'):
                directory = Path(saved[field])
                models.append({'path':str(directory), 'files':{name:fingerprint(directory/name) for name in MODEL_FILES}})
            if models[0]['files']['model.bin'] != models[1]['files']['model.bin']:
                revision = 8 if supports_inline_whisper(job['backend']) else 7
                job['quote_timing_models'] = {'worker':fingerprint(WORKER), 'python':saved['python'],
                                              'models':models, 'timing_algorithm':revision}
                if saved.get('requested_profile')!='whisper' and (fluid:=configured_fluid(saved)):
                    try:
                        _, identity = asr_fluid.observer_identity()
                        job['quote_timing_models']['fluid'] = {
                            'observer_sha256':identity, 'model':fluid['model'], 'python':fluid['python'],
                        }
                    except Error:
                        pass
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


def observe_fluid(recipe, path, receipt):
    if asr_fluid.observer_identity()[1] != recipe['observer_sha256']:
        return None
    cache = path/'fluid-tokens.json'
    row = asr_fluid.cached_observation(cache,recipe['observer_sha256'],receipt)
    if row is None:
        row = asr_fluid.observe(recipe,path/'audio.pcm',receipt,timestamps=True)
        writejson(cache,{'observation':row, 'observation_sha256':key(row)})
    if row.get('timing_kind') == 'tokens':
        return emitted_words(row)
    return None


def timed_transcripts(recipe, trial, receipt, text, spellings):
    observations = observe(recipe,trial,receipt)
    if not observations:
        return []
    expected = words(text)
    allow_trailing = recipe.get('timing_algorithm',3)>=7
    return [observation_spellings(rows,expected,spellings,allow_trailing=allow_trailing) for rows in observations]


def measure_take(recipe, trial, receipt, text, quotations, spellings):
    transcripts = timed_transcripts(recipe,trial,receipt,text,spellings)
    if not transcripts:
        return [], []
    samples = receipt['samples']
    expected = words(text)
    primary,secondary = transcripts
    measured = [measurements(text,span,primary,secondary,samples=samples) for span in quotations]
    corroborated = []
    if not all(measured) and recipe.get('fluid') and primary_word_groups((text,),[row['word'] for row in primary]):
        try:
            independent = observe_fluid(recipe['fluid'],trial,receipt)
        except Error:
            independent = None
        if independent:
            independent = observation_spellings(independent,expected,spellings)
            for index, span in enumerate(quotations):
                if measured[index] is None and (edges:=corroborated_edges(text,span,primary,independent,samples)):
                    measured[index] = edges
                    corroborated.append(index)
    return measured,corroborated


def refine(measured, schedule, *, ends_in_quote=False, overlaps=None):
    overlaps = overlaps or {}
    if len(measured)==1:
        if 0 in overlaps:
            return overlaps[0].proposal()
        return measured[0].proposal(schedule)
    previous = schedule['windows'] if schedule is not None else [None]*len(measured)
    windows = []
    for index, (edges, prior) in enumerate(zip(measured,previous,strict=True)):
        overlap = overlaps.get(index)
        if overlap:
            chosen = overlap.proposal()
        else:
            chosen = prior if prior and edges.contains(prior) else edges.proposal(prior)
        if chosen is None:
            return None
        windows.append({name:value for name,value in chosen.items() if name!='base_scale'})
    if ends_in_quote:
        # There is no following narration to protect. Use the speech's actual
        # end, which is free to move as the director changes the last words.
        windows[-1].update(end_frame='speech',release_frames=0)
    for left,right in zip(windows,windows[1:]):
        if left['end_frame'] > right['start_frame']:
            return None
        left['release_frames'] = min(left['release_frames'],right['start_frame']-left['end_frame'])
    return performance.validated_frame_guidance({'windows':windows})


def one_frame_taper(measured, schedule):
    """Try a short release when exactly one quote ends just beyond a hard cut."""
    windows = schedule['windows']
    changes = []
    for index, (edges, window) in enumerate(zip(measured, windows, strict=True)):
        if edges.contains(window, allow_tapered_tail=True):
            continue
        if (window['end_frame']=='speech' or window['release_frames']
                or window['end_frame'] >= (windows[index+1]['start_frame'] if index+1<len(windows) else 1500)):
            return None
        tapered = {**window,'release_frames':1}
        if not edges.contains(tapered, allow_tapered_tail=True):
            return None
        changes.append((index,tapered))
    if len(changes)!=1:
        return None
    index, window = changes[0]
    return {**schedule,'windows':[ *windows[:index], window, *windows[index+1:]]}


def supports_inline_whisper(backend):
    return (performance.supports_quote_guidance(backend)
            and performance.supports_narration_conditioning(backend)
            and performance.supports_frame_guidance(backend,multiple=True))


def checkpoint_take(trial, text, options, synthesize, fault_stage, *, identity=None):
    owned_checkpoint(trial)
    identity = options if identity is None else identity
    if (saved:=read_take(trial,identity)) is not None:
        return saved,True
    data,receipt = synthesize(text,options)
    atomic(trial/'audio.pcm',data)
    writejson(trial/'receipt.json',{'request':identity,'audio':receipt})
    fault(fault_stage)
    return (data,receipt),False


def verified_edges(job, trial, receipt, text, quotations, target):
    try:
        return measure_take(job['quote_timing_models'],trial,receipt,text,quotations,
                            target.get('source_spellings'))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, subprocess.SubprocessError):
        return [],[]


def maximum_quote_power(data, measured):
    """Measure the strongest quoted interval relative to opening narration."""
    import numpy as np
    pcm = np.frombuffer(data,dtype='<i2').astype('float64')
    opening = pcm[:measured[0].before]
    opening_power = np.mean(opening**2) if opening.size else 0
    if opening_power>0 and all(0<=edge.start<edge.end<=len(pcm) for edge in measured):
        return max(float(np.mean(pcm[edge.start:edge.end]**2)/opening_power) for edge in measured)
    return None


def decoder_take(trial, job, text, ordinary, target, quotations, instruction, synthesize):
    """Try one causal take and verify its actual speech and emitted frame intervals."""
    scopes = [[len(text[:left+1].encode('utf-8')),len(text[:right-1].encode('utf-8'))]
              for left,right in quotations]
    options = {**ordinary,'instruction':instruction,'quote_guidance':scopes}
    if performance.supports_narration_conditioning(job['backend']):
        options['narration_instruction'] = NARRATION_INSTRUCTION
    try:
        result,reused = checkpoint_take(trial,text,options,synthesize,'after_quote_decoder')
    except Error as exc:
        if exc.code not in ('quote_guidance_unavailable','quote_guidance_unconfirmed'):
            raise
        return None,{'reason':exc.code}
    data,receipt = result
    report = {'pcm_sha256':receipt['sha256'],'checkpoint_reused':reused}
    trace = receipt.get('quote_guidance') or {}
    windows = trace.get('windows',[])
    report['windows'] = windows
    if trace.get('scopes')!=scopes or [w['scope'] for w in windows]!=list(range(1,len(scopes)+1)):
        return None,{**report,'reason':'guidance_scope_mismatch'}
    measured,corroborated = verified_edges(job,trial,receipt,text,quotations,target)
    if len(measured)!=len(windows) or not all(measured):
        return None,{**report,'reason':'words_or_timing_uncertain'}
    report['quote_edges_samples'] = [asdict(edges) for edges in measured]
    report['maximum_quote_power_ratio'] = contrast = maximum_quote_power(data,measured)
    if corroborated:
        report['fluid_corroborated_quotes'] = corroborated
    if not all(edges.contains(window) for edges,window in zip(measured,windows,strict=True)):
        return None,{**report,'reason':'guidance_outside_quote'}
    # A scoped whisper may be voiced, but a quote louder than the reading
    # around it is a poor automatic substitution. Keep the timed fallback.
    if contrast is None or contrast>1:
        return None,{**report,'reason':'whisper_louder_than_opening'}
    return result,report


def inline_request(text, quotations):
    """Insert engine cues and keep their UTF-8 scopes separate from spoken offsets."""
    parts, scopes = [], []
    cursor = size = 0
    for left,right in quotations:
        lead = text[cursor:left+1]
        line = '(whispering) '+text[left+1:right-1]
        start = size + len(lead.encode('utf-8'))
        size = start + len(line.encode('utf-8'))
        scopes.append([start,size])
        parts.extend((lead,line))
        cursor = right-1
    return ''.join([*parts,text[cursor:]]),scopes


def inline_schedule(fragments, measured, *, terminal):
    """Calibrate one continuous window per quote from measured words and causal frames."""
    scopes = {}
    for window in fragments:
        pair = scopes.setdefault(window['scope'],[window['start_frame'],window['end_frame']])
        pair[1] = window['end_frame']
    if list(scopes)!=list(range(1,len(measured)+1)):
        return None
    windows = []
    for index,edge in enumerate(measured,1):
        start,end = scopes[index]
        if not edge.valid_start(start):
            start = max((edge.before+FRAME-1)//FRAME,edge.start//FRAME)
        end = min(edge.after//FRAME,max((edge.end+FRAME-1)//FRAME,end))
        if terminal and index==len(measured):
            end = 'speech'
        window = {'start_frame':start,'end_frame':end,'scale':1.5,'release_frames':0}
        if (end!='speech' and end<=start) or not edge.contains(window):
            return None
        if windows and windows[-1]['end_frame']>start:
            return None
        windows.append(window)
    return performance.validated_frame_guidance({'windows':windows,'base_scale':0})


def inline_take(checkpoint, job, text, ordinary, target, quotations, instruction, synthesize):
    """Try semantic cues and one independently verified adjustment of their timing."""
    marked,scopes = inline_request(text,quotations)
    common = {**ordinary,'instruction':instruction,'narration_instruction':NARRATION_INSTRUCTION}
    options = {**common,'quote_guidance':scopes}
    trials = []
    for index in range(2):
        trial = checkpoint/f'inline-{index}'
        try:
            result,reused = checkpoint_take(trial,marked,options,synthesize,'after_quote_inline',
                                            identity={'text':marked,**options})
        except Error as exc:
            if exc.code not in ('quote_guidance_unavailable','quote_guidance_unconfirmed',
                                'frame_guidance_unreached','frame_guidance_unavailable',
                                'frame_guidance_unconfirmed','speech_input_limit'):
                raise
            trials.append({'reason':exc.code})
            break
        data,receipt = result
        trace = receipt.get('quote_guidance' if index==0 else 'frame_guidance') or {}
        windows = trace.get('windows',[])
        report = {'pcm_sha256':receipt['sha256'],'checkpoint_reused':reused,'windows':windows}
        trials.append(report)
        if index==0 and trace.get('scopes')!=scopes:
            report['reason'] = 'guidance_scope_mismatch'
            break
        measured, corroborated = verified_edges(job,trial,receipt,text,quotations,target)
        if not windows or len(measured)!=len(quotations) or not all(measured):
            report['reason'] = 'words_or_timing_uncertain'
            break
        report['quote_edges_samples'] = [asdict(edge) for edge in measured]
        if corroborated:
            report['fluid_corroborated_quotes'] = corroborated
        report['maximum_quote_power_ratio'] = contrast = maximum_quote_power(data,measured)
        if contrast is None or contrast>QUIET_QUOTE_POWER:
            report['reason'] = 'insufficient_contrast'
            break
        if (len(windows)==len(quotations) and (index or
                [window['scope'] for window in windows]==list(range(1,len(quotations)+1)))
                and all(edge.contains(window) for edge,window in zip(measured,windows,strict=True))):
            return result,trials
        report['reason'] = 'guidance_outside_quote'
        if index==0:
            terminal = not words(text[quotations[-1][1]:])
            if not (schedule:=inline_schedule(windows,measured,terminal=terminal)):
                break
            options = {**common,'frame_guidance':schedule}
    return None,trials


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
    ends_in_quote = multiple and not words(text[quotations[-1][1]:])
    history = []
    reason = 'timing_models_changed'
    schedule = ANCHOR
    seed = ordinary['seed']
    attempts = 4 if multiple else 3
    allow_short_taper = multiple and recipe.get('timing_algorithm',3)>=4
    allow_exit_overlap = recipe.get('timing_algorithm',3)>=5
    decoder = {}
    if verified_models(recipe):
        if performance.supports_quote_guidance(job.get('backend') or {}):
            decoded, attempt = decoder_take(checkpoint/'decoder',job,text,ordinary,target,quotations,
                                            instruction,synthesize)
            decoder['decoder_attempt'] = attempt
            power = attempt.get('maximum_quote_power_ratio')
            if (recipe.get('timing_algorithm',3)>=8 and supports_inline_whisper(job['backend'])
                    and attempt.get('reason')!='quote_guidance_unavailable'
                    and (decoded is None or power is None or power>QUIET_QUOTE_POWER)):
                inline, trials = inline_take(checkpoint,job,text,ordinary,target,quotations,instruction,synthesize)
                decoder['inline_attempts'] = trials
                if inline is not None:
                    data, receipt = inline
                    return data,{**receipt,'quote_timing':{**target,'selected':'directed',
                        'controller':'inline','trials':[],**decoder}}
            if decoded is not None:
                data,receipt = decoded
                return data,{**receipt,'quote_timing':{**target,'selected':'directed','controller':'decoder',
                                                       'trials':[],**decoder}}
        for index in range(attempts+allow_short_taper):
            trial = checkpoint/str(index)
            owned_checkpoint(trial)
            anchoring = schedule==ANCHOR
            options = {**ordinary,'seed':seed,'instruction':instruction,'frame_guidance':schedule}
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
                fault('after_quote_anchor' if anchoring else 'after_quote_candidate')
            data,receipt = result
            history.append({'pcm_sha256':receipt['sha256'],'schedule':schedule,'checkpoint_reused':reused})
            overlaps = {}
            try:
                spellings = target.get('source_spellings')
                measured, corroborated = measure_take(recipe,trial,receipt,text,quotations,
                                                       spellings)
                if allow_exit_overlap and measured and not all(measured) and index<attempts-1:
                    missing = [position for position,edges in enumerate(measured) if edges is None]
                    if len(missing)==1 and (transcripts:=timed_transcripts(recipe,trial,receipt,text,spellings)):
                        position, = missing
                        if hint:=exit_overlap(text,quotations[position],*transcripts,receipt['samples']):
                            overlaps[position] = hint
            except (OSError, ValueError, TypeError, KeyError, OverflowError, subprocess.SubprocessError):
                measured, corroborated = [], []
            if not measured or (not all(measured) and not overlaps):
                reason = 'words_or_timing_uncertain'
                break
            if overlaps:
                history[-1]['exit_overlap_proposals'] = {str(i):asdict(hint) for i,hint in overlaps.items()}
            if allow_exit_overlap:
                history[-1]['seed'] = seed
            if multiple:
                history[-1]['quote_edges_samples'] = [asdict(edges) if edges else None for edges in measured]
            else:
                history[-1]['edges_samples'] = asdict(measured[0]) if measured[0] else None
            if corroborated:
                history[-1]['fluid_corroborated_quotes'] = corroborated
            if not anchoring and not overlaps:
                windows = schedule['windows'] if multiple else [schedule]
                pairs = list(zip(measured,windows,strict=True))
                if all(edges.contains(window) for edges,window in pairs):
                    return data,{**receipt,'quote_timing':{**target,'selected':'directed','trials':history,**decoder}}
                if (multiple and index >= attempts-1
                        and all(edges.contains(window,allow_tapered_tail=True) for edges,window in pairs)):
                    return data,{**receipt,'quote_timing':{**target,'selected':'directed','trials':history,
                                                           'exit_taper_covers_last_word':True,**decoder}}
            if index==attempts:
                break
            if index==0 and overlaps and recipe.get('timing_algorithm',3)>=6:
                # Timing can move when the seed changes. Measure the alternate
                # seed in ordinary delivery before using it for direction.
                seed += 1 if seed<2**31-1 else -1
                continue
            tried = [trial['schedule'] for trial in history]
            proposal = refine(measured,None if anchoring else schedule,ends_in_quote=ends_in_quote,overlaps=overlaps)
            if allow_short_taper and index==attempts-1:
                if proposal is not None and proposal not in tried:
                    break
                proposal = one_frame_taper(measured,schedule)
            if proposal is None or proposal in tried:
                reason = 'no_clear_frame_window'
                break
            schedule = proposal
            reason = 'corrected_take_outside_quote'
    data,receipt = synthesize(text,ordinary)
    return data,{**receipt,'quote_timing':{**target,'selected':'ordinary','reason':reason,'trials':history,**decoder}}
