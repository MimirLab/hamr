"""Read authored formula notation from PDF text and positioned marks."""

import re
import unicodedata

from .context_candidate import number_spoken


MATH_FAMILY = re.compile(r'CM(?:MI|SY|EX)\d*|(?:STIX(?:Two)?|Cambria|LatinModern)Math', re.I)
ASCII_WORD = re.compile(r'[a-zA-Z]+')
NUMBER = re.compile(r'(?:[0-9]{1,3}(?:[ \u00a0\u2009\u202f][0-9]{3}){1,3}|[0-9]{1,12})'
                    r'(?:\.[0-9]{1,15}(?:[ \u00a0\u2009\u202f][0-9]{1,4}){0,4})?')
SHORT_PROSE = {'a','am','an','as','at','be','by','do','go','he','if','in','is','it','me','my','no','of','on','or','so','to','up','us','we'}
OPERATORS = {'+':'plus','-':'minus','−':'minus','±':'plus or minus','×':'times','÷':'divided by',
             '/':'slash','=':'equals','<':'less than','>':'greater than', '∞':'infinity',
             '′':'prime', "'":'prime','°':'degrees','◦':'degrees'}
OPEN = {'(':'parenthesis','[':'bracket'}
CLOSE = {')':'(',']':'['}


def letter(char):
    if char.isascii() and char.isalpha():
        return ('capital ' if char.isupper() else '')+char.upper()
    canonical = unicodedata.normalize('NFC',char) if char!='µ' else 'μ'
    match = re.fullmatch('GREEK (SMALL|CAPITAL) LETTER ([A-Z]+)',unicodedata.name(canonical,''))
    if match:
        return ('capital ' if match[1]=='CAPITAL' else '')+{'LAMDA':'lambda'}.get(match[2],match[2].lower())
    return None


def positions(spans):
    return {i for a,b in spans for i in range(a,b)}


def read_formula(cell, start=0):
    """Read printed order, explicit grouping, scripts, and verified glyph attachments."""
    text = cell['text']
    scripts = {s['span'][0]:s for s in cell['scripts'] if s['span'][0]>=start}
    glyphs = positions(cell.get('math_font_spans',()))
    marks = [mark for mark in cell.get('math_marks',()) if mark['owner'][0]>=start]
    if any(mark['kind']=='bar' and mark['span'][0]<start for mark in marks):
        return None
    bars = {mark['owner'][0] for mark in marks if mark['kind']=='bar'}
    removed = positions(mark['span'] for mark in marks if mark['kind']=='bar')
    fractions = {mark['span'][0]:mark for mark in marks if mark['kind']=='fraction'}
    pieces, nesting = [], []
    index = start
    while index<len(text):
        ch = text[index]
        if index in fractions:
            mark = fractions[index]
            pieces.append('the fraction with numerator '+number_spoken(mark['numerator'])+
                          ' and denominator '+number_spoken(mark['denominator'])+',')
            index = mark['span'][1]
            continue
        if index in removed or ch.isspace():
            index += 1
            continue
        if index in scripts:
            script = scripts[index]
            end = script['span'][1]
            surface = text[index:end]
            if script['kind']=='superscript' and surface in ('2','3') and pieces and pieces[-1] not in ('open parenthesis','open bracket'):
                pieces.append('squared' if surface=='2' else 'cubed')
            elif surface in ('′',"'",'°','◦'):
                pieces.append(OPERATORS[surface])
            elif re.fullmatch(r'[+−-]?[0-9]{1,12}',surface):
                pieces.extend((script['kind'],number_spoken(surface)))
            else:
                spelled = [letter(c) or OPERATORS.get(c) for c in surface if not c.isspace()]
                if not spelled or not all(spelled):
                    return None
                pieces.extend((script['kind'],' '.join(spelled)))
            prior=text[:index].rstrip()[-1:]
            following=end+len(text[end:])-len(text[end:].lstrip())
            if (prior and (prior.isalnum() or prior in ')]') and following<len(text)
                    and following not in scripts and (letter(text[following]) or text[following]=='(')):
                pieces.append('times')
            index = end
            continue
        if ch in OPEN:
            nesting.append(ch)
            pieces.append('open '+OPEN[ch])
        elif ch in CLOSE:
            if not nesting or nesting.pop()!=CLOSE[ch]:
                return None
            pieces.append('close '+OPEN[CLOSE[ch]])
        elif ch=='|':
            if nesting and nesting[-1]=='|':
                nesting.pop()
                pieces.append('close absolute value')
            else:
                nesting.append(ch)
                pieces.append('absolute value of')
        elif ch in OPERATORS:
            pieces.append(OPERATORS[ch])
        elif text[index:index+3]=='...' or ch=='…':
            pieces.append('followed by an ellipsis')
            index += 3 if ch=='.' else 1
            continue
        elif ch=='¯' or ch=='\u0305':
            return None  # An unbound bar cannot be assigned to its string neighbor.
        elif ch.isascii() and ch.isdigit():
            limit = min((a for a in scripts if a>index), default=len(text))
            match = NUMBER.match(text,index,limit)
            if not match:
                return None
            pieces.append(number_spoken(re.sub(r'\s','',match[0])))
            index = match.end()
            continue
        elif spoken:=letter(ch):
            word = ASCII_WORD.match(text,index)
            if word and (index==start or not text[index-1].isalpha()):
                if word[0]=='cycle' and nesting:
                    pieces.append('cycle')
                    index=word.end()
                    continue
                word_positions = set(range(*word.span()))
                has_math = bool(word_positions & (glyphs | scripts.keys() | bars))
                if not has_math and (word[0] in SHORT_PROSE or len(word[0])>2):
                    return None
            pieces.append(spoken+(' bar' if index in bars else ''))
        elif ch==',':
            pieces.append('comma')
        else:
            return None
        index += 1
    return ' '.join(pieces) if pieces and not nesting else None


def formula_suffix(cell):
    text = cell['text']
    witnesses = [a for a,_ in cell.get('math_font_spans',())]
    witnesses.extend(mark['owner'][0] for mark in cell.get('math_marks',()))
    if not witnesses:
        return None
    named_hyphens = {m.start() for m in re.finditer(r'\b[A-Za-z]-[a-z]+\b',text)}
    first = min((i for i in witnesses if i not in named_hyphens),default=len(text))
    if first==len(text):
        return None
    # Ordinary title words can precede a roman digit, bracket or factor at
    # the start of the notation. Complete tokenization tests each boundary.
    for start in [0,*(m.end() for m in re.finditer(r'\s+',text[:first+1]) if m.end()<=first)]:
        if spoken:=read_formula(cell,start):
            return start, spoken
    return None
