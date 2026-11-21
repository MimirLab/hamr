"""Sentence stops and contextual readings, distinct from synthesis joins.

The saved faithful-5 implementation remains unchanged. These rules are a new
revision qualified with paired source examples, not a claim of general parsing.
"""
import re
from . import abbreviations as old

STARTERS={'Then','She','He','They','We','I','You','It','Bring','Use','Keep','Do',
          'Next','However','Finally','Meanwhile','This','That','Another'}
DAYS={'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'}
SPEECH_VERBS=r'(?:said|asked|replied|whispered|shouted|answered|murmured|added)\b'
ATTRIBUTION=re.compile(r'^(?:(?:Dr|Mr|Mrs|Ms|Prof)\.\s+)?(?:[A-Z][\w’\'-]*\s+){1,4}'+SPEECH_VERBS)
TOKEN=old.TOKEN.replace(r'(?![\w./@-])',r'(?![^\W\d]|[./@-])')


def following(text):
    match=re.match(r'''\s*["“”'’()\[\]]*([^\W\d_]\w*|\d+)''',text)
    return match[1] if match else ''


def period_stop(text,end,token):
    before=text[:end-len(token)];after=text[end:];word=token[:-1].lower();next_word=following(after)
    if not after.strip():return True
    if word in ('dr','st') and old.ADDRESS.search(before):
        return bool(next_word and next_word[0].isupper())
    if word in old.TITLES:return False
    if word=='st' and next_word and next_word[0].isupper() and next_word not in STARTERS:return False
    if word in old.LABELS and re.match(r'\s*\d',after):return False
    if word in ('e.g','i.e','vs','cf','dept','approx','mt'):return False
    if re.fullmatch(r'et[ \t]+al',word) or word=='al' and re.search(r'(?<!\w)et[ \t]+$',before,re.I):
        return not after.strip(" \t\r\n\"”’')]") or bool(next_word and next_word[0].isupper())
    if word in ('a.m','p.m') and old.NUMBER_BEFORE.search(before):
        if next_word in DAYS or re.match(r'\s*(?:Dr|Mr|Mrs|Ms|Prof)\.',after):return False
        return next_word in STARTERS
    if token=='M.' and re.search(r'\d\s+[AP]\s+$',before):return next_word in STARTERS
    if re.fullmatch(r'(?:[A-Z]\.){2,}',token):return next_word in STARTERS
    if re.fullmatch(r'[A-Z]\.',token):return False
    if word in ('jr','sr'):return next_word in STARTERS
    if word in ('no','nos'):return True  # Standalone rejection is a sentence.
    if word in old.UNITS or word in ('etc','inc','ltd'):return bool(next_word and next_word[0].isupper())
    return True


def sentence_spans(text):
    start=0
    for match in re.finditer(r'''[.!?](?:["”’')\]]*)\s+''',text):
        punctuation=text[match.start()];stop=match.start()+1
        if punctuation=='.':
            prefix=text[:stop];token=prefix.split()[-1].lstrip('([“"')
            if not period_stop(text,stop,token):continue
        elif text[stop:match.end()].strip() and ATTRIBUTION.match(text[match.end():]):
            # A quoted question plus attribution is one grammatical sentence;
            # keep the '?' itself for the model's question intonation.
            continue
        end=match.end()
        yield {'start':start,'end':end,'text':text[start:end].strip()}
        start=end
    if text[start:].strip():yield {'start':start,'end':len(text),'text':text[start:].strip()}


def sentence_parts(text):
    return (s['text'] for s in sentence_spans(text))


def expand(text,aliases=None):
    aliases=aliases or {}
    alternatives=[r'(?<!\w)'+re.escape(src)+r'(?!\w)' for src in sorted(aliases,key=len,reverse=True)]
    pattern=re.compile('|'.join(alternatives+['(?i:'+TOKEN+')']))
    protected=[m.span() for m in old.PROTECTED.finditer(text)];edits=[];pending=[]
    def replace(m):
        token=m[0]
        if token in aliases:return aliases[token]
        if any(a<=m.start()<b for a,b in protected):return token
        before=text[:m.start()];after=text[m.end():];word=token[:-1].lower()
        spoken,reason,uncertain=old.reading(token,before,after)
        name=old.NAME.match(after)
        if word=='st' and not old.ADDRESS.search(before) and name and name[1].isupper() and following(after) not in STARTERS:
            spoken,reason,uncertain='Saint','Saint before a proper name; distinct from a numbered street address',None
        stop=period_stop(text,m.end(),token)
        if uncertain:pending.append({'source':token,'input_start':m.start(),'input_end':m.end(),'reason':uncertain})
        if spoken!=token:
            if stop:spoken+='.'
            edits.append({'source':token,'spoken':spoken,'sentence_stop':stop,'input_start':m.start(),'input_end':m.end(),'reason':reason})
        return spoken
    return pattern.sub(replace,text),edits,pending


def safe_cut(tokens,end):
    for i in range(len(tokens)-2):
        if re.fullmatch(r'\d+(?::\d+)?',tokens[i]) and tokens[i+1] in ('A','P') and tokens[i+2].rstrip('.')=='M' and i<end<i+3:
            end=i if i else i+3
    end=old.attached_boundary(tokens,end)
    # Prefer a clause when a long sentence exceeds the budget. Keep critical
    # operators, unary signs and numeric units together at a forced join.
    clauses=[i for i in range(max(1,end//2),end+1) if tokens[i-1].endswith((',', ';', ':'))]
    if clauses:end=clauses[-1]
    operators={'not','only','if','greater','less','than','or','equal','to','minus','plus','per','square','power','of'}
    while end>1 and (tokens[end-1].lower() in operators or
                      (end<len(tokens) and re.fullmatch(r'[+-]?\d+(?:\.\d+)?',tokens[end-1]) and re.match(r'(?:to\b|mV|mg|cm|mm|kPa|megab|millivolt)',tokens[end]))):
        end-=1
    for i in range(len(tokens)-2):
        if re.fullmatch(r'\d+(?::\d+)?',tokens[i]) and tokens[i+1] in ('A','P') and tokens[i+2].rstrip('.')=='M' and i<end<i+3:
            end=i if i else i+3
    return end
