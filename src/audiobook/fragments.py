"""Keep an interrupted sound aligned with a name echoed in the source."""

from itertools import pairwise
import re

from . import performance
from .speech_compare import comparison_tokens


VERSION = 1
STOPPING = re.compile(
    r'\s*(?:[Hh]e\s+checked\s+himself|[Ss]he\s+checked\s+herself|'
    r'[Tt]hey\s+checked\s+themselves)(?:\s+[a-z]+ly)?[.!?]\s*')
FINAL_SOUND = re.compile(r'(?P<word>\b[a-z]{3,})(?=\s*(?:--+|[—–])\s*$)')


def named_echo(fragment, reply):
    name = fragment.capitalize()
    spelling = rf'({re.escape(name)})\b'
    before_name = rf'(?i:\b(?:where|who)\s+(?:is|was)\s+){spelling}'
    after_name = rf'(?i:\bwhere\s+){spelling}\s+(?i:is|was|may\s+be|might\s+be|could\s+be)\b'
    matches = list(re.finditer(rf'{before_name}|{after_name}',reply))
    if len(matches)==1:
        match, = matches
        return match.start(match.lastindex),name
    return None


def signals(text, current_end):
    """Find a self-cutoff within the current request and its named echo nearby."""
    matches = []
    for first, reply in pairwise(re.finditer(performance.QUOTATION, text)):
        if first.end()>current_end:
            break
        if not STOPPING.fullmatch(text[first.end():reply.start()]):
            continue
        if fragment:=FINAL_SOUND.search(first[0][1:-1]):
            if echo:=named_echo(fragment['word'],reply[0]):
                start = first.start()+1+fragment.start()
                offset,name = echo
                matches.append((start,reply.start()+offset,name))
    return matches


def annotate(units, by_id, decisions, order, occurrences, disabled, passages):
    def source(unit):
        blocks = [by_id[bid] for bid in unit['sources']]
        if not blocks or any(occurrences[b.id]!=1 or order[b.id]!=order[blocks[0].id]+i
                             or not performance.eligible(b,decisions[b.id],disabled)
                             for i,b in enumerate(blocks)):
            return None
        if unit['text']!=performance.normalize_space(' '.join(decisions[b.id]['spoken'] for b in blocks)):
            return None
        printed = ' '.join(passages.text(b) for b in blocks)
        return performance.normalize_space(printed)

    for index, unit in enumerate(units):
        spoken = unit['text']
        if not any(dash in spoken for dash in ('--','—','–')):
            continue
        printed = source(unit)
        if printed is None:
            continue
        printed_end, spoken_end = len(printed), len(spoken)
        neighbor = None
        if index+1<len(units):
            next_unit = units[index+1]
            if (unit['chapter'],unit.get('chapter_id'))==(next_unit['chapter'],next_unit.get('chapter_id')):
                following = source(next_unit)
                if following and order[unit['sources'][-1]]+1==order[next_unit['sources'][0]]:
                    neighbor = next_unit
                    printed += ' '+following
                    spoken += ' '+next_unit['text']
        original = signals(printed,printed_end)
        prepared = signals(spoken,spoken_end)
        if len(original)==len(prepared)==1 and original[0][2]==prepared[0][2]:
            start,echo,name = prepared[0]
            add_target(unit,start,name,'cutoff')
            if echo<spoken_end:
                add_target(unit,echo,name,'reply')
            elif neighbor is not None:
                add_target(neighbor,echo-spoken_end-1,name,'reply')


def add_target(unit, offset, name, role):
    target = {'span':[offset,offset+len(name)],'name':name,'role':role}
    unit.setdefault('fragment_echo',{'version':VERSION,'targets':[]})['targets'].append(target)


def synthesis_text(unit, text):
    if text!=unit['text']:
        return text
    for target in unit['fragment_echo']['targets']:
        start,stop = target['span']
        name = target['name']
        if target['role']=='cutoff' and text[start:stop]==name.lower():
            text = text[:start]+name+text[stop:]
    return text


def lexical_observation(unit, observed):
    # The doubled consonant can be written once by ASR. License that spelling
    # at the exact authored positions with the entire script still recovered.
    expected = unit['text']
    corrected = observed
    for target in sorted(unit['fragment_echo']['targets'],key=lambda item:item['span'][0],reverse=True):
        start,_ = target['span']
        name = target['name'].lower()
        if not re.search(r'([b-df-hj-np-tv-z])\1$',name):
            continue
        index = len(comparison_tokens(expected[:start]))
        for match in re.finditer(rf'\b{re.escape(name[:-1])}\b',corrected,re.I):
            if len(comparison_tokens(corrected[:match.start()]))==index:
                corrected = corrected[:match.start()]+name+corrected[match.end():]
                break
    return corrected if corrected!=observed and comparison_tokens(corrected)==comparison_tokens(expected) else observed
