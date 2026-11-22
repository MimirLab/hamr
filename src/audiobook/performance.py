"""Source-owned delivery cues and the corresponding local Breeze requests."""
from collections import Counter
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
import re
import sys

from .boundaries import sentence_spans
from .core import Error, RATE, key, loadjson
from .linguistic import spoken_vocal_marker

MODES = ('auto', 'off', 'experimental')
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
NEUTRAL_REPORTING = ('said','added','answered','replied','continued','asked','remarked','observed','returned','repeated')
POSSESSIVES = {'he':'his','she':'her','they':'their','i':'my','we':'our'}
PRIVATE_KINDS = {'literal', 'hidden', 'generated_label', 'object_label', 'note_reference'}
SAMPLING_LIMITS = {'temperature':(.1,2), 'top_k':(1,256), 'top_p':(.05,1), 'repetition_penalty':(.8,1.4)}


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


def tuning_preferences(config, mode, registry):
    overrides=config.get('tuning',{})
    if not isinstance(overrides,dict):
        raise Error('performance_sampling','Performance tuning must map recipe names to settings.')
    known=set(registry['directions'])|set(registry['events'])|set(registry['structural'])|{'contrastive_emphasis','gentle_entry'}
    result={}
    for recipe,fields in overrides.items():
        if recipe not in known or not isinstance(fields,dict) or not fields:
            raise Error('performance_sampling',f'Choose a known performance recipe with tuning settings: {recipe}.')
        cfg=fields.get('cfg_scale')
        if 'cfg_scale' in fields and (type(cfg) not in (int,float) or cfg not in (1,2,2.5,3)):
            raise Error('speech_guidance','Choose Breeze guidance of 1, 2, 2.5, or 3.')
        result[recipe]=validated_sampling({k:v for k,v in fields.items() if k!='cfg_scale'})
        if cfg is not None:result[recipe]['cfg_scale']=float(cfg)
    return result if mode=='experimental' else {}


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


def dialogue_attribution(source, cue_pattern):
    after = re.fullmatch(r'(?P<quote>'+QUOTATION+r')\s*,?\s*(?P<actor>'+ATTRIBUTION+r')'+
                         r'\s+(?P<verb>'+cue_pattern+r')\s*[.!?]', source)
    inverted = re.fullmatch(r'(?P<quote>'+QUOTATION+r')\s*,?\s*(?P<inverted>'
                            r'(?P<verb>'+cue_pattern+r')\s+(?P<actor>'+ATTRIBUTION+r'))\s*[.!?]', source)
    before = re.fullmatch(r'(?P<actor>'+ATTRIBUTION+r')\s+(?P<verb>'+cue_pattern+
                          r'),\s*(?P<quote>'+QUOTATION+r')\s*[.!?]?', source)
    if simple:=after or inverted or before:return simple
    opening=r'(?P<quote>'+QUOTATION+r')\s+'
    ending=r',\s*(?P<continuation>'+QUOTATION+r')\s*[.!?]?'
    interrupted = re.fullmatch(opening+r'(?P<actor>'+ATTRIBUTION+r')\s+(?P<verb>'+cue_pattern+r')'+ending,source)
    if not interrupted:
        interrupted=re.fullmatch(opening+r'(?P<inverted>(?P<verb>'+cue_pattern+r')\s+(?P<actor>'+ATTRIBUTION+r'))'+ending,source)
    if (interrupted and interrupted['quote'][-2] in ',.!?'
            and re.match(r'[a-z]',interrupted['continuation'][1:].lstrip())):
        return interrupted
    return None


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


def continued_direction(source, previous, attribution_cues, config, anchor):
    """Carry an established delivery only into its explicitly continuing speaker."""
    if not anchor or anchor['recipe'] not in config['recipes'] or anchor.get('event') or anchor.get('anchor'):
        return None
    current=dialogue_attribution(source,'|'.join(map(re.escape,config['attributions'])))
    earlier=dialogue_attribution(previous.source,attribution_cues)
    if not current or not earlier or current['actor']!=earlier['actor'] or current['actor'].casefold() in POSSESSIVES:
        return None
    return {'kind':'continued_delivery','span':list(current.span('verb')),
            'previous_block':previous.id,'previous_attribution_span':list(earlier.span('verb'))}


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


def speaker_evidence(actor, setup, patterns, *, allow_local_pronoun=False):
    pronoun = POSSESSIVES.get(actor.casefold())
    possessives = (pronoun,) if pronoun else ('his','her','their')
    named_possessive = () if pronoun else (actor + "'s", actor + "’s")
    owners = (*named_possessive,*possessives) if pronoun or allow_local_pronoun else named_possessive
    normalized = normalize_space(setup).casefold()
    for entry in patterns:
        frames = entry['frames']
        for frame in frames:
            for possessive in possessives:
                for owner in owners if '{owner}' in frame else ('',):
                    if normalized == frame.format(actor=actor,possessive=possessive,owner=owner).casefold():
                        return entry
    return None


def dialogue_context(source, config, previous, preference):
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
                                  allow_local_pronoun=bool(rows and rows[-1]))
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
    if any(entry['recipe']!=newest['recipe'] for entry,_ in matching):
        return None
    strong = any(entry['strong'] for entry,_ in matching)
    corroborated = len({entry['signal'] for entry,_ in matching}) > 1
    if not strong and (preference != 'corroborated' or not corroborated):
        return None
    return newest['recipe'], [item for _,item in matching], owner is None


def contextual_direction(source, config, previous=None, preference='corroborated'):
    """Keep prose evidence local to a speaker or a coherent scene."""
    if result := dialogue_context(source,config,previous,preference):
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


def eligible(block, decision, disabled, role='body', modes=('listen',)):
    locator = block.locator
    return (block.id not in disabled and block.role == role and not block.issue
            and decision['action'] == 'speak' and decision['section_policy']['mode'] in modes
            and not locator.get('checklist') and not block.links
            and not any(span['kind'] in PRIVATE_KINDS for span in locator.get('inline_semantics', ()))
            and locator.get('format') in ('epub', 'markdown', 'text'))


def performance_preferences(document_policy, known):
    config = document_policy.get('performance', {})
    if not isinstance(config, dict) or set(config)-{'disabled_blocks','contextual_dialogue','tuning'}:
        raise Error('performance_policy', 'Performance policy supports disabled_blocks, contextual_dialogue and tuning.')
    blocks = config.get('disabled_blocks', [])
    if not isinstance(blocks,list) or any(not isinstance(block,str) or block not in known for block in blocks):
        raise Error('performance_policy', 'Performance policy names unknown source blocks.')
    dialogue = config.get('contextual_dialogue','corroborated')
    if dialogue not in ('off','explicit','corroborated'):
        raise Error('performance_policy', 'Choose off, explicit, or corroborated for contextual_dialogue.')
    return set(blocks), dialogue


def add_gentle_entries(units, by_id, decisions, occurrences, disabled, digest, registry, registry_sha):
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
        if (occurrences[owner]!=1 or not eligible(by_id[owner],decisions[owner],disabled,role)
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


def add_structural_directions(units, by_id, decisions, occurrences, disabled, digest, registry, registry_sha):
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
            span['kind']=='mathml' for span in block.locator.get('inline_semantics',()))
        if block.role == 'heading':
            recipe, modes = 'heading', ('listen','study','procedure')
        elif block.role in ('table','equation') or has_equation:
            recipe, modes = 'technical', ('study','procedure')
        else:
            continue
        if not eligible(block,decision,disabled,block.role,modes):
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


def annotate(plan, document, *, automatic_prose=True):
    """Recognize cues only when the compiled request has one demonstrable owner."""
    mode = plan['policy'].get('performance', 'auto')
    if mode not in MODES:
        raise Error('performance_policy', 'Choose auto, off, or experimental performance.')
    document_policy=plan['policy'].get('document', {})
    disabled, preference = performance_preferences(document_policy, {b.id for b in document.blocks})
    registry = loadjson(RULES_FILE)
    tuning=tuning_preferences(document_policy.get('performance',{}),mode,registry)
    if mode == 'off':
        plan['performance'] = {'mode':mode, 'bound':True, 'active':0, 'candidates':0}
        if document_policy.get('performance',{}).get('tuning'):
            plan['performance']['tuning_state']='inactive'
        return
    recipes = registry['directions']
    verbs = {cue:kind for kind,item in recipes.items() for cue in item['cues']}
    cue_pattern = '|'.join(map(re.escape, [*NEUTRAL_REPORTING,*verbs]))
    events = {label:kind for kind,item in registry['events'].items() for label in item['spellings']}
    by_id = {b.id:b for b in document.blocks}
    decisions = {d['block']:d for d in plan['decisions']}
    source_order = {b.id:i for i,b in enumerate(document.blocks)}
    occurrences = Counter(bid for u in plan['utterances'] for bid in u['sources'])
    by_source_candidate={}
    candidates = 0
    registry_sha = key(registry)
    for unit in plan['utterances']:
        sources = unit['sources']
        if not 1 <= len(sources) <= 2 or any(occurrences[bid]!=1 or not eligible(
                by_id[bid],decisions[bid],disabled) for bid in sources):
            continue
        if len(sources)==2 and source_order[sources[1]] != source_order[sources[0]]+1:
            continue
        blocks = [by_id[bid] for bid in sources]
        if len(blocks)==2 and (blocks[0].chapter!=blocks[1].chapter or
                              blocks[0].locator.get('chapter_id')!=blocks[1].locator.get('chapter_id')):
            continue
        neutral = unit['text']
        if neutral != normalize_space(' '.join(decisions[b.id]['spoken'] for b in blocks)):
            continue
        source = blocks[0].source
        direction = None
        focus = None
        event = None
        event_attribution = None
        if len(blocks)==2:
            if STANDALONE.fullmatch(source) and (event_attribution:=dialogue_attribution(blocks[1].source,cue_pattern)):
                event = source, 'before', blocks[0], 0, ''
            elif STANDALONE.fullmatch(blocks[1].source) and (event_attribution:=dialogue_attribution(source,cue_pattern)):
                event = blocks[1].source, 'after', blocks[1], 0, ''
        elif (match:=dialogue_attribution(source,cue_pattern)) and (kind:=verbs.get(match['verb'])):
            direction = kind, match
        elif (focus:=focus_direction(source,registry['context']['emphasis'])) is not None:
            pass
        elif (match:=LEADING.fullmatch(source)) and (event_attribution:=dialogue_attribution(match['dialogue'],cue_pattern)):
            event = match['cue'], 'before', blocks[0], match.start('cue'), ''
        elif (match:=TRAILING.fullmatch(source)) and (event_attribution:=dialogue_attribution(
                match['dialogue']+(match['stop'] or ''),cue_pattern)):
            event = match['cue'], 'after', blocks[0], match.start('cue'), ''
        elif (speaker:=SPEAKER.fullmatch(source)) and (match:=LEADING.fullmatch(speaker['line'])):
            index=source_order[blocks[0].id]
            neighbors=document.blocks[max(0,index-1):index]+document.blocks[index+1:index+2]
            if any(other.chapter==blocks[0].chapter and other.role=='body' and not other.issue
                   and (turn:=SPEAKER.fullmatch(other.source)) and turn['name']!=speaker['name'] for other in neighbors):
                event=match['cue'],'before',blocks[0],speaker.start('line')+match.start('cue'),speaker['name']+':'
        if direction:
            kind, attribution = direction
            recipe = recipes[kind]
            start,end = attribution.span('verb')
            continuation = attribution.groupdict().get('continuation')
            instruction = recipe['instruction']
            if recipe.get('progression') and (continuation or len(list(sentence_spans(attribution['quote'][1:-1])))<2):
                continue
            if continuation:
                instruction += ' Treat both quoted spans as the same speaker’s continuing turn.'
            candidate = {'recipe':kind, 'speech':neutral, 'synthesis':neutral,
                         'instruction':instruction, 'cfg_scale':recipe.get('cfg_scale',1),
                         'source':{'block':sources[0], 'span':[start,end]},
                         'evidence':[{'kind':'direct_attribution','span':[start,end]}]}
            if continuation:
                candidate['contextual']=True
                candidate['evidence'][0].update(form='interrupted_quotation',
                    quotation_spans=[list(attribution.span('quote')),list(attribution.span('continuation'))])
                if attribution.groupdict().get('inverted'):candidate['evidence'][0]['attribution_order']='verb_first'
            elif attribution.groupdict().get('inverted'):
                candidate['contextual']=True
                candidate['evidence'][0]['form']='verb_first'
            if suffixes:=recipe.get('voice_suffixes'):
                candidate['voice_suffixes']=suffixes
        elif focus:
            phrase,evidence=focus
            candidate={'recipe':'contrastive_emphasis','speech':neutral,'synthesis':neutral,'cfg_scale':1,
                       'instruction':registry['context']['emphasis']['instruction'].format(phrase=phrase),
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
        elif len(blocks)==1:
            index=source_order[blocks[0].id]
            previous=document.blocks[index-1] if index else None
            if (previous and (previous.chapter!=blocks[0].chapter
                              or previous.locator.get('chapter_id')!=blocks[0].locator.get('chapter_id')
                              or occurrences[previous.id]!=1 or not eligible(previous,decisions[previous.id],disabled))):
                previous=None
            carry = (continued_direction(source,previous,cue_pattern,registry['context']['continuation'],
                                         by_source_candidate.get(previous.id)) if previous and preference!='off' else None)
            if carry:
                anchor=by_source_candidate[previous.id]
                kind,evidence,has_lead=anchor['recipe'],[carry],False
            elif (context:=contextual_direction(source,registry['context'],previous,preference)):
                kind,evidence,has_lead=context
            else:
                continue
            recipe=recipes[kind]
            instruction=recipe['instruction']
            if has_lead:
                instruction='Read the opening narration in the usual reference voice. '+instruction
            span=carry['span'] if carry else [0,len(source)]
            candidate={'recipe':kind,'speech':neutral,'synthesis':neutral,'instruction':instruction,
                       'cfg_scale':recipe.get('cfg_scale',1),'source':{'block':sources[0],'span':span},
                       'evidence':evidence,'contextual':True}
            if carry:
                candidate['anchor']=anchor['id']
            if suffixes:=recipe.get('voice_suffixes'):
                candidate['voice_suffixes']=suffixes
        else:
            continue
        if (direction or candidate.get('contextual')) and not focus and not candidate.get('anchor'):
            if marker:=recipe.get('quote_marker'):
                quotes=list(re.finditer(QUOTATION,candidate['speech']))
                if len(quotes)==1:
                    pos=quotes[0].start()+1
                    candidate['synthesis']=candidate['speech'][:pos]+marker+' '+candidate['speech'][pos:]
                    candidate['control_labels']=[marker[1:-1]]
                elif not direction or not continuation:continue
        from .backend import validate_request
        try:
            validate_request(candidate['synthesis'],instruction=candidate.get('instruction'))
        except Error as exc:
            if exc.code not in ('speech_input_limit','unspeakable_text'):
                raise
            continue
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
            candidates+=add_gentle_entries(*sources)
        if mode=='experimental':
            candidates+=add_structural_directions(*sources)
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
    if candidates or 'performance' in plan['policy']:
        plan['performance'] = {'mode':mode, 'bound':not candidates, 'candidates':candidates,
                               'active':0, 'registry_sha256':registry_sha}
        if expressive_default:
            plan['performance']['automatic_scope'] = 'prose'
        if document_policy.get('performance',{}).get('tuning'):
            plan['performance']['tuning_state']='experimental' if tuning else 'inactive'


def candidate_id(source_digest, candidate):
    identity=[source_digest,candidate['source'],candidate['recipe'],candidate['neutral']]
    if tuning:=candidate.get('tuning'):identity.append(tuning)
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


def supports_prose_direction(backend):
    runtime, models, archives = pinned_backends()
    platform = backend.get('runtime_platform') or {'linux': 'linux_x86_64', 'win32': 'windows_x86_64'}.get(sys.platform)
    model = models.get(backend.get('profile'))
    # Older verified setups predate the archive field. The pinned runtime and
    # model still identify the instruction-only transport in those setups.
    return (platform in archives and model is not None and backend.get('model_sha256') == model
            and backend.get('runtime_revision') == runtime
            and backend.get('runtime_archive_sha256') in (None, archives[platform]))


def bind(plan, backend, voice, *, fallbacks=(), previous=None):
    """Freeze planned requests once a job has a pinned backend and narrator."""
    summary = plan.get('performance')
    if not summary or summary['bound'] or not backend or not voice:
        return False
    registry = loadjson(RULES_FILE)
    previous = previous or {}
    fallback_ids = {entry['candidate'] for entry in fallbacks if not entry.get('variant')}
    instructions_only = {entry['candidate'] for entry in fallbacks if entry.get('variant')=='instruction_only'}
    same_registry = summary['registry_sha256']==key(registry)
    platform = backend.get('runtime_platform',sys.platform)
    archive_sha256 = backend.get('runtime_archive_sha256')
    if archive_sha256 is None and platform == 'linux':
        archive_sha256 = registry['legacy_linux_archives'].get(backend.get('binary_sha256'))
    backend_target = {'profile':backend.get('profile'),'runtime':backend.get('runtime_revision'),
                      'archive_sha256':archive_sha256,'model_sha256':backend.get('model_sha256'),
                      'platform':platform}
    reference = key({'reference_sha256':voice['reference_sha256'],'transcript':voice['transcript']}) if (
        voice.get('reference_sha256') and voice.get('transcript')) else None
    prose = (same_registry and summary.get('automatic_scope') == 'prose' and reference is not None
             and supports_prose_direction(backend))
    automatic = registry['automatic_directions']
    settings = backend.get('settings',{})
    execution = {'device':settings.get('device'),'parallel':settings.get('parallel',1)}
    candidates=[(unit,unit['performance_candidate']) for unit in plan['utterances'] if 'performance_candidate' in unit]
    candidates.sort(key=lambda item:'anchor' in item[1])
    active_ids=set()
    for unit,candidate in candidates:
        cid = candidate['id']
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
            target['placement']='after_speaker' if event['word_offset'] else event['position']
        qualified = same_registry and all(backend_target.values()) and (
            target in registry['qualified'] or
            reference is not None and {**target,'reference_identity':reference,'execution':execution} in registry['qualified'])
        recipe = candidate['recipe']
        directed = prose and candidate.get('instruction') and (recipe in automatic['prose'] or (
            recipe in automatic['direct_attribution'] and any(e['kind']=='direct_attribution'
                                                            for e in candidate.get('evidence', ()))))
        enabled = backend.get('runtime_revision') and (summary['mode']=='experimental' or qualified or directed)
        if enabled:
            unit['text'] = candidate['speech']
            if execution['parallel']!=1 and candidate['cfg_scale']!=1:
                if candidate.get('tuning'):
                    raise Error('speech_guidance','Stronger custom guidance requires serial generation.')
                candidate={**candidate,'cfg_scale':1}
            suffix=candidate.get('voice_suffixes',{}).get(voice.get('character'))
            if suffix and (summary['mode']=='experimental' or directed):
                candidate={**candidate,'instruction':candidate['instruction']+' '+suffix}
            unit['performance'] = candidate
            active_ids.add(cid)
            if cid in instructions_only or (directed and candidate.get('control_labels')):
                use_instruction_only(unit)
            elif summary['mode']=='experimental':
                unit['performance_state']='experimental'
            else:
                unit['performance_state']='qualified' if qualified else 'automatic'
        else:
            unit['performance_state'] = 'unqualified'
    summary['active'] = sum('performance' in unit for unit in plan['utterances'])
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
        if samples:={k:v for k,v in performance.get('tuning',{}).items() if k!='cfg_scale'}:
            options['sampling']=samples
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
        return identity
    return None


def status(job):
    if details:=job['plan'].get('performance'):
        history=job.get('performance_fallbacks',())
        return {**details,'fallbacks':sum(not record.get('variant') for record in history),
                'instruction_only_retries':sum(record.get('variant')=='instruction_only' for record in history)}
    return None


def lexical_observation(unit, observed):
    event = (unit.get('performance') or {}).get('event')
    if not event:
        return observed
    labels = '|'.join(map(re.escape,event['asr_annotations']))
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
