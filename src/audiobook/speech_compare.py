"""Conservative speech comparison; keep literal source/raw ASR separately.

Bounded cardinal, negation and contextual abbreviation readings are
canonicalized. Decimal/version strings and leading zeros remain distinct.
Homophones such as for/four and know/no are deliberately not equated.
"""
import re,unicodedata
from .planner import SMALL,TENS

POLICY='bounded-english-composed-scale-typed-v11'
LEX=re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)*|[\w]+(?:[.'’][\w]+)*")
SMALL_VALUES={s:n for n,s in enumerate(SMALL)}
TENS_VALUES={s:n*10 for n,s in enumerate(TENS) if s}
SPECIAL_NEGATIVES={"can't":'can',"won't":'will',"shan't":'shall'}
ORDINALS={word:i for i,word in enumerate(['zeroth','first','second','third','fourth','fifth','sixth','seventh','eighth','ninth','tenth','eleventh','twelfth','thirteenth','fourteenth','fifteenth','sixteenth','seventeenth','eighteenth','nineteenth','twentieth'])}
ORDINALS.update(thirtieth=30,fortieth=40,fiftieth=50,sixtieth=60,seventieth=70,eightieth=80,ninetieth=90)
SCALES={'thousand':1000,'million':10**6,'billion':10**9}
UNPARSED_SCALES={'hundred',*SCALES,'trillion','quadrillion','quintillion','sextillion','septillion','octillion','nonillion','decillion'}
FRACTIONS={'half','halves','quarter','quarters',*(word for word in ORDINALS if 3<=ORDINALS[word]<=20),*(word+'s' for word in ORDINALS if 3<=ORDINALS[word]<=20)}


def numeric_literal(token):
    if not re.fullmatch(r'[+-]?\d+(?:[.,]\d+)*',token):return None
    # This comparator is for English narration. Accept conventional groups,
    # not decimal commas, irregular groups, or leading-zero identifiers.
    if re.fullmatch(r'[+-]?[1-9][0-9]{0,2}(?:,[0-9]{3})+(?:\.[0-9]+)?',token):token=token.replace(',','')
    return token.removeprefix('+')


def integer_value(token):
    # Longer strings can be identifiers; keep them opaque and avoid an
    # unbounded conversion on document/recognizer input.
    return int(token) if len(token)<=12 and re.fullmatch(r'0|[1-9][0-9]*',token) else None


def ordinal(ts,i,previous=None):
    # A numeric measurement's singular second/third is not an ordinal.
    if ts[i] in ORDINALS and not (previous or '').startswith('#'):return ORDINALS[ts[i]],i+1
    if i+1<len(ts) and ts[i] in TENS_VALUES and ts[i+1] in ORDINALS and 0<ORDINALS[ts[i+1]]<10:return TENS_VALUES[ts[i]]+ORDINALS[ts[i+1]],i+2
    if i+1<len(ts) and ts[i+1] in ('st','nd','rd','th') and len(ts[i])<=12 and re.fullmatch(r'(?:0|[1-9]\d*)',ts[i]):
        value=int(ts[i]);suffix='th' if 10<=value%100<=20 else {1:'st',2:'nd',3:'rd'}.get(value%10,'th')
        if ts[i+1]==suffix:return value,i+2
    return None

def cardinal(ts,i):
    if i>=len(ts):return None
    literal=numeric_literal(ts[i]);negative=bool(literal and literal.startswith('-'))
    if literal is not None and integer_value(literal.removeprefix('-')) is None:return literal,i+1
    def small(at,first=False,hundreds=True):
        if at>=len(ts):return None
        token=ts[at]
        number=numeric_literal(token)
        if number is not None:
            if ordinal(ts,at):return None
            n=integer_value(number.removeprefix('-') if first and negative else number)
            if n is None:return None
        elif token=='hundred' and hundreds:n=100
        elif token=='a' and at+1<len(ts) and ts[at+1] in ('hundred',*SCALES):n=1
        elif token in SMALL_VALUES:n=SMALL_VALUES[token]
        elif token in TENS_VALUES:n=TENS_VALUES[token]
        else:return None
        end=at+1
        if token in TENS_VALUES and end<len(ts) and ts[end] in SMALL_VALUES and 0<SMALL_VALUES[ts[end]]<10:n+=SMALL_VALUES[ts[end]];end+=1
        # Includes the bounded English year-style form "eleven hundred".
        if hundreds and (token=='hundred' or 0<n<100 and end<len(ts) and ts[end]=='hundred'):
            if token!='hundred':n*=100;end+=1
            rest=end+1 if end<len(ts) and ts[end]=='and' else end
            if rest<len(ts):
                tail=small(rest,hundreds=False)
                if tail and tail[0]<100 and not (tail[1]<len(ts) and ts[tail[1]]=='hundred'):n+=tail[0];end=tail[1]
        return n,end
    def coefficient(at,first=False):
        # Half-scale phrases have an exact integer value. Unscaled fractions
        # remain separate mathematical markers rather than guessed decimals.
        if at<len(ts) and ts[at]=='half':
            end=at+1
            if end<len(ts) and ts[end]=='a':end+=1
            if end<len(ts) and ts[end] in SCALES:return 0,True,end
        group=small(at,first)
        if group is None:return None
        n,end=group;rest=end
        if rest<len(ts) and ts[rest]=='and':
            rest+=1
            if rest<len(ts) and ts[rest] in ('a','one'):rest+=1
            if rest+1<len(ts) and ts[rest]=='half' and ts[rest+1] in SCALES:return n,True,rest+1
        return n,False,end
    first=coefficient(i,True)
    if first is None:return None
    n,half,end=first;total=0;last_scale=10**12
    def scale_at(at):
        # A conventional English composed scale, not a general permission to
        # multiply arbitrary adjacent magnitude words or a numeric list.
        if ts[at:at+2]==['thousand','million']:return 10**9,at+2
        if at<len(ts) and ts[at] in SCALES:return SCALES[ts[at]],at+1
        return None
    while (scale:=scale_at(end)) and (n>0 or half) and scale[0]<last_scale:
        last_scale,end=scale;total+=n*last_scale+(last_scale//2 if half else 0)
        rest=end+1 if end<len(ts) and ts[end]=='and' else end
        tail=coefficient(rest)
        # Repeated/ascending scales name another quantity; do not swallow its
        # coefficient or the separating conjunction into this one.
        if tail is None or (tail_scale:=scale_at(tail[2])) and tail_scale[0]>=last_scale:n=0;break
        n,half,end=tail
    value=('-' if negative else '')+str(total+n)
    if end<len(ts) and ts[end]=='point':
        position=end+1;digits=[]
        while position<len(ts) and ts[position] in SMALL_VALUES and SMALL_VALUES[ts[position]]<10:
            digits.append(str(SMALL_VALUES[ts[position]]));position+=1
        if digits:value+='.'+''.join(digits);end=position
    return value,end

def comparison_tokens(text):
    from .abbreviations import expand
    from .linguistic import comparison_text
    text=comparison_text(text)
    text,_,_=expand(text)
    text=text.lower().replace('’',"'").replace('±',' plus or minus ')
    ts=[];end=0
    for match in LEX.finditer(text):
        # Ignore punctuation in the final comparison but keep it as a barrier
        # while parsing a number phrase. A list is not one large quantity.
        gap=text[end:match.start()];token=match[0]
        # Composed scales require adjacent words separated only by whitespace;
        # a slash, dash or operator between them is not multiplication syntax.
        if re.search(r'[.!?;:,]',gap) or (ts and ts[-1]=='thousand' and token=='million' and gap.strip()):ts.append('|number-boundary|')
        end=match.end()
        if token.endswith('fold') and token[:-4] in (*SMALL_VALUES,*TENS_VALUES,'hundred',*SCALES):ts.extend((token[:-4],'fold'))
        else:ts.append(token)
    out=[];i=0
    while i<len(ts):
        t=ts[i]
        if t=='|number-boundary|':i+=1;continue
        ordered=ordinal(ts,i,out[-1] if out else None)
        if ordered:out.append('#ordinal:'+str(ordered[0]));i=ordered[1];continue
        if t in ('minus','negative','plus','positive'):
            number=cardinal(ts,i+1)
            if number and not number[0].startswith('-'):
                out.append('#'+('-' if t in ('minus','negative') else '')+number[0]);i=number[1];continue
        number=cardinal(ts,i)
        if number:out.append('#'+number[0]);i=number[1];continue
        if t in SPECIAL_NEGATIVES:out.extend([SPECIAL_NEGATIVES[t],'not'])
        elif t=='cannot':out.extend(['can','not'])
        elif t.endswith("n't") and t!="ain't":out.extend([t[:-3],'not'])
        elif t=="ain't":out.append(t)
        elif re.fullmatch('n+o+',t):out.append('no')
        else:out.append(t)
        i+=1
    return out

def quantities(ts):return [t[1:] for t in ts if t.startswith('#')]
def negations(ts):return sum(t in ('not','never','no',"ain't") for t in ts)


def mathematical_markers(text):
    """Extra inspection guard; never interpret a marker match as a quality pass.

    A lost fractional word, uncertainty operator or unit exponent can change a
    scientific claim while keeping all ordinary numbers and a low overall WER.
    Equivalent decimal/fraction spellings may remain flagged ASR uncertainty.
    """
    from collections import Counter
    from .abbreviations import expand
    from .linguistic import comparison_text
    text=comparison_text(text)
    text,_,_=expand(text)
    text=text.lower().replace('²',' squared ').replace('³',' cubed ').replace('°',' degrees ')
    text=text.replace('½',' half ').replace('¼',' quarter ').replace('¾',' three quarters ')
    markers=[];ts=comparison_tokens(text)
    markers += ['plus-or-minus']*len(re.findall(r'plus\s+(?:or\s+)?minus|±|\+\s*/\s*-',text))
    # A half already included in an exact scaled quantity must be compared by
    # that value. Independent fractional words/measurements stay protected.
    markers += ['fraction:'+('half' if w=='halves' else w.rstrip('s')) for w in ts if w in FRACTIONS]
    # An unsupported compound scale remains consequential. A low lexical WER
    # and matching parsed coefficient cannot clear a dropped magnitude word.
    markers += ['unparsed-scale:'+w for w in ts if w in UNPARSED_SCALES or w.endswith('s') and w[:-1] in UNPARSED_SCALES]
    markers += ['exponent:2']*len(re.findall(r'\bsquare(?:d)?\b',text))
    markers += ['exponent:3']*len(re.findall(r'\bcubic\b|\bcubed\b',text))
    for name,pattern in [('divide',r'\bdivided by\b'),('sine',r'\bsine\b'),('cosine',r'\bcosine\b')]:
        markers += [name]*len(re.findall(pattern,text))
    units=r'centimet(?:er|re)s?|millimet(?:er|re)s?|milligrams?|kilograms?|grams?|pounds?|yards?|seconds?|degrees?|inch(?:es)?|feet|foot|cm|mm|mg|kg|lbs?|millivolts?|microvolts?|kilopascals?|megabytes?|megabits?|kilobytes?|kilobits?|cups?|teaspoons?|tablespoons?|lit(?:er|re)s?|ounces?|hours?|minutes?'
    def unit(word):
        word=word.replace('metre','meter')
        return {'cm':'centimeter','mm':'millimeter','mg':'milligram','kg':'kilogram','lb':'pound','lbs':'pound','inches':'inch','feet':'foot'}.get(word,word.rstrip('s'))
    markers += ['unit:'+unit(w) for w in re.findall(r'\b('+units+r')\b',text)]
    markers += ['denominator:'+unit(w) for w in re.findall(r'\bper\s+(?:square\s+|cubic\s+)?('+units+r')\b',text)]
    # Inspect operators independently of matching numbers or aggregate WER.
    phrases={'greater than or equal to':'ge','less than or equal to':'le','greater than':'gt','less than':'lt'}
    operators=re.compile('|'.join(phrases))
    markers += ['comparison:'+phrases[m[0]] for m in operators.finditer(text)]
    for i,t in enumerate(ts):
        if t=='power' and i+1<len(ts) and ts[i+1].startswith('#'):markers.append('exponent:'+ts[i+1][1:])
        if t=='subscript' and i+1<len(ts) and ts[i+1].startswith('#'):markers.append('subscript:'+ts[i+1][1:])
        if t=='to' and i and i+1<len(ts) and ts[i-1].startswith('#') and ts[i+1].startswith('#'):markers.append('numeric-range')
    return dict(sorted(Counter(markers).items()))
