"""Interpret a raised integer when its surrounding expression identifies its role."""

import re

from .context_candidate import number_spoken


INTEGER = re.compile(r'[+−-]?[0-9]{1,4}')
BASE = re.compile(r'(?<![\w.,/−+\-])[0-9]{1,9}$')
MULTIPLIER = re.compile(r'(?<![\w.,/−+\-])([+−-]?[0-9]{1,9}(?:\.[0-9]{1,12})?)'
                        r'(?:[^\S\r\n]*×[^\S\r\n]*|[^\S\r\n]+[xX][^\S\r\n]+)$')
EQUALS = re.compile(r'[^\S\r\n]*(?P<operator>is\b|equals\b|=|≈)[^\S\r\n]*', re.I)
LITERAL_RESULT = re.compile(r'([0-9]{1,12})(?=[.!?](?!\d)|[^\S\r\n]*$)')
LABEL = re.compile(r'\b(?:appendix|article|chapter|equation|figure|item|note|page|room|route|section|step|table|verse|version)'
                   r'[^\S\r\n]+$', re.I)
INTRODUCTION = re.compile(r'\b(?:(?:calculate|compute|evaluate|simplify)(?: the (?:power|expression))?'
                          r'|(?:powers?|exponents?)(?: (?:of|such as|like|including)))\s*[:=]?[^\S\r\n]*$', re.I)
PROBABILITY = re.compile(r'\bprobability\b[^.!?\n]{0,88}\b(?:is|equals) (?:about )?$', re.I)
FORMULA_EXPLANATION = re.compile(r'[^\S\r\n]*\(the (?:formula|equation)\b', re.I)
ATTRIBUTED = frozenset(('hidden','note_reference','mathml','formula'))
SEPARATE = ATTRIBUTED | {'hyperlink','literal','superscript','subscript'}


def visible_context(source, start, end, hidden):
    context = source[start:end]
    for first,last in hidden:
        a,b = max(first,start)-start,min(last,end)-start
        if a<b:
            context = context[:a]+'\n'*(b-a)+context[b:]
    return context


def prepare(source, spans):
    """Retain display evidence and add a common reading for supported powers."""
    raised = {(item['source_start'], item['source_end']):item for item in spans
              if item['kind']=='superscript' and INTEGER.fullmatch(item['source'])}
    if not raised:
        return spans
    barriers = [(item['kind'],item['source_start'],item['source_end']) for item in spans if item['kind'] in SEPARATE]
    hidden = [(a,b) for kind,a,b in barriers if kind=='hidden']
    attributed = {key for key in raised if any(kind in ATTRIBUTED and lo<=key[0]<key[1]<=hi
                                                for kind,lo,hi in barriers)}
    candidates = {}
    for (start,end),item in raised.items():
        if ((start,end) in attributed or source[start:end]!=item['source']
                or end<len(source) and (source[end].isalnum() or source[end]=='_')):
            continue
        offset = max(0,start-128)
        left = source[offset:start]
        if not (base:=BASE.search(left)) or LABEL.search(left[:base.start()]):
            continue
        coefficient = MULTIPLIER.search(left[:base.start()]) if base[0]=='10' else None
        first = offset + (coefficient or base).start()
        if any(lo<end and first<hi and (kind,lo,hi)!=('superscript',start,end) for kind,lo,hi in barriers):
            continue
        candidates[start,end] = first,base[0],coefficient[1] if coefficient else None

    scientific = {first for first,_,coefficient in candidates.values() if coefficient is not None}
    resolved = {}
    for (start,end),(first,base,coefficient) in candidates.items():
        exponent = raised[start,end]['source']
        left = visible_context(source,max(0,first-128),first,hidden)
        right = visible_context(source,end,end+128,hidden)
        relation = EQUALS.match(right)
        stop,ending = end,''
        if coefficient is not None:
            evidence = 'decimal multiplier and raised power of ten'
        elif relation and end+relation.end() in scientific:
            evidence = 'equality with separately identified scientific notation'
            if relation['operator'] in ('=','≈'):
                stop = end+relation.end()
                ending = ' equals ' if relation['operator']=='=' else ' approximately equals '
        elif (relation and relation['operator']!='≈' and (value:=LITERAL_RESULT.match(right,relation.end()))
              and 0<=int(exponent.replace('−','-'))<=12 and int(base)**int(exponent.replace('−','-'))==int(value[1])
              and not any(lo<end+value.end() and end<hi for _,lo,hi in barriers)):
            evidence = 'matching numeric result in the same equation'
            stop,ending = end+value.end(),' equals '+number_spoken(value[1])
        elif INTRODUCTION.search(left) or PROBABILITY.search(left) and FORMULA_EXPLANATION.match(right):
            evidence = 'explicit explanation of the expression in nearby prose'
        else:
            continue
        audible = number_spoken(base)+' to the power of '+number_spoken(exponent)
        if coefficient is not None:
            audible = number_spoken(coefficient)+' times '+audible
        formula = {'kind':'formula','source_start':first,'source_end':stop,'source':source[first:stop],
                   'reading':audible+ending,'origin':'Context-qualified superscript','evidence':evidence}
        script = {**raised[start,end],'kind':'math_script','relation':'power','base':base,'value':exponent,
                  'display_kind':'superscript','evidence':evidence}
        resolved[start,end] = formula,script

    result,seen = [],set()
    for item in spans:
        position = item['source_start'],item['source_end']
        if item['kind']=='superscript' and position in resolved:
            if position not in seen:
                result.extend(resolved[position])
                seen.add(position)
        elif item['kind']=='superscript' and position in attributed:
            result.append({**item,'kind':'presentation_script','display_kind':'superscript'})
        else:
            result.append(item)
    return result
