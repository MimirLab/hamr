"""Source-owned delivery cues and the corresponding local Breeze requests."""
from collections import Counter
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
import re
import struct
import sys

from .boundaries import sentence_spans
from .core import Error, RATE, key, loadjson
from .linguistic import spoken_vocal_marker
from .source_math import MATH_SPANS
from .source_passages import Continuation, SourcePassages, legacy_prose_scope

MODES = ('auto', 'off', 'experimental')
CONTEXT_VERSION = 35
RULES_FILE = Path(__file__).with_name('performance_rules.json')
CATALOG_FILE = Path(__file__).with_name('catalog.json')
MARKER = r'(?:\[[a-z]+(?: [a-z]+){0,3}\]|\([a-z]+(?: [a-z]+){0,3}\))'
LEADING = re.compile(r'^(?P<cue>'+MARKER+r')\s+(?P<dialogue>.+)$', re.I|re.S)
TRAILING = re.compile(r'^(?P<dialogue>.+?)\s+(?P<cue>'+MARKER+r')(?P<stop>[.!?])?$', re.I|re.S)
STANDALONE = re.compile(r'^'+MARKER+r'$', re.I)
SPEAKER = re.compile(r'^(?P<name>[A-Z][A-Z ]{1,30}):\s*(?P<line>.+)$', re.S)
QUOTATION = (r'(?:“[^“”\n]+”|"[^"\n]+"|'
             r'‘(?:[^‘’\n]|(?<=\w)’(?=\w))+’|'
             r"(?<!\w)'(?:[^'\n]|(?<=\w)'(?=\w))+'(?!\w))")
ATTRIBUTION = (r'(?:(?:(?:Mr|Mrs|Ms|Dr|Prof|Rev|Capt|Sgt|Col)\.\s+)?'
               r'[A-Z][a-z]+(?:[ -][A-Z][a-z]+){0,2}|[Hh]e|[Ss]he|[Tt]hey|I|[Ww]e)')
ROLE_DETERMINER = r'(?i:the|an?|this|that|my|your|his|her|our|their|its)'
ROLE_CLAUSE_WORDS = (
    'the|a|an|and|or|but|because|if|when|whenever|while|although|where|that|who|whom|which|as|'
    'at|in|to|from|of|after|before|with|for|on|by|not|never|then|may|might|could|should|would|'
    'will|had|did|has|does|was|were|is|are'
)
ROLE_WORD = rf"(?!(?i:{ROLE_CLAUSE_WORDS})\b)[A-Za-z]+(?:[-'’][A-Za-z]+)*"
ROLE_TAIL = r'(?!(?i:very|rather|somewhat|quite|slightly|almost|barely|hardly|really|still)\b)'
ROLE_ATTRIBUTION = rf'(?:{ATTRIBUTION}|{ROLE_DETERMINER}\s+(?:{ROLE_WORD}\s+){{0,3}}{ROLE_TAIL}{ROLE_WORD})'
MIXED_DOUBLE_QUOTES = re.compile(r'(?P<before>[^"“”]*)(?:“(?P<curly>[^"“”\n]+)"|'
                                 r'(?<!\w)"(?P<straight>[^"“”\n]+)”)(?P<after>[^"“”]*)')
NEUTRAL_REPORTING = ('said','added','answered','replied','continued','asked','remarked','observed','returned','repeated')
POSSESSIVES = {'he':'his','she':'her','they':'their','i':'my','we':'our'}
PRIVATE_KINDS = {'literal', 'hidden', 'generated_label', 'object_label', 'note_reference'}
SAMPLING_LIMITS = {'temperature':(.1,2), 'top_k':(1,256), 'top_p':(.05,1), 'repetition_penalty':(.8,1.4)}
FRAME_GUIDANCE_CAPABILITY = {'version':1, 'encoding':'multipart', 'frame_samples':1920, 'chunk_frames':10}
FRAME_WINDOWS_CAPABILITY = {'version':1, 'max_windows':8, 'speech_end':True}
NARRATION_CONDITIONING_VERSION = 1
QUOTE_GUIDANCE_VERSION = 1


def supports_frame_guidance(backend, *, multiple=False):
    return (backend.get('frame_guidance') == FRAME_GUIDANCE_CAPABILITY
            and (not multiple or backend.get('frame_guidance_windows') == FRAME_WINDOWS_CAPABILITY))


def supports_narration_conditioning(backend):
    value = backend.get('narration_conditioning')
    return type(value) is int and value == NARRATION_CONDITIONING_VERSION


def supports_quote_guidance(backend):
    value = backend.get('quote_guidance')
    return type(value) is int and value == QUOTE_GUIDANCE_VERSION and supports_frame_guidance(backend)


def validate_narration_instruction(instruction, schedule):
    if (not schedule or schedule['base_scale']!=0 or not isinstance(instruction,str)
            or not instruction.strip() or len(instruction.encode('utf-8'))>1024
            or any(token in instruction for token in ('<ins_','[S'))):
        raise Error('narration_conditioning','A narration instruction needs plain direction text and zero-base frame guidance.')


def guidance_scale(number):
    if type(number) not in (float, int) or not 0 <= number <= 3.4028234663852886e38:
        raise Error('frame_guidance', 'Use finite nonnegative guidance scales.')
    return struct.unpack('f', struct.pack('f', number))[0]


def validated_windows(value):
    windows = value['windows']
    if (value.keys() - {'windows','base_scale'} or not isinstance(windows,list)
            or not 1 <= len(windows) <= FRAME_WINDOWS_CAPABILITY['max_windows']):
        raise Error('frame_guidance', 'Supply one to eight ordered guidance windows and an optional base_scale.')
    result = []
    previous_end = 0
    for index,window in enumerate(windows):
        if not isinstance(window,dict) or window.keys()-{'start_frame','end_frame','scale','release_frames'}:
            raise Error('frame_guidance', 'Use start_frame, end_frame, scale and optional release_frames for each window.')
        through_end = window.get('end_frame') == 'speech'
        if through_end and (index != len(windows)-1 or window.get('release_frames',0)!=0):
            raise Error('frame_guidance', 'A window ending at speech completion must be last and use zero release.')
        checked = validated_frame_guidance({**window,'end_frame':1500} if through_end else window)
        del checked['base_scale']
        if checked['start_frame'] < previous_end:
            raise Error('frame_guidance', 'Guidance intervals and their releases must be ordered and cannot overlap.')
        previous_end = checked['end_frame'] + checked['release_frames']
        if through_end:
            checked['end_frame'] = 'speech'
        result.append(checked)
    return {'windows':result, 'base_scale':guidance_scale(value.get('base_scale',0))}


def validated_frame_guidance(value):
    if isinstance(value,dict) and 'windows' in value:
        return validated_windows(value)
    required = {'start_frame', 'end_frame', 'scale'}
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - {'base_scale', 'release_frames'}:
        raise Error('frame_guidance', 'Supply start_frame, end_frame and scale, with optional base_scale and release_frames.')
    start, end, scale = (value[field] for field in ('start_frame', 'end_frame', 'scale'))
    release, base = value.get('release_frames', 0), value.get('base_scale', 0)
    if (not all(type(number) is int for number in (start, end, release))
            or not 0 <= start < end <= 1500 or not 0 <= release <= 1500 - end):
        raise Error('frame_guidance', 'The held interval and release must fit inside the 1,500-frame speech request.')
    return {'start_frame':start, 'end_frame':end, 'release_frames':release,
            'scale':guidance_scale(scale), 'base_scale':guidance_scale(base)}


def validate_guidance(value, parallel=1):
    if type(value) not in (int, float) or value not in (1, 2, 2.5, 3):
        raise Error('speech_guidance', 'Choose Breeze guidance of 1, 2, 2.5, or 3.')
    if value != 1 and parallel != 1:
        raise Error('speech_guidance', 'Stronger guidance needs a serial audition.')


def validated_sampling(fields):
    if not isinstance(fields,dict) or fields.keys()-SAMPLING_LIMITS.keys():
        raise Error('performance_sampling','Use temperature, top_k, top_p or repetition_penalty.')
    for name,value in fields.items():
        low,high=SAMPLING_LIMITS[name]
        allowed=(int,) if name=='top_k' else (int,float)
        if type(value) not in allowed or not low<=value<=high:
            raise Error('performance_sampling',f'{name} must be between {low} and {high}'+
                        (', as a whole number.' if name=='top_k' else '.'))
    return {name:int(value) if name=='top_k' else float(value) for name,value in fields.items()}


def validated_tuning(fields):
    if not isinstance(fields,dict) or not fields:
        raise Error('performance_sampling','Provide guidance or sampling settings as an object.')
    sampling = validated_sampling({k:v for k,v in fields.items() if k!='cfg_scale'})
    if 'cfg_scale' in fields:
        validate_guidance(fields['cfg_scale'])
        sampling['cfg_scale'] = float(fields['cfg_scale'])
    return sampling


def tuning_preferences(config, enabled, registry):
    overrides=config.get('tuning',{})
    if not isinstance(overrides,dict):
        raise Error('performance_sampling','Performance tuning must map recipe names to settings.')
    known=set(registry['directions'])|set(registry['events'])|set(registry['structural'])|{'contrastive_emphasis','gentle_entry'}
    result={}
    for recipe,fields in overrides.items():
        if recipe not in known or not isinstance(fields,dict) or not fields:
            raise Error('performance_sampling',f'Choose a known performance recipe with tuning settings: {recipe}.')
        result[recipe]=validated_tuning(fields)
    return result if enabled else {}


@cache
def literal_pattern(path):
    registry = loadjson(path)
    labels = dict.fromkeys([*registry['literal_markers'],
                           *(label for event in registry['events'].values() for label in event['spellings'])])
    alternatives = '|'.join(map(re.escape,labels))
    return re.compile(r'\[\s*(?:'+alternatives+r')\s*\]|\(\s*(?:'+alternatives+r')\s*\)', re.I)


def neutralize_controls(text):
    return literal_pattern(RULES_FILE).sub(
        lambda match:spoken_vocal_marker(match[0][0]+match[0][1:-1].strip()+match[0][-1]),text)


def normalize_space(text):
    return ' '.join(text.split())


def quotation_text(text, context_version):
    """Expose a single mixed pair to the matcher without moving source offsets."""
    if context_version >= 17 and (match := MIXED_DOUBLE_QUOTES.fullmatch(text)):
        return match['before'] + '“' + (match['curly'] or match['straight']) + '”' + match['after']
    return text


def block_spans(blocks, start, stop):
    parts = []
    offset = 0
    for block in blocks:
        end = offset + len(block.source)
        if start < end and stop > offset:
            parts.append({'block':block.id,'span':[max(start,offset)-offset,min(stop,end)-offset]})
        offset = end + 1  # The space joining adjacent fragments.
    return parts


def locate_passage_candidate(candidate, blocks):
    source = candidate['source']
    candidate['source'] = block_spans(blocks,*source['span'])[0]
    for evidence in candidate.get('evidence', ()):
        if evidence.get('block'):
            continue  # Evidence from the preceding source retains its own offsets.
        cue = block_spans(blocks,*evidence['span'])
        evidence.update(block=cue[0]['block'],span=cue[0]['span'],cue_parts=cue)
        if quotes:=evidence.pop('quotation_spans',None):
            evidence['quotation_parts'] = [part for span in quotes for part in block_spans(blocks,*span)]
        if target:=evidence.pop('target_span',None):
            evidence['target_parts'] = block_spans(blocks,*target)
        if turn:=evidence.pop('turn_span',None):
            evidence['turn_parts'] = block_spans(blocks,*turn)
        if reporting:=evidence.pop('reporting_span',None):
            evidence['reporting_parts'] = block_spans(blocks,*reporting)
        if speaker:=evidence.pop('speaker_span',None):
            evidence['speaker_parts'] = block_spans(blocks,*speaker)


def attribution_actor(version):
    return ROLE_ATTRIBUTION if version>=8 else ATTRIBUTION


def attributed_modifiers(registry, version):
    if version<15:
        return {}
    return {f'{verb} {word}':affect for verb,modifiers in registry['attributed_modifiers'].items()
            for word,affect in modifiers.items()}


def attribution_rules(registry, version, custom):
    recipes = registry['directions']
    specific = {cue:kind for kind,item in recipes.items() if version>=item.get('since',1) for cue in item['cues']}
    reporting = registry['context']['reporting_modifiers']
    modifiers = reporting['modifiers'] if version>=2 else {}
    if version>=4:
        modifiers = {**modifiers, **reporting['manner']}
    verbs = {f'{verb} {modifier}':kind for kind,terms in modifiers.items()
             for verb in reporting['verbs'] for modifier in terms}
    verbs.update(specific)
    compound = attributed_modifiers(registry,version)
    verbs.update({phrase:specific[phrase.split(' ',1)[0]] for phrase in compound})
    verbs.update(custom)
    neutral = '|'.join(map(re.escape,dict.fromkeys([*NEUTRAL_REPORTING,*verbs])))
    postposed = None
    if version>=4 or custom:
        heads = list(dict.fromkeys([*reporting['verbs'],*(phrase.split(' ',1)[0] for phrase in compound)]))
        terms = sorted({tail for phrase in verbs for head,_,tail in [phrase.partition(' ')]
                        if head in heads and tail})
        base = '|'.join(map(re.escape,heads))
        ending = '|'.join(map(re.escape,terms))
        tail = r'(?:\s*[.!?]|,\s*(?P<continuation>'+QUOTATION+r')\s*[.!?]?)' if compound else r'\s*[.!?]'
        postposed = re.compile(r'(?P<quote>'+QUOTATION+r')\s*,?\s*(?P<inverted>(?P<verb>'+base+
                               r')\s+(?P<actor>'+attribution_actor(version)+r')\s+(?P<modifier>'+ending+r'))'+tail)
    return verbs,neutral,postposed


def attribution_phrase(match):
    if modifier:=match.groupdict().get('modifier'):
        return match['verb']+' '+modifier
    return match['verb']


def continuing_quotation(match):
    first,second = match['quote'],match['continuation']
    return (first[0]==second[0] and first[-2] in ',.!?'
            and re.match(r'[a-z]',second[1:].lstrip()))


def reporting_clause_continues_quote(match, context_version):
    if context_version < 19 or match.start('actor') < match.start('quote'):
        return True
    quoted = match['quote'][1:-1].rstrip()
    after = match.string[match.end('quote'):].lstrip()
    # A full stop and a capitalized new clause end the preceding speech.
    return not (quoted.endswith('.') and not quoted.endswith('...')
                and after and after[0].isupper())


def dialogue_attribution(source, cue_pattern, postposed=None, *, actor=ATTRIBUTION, allow_colon=False,
                         context_version=1):
    # A recognized manner following the speaker belongs to the reporting verb.
    if postposed is not None and (match:=postposed.fullmatch(source)):
        if (reporting_clause_continues_quote(match, context_version)
                and (not match.groupdict().get('continuation') or continuing_quotation(match))):
            return match
    after = re.fullmatch(r'(?P<quote>'+QUOTATION+r')\s*,?\s*(?P<actor>'+actor+r')'+
                         r'\s+(?P<verb>'+cue_pattern+r')\s*[.!?]', source)
    inverted = re.fullmatch(r'(?P<quote>'+QUOTATION+r')\s*,?\s*(?P<inverted>'
                            r'(?P<verb>'+cue_pattern+r')\s+(?P<actor>'+actor+r'))\s*[.!?]', source)
    separator = r'(?:,|\s*:)' if allow_colon else ','
    before = re.fullmatch(r'(?P<actor>'+actor+r')\s+(?P<verb>'+cue_pattern+
                          r')'+separator+r'\s*(?P<quote>'+QUOTATION+r')\s*[.!?]?', source)
    if simple:=after or inverted or before:
        return simple if reporting_clause_continues_quote(simple, context_version) else None
    opening=r'(?P<quote>'+QUOTATION+r')\s+'
    ending=r',\s*(?P<continuation>'+QUOTATION+r')\s*[.!?]?'
    interrupted = re.fullmatch(opening+r'(?P<actor>'+actor+r')\s+(?P<verb>'+cue_pattern+r')'+ending,source)
    if not interrupted:
        interrupted=re.fullmatch(opening+r'(?P<inverted>(?P<verb>'+cue_pattern+r')\s+(?P<actor>'+actor+r'))'+ending,source)
    if (interrupted and reporting_clause_continues_quote(interrupted, context_version)
            and interrupted['quote'][-2] in ',.!?'
            and re.match(r'[a-z]',interrupted['continuation'][1:].lstrip())):
        return interrupted
    return None


def embedded_attribution(source, cue_pattern, postposed=None, *, actor=ATTRIBUTION,
                         allow_interrupted=False, allow_colon=False, context_version=1):
    """Find one attributed turn with at most one narrator sentence on each side."""
    if len(source)>480:
        return None
    quotes = list(re.finditer(QUOTATION,source))
    interrupted = allow_interrupted and len(quotes)==2
    if len(quotes)!=1 and not interrupted:
        return None
    sentences = list(sentence_spans(source))
    before = [part for part in sentences if part['end']<=quotes[0].start()]
    if len(before)>1:
        return None
    start = before[-1]['end'] if before else 0
    for part in sentences:
        if part['end']<quotes[-1].end():
            continue
        stop=len(source[:part['end']].rstrip())
        if candidate:=dialogue_attribution(source[start:stop],cue_pattern,postposed,actor=actor,
                                            allow_colon=allow_colon,context_version=context_version):
            if interrupted and (not candidate.groupdict().get('continuation')
                                or quotes[0][0][0]!=quotes[-1][0][0]):
                return None
            tail=source[stop:].strip()
            if interrupted and re.fullmatch(r'\([^()]+[.!?]\)', tail):
                tail=tail[1:-1].strip()
            if tail and (tail[-1] not in '.!?' or len(list(sentence_spans(tail)))!=1):
                return None
            if start or tail:
                return candidate,start,stop
    return None


def attributed_turn_continuation(source, cue_pattern, postposed, *, actor, context_version=1):
    """Recognize a second complete quotation in the same imported paragraph."""
    quotes = list(re.finditer(QUOTATION, source))
    if len(quotes) != 2:
        return None
    first, second = quotes
    if (first.start() or first[0][0] != second[0][0] or not second[0][1].isupper()
            or second[0][-2] not in '.!?' or source[second.end():].strip() not in ('', '.', '!', '?')
            or not re.fullmatch(r'[^\n]+\.\s+', source[first.end():second.start()])):
        return None
    reporting = dialogue_attribution(source[:second.start()].rstrip(), cue_pattern, postposed,
                                     actor=actor, context_version=context_version)
    if reporting and reporting.start('quote') == 0:
        return reporting, second.span()
    return None


def quotation_flow(left, right, relationship, config, version):
    """Recognize ownership when intake found adjacent passage fragments."""
    continuation = re.fullmatch(QUOTATION, right)
    description = (version>=31 and re.search(r'\b(?:voice|words)\b',right,re.I)
                   and re.search(QUOTATION,left))
    if (not isinstance(config, dict) or not (continuation or description)
            or relationship < Continuation.ALIGNED_LINES or len(left) + len(right) > 480):
        return None
    registry = loadjson(RULES_FILE)
    source = left + ' ' + right
    if description and trailing_voice_description(source,registry['context'],'direct',version):
        return 'pdf_voice_description'
    if not continuation:
        return None
    custom = attribution_preferences(config, registry['directions'])
    verbs, cues, postposed = attribution_rules(registry, version, custom)
    turn = attributed_turn_continuation(source, cues, postposed, actor=attribution_actor(version),
                                         context_version=version)
    if turn and (kind := verbs.get(attribution_phrase(turn[0]))) and not registry['directions'][kind].get('progression'):
        if relationship == Continuation.PARAGRAPH:
            return 'tagged_pdf_quotation'
        return 'adjacent_pdf_quotation'
    return None


def voice_paragraphs(passages, excluded, version):
    """Keep the lead-in of an authored paragraph when it owns a voice cue."""
    if version<32:
        return []

    registry = loadjson(RULES_FILE)
    paragraphs = []
    for lines in passages.authored_paragraphs():
        if len(lines)<3 or any(line.id in excluded for line in lines):
            continue
        source = ' '.join(passages.text(line,legacy_layout=False) for line in lines)
        if trailing_voice_description(source,registry['context'],'direct',version):
            paragraphs.append(tuple(line.id for line in lines))
    return paragraphs


def focus_direction(source, config):
    """Bind an author's explicit emphasis to one unique phrase in the quotation."""
    verbs='|'.join(map(re.escape,config['attributions']))
    match=re.fullmatch(r'(?P<quote>'+QUOTATION+r')\s+(?P<actor>'+ATTRIBUTION+r')\s+said,\s*'
                       r'(?P<cue>(?:'+verbs+r')\s+(?:(?:the word|the words|the phrase)\s+)?'
                       r'(?P<focus>'+QUOTATION+r'))\s*[.!?]?',source)
    if not match:return None
    focus=normalize_space(match['focus'][1:-1])
    if not re.fullmatch(r'[A-Za-z]+(?:[ -][A-Za-z]+){0,2}',focus) or len(focus)>35:
        return None
    hits=list(re.finditer(r'(?<![\w’\'])'+re.escape(focus)+r'(?![\w’\'])',match['quote'][1:-1],re.I))
    if len(hits)!=1:return None
    start=match.start('quote')+1
    cue=list(match.span('cue'))
    evidence={'kind':'explicit_emphasis','span':cue,'target_span':[start+i for i in hits[0].span()]}
    return focus,evidence


def typographic_focus(source, spoken, spans, config, actor, *, owned_span=None):
    """Use a single marked personal pronoun inside a complete spoken turn."""
    marks = [span for span in spans if span['kind'] in ('emphasis','font_style')]
    if not marks or len(source)>480:
        return None
    quotes = list(re.finditer(QUOTATION,source))
    if len(quotes)!=1:
        return None
    quote, = quotes
    if len(quote[0].split())<3:
        return None
    standalone = source.strip()==quote[0] and quote[0][-2] in '.!?'
    reporting = '|'.join(NEUTRAL_REPORTING)
    if not (owned_span==quote.span() or standalone or dialogue_attribution(source,reporting,actor=actor)
            or embedded_attribution(source,reporting,actor=actor)):
        return None
    authored = {}
    visual = {}
    for mark in marks:
        start,end = mark['source_start'],mark['source_end']
        raw = source[start:end]
        word = re.fullmatch(r'\s*(?P<word>[A-Za-z]+)[,.!?]?\s*',raw)
        if not word or word['word'].casefold() not in config['marked_pronouns']:
            continue
        lo,hi = [start+i for i in word.span('word')]
        if not quote.start()<lo<hi<quote.end():
            continue
        phrase = word['word']
        if phrase!='I' and phrase!=phrase.lower() and (not phrase.istitle() or source[quote.start()+1:lo].strip()):
            # A mid-phrase capital such as an italicized "It" can be a title.
            continue
        if any(span['kind']=='title_style' and span['source_start']<hi and span['source_end']>lo
               for span in spans):
            continue
        # XHTML inline spans are recorded from inner to outer. For equal
        # ranges the closest authored font choice controls visual evidence.
        destination = authored if mark['kind']=='emphasis' else visual
        destination.setdefault((lo,hi),(phrase,mark))
    eligible = {site:value for site,value in visual.items() if value[1].get('style')!='normal'}
    eligible.update(authored)
    if len(eligible)!=1:
        return None
    (start,end),(phrase,mark) = next(iter(eligible.items()))
    unique = r"(?<![\w'’-])"+re.escape(phrase)+r"(?![\w'’-])"
    if [match.span() for match in re.finditer(unique,source,re.I)]!=[(start,end)] or len(re.findall(unique,spoken,re.I))!=1:
        return None
    evidence = {'kind':'typographic_emphasis','span':[mark['source_start'],mark['source_end']],
                'target_span':[start,end],'quotation_spans':[list(quote.span())],
                'source_kind':mark['kind']}
    return phrase,evidence


def continued_direction(source, previous, attribution_cues, config, anchor, postposed=None):
    """Carry an established delivery only into its explicitly continuing speaker."""
    if not anchor or anchor['recipe'] not in config['recipes'] or anchor.get('event') or anchor.get('anchor'):
        return None
    current=dialogue_attribution(source,'|'.join(map(re.escape,config['attributions'])))
    earlier=dialogue_attribution(previous.source,attribution_cues,postposed)
    if not current or not earlier or current['actor']!=earlier['actor'] or current['actor'].casefold() in POSSESSIVES:
        return None
    return {'kind':'continued_delivery','span':list(current.span('verb')),
            'previous_block':previous.id,
            'previous_attribution_span':list(earlier.span('modifier' if earlier.groupdict().get('modifier') else 'verb'))}


def context_matches(source, phrases):
    if not phrases:return None
    alternatives='|'.join(map(re.escape,phrases))
    return re.search(r'\b(?:'+alternatives+r')\b',source,re.I)


def dialogue_setup(source, previous):
    """Find up to two adjacent narrative sentences and one owned quotation."""
    reporting = '|'.join(NEUTRAL_REPORTING)
    if line := dialogue_attribution(source,reporting):
        return line['actor'], previous.source if previous else '', previous
    quotes = list(re.finditer(QUOTATION,source))
    if len(quotes) == 1:
        for boundary in re.finditer(r'[.!?]\s+',source[:quotes[0].start()]):
            if line := dialogue_attribution(source[boundary.end():],reporting):
                return line['actor'], source[:boundary.start()+1], None
    return None


def speaker_evidence(actor, setup, patterns, *, allow_local_pronoun=False, context_version=CONTEXT_VERSION):
    pronoun = POSSESSIVES.get(actor.casefold())
    possessives = (pronoun,) if pronoun else ('his','her','their')
    named_possessive = () if pronoun else (actor + "'s", actor + "’s")
    owners = (*named_possessive,*possessives) if pronoun or allow_local_pronoun else named_possessive
    normalized = normalize_space(setup).casefold()
    for entry in patterns:
        if context_version < entry.get('since',1):
            continue
        frames = entry['frames']
        if context_version >= 17:
            frames = [*frames, *entry.get('register_frames', ())]
        for frame in frames:
            for possessive in possessives:
                for owner in owners if '{owner}' in frame else ('',):
                    if normalized == frame.format(actor=actor,possessive=possessive,owner=owner).casefold():
                        if context_version < 17 and 'legacy_recipe' in entry:
                            return {**entry, 'recipe':entry['legacy_recipe']}
                        return entry
    return None


def dialogue_context(source, config, previous, preference, context_version):
    if preference == 'off' or not (context := dialogue_setup(source,previous)):
        return None
    actor, setup, owner = context
    if not setup or len(setup)>300 or re.search(r'["“”\n]',setup):
        return None
    spans = list(sentence_spans(setup))[-2:]
    rows = []
    for sentence in spans:
        body = re.fullmatch(r'(?P<body>[^!?]{4,150})[.!]',sentence['text'])
        entry = (speaker_evidence(actor,body['body'],config['speaker_cues'],
                                  allow_local_pronoun=bool(rows and rows[-1]),
                                  context_version=context_version)
                 if body else None)
        if not entry:
            rows.append(None)
            continue
        evidence = {'kind':'preceding_speaker_setup' if owner else 'same_speaker_setup',
                    'span':[sentence['start'],sentence['start']+body.end('body')],
                    'signal':entry['signal']}
        if owner:
            evidence['block'] = owner.id
        rows.append((entry,evidence))
    if not rows or not rows[-1]:
        return None
    newest, _ = rows[-1]
    matching = [row for row in rows if row]
    recipes = {entry['recipe'] for entry,_ in matching}
    recipe = newest['recipe']
    if len(recipes) != 1:
        if context_version >= 17 and recipes == {'murmured', 'whisper'}:
            # An explicit whisper refines other evidence of soft speech.
            recipe = 'whisper'
        else:
            return None
    strong = any(entry['strong'] for entry,_ in matching)
    corroborated = len({entry['signal'] for entry,_ in matching}) > 1
    if not strong and (preference != 'corroborated' or not corroborated):
        return None
    return recipe, [item for _,item in matching], owner is None


def trailing_voice_description(source, config, preference, context_version):
    """A description of the delivered words can support the preceding quote."""
    if preference=='off':
        return None
    cue_pattern = '|'.join(NEUTRAL_REPORTING)
    offset = 0
    if context_version>=31:
        actor = attribution_actor(context_version)
        owned = embedded_attribution(source,cue_pattern,actor=actor,allow_colon=True,
                                     context_version=context_version)
        if not owned:
            # An exact reporting clause still owns its quote after earlier
            # narration. The object of “asked” is the listener, not its owner.
            quotes = list(re.finditer(QUOTATION,source))
            if len(quotes)!=1:
                return None
            offset = quotes[0].start()
            asked = r'asked (?:him|her|them|me|us|you)'
            owned = embedded_attribution(source[offset:],f'{cue_pattern}|{asked}',actor=actor,
                                         context_version=context_version)
    else:
        owned = embedded_attribution(source,cue_pattern)
    if not owned:
        return None
    line,start,stop=owned
    stop += offset
    tail = source[stop:].lstrip()
    if not tail or (start and context_version<31):
        return None
    tail_start = len(source)-len(tail)
    body = tail.rstrip()[:-1]
    if context_version>=31 and body.startswith(('And ', 'and ')):
        body = body[4:]
        tail_start += 4
    if entry:=speaker_evidence(line['actor'],body,config['trailing_voice'],context_version=context_version):
        turn_start = offset+start
        evidence={'kind':'trailing_voice_description','span':[tail_start,tail_start+len(body)],
                  'signal':entry['signal'],
                  'quotation_spans':[[turn_start+point for point in line.span('quote')]]}
        return entry['recipe'], [evidence], bool(turn_start)
    return None


def voice_description_rules(config, context_version):
    """Compile literal delivery descriptions owned by the reporting clause."""
    prepositional = {phrase: recipe for recipe, entry in config.items() for phrase in entry['prepositional']}
    if context_version >= 17:
        prepositional.update({phrase: recipe for recipe, entry in config.items()
                              for phrase in entry.get('register_phrases', ())})
    if context_version >= 24:
        prepositional.update({phrase: recipe for recipe, entry in config.items()
                              for phrase in entry.get('register_aliases', ())})
    introductions = {**prepositional, **{phrase: recipe for recipe, entry in config.items()
                                        for phrase in entry.get('introductions', ())}}
    possessive = {phrase: recipe for recipe, entry in config.items() for phrase in entry['possessive']}
    voice_phrase = '|'.join(map(re.escape, prepositional))
    introduction_phrase = '|'.join(map(re.escape, introductions))
    possessive_phrase = '|'.join(map(re.escape, possessive))
    actor = (ROLE_ATTRIBUTION if context_version >= 17 else
             r'(?:' + ATTRIBUTION + r'|[Tt]he\s+(?:[a-z-]+\s+){0,2}[a-z-]+)')
    verbs = '|'.join(NEUTRAL_REPORTING)
    reporting = (rf'(?:(?P<actor_first>{actor})\s+(?P<verb_after>{verbs})|'
                 rf'(?P<verb_first>{verbs})\s+(?P<actor_after>{actor}))')
    after = re.compile(
        rf'\s*,?\s*{reporting}(?:\s*,?\s*(?P<prepositional>{voice_phrase})|'
        rf",\s*(?P<possessive>(?P<owner>my|our|his|her|their|{actor}['’]s)\s+"
        rf'(?P<predicate>{possessive_phrase})))\s*[.!?](?=\s|$)')
    before = re.compile(
        rf'(?:(?:^|,\s+)(?:(?:[Tt]hen|[Aa]t last|[Aa]t length)\s+)?{reporting}|'
        rf'[, ]\s*and\s+(?P<verb_linked>{verbs}))\s*,?\s*'
        rf'(?P<prepositional>{introduction_phrase})(?:\s+to\s+{actor})?\s*[:,]$')
    between = (re.compile(rf'\s*{reporting}\s*,?\s*(?P<prepositional>{voice_phrase})\s*,\s*')
               if context_version >= 24 else None)
    return before, after, between, introductions, prepositional, possessive


def described_attribution(source, rules):
    if len(source)>480:
        return None
    quotes = list(re.finditer(QUOTATION, source))
    before, after, between, introductions, prepositional, possessive = rules
    if len(quotes)==2 and between:
        first,second = quotes
        if first[0][-2]!=',' or not (
                match:=between.fullmatch(source[first.end():second.start()])):
            return None
        verb = 'verb_after' if match['verb_after'] else 'verb_first'
        offset = first.end()
        evidence = {'kind':'attached_voice_description','position':'between',
                    'span':[offset+i for i in match.span('prepositional')],
                    'reporting_span':[offset+i for i in match.span(verb)],
                    'quotation_spans':[list(quote.span()) for quote in quotes]}
        return prepositional[match['prepositional']], [evidence], first.start()>0
    if len(quotes)!=1:
        return None
    quote, = quotes
    lead = list(sentence_spans(source[:quote.start()]))
    opening = before.search(lead[-1]['text']) if lead else None
    if opening and opening['verb_linked']:
        setup = lead[-1]['text'][:opening.start()]
        if re.search(r"\b(?:that|whether|if|not|never|denied|doubted)\b|n['’]t\b",setup,re.I):
            opening = None
    closing = after.match(source[quote.end():])
    if bool(opening)==bool(closing):
        return None
    match = opening or closing
    offset = lead[-1]['start'] if opening else quote.end()
    verb = next(group for group in ('verb_after','verb_first','verb_linked') if match.groupdict().get(group))
    if match.groupdict().get('owner'):
        actor = (match['actor_first'] or match['actor_after']).casefold()
        owner = match['owner'].casefold().replace('’', "'")
        # A named reporter can own the adjacent possessive when the entire
        # passage is that one turn and its description.
        local_owner = (quote.start()==0 and offset+match.end()==len(source)
                       and actor not in POSSESSIVES and owner in ('his','her','their'))
        if owner != POSSESSIVES.get(actor, actor + "'s") and not local_owner:
            return None
        recipe = possessive[match['predicate']]
        cue = 'possessive'
    else:
        recipe = (introductions if opening else prepositional)[match['prepositional']]
        cue = 'prepositional'
    evidence = {'kind':'attached_voice_description', 'position':'before' if opening else 'after',
                'span':[offset+i for i in match.span(cue)],
                'reporting_span':[offset+i for i in match.span(verb)], 'quotation_spans':[list(quote.span())]}
    return recipe, [evidence], quote.start()>0


def sentence_seam(text, start, end):
    """A marker can own the pause between two complete spoken sentences."""
    before, after = text[:start].rstrip(), text[end:].lstrip()
    if not before or before[-1] not in '.!?' or not after:
        return False
    boundary = len(before)+1
    return any(span['end']==boundary for span in sentence_spans(before+' '+after))


def inline_event_rules(registry, version):
    return {kind:recipe for kind,recipe in registry['events'].items()
            if (since := recipe.get('inline_since')) is not None and since<=version}


def inline_event_site(source, recipes, actor, *, owned_span=None):
    if len(source)>480 or not any(marker in source for marker in '[('):
        return None
    marks = list(literal_pattern(RULES_FILE).finditer(source))
    quoted = list(re.finditer(QUOTATION,source))
    if len(marks)!=1 or len(quoted)!=1:
        return None
    mark, = marks
    quote, = quoted
    start, end = quote.start()+1, quote.end()-1
    if not start<mark.start()<mark.end()<end or not (
            source[mark.start()-1].isspace() and source[mark.end()].isspace()):
        return None
    if not sentence_seam(source[start:end],mark.start()-start,mark.end()-start):
        return None
    spelling = mark[0][1:-1].strip().lower()
    kind = next((kind for kind,rule in recipes.items() if spelling in rule['spellings']),None)
    if not kind:
        return None
    reporting = '|'.join(NEUTRAL_REPORTING)
    if not (owned_span==quote.span() or dialogue_attribution(source,reporting,actor=actor)
            or embedded_attribution(source,reporting,actor=actor)):
        return None
    return kind, quote, mark


def inline_event_continues(left, right, relationship, version, config=None):
    if not relationship or not right.lstrip().startswith(('[','(')):
        return False
    source = quotation_text(left+' '+right,version)
    registry = loadjson(RULES_FILE)
    rules = inline_event_rules(registry,version)
    actor = attribution_actor(version)
    owned_span = None
    if version >= 14:
        custom = attribution_preferences(config or {}, registry['directions'])
        verbs,cues,postposed = attribution_rules(registry,version,custom)
        match = dialogue_attribution(source,cues,postposed,actor=actor,
                                     allow_colon=version>=16,context_version=version)
        if not match and (embedded:=embedded_attribution(source,cues,postposed,actor=actor,
                                                         allow_colon=version>=16,context_version=version)):
            match,offset,_ = embedded
        else:
            offset = 0
        if match and attribution_phrase(match) in verbs:
            owned_span = tuple(offset+i for i in match.span('quote'))
    return inline_event_site(source,rules,actor,owned_span=owned_span) is not None


def inline_vocal_event(source, speech, block, recipes, actor, context_version, *, owned_span=None):
    if not (located := inline_event_site(source,recipes,actor,owned_span=owned_span)):
        return None
    kind, quote, mark = located
    spoken_quotes = list(re.finditer(QUOTATION,quotation_text(speech,context_version)))
    if len(spoken_quotes)!=1:
        return None
    spoken, = spoken_quotes
    label = re.escape(spoken_vocal_marker(mark[0]))
    body = spoken[0][1:-1]
    locations = [hit for hit in re.finditer(r'(?<=\s)'+label+r'(?=\s)',body)
                 if sentence_seam(body,hit.start(),hit.end())]
    if len(locations)!=1:
        return None
    site, = locations
    start,end = (spoken.start()+1+index for index in site.span())
    prefix,suffix = speech[:start].rstrip(),speech[end:].lstrip()
    recipe = recipes[kind]
    from .speech_compare import comparison_tokens
    return {
        'recipe':kind, 'speech':prefix+' '+suffix, 'synthesis':prefix+' '+recipe['marker']+' '+suffix,
        'instruction':recipe['inline_instruction'], 'cfg_scale':1,
        'source':{'block':block,'span':list(mark.span())},
        'evidence':[{'kind':'authored_sentence_boundary','span':list(mark.span()),
                     'quotation_spans':[list(quote.span())]}],
        'event':{'kind':kind,'position':'within','word_offset':len(comparison_tokens(prefix)),
                 **{field:recipe[field] for field in ('asr_labels','asr_annotations')}},
    }


def compose_quotation_cues(candidate, source, neutral, blocks, actor, owned_span, inline_rules, registry, context_version):
    """Retain independent authored event and stress evidence within one owned quotation."""
    event = candidate.get('event')
    delivery = candidate['recipe'] if owned_span and not event else None
    inline = (inline_vocal_event(source,neutral,blocks[0].id,inline_rules,actor,context_version,owned_span=owned_span)
              if delivery and inline_rules else None)
    focus = None
    if len(blocks)==1:
        focus = typographic_focus(source,neutral,blocks[0].locator.get('inline_semantics',()),
                                 registry['context']['emphasis'],actor,owned_span=owned_span)
    if not (inline or focus):
        return
    templates = registry['composition']
    instruction = candidate['instruction']
    components = dict(candidate.get('composition',{}))
    if delivery:
        components['delivery'] = delivery
        if inline and 'affect' not in components and delivery in templates['delivery_with_event']:
            instruction = templates['delivery_with_event'][delivery]
    if inline:
        candidate.update(speech=inline['speech'],synthesis=inline['synthesis'],event=inline['event'])
        candidate['evidence'][0].setdefault('quotation_spans',[list(owned_span)])
        candidate['evidence'].extend(inline['evidence'])
        instruction += ' '+templates['vocal_event'][inline['recipe']]
    if event or inline:
        components['vocal_event'] = candidate['event']['kind']
    if focus:
        phrase,evidence = focus
        components['focus'] = phrase
        candidate['evidence'].append(evidence)
        instruction += ' '+templates['focus'].format(phrase=phrase)
    if inline and 'affect' not in components and delivery in templates['delivery_with_event']:
        returned = ('embedded_return_to_narrator' if candidate['evidence'][0].get('turn_span')
                    else 'return_to_narrator')
        instruction += ' '+templates[returned]
    candidate.update(instruction=instruction,composition=components)


def apply_attributed_affect(candidate, attribution, phrase, affect, templates, surrounding, following_quote):
    """Set an affect on the same quotation as its spoken delivery."""
    offset = surrounding[0] if surrounding else 0
    quotes = [attribution.span('quote')]
    if following_quote:
        quotes.append(following_quote)
    elif attribution.groupdict().get('continuation'):
        quotes.append(attribution.span('continuation'))
    form = 'continued' if len(quotes)>1 else 'question' if attribution['quote'][-2]=='?' else 'single'
    act = templates['affect'][affect]
    instruction = ' '.join((templates['delivery'][candidate['recipe']][form], templates['attribution'],
                            act.get(form,act['single'])))
    if surrounding:
        instruction += ' '+templates['surrounding']
    modifier = phrase.rsplit(' ',1)[-1]
    end = offset+attribution.end('modifier' if attribution.groupdict().get('modifier') else 'verb')
    quote_spans = [[offset+i for i in span] for span in quotes]
    candidate['evidence'][0].setdefault('quotation_spans',quote_spans)
    candidate['evidence'].append({'kind':'attributed_affect','span':[end-len(modifier),end],
                                  'affect':affect,'quotation_spans':quote_spans})
    candidate.update(instruction=instruction,composition={'delivery':candidate['recipe'],'affect':affect})


def contextual_direction(source, config, previous=None, preference='corroborated', context_version=CONTEXT_VERSION):
    """Keep prose evidence local to a speaker or a coherent scene."""
    if result := dialogue_context(source,config,previous,preference,context_version):
        return result
    if len(source)>480 or any(quote in source for quote in ('“','”','"','‘')) or re.search(QUOTATION,source):
        return None
    scene=config['quiet_scene']
    if context_matches(source,scene['conflict']):return None
    setting=context_matches(source,scene['setting'])
    if not setting:return None
    matches=((match,signal) for signal,phrases in scene['restraint'].items()
             if (match:=context_matches(source,phrases)))
    restraint, signal=min(matches,key=lambda pair:abs(setting.start()-pair[0].start()),default=(None,None))
    if not restraint or abs(setting.start()-restraint.start())>150:return None
    unsettled=r"\b(?:not|never|denied|doubted|didn['’]t|couldn['’]t|wasn['’]t)\b"
    for match in (setting,restraint):
        lead=re.split(r'[.!?;]',source[:match.start()])[-1]
        if re.search(unsettled,lead,re.I):return None
    return 'quiet_scene',[{'kind':'scene_setting','span':list(setting.span())},
                          {'kind':'deliberate_restraint','signal':signal,'span':list(restraint.span())}],False


def eligible(block, decision, disabled, role='body', modes=('listen',), *, all_formats=True):
    locator = block.locator
    return (block.id not in disabled and block.role == role and not block.issue
            and decision['action'] == 'speak' and decision['section_policy']['mode'] in modes
            and not locator.get('checklist') and not block.links
            and not any(span['kind'] in PRIVATE_KINDS for span in locator.get('inline_semantics', ()))
            and (all_formats or legacy_prose_scope(block)))


def performance_preferences(document_policy, known):
    config = document_policy.get('performance', {})
    if not isinstance(config, dict) or set(config)-{'disabled_blocks','contextual_dialogue','tuning','attribution_cues','passage_instructions'}:
        raise Error('performance_policy', 'Performance policy supports disabled_blocks, contextual_dialogue, tuning, attribution_cues and passage_instructions.')
    blocks = config.get('disabled_blocks', [])
    if not isinstance(blocks,list) or any(not isinstance(block,str) or block not in known for block in blocks):
        raise Error('performance_policy', 'Performance policy names unknown source blocks.')
    dialogue = config.get('contextual_dialogue','corroborated')
    if dialogue not in ('off','explicit','corroborated'):
        raise Error('performance_policy', 'Choose off, explicit, or corroborated for contextual_dialogue.')
    return set(blocks), dialogue


def attribution_preferences(config, recipes):
    cues = config.get('attribution_cues', {})
    if not isinstance(cues,dict):
        raise Error('performance_policy','attribution_cues must map exact reporting phrases to dialogue recipes.')
    for phrase, recipe in cues.items():
        if (not isinstance(phrase,str) or not re.fullmatch(r"[a-z]+(?:[ ,'’\-]+[a-z]+)*",phrase)
                or phrase!=normalize_space(phrase) or not isinstance(recipe,str) or recipe not in recipes
                or recipe=='quiet_scene'):
            raise Error('performance_policy','Use a lowercase reporting phrase and a dialogue recipe in attribution_cues.')
    return cues


def passage_instructions(config, blocks):
    entries = config.get('passage_instructions', {})
    if not isinstance(entries, dict):
        raise Error('performance_policy', 'Passage instructions must map source block IDs to text and instruction.')
    required = {'text','instruction'}
    checked = {}
    for block_id, entry in entries.items():
        if (block_id not in blocks or not isinstance(entry, dict)
                or entry.keys()-required-{'tuning','frame_guidance','narration_instruction'}
                or not all(isinstance(entry.get(key), str) and entry[key].strip() for key in required)):
            raise Error('performance_policy', f'Passage instruction for {block_id} needs a known source block, exact text, and a nonempty instruction.')
        checked[block_id] = dict(entry)
        if 'tuning' in entry:
            checked[block_id]['tuning'] = validated_tuning(entry['tuning'])
        if 'frame_guidance' in entry:
            if 'cfg_scale' in entry.get('tuning', {}):
                raise Error('frame_guidance', 'Set base_scale on the frame schedule when supplying a schedule.')
            checked[block_id]['frame_guidance'] = validated_frame_guidance(entry['frame_guidance'])
        if 'narration_instruction' in entry:
            validate_narration_instruction(entry['narration_instruction'],checked[block_id].get('frame_guidance'))
    return checked


def apply_passage_instructions(units, entries, blocks, document_digest, registry_sha, revision):
    from .backend import validate_request

    targets = {}
    for unit in units:
        speech = unit.get('performance_candidate', {}).get('speech', unit['text'])
        for block_id in unit['sources']:
            if block_id in entries and entries[block_id]['text'] == speech:
                targets.setdefault(block_id, []).append(unit)
    added = 0
    redirects = {}
    seen = set()
    for block_id, entry in entries.items():
        matches = targets.get(block_id, ())
        if len(matches) != 1:
            raise Error('performance_instruction_scope',
                        f'Passage instruction for {block_id} matched {len(matches)} rendered passages.',
                        'Use hamr plan or hamr show JOB --content to copy the exact spoken text for one passage.')
        unit, = matches
        if unit['id'] in seen:
            raise Error('performance_instruction_scope', f'More than one passage instruction matches {unit["id"]}.')
        seen.add(unit['id'])
        source = {'block': block_id, 'span': [0, len(blocks[block_id].source)]}
        prior = unit.get('performance_candidate')
        candidate = dict(prior) if prior else {
            'recipe': 'passage_instruction', 'speech': unit['text'], 'synthesis': unit['text'],
            'source': source, 'neutral': unit['text'], 'cfg_scale': 1, 'contextual': True,
            'evidence': [], 'registry_sha256': registry_sha, 'revision': revision,
        }
        candidate.pop('anchor', None)
        candidate.pop('voice_suffixes', None)
        candidate['passage_instruction'] = entry['instruction']
        candidate['instruction'] = entry['instruction']
        if tuning:=entry.get('tuning'):
            candidate['tuning'] = {**candidate.get('tuning',{}),**tuning}
            candidate['cfg_scale'] = tuning.get('cfg_scale',candidate['cfg_scale'])
        if schedule:=entry.get('frame_guidance'):
            candidate['frame_guidance'] = schedule
            candidate['cfg_scale'] = schedule['base_scale']
        if narration:=entry.get('narration_instruction'):
            candidate['narration_instruction'] = narration
        candidate['evidence'] = [*candidate['evidence'], {'kind': 'source_policy_instruction', **source}]
        validate_request(candidate['synthesis'], instruction=candidate['instruction'])
        candidate['id'] = candidate_id(document_digest, candidate)
        unit['performance_candidate'] = candidate
        if prior:
            redirects[prior['id']] = candidate['id']
        else:
            added += 1
    for unit in units:
        candidate = unit.get('performance_candidate')
        if candidate and candidate.get('anchor') in redirects:
            candidate['anchor'] = redirects[candidate['anchor']]
    return added


def add_gentle_entries(units, by_id, decisions, occurrences, disabled, digest, registry, registry_sha, *, all_formats):
    """Guide the first ordinary entrance at two auditioned kinds of quiet join."""
    count = 0
    quiet = {'sigh','whisper','tearful','sobbing'}
    for index,unit in enumerate(units[1:],1):
        if unit.get('performance_candidate') or len(unit['sources'])!=1:
            continue
        previous=units[index-1]
        following=units[index+1] if index+1<len(units) else {}
        current_chapter=(unit['chapter'],unit.get('chapter_id'))
        prior_chapter=(previous['chapter'],previous.get('chapter_id'))
        prior_cue=previous.get('performance_candidate',{})
        next_cue=following.get('performance_candidate',{})
        if current_chapter==prior_chapter and prior_cue.get('recipe')=='whisper':
            role,anchor='body',prior_cue
        elif (current_chapter!=prior_chapter and previous['pause_samples']>=RATE
              and (following.get('chapter'),following.get('chapter_id'))==current_chapter
              and next_cue.get('recipe') in quiet):
            role,anchor='heading',next_cue
        else:
            continue
        owner=unit['sources'][0]
        if (occurrences[owner]!=1 or not eligible(by_id[owner],decisions[owner],disabled,role,all_formats=all_formats)
                or role=='body' and re.search(QUOTATION,unit['text'])
                or unit['text']!=normalize_space(decisions[owner]['spoken'])):
            continue
        text=unit['text']
        candidate={'recipe':'gentle_entry','speech':text,'synthesis':text,'neutral':text,'cfg_scale':1,
                   'instruction':registry['gentle_entry']['instruction'],
                   'source':{'block':owner,'span':[0,len(by_id[owner].source)]},'anchor':anchor['id'],
                   'revision':registry['schema_version'],'registry_sha256':registry_sha}
        candidate['id']=candidate_id(digest,candidate)
        unit['performance_candidate']=candidate
        count+=1
    return count


def add_structural_directions(units, by_id, decisions, occurrences, disabled, digest, registry, registry_sha, *, all_formats):
    """Use imported roles to guide complete headings and technical readings."""
    count = 0
    for unit in units:
        if unit.get('performance_candidate') or len(unit['sources']) != 1:
            continue
        owner, = unit['sources']
        block, decision = by_id[owner], decisions[owner]
        if occurrences[owner] != 1 or unit['text'] != normalize_space(decision['spoken']):
            continue
        has_equation = block.role=='body' and any(
            span['kind'] in MATH_SPANS for span in block.locator.get('inline_semantics',()))
        if block.role == 'heading':
            recipe, modes = 'heading', ('listen','study','procedure')
        elif block.role in ('table','equation') or has_equation:
            recipe, modes = 'technical', ('study','procedure')
        else:
            continue
        if not eligible(block,decision,disabled,block.role,modes,all_formats=all_formats):
            continue
        text = unit['text']
        candidate = {'recipe':recipe,'speech':text,'synthesis':text,'neutral':text,'cfg_scale':1,
                     'instruction':registry['structural'][recipe],'source':{'block':owner,'span':[0,len(block.source)]},
                     'evidence':[{'kind':'document_role','role':block.role,'mode':decision['section_policy']['mode']}],
                     'revision':registry['schema_version'],'registry_sha256':registry_sha}
        candidate['id'] = candidate_id(digest,candidate)
        unit['performance_candidate'] = candidate
        count += 1
    return count


def annotate(plan, document, *, automatic_prose=True, context_version=None, passages=None):
    """Recognize cues only when the compiled request has one demonstrable owner."""
    mode = plan['policy'].get('performance', 'auto')
    if mode not in MODES:
        raise Error('performance_policy', 'Choose auto, off, or experimental performance.')
    document_policy=plan['policy'].get('document', {})
    disabled, preference = performance_preferences(document_policy, {b.id for b in document.blocks})
    registry = loadjson(RULES_FILE)
    version = context_version or (CONTEXT_VERSION if automatic_prose else 1)
    passages = passages or SourcePassages(document,unified=version>=35)
    full_delivery = version >= 3
    recipes = registry['directions']
    custom = attribution_preferences(document_policy.get('performance',{}),recipes)
    tuning=tuning_preferences(document_policy.get('performance',{}),
                              mode=='experimental' or (mode=='auto' and full_delivery),registry)
    by_id = {b.id:b for b in document.blocks}
    instructions = passage_instructions(document_policy.get('performance', {}), by_id)
    if mode == 'off':
        plan['performance'] = {'mode':mode, 'bound':True, 'active':0, 'candidates':0}
        if version >= 11 and (passages.unified or passages.legacy_paginated_prose):
            plan['performance']['context_version'] = version
        if document_policy.get('performance',{}).get('tuning'):
            plan['performance']['tuning_state']='inactive'
        if instructions:
            plan['performance']['instruction_state']='inactive'
        return
    verbs,cue_pattern,postposed = attribution_rules(registry,version,custom)
    affects = attributed_modifiers(registry,version)
    actor = attribution_actor(version)
    allow_colon = version>=16
    voice_rules = voice_description_rules(registry['context']['attached_voice'],version) if version>=5 else None
    inline_rules = inline_event_rules(registry,version)
    modern_context = version >= 2
    events = {label:kind for kind,item in registry['events'].items() for label in item['spellings']}
    decisions = {d['block']:d for d in plan['decisions']}
    source_order = {b.id:i for i,b in enumerate(document.blocks)}
    occurrences = Counter(bid for u in plan['utterances'] for bid in u['sources'])
    if version>=29 and mode=='auto' and preference!='off':
        from . import fragments
        fragments.annotate(plan['utterances'],by_id,decisions,source_order,occurrences,disabled,passages)
    by_source_candidate={}
    candidates = 0
    registry_sha = key(registry)
    for unit in plan['utterances']:
        sources = unit['sources']
        blocks = [by_id[bid] for bid in sources]
        joined_passage = full_delivery and passages.joined_prose(blocks)
        if not sources or (len(sources)>2 and not joined_passage) or any(occurrences[bid]!=1 or not eligible(
                by_id[bid],decisions[bid],disabled,all_formats=full_delivery) for bid in sources):
            continue
        start_index = source_order[blocks[0].id]
        chapter = (blocks[0].chapter, blocks[0].locator.get('chapter_id'))
        if any(source_order[b.id]!=start_index+i or (b.chapter,b.locator.get('chapter_id'))!=chapter
               for i,b in enumerate(blocks[1:],1)):
            continue
        neutral = unit['text']
        if neutral != normalize_space(' '.join(decisions[b.id]['spoken'] for b in blocks)):
            continue
        source = passages.text(blocks[0],legacy_layout=full_delivery)
        if joined_passage:
            source = ' '.join(passages.text(b) for b in blocks)
        source = quotation_text(source,version)
        direction = None
        surrounding = None
        following_quote = None
        focus = None
        event = None
        inline_event = None
        event_attribution = None
        if len(blocks)==2 and not joined_passage:
            next_source=quotation_text(passages.text(blocks[1],legacy_layout=full_delivery),version)
            if STANDALONE.fullmatch(source) and (
                    event_attribution:=dialogue_attribution(next_source,cue_pattern,postposed,actor=actor,
                                                            context_version=version)):
                event = source, 'before', blocks[0], 0, ''
            elif STANDALONE.fullmatch(next_source) and (
                    event_attribution:=dialogue_attribution(source,cue_pattern,postposed,actor=actor,
                                                            context_version=version)):
                event = next_source, 'after', blocks[1], 0, ''
        elif (match:=dialogue_attribution(source,cue_pattern,postposed,actor=actor,allow_colon=allow_colon,
                                          context_version=version)) and (
                kind:=verbs.get(attribution_phrase(match))):
            direction = kind, match
        elif version >= 10 and (len(blocks) == 1 or unit.get('delivery_boundary') == 'tagged_pdf_quotation') and (
                turn := attributed_turn_continuation(source, cue_pattern, postposed, actor=actor,
                                                       context_version=version)) and (
                kind := verbs.get(attribution_phrase(turn[0]))):
            direction = kind, turn[0]
            following_quote = turn[1]
        elif inline_rules and (inline_event:=inline_vocal_event(source,neutral,sources[0],inline_rules,actor,version)):
            pass
        elif modern_context and (owned:=embedded_attribution(source,cue_pattern,postposed,actor=actor,
                                                            allow_interrupted=version>=12,allow_colon=allow_colon,
                                                            context_version=version)) and (
                kind:=verbs.get(attribution_phrase(owned[0]))):
            direction = kind, owned[0]
            surrounding = owned[1:]
        elif (focus:=focus_direction(source,registry['context']['emphasis'])) is not None:
            pass
        elif version>=9 and len(blocks)==1 and (focus:=typographic_focus(
                source,neutral,blocks[0].locator.get('inline_semantics',()),
                registry['context']['emphasis'],actor)):
            pass
        elif (match:=LEADING.fullmatch(source)) and (
                event_attribution:=dialogue_attribution(match['dialogue'],cue_pattern,postposed,actor=actor,
                                                        context_version=version)):
            event = match['cue'], 'before', blocks[0], match.start('cue'), ''
        elif (match:=TRAILING.fullmatch(source)) and (event_attribution:=dialogue_attribution(
                match['dialogue']+(match['stop'] or ''),cue_pattern,postposed,actor=actor,context_version=version)):
            event = match['cue'], 'after', blocks[0], match.start('cue'), ''
        elif (speaker:=SPEAKER.fullmatch(source)) and (match:=LEADING.fullmatch(speaker['line'])):
            index=source_order[blocks[0].id]
            neighbors=document.blocks[max(0,index-1):index]+document.blocks[index+1:index+2]
            if any(other.chapter==blocks[0].chapter and other.role=='body' and not other.issue
                   and (turn:=SPEAKER.fullmatch(other.source)) and turn['name']!=speaker['name'] for other in neighbors):
                event=match['cue'],'before',blocks[0],speaker.start('line')+match.start('cue'),speaker['name']+':'
        if inline_event:
            candidate = inline_event
        elif direction:
            kind, attribution = direction
            recipe = recipes[kind]
            modifier = attribution.groupdict().get('modifier')
            start,end = attribution.span('modifier' if modifier else 'verb')
            continuation = attribution.groupdict().get('continuation')
            described = not re.fullmatch(ATTRIBUTION,attribution['actor'])
            role_instruction = recipe.get('role_instruction') if described else None
            if described and not (continuation or following_quote) and attribution.start('quote')<attribution.start('actor'):
                role_instruction = recipe.get('role_after_instruction',role_instruction)
            instruction = role_instruction or recipe['instruction']
            if recipe.get('progression') and (continuation or following_quote
                                              or len(list(sentence_spans(attribution['quote'][1:-1]))) < 2):
                continue
            if continuation:
                instruction += ' Treat both quoted spans as the same speaker’s continuing turn.'
            if following_quote:
                instruction = recipe.get('continued_turn_instruction') or (
                    instruction + ' Use the same delivery for both quotations. '
                    'Read the words between them in the usual narrator voice.')
            if surrounding:
                offset,stop=surrounding
                start+=offset
                end+=offset
                if continuation:
                    instruction = recipe.get('continued_turn_instruction') or instruction
                    instruction += ' Read the narration around and between the quotes in the usual reference voice.'
                elif not role_instruction:
                    instruction += ' Read any narration around the quotation in the usual reference voice.'
            candidate = {'recipe':kind, 'speech':neutral, 'synthesis':neutral,
                         'instruction':instruction, 'cfg_scale':recipe.get('cfg_scale',1),
                         'source':{'block':sources[0], 'span':[start,end]},
                         'evidence':[{'kind':'direct_attribution','span':[start,end]}]}
            if continuation or following_quote:
                candidate['contextual']=True
                form = 'continued_quotation' if following_quote else 'interrupted_quotation'
                second_span = following_quote or attribution.span('continuation')
                candidate['evidence'][0].update(form=form,
                    quotation_spans=[list(attribution.span('quote')),list(second_span)])
                if attribution.groupdict().get('inverted'):
                    candidate['evidence'][0]['attribution_order']='verb_first'
            elif attribution.groupdict().get('inverted'):
                candidate['contextual']=True
                candidate['evidence'][0]['form']='verb_first'
            if modifier:
                offset=surrounding[0] if surrounding else 0
                candidate['evidence'][0]['reporting_span']=[offset+i for i in attribution.span('verb')]
            if attribution_phrase(attribution) in custom:
                candidate['evidence'][0]['policy_cue']=attribution_phrase(attribution)
            if surrounding:
                candidate['contextual']=True
                spans = [attribution.span('quote')]
                if continuation:
                    spans.append(attribution.span('continuation'))
                candidate['evidence'][0].update(
                    form='embedded_interrupted_quotation' if continuation else 'embedded_quotation',
                    turn_span=[offset,stop], quotation_spans=[[offset+i for i in span] for span in spans])
            if described:
                offset = surrounding[0] if surrounding else 0
                evidence = candidate['evidence'][0]
                evidence['speaker_span'] = [offset+i for i in attribution.span('actor')]
                evidence.setdefault('quotation_spans',[[offset+i for i in attribution.span('quote')]])
            if joined_passage:
                candidate['evidence'][0].setdefault('quotation_spans',[list(attribution.span('quote'))])
            phrase = attribution_phrase(attribution)
            if phrase not in custom and (affect:=affects.get(phrase)):
                apply_attributed_affect(candidate,attribution,phrase,affect,registry['compound_attribution'],
                                         surrounding,following_quote)
            if suffixes:=recipe.get('voice_suffixes'):
                candidate['voice_suffixes']=suffixes
        elif focus:
            phrase,evidence=focus
            emphasis = registry['context']['emphasis']
            template = emphasis['typographic_instruction'] if evidence['kind']=='typographic_emphasis' else emphasis['instruction']
            candidate={'recipe':'contrastive_emphasis','speech':neutral,'synthesis':neutral,'cfg_scale':1,
                       'instruction':template.format(phrase=phrase),
                       'source':{'block':sources[0],'span':evidence['span']},'evidence':[evidence],'contextual':True}
        elif event:
            raw, position, owner, offset, prefix = event
            kind = events.get(raw[1:-1].lower())
            if not kind:
                continue
            recipe = registry['events'][kind]
            starts_request = position=='before' and not prefix
            synthesis_recipe = registry['events'][recipe['leading_recipe']] if (
                starts_request and 'leading_recipe' in recipe) else recipe
            spoken_marker = spoken_vocal_marker(raw)
            if prefix and not neutral.startswith(prefix+' '):
                continue
            spoken_prefix = prefix+' ' if prefix else ''
            if position=='before' and neutral.startswith(spoken_prefix+spoken_marker+' '):
                rest = neutral.removeprefix(spoken_prefix+spoken_marker+' ')
                speech = spoken_prefix+rest
                synthesis = spoken_prefix+synthesis_recipe['marker']+' '+rest
            elif position=='after' and (rest:=neutral.rstrip('.!?')).endswith(' '+spoken_marker):
                speech = rest.removesuffix(' '+spoken_marker)
                if not speech.endswith(('.','!','?')):speech+=neutral[len(rest):]
                synthesis = speech+' '+recipe['marker']
            else:
                continue
            candidate = {'recipe':kind, 'speech':speech, 'synthesis':synthesis, 'cfg_scale':synthesis_recipe.get('cfg_scale',1),
                         'source':{'block':owner.id, 'span':[offset,offset+len(raw)]},
                         'event':{'kind':kind, 'position':position, 'word_offset':len(spoken_prefix.split()),
                                  **{field:synthesis_recipe[field] for field in ('marker','asr_labels','asr_annotations','interjections',
                                                                                'interjections_before','interjections_after')
                                     if field in synthesis_recipe}}}
            instruction = synthesis_recipe.get('leading_instruction') if starts_request else None
            if instruction:
                candidate['instruction']=instruction
            if event_attribution and (event_attribution.groupdict().get('inverted')
                                      or event_attribution.groupdict().get('continuation')
                                      or event_attribution['verb'] in NEUTRAL_REPORTING[1:]):
                candidate['contextual']=True
        elif len(blocks)==1 or joined_passage:
            index=source_order[blocks[0].id]
            previous=document.blocks[index-1] if index else None
            if (previous and (previous.chapter!=blocks[0].chapter
                              or previous.locator.get('chapter_id')!=blocks[0].locator.get('chapter_id')
                              or occurrences[previous.id]!=1 or not eligible(previous,decisions[previous.id],disabled,
                                                                            all_formats=full_delivery))):
                previous=None
            carry = (continued_direction(source,previous,cue_pattern,registry['context']['continuation'],
                                         by_source_candidate.get(previous.id),postposed) if previous and preference!='off' else None)
            if carry:
                anchor=by_source_candidate[previous.id]
                kind,evidence,has_lead=anchor['recipe'],[carry],False
            elif voice_rules and (context:=described_attribution(source,voice_rules)):
                kind,evidence,has_lead=context
            elif (context:=contextual_direction(source,registry['context'],previous,preference,version)):
                kind,evidence,has_lead=context
            elif modern_context and (context:=trailing_voice_description(source,registry['context'],preference,version)):
                kind,evidence,has_lead=context
            else:
                if version>=20 and mode=='auto' and preference!='off' and 'whispered' in source.lower():
                    from .quote_timing import scope, source_spellings
                    if target:=scope(source,neutral,version):
                        if version>=23 and (spellings:=source_spellings(
                                (decisions[b.id] for b in blocks), include_names=version>=33)):
                            target['source_spellings'] = spellings
                        unit['quote_timing'] = target
                continue
            recipe=recipes[kind]
            instruction=recipe['instruction']
            attached = evidence[0]['kind']=='attached_voice_description'
            trailing = evidence[0]['kind']=='trailing_voice_description'
            opening_instruction = (registry['context']['attached_voice'][kind].get('opening_instruction')
                                   if attached and evidence[0]['position']=='before' else None)
            if opening_instruction:
                instruction = opening_instruction
            elif attached and evidence[0]['position']=='between':
                instruction = recipe['interrupted_turn_instruction']
            elif has_lead:
                instruction='Read the opening narration in the usual reference voice. '+instruction
            if trailing:
                if evidence[0]['signal']=='softened_delivery':
                    quoted = source[slice(*evidence[0]['quotation_spans'][0])]
                    target = 'question' if quoted[1:-1].rstrip().endswith('?') else 'words'
                    instruction = recipe['softened_instruction'].format(target=target)
                else:
                    instruction+=' Read the description after the quotation in the usual reference voice.'
            span = carry['span'] if carry else [0,len(source)]
            if attached:
                span = evidence[0]['span']
            candidate={'recipe':kind,'speech':neutral,'synthesis':neutral,'instruction':instruction,
                       'cfg_scale':recipe.get('cfg_scale',1),'source':{'block':sources[0],'span':span},
                       'evidence':evidence,'contextual':True}
            if carry:
                candidate['anchor']=anchor['id']
            if suffixes:=recipe.get('voice_suffixes'):
                candidate['voice_suffixes']=suffixes
        else:
            continue
        if version>=14:
            owned_span = None
            if direction and not (continuation or following_quote or recipe.get('progression')):
                offset = surrounding[0] if surrounding else 0
                owned_span = tuple(offset+i for i in attribution.span('quote'))
            if owned_span or inline_event:
                compose_quotation_cues(candidate,source,neutral,blocks,actor,owned_span,inline_rules,registry,version)
        if joined_passage:
            locate_passage_candidate(candidate,blocks)
        narrated_context = surrounding or candidate.get('evidence',[{}])[0].get('kind') in (
            'trailing_voice_description','attached_voice_description')
        if ((direction or candidate.get('contextual')) and not focus and not candidate.get('anchor')
                and not narrated_context and not candidate.get('composition')):
            if marker:=recipe.get('quote_marker'):
                quotes=list(re.finditer(QUOTATION,quotation_text(candidate['speech'],version)))
                if len(quotes)==1:
                    pos=quotes[0].start()+1
                    candidate['synthesis']=candidate['speech'][:pos]+marker+' '+candidate['speech'][pos:]
                    candidate['control_labels']=[marker[1:-1]]
                elif not direction or not (continuation or following_quote):continue
        from .backend import validate_request
        try:
            validate_request(candidate['synthesis'],instruction=candidate.get('instruction'))
        except Error as exc:
            if exc.code not in ('speech_input_limit','unspeakable_text'):
                raise
            continue
        if (version>=34 and mode=='auto' and unit.get('delivery_boundary')=='pdf_voice_description'
                and candidate.get('evidence',[{}])[0].get('signal')=='softened_delivery'):
            from .quote_timing import source_spellings
            # The compiler can expand words before the question. Match the
            # owner again to obtain offsets in the text sent to Breeze.
            compiled = trailing_voice_description(quotation_text(neutral,version),registry['context'],preference,version)
            if compiled and compiled[1][0]['signal']=='softened_delivery':
                start,stop = compiled[1][0]['quotation_spans'][0]
                scoped = {'version':1,'direction':'softened','quotation':[start,stop]}
                if spellings:=source_spellings((decisions[b.id] for b in blocks),include_names=True):
                    scoped['source_spellings'] = spellings
                unit['quote_timing'] = scoped
        candidate.update(neutral=neutral, registry_sha256=registry_sha, revision=registry['schema_version'])
        # This receipt identifies a source occurrence, independent of utterance numbering.
        candidate['id'] = candidate_id(document.source_sha256,candidate)
        unit['performance_candidate'] = candidate
        if len(sources)==1:by_source_candidate[sources[0]]=candidate
        candidates += 1
    expressive_default = mode == 'auto' and automatic_prose
    if mode=='experimental' or expressive_default:
        sources = (plan['utterances'],by_id,decisions,occurrences,disabled,document.source_sha256,registry,registry_sha)
        if candidates:
            candidates+=add_gentle_entries(*sources,all_formats=full_delivery)
        if mode=='experimental' or full_delivery:
            candidates+=add_structural_directions(*sources,all_formats=full_delivery)
        if tuning:
            tunes = [u['performance_candidate'] for u in plan['utterances'] if 'performance_candidate' in u]
            redirects={}
            for candidate in tunes:
                if settings:=tuning.get(candidate['recipe']):
                    effective={k:v for k,v in settings.items() if k!='cfg_scale' or v!=candidate['cfg_scale']}
                    if not effective:continue
                    prior=candidate['id']
                    candidate['tuning']=effective
                    candidate['cfg_scale']=effective.get('cfg_scale',candidate['cfg_scale'])
                    candidate['id']=candidate_id(document.source_sha256,candidate)
                    redirects[prior]=candidate['id']
            for candidate in tunes:
                if candidate.get('anchor') in redirects:candidate['anchor']=redirects[candidate['anchor']]
    if instructions:
        candidates += apply_passage_instructions(plan['utterances'], instructions, by_id,
                                                document.source_sha256, registry_sha, registry['schema_version'])
    if full_delivery or candidates or 'performance' in plan['policy']:
        plan['performance'] = {'mode':mode, 'bound':not candidates, 'candidates':candidates,
                               'active':0, 'registry_sha256':registry_sha}
        if expressive_default:
            plan['performance']['automatic_scope'] = 'prose'
        if modern_context:
            plan['performance']['context_version'] = version
        if document_policy.get('performance',{}).get('tuning'):
            plan['performance']['tuning_state']='active' if tuning else 'inactive'
        if instructions:
            plan['performance']['instruction_state']='planned'


def candidate_id(source_digest, candidate):
    identity=[source_digest,candidate['source'],candidate['recipe'],candidate['neutral']]
    if tuning:=candidate.get('tuning'):identity.append(tuning)
    if 'passage_instruction' in candidate:
        identity.append(('passage_instruction', candidate['passage_instruction']))
    if schedule:=candidate.get('frame_guidance'):
        identity.append(('frame_guidance', schedule))
    if narration:=candidate.get('narration_instruction'):
        identity.append(('narration_instruction',narration))
    if candidate.get('composition'):
        identity.append(('composed_request',candidate['synthesis'],candidate['instruction']))
    return key(identity)


def rebase(previous, mapping, fallbacks, source_digest):
    rebased = {}
    for unit in previous.values():
        candidate = unit['performance_candidate']
        source = candidate['source']
        new_candidate = {**candidate,'source':{**source,'block':mapping[source['block']]}}
        new_id = candidate_id(source_digest,new_candidate)
        new_candidate['id'] = new_id
        new_unit = {**unit,'performance_candidate':new_candidate}
        if 'performance' in unit:
            new_unit['performance'] = {**unit['performance'],'source':new_candidate['source'],'id':new_id}
        rebased[new_id] = new_unit
    for entry in fallbacks:
        source = entry['source']
        entry['source'] = {**source,'block':mapping[source['block']]}
        entry['candidate'] = candidate_id(source_digest,entry)
    return rebased


@cache
def pinned_backends():
    catalog = loadjson(CATALOG_FILE)
    runtime = catalog['runtimes']['hoppou']
    profiles = {entry['id']: catalog['packages'][entry['packages'][0]]['files'][0]['sha256']
                for entry in catalog['profiles'] if entry['runtime'] == 'hoppou'}
    archives = {name: runtime[name]['sha256'] for name in ('linux_x86_64', 'windows_x86_64')}
    return runtime['revision'], profiles, archives


@cache
def compatible_runtime_archives():
    runtime = loadjson(CATALOG_FILE)['runtimes']['hoppou']
    return {(platform, profile, entry['revision'], entry['sha256'])
            for platform, entries in runtime.get('profile_archives', {}).items()
            for entry in entries for profile in entry['profiles']}


def supports_prose_direction(backend):
    runtime, models, archives = pinned_backends()
    platform = backend.get('runtime_platform') or {'linux': 'linux_x86_64', 'win32': 'windows_x86_64'}.get(sys.platform)
    profile = backend.get('profile')
    model = models.get(profile)
    if model is None or backend.get('model_sha256') != model:
        return False
    # Older verified setups predate the archive field. The pinned runtime and
    # model still identify the instruction-only transport in those setups.
    revision, archive = backend.get('runtime_revision'), backend.get('runtime_archive_sha256')
    return ((platform in archives and revision == runtime and archive in (None, archives[platform]))
            or (platform,profile,revision,archive) in compatible_runtime_archives())


def bind(plan, backend, voice, *, fallbacks=(), previous=None):
    """Freeze planned requests once a job has a pinned backend and narrator."""
    summary = plan.get('performance')
    if not summary or summary['bound'] or not backend or not voice:
        return False
    registry = loadjson(RULES_FILE)
    previous = previous or {}
    fallback_ids = {entry['candidate'] for entry in fallbacks if not entry.get('variant')}
    instructions_only = {entry['candidate'] for entry in fallbacks if entry.get('variant')=='instruction_only'}
    # A queue created before backend setup still needs to bind its saved direction.
    compatible_registry = summary['registry_sha256'] in (key(registry), *registry.get('compatible_registry_sha256', ()))
    platform = backend.get('runtime_platform',sys.platform)
    archive_sha256 = backend.get('runtime_archive_sha256')
    if archive_sha256 is None and platform == 'linux':
        archive_sha256 = registry['legacy_linux_archives'].get(backend.get('binary_sha256'))
    backend_target = {'profile':backend.get('profile'),'runtime':backend.get('runtime_revision'),
                      'archive_sha256':archive_sha256,'model_sha256':backend.get('model_sha256'),
                      'platform':platform}
    reference = key({'reference_sha256':voice['reference_sha256'],'transcript':voice['transcript']}) if (
        voice.get('reference_sha256') and voice.get('transcript')) else None
    pinned_direction = compatible_registry and reference is not None and supports_prose_direction(backend)
    prose = pinned_direction and summary.get('automatic_scope') == 'prose'
    automatic = registry['automatic_directions']
    full_delivery = summary.get('context_version',1)>=3
    settings = backend.get('settings',{})
    execution = {'device':settings.get('device'),'parallel':settings.get('parallel',1)}
    candidates=[(unit,unit['performance_candidate']) for unit in plan['utterances'] if 'performance_candidate' in unit]
    candidates.sort(key=lambda item:'anchor' in item[1])
    active_ids=set()
    for unit,candidate in candidates:
        cid = candidate['id']
        schedule = candidate.get('frame_guidance')
        scheduled = schedule is not None
        if scheduled and not (reference and supports_frame_guidance(backend,multiple='windows' in schedule)):
            raise Error('frame_guidance_unavailable', 'This passage needs a voice reference and a backend with frame-local guidance.')
        if candidate.get('narration_instruction') and not supports_narration_conditioning(backend):
            raise Error('narration_conditioning_unavailable','This passage needs a backend with separate narration conditioning.')
        if cid in fallback_ids:
            unit['performance_state'] = 'neutral_fallback'
            continue
        if (anchor:=candidate.get('anchor')) and anchor not in active_ids:
            unit['performance_state']='anchor_neutral'
            continue
        if cid in previous:
            old = previous[cid]
            active = old.get('performance')
            unit['performance_state'] = old.get('performance_state','unqualified')
            if active:
                unit['text'] = active['speech']
                unit['performance'] = {**active,**{field:candidate[field] for field in ('anchor','evidence') if field in candidate}}
                if cid in instructions_only:
                    use_instruction_only(unit)
                active_ids.add(cid)
            continue
        target={**backend_target,'recipe':candidate['recipe']}
        if candidate.get('contextual'):target['contextual']=True
        if support:=next((evidence.get('signal') for evidence in candidate.get('evidence',())
                          if evidence['kind']=='deliberate_restraint'),None):
            target['context_support']=support
        if event:=candidate.get('event'):
            position = event['position']
            target['placement']='after_speaker' if position=='before' and event['word_offset'] else position
        qualified = compatible_registry and all(backend_target.values()) and (
            target in registry['qualified'] or
            reference is not None and {**target,'reference_identity':reference,'execution':execution} in registry['qualified'])
        recipe = candidate['recipe']
        direct_attribution = any(e['kind']=='direct_attribution' for e in candidate.get('evidence', ()))
        legacy_direction = candidate.get('instruction') and (recipe in automatic['prose'] or
                            (recipe in automatic['direct_attribution'] and direct_attribution))
        policy_direction = any('policy_cue' in e for e in candidate.get('evidence', ()))
        directed = (prose and (full_delivery or legacy_direction or policy_direction)
                    or pinned_direction and 'passage_instruction' in candidate or scheduled)
        enabled = backend.get('runtime_revision') and (summary['mode']=='experimental' or qualified or directed)
        if enabled:
            unit['text'] = candidate['speech']
            if execution['parallel']!=1 and candidate['cfg_scale']!=1 and not scheduled:
                if candidate.get('tuning'):
                    raise Error('speech_guidance','Stronger custom guidance requires serial generation.')
                candidate={**candidate,'cfg_scale':1}
            suffix=candidate.get('voice_suffixes',{}).get(voice.get('character'))
            if suffix and (summary['mode']=='experimental' or directed):
                candidate={**candidate,'instruction':candidate['instruction']+' '+suffix}
            unit['performance'] = candidate
            active_ids.add(cid)
            if (cid in instructions_only or (directed and candidate.get('control_labels'))
                    or (directed and full_delivery and event and event['position']!='within'
                        and not qualified and candidate.get('instruction'))):
                use_instruction_only(unit)
            elif summary['mode']=='experimental':
                unit['performance_state']='experimental'
            else:
                unit['performance_state']='qualified' if qualified else 'automatic'
        else:
            unit['performance_state'] = 'unqualified'
    summary['active'] = sum('performance' in unit for unit in plan['utterances'])
    if summary.get('instruction_state') == 'planned':
        count = sum('passage_instruction' in unit.get('performance', {}) for unit in plan['utterances'])
        requested = sum('passage_instruction' in candidate for _, candidate in candidates)
        summary['instruction_state'] = 'active' if count == requested else 'partially_active' if count else 'inactive'
    summary['bound'] = True
    plan['plan_sha256'] = key({k:v for k,v in plan.items() if k!='plan_sha256'})
    return True


def request(unit):
    performance = unit.get('performance')
    text = unit['text']
    options = {'seed':unit.get('seed',42)}
    if performance:
        text = performance['synthesis']
        options.update(instruction=performance.get('instruction'), cfg_scale=performance['cfg_scale'])
        if schedule:=performance.get('frame_guidance'):
            options['frame_guidance']=schedule
        if narration:=performance.get('narration_instruction'):
            options['narration_instruction']=narration
        if samples:={k:v for k,v in performance.get('tuning',{}).items() if k!='cfg_scale'}:
            options['sampling']=samples
    if unit.get('fragment_echo'):
        from .fragments import synthesis_text
        text = synthesis_text(unit,text)
    return text, options


def use_instruction_only(unit):
    active={**unit['performance'],'synthesis':unit['text']}
    active.pop('control_labels',None)
    unit['performance']=active
    unit['performance_state']='instruction_only'


def request_identity(unit):
    active = unit.get('performance')
    if active:
        identity={field:active.get(field) for field in ('recipe','synthesis','instruction','cfg_scale','revision')}
        if tuning:=active.get('tuning'):identity['tuning']=tuning
        if schedule:=active.get('frame_guidance'):identity['frame_guidance']=schedule
        if narration:=active.get('narration_instruction'):identity['narration_instruction']=narration
        return identity
    return None


def status(job):
    if details:=job['plan'].get('performance'):
        history=job.get('performance_fallbacks',())
        return {**details,'fallbacks':sum(not record.get('variant') for record in history),
                'instruction_only_retries':sum(record.get('variant')=='instruction_only' for record in history)}
    return None


def lexical_observation(unit, observed):
    if unit.get('fragment_echo'):
        from .fragments import lexical_observation as fragment_observation
        observed = fragment_observation(unit,observed)
    event = (unit.get('performance') or {}).get('event')
    if not event:
        return observed
    labels = '|'.join(map(re.escape,event['asr_annotations']))
    if event['position']=='within':
        from .speech_compare import comparison_tokens
        expected = comparison_tokens(unit['text'])
        index = event['word_offset']
        for annotation in re.finditer(r'\[(?:'+labels+r')\]',observed,re.I):
            before,after = observed[:annotation.start()].rstrip(),observed[annotation.end():].lstrip()
            if comparison_tokens(before)==expected[:index] and comparison_tokens(after)==expected[index:]:
                return before+' '+after
        return observed
    # Unprompted ASR can emit an explicit bracketed annotation for a sound.
    # Remove only this event's annotation at its planned request edge.
    if event['position']=='after':
        pattern = r'\s*\[(?:'+labels+r')\][.!?]?\s*$'
    elif event['word_offset']:
        pattern = r'^(?P<start>(?:\S+\s+){'+str(event['word_offset'])+r'})\[(?:'+labels+r')\]\s*'
        observed = re.sub(pattern,r'\g<start>',observed,count=1,flags=re.I)
        pattern=None
    else:
        pattern = r'^\s*\[(?:'+labels+r')\]\s*'
    if pattern:
        observed = re.sub(pattern,'',observed,count=1,flags=re.I)
    interjections=event.get('interjections_'+event['position'],())
    interjections=(*event.get('interjections',()),*interjections)
    if not interjections:
        return observed
    # Recognizers can write a performed chuckle as “heh” or “mm-hmm”. Accept an extra
    # interjection only at the authored event location, and only while every
    # expected spoken word and quantity still matches.
    from .speech_compare import comparison_tokens
    syllable=r'(?:'+'|'.join(map(re.escape,interjections))+r')[,.!?]*'
    expected=comparison_tokens(unit['text'])
    for count in range(1,7):
        repeat='{'+str(count)+'}'
        if event['position']=='after':
            match=re.fullmatch(r'(?P<speech>.*?)(?:\s+'+syllable+r')'+repeat+r'\s*',observed,re.I|re.S)
            candidate=match['speech'] if match else observed
        else:
            prefix=r'(?:\S+\s+){'+str(event['word_offset'])+'}'
            pattern=r'^(?P<prefix>'+prefix+r')(?:'+syllable+r'\s+)'+repeat
            candidate=re.sub(pattern,r'\g<prefix>',observed,count=1,flags=re.I)
        if candidate==observed:break
        if comparison_tokens(candidate)==expected:return candidate
    return observed


def label_spoken(unit, expected, observed):
    performance = unit.get('performance') or {}
    labels={tuple(label.split()) for label in (*performance.get('control_labels',()),
                                               *performance.get('event',{}).get('asr_labels',()))}
    if not labels:
        return False
    for action,_,_,start,end in SequenceMatcher(None,expected,observed,autojunk=False).get_opcodes():
        if action not in ('insert','replace'):continue
        if any(tuple(observed[i:i+len(label)])==label for label in labels
               for i in range(max(0,start-len(label)+1),end)):
            return True
    return False


def extra_event_words(unit, observed):
    """Locate extra lexical speech at the authored sound, with the line still intact."""
    if not (event := unit.get('performance',{}).get('event')):
        return ()
    from .speech_compare import comparison_tokens
    expected, words = map(comparison_tokens,(unit['text'],observed))
    extra = len(words)-len(expected)
    if extra <= 0:
        return ()
    index = len(expected) if event['position']=='after' else event['word_offset']
    if words[:index] == expected[:index] and words[index+extra:] == expected[index:]:
        return tuple(words[index:index+extra])
    return ()


def authored_sound_observation(unit, raw, comparison):
    """Identify uncertainty at an authored sound without calling it a defect."""
    event = unit.get('performance',{}).get('event')
    if not event:
        return None
    words = extra_event_words(unit, comparison)
    if not words:
        if original := extra_event_words(unit, raw):
            return {'status':'recognized_cue_variant','tokens':original}
        return None
    ambiguous = {'ah','ahem','ha','hah','heh','hm','hmm','huh','mm','mhm','oh','ooh','uh','um'}
    labels = {tuple(label.split()) for label in event['asr_labels']}
    if words in labels or len(words)<=3 and all(word in ambiguous for word in words):
        return {'status':'ambiguous_vocalization','tokens':words}
    return None
