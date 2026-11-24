"""Conservative speech comparison; keep literal source/raw ASR separately.

Bounded cardinal, negation and contextual abbreviation readings are
canonicalized. Decimal/version strings and leading zeros remain distinct.
Homophones such as for/four and know/no are deliberately not equated.
"""
from calendar import monthrange
import re,unicodedata
from .context_forms import MONTHS, duration_pair, money_words
from .planner import SMALL,TENS
from .spoken_forms import roman

POLICY='bounded-english-composed-scale-typed-v42'
LEX=re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)*|[\w]+(?:[.'’][\w]+)*")
SMALL_VALUES={s:n for n,s in enumerate(SMALL)}
TENS_VALUES={s:n*10 for n,s in enumerate(TENS) if s}
SPECIAL_NEGATIVES={"can't":'can',"won't":'will',"shan't":'shall'}
SPELLING_VARIANTS={'harbour':'harbor','harbours':'harbors','harboured':'harbored','harbouring':'harboring'}
COMPOUND_SPELLINGS={'dustbowl':('dust','bowl'),'mathml':('math','ml'),'dtbook':('dt','book'),
                    'textbox':('text','box'),'textboxes':('text','boxes')}
ORDINALS={word:i for i,word in enumerate(['zeroth','first','second','third','fourth','fifth','sixth','seventh','eighth','ninth','tenth','eleventh','twelfth','thirteenth','fourteenth','fifteenth','sixteenth','seventeenth','eighteenth','nineteenth','twentieth'])}
ORDINALS.update(thirtieth=30,fortieth=40,fiftieth=50,sixtieth=60,seventieth=70,eightieth=80,ninetieth=90)
SCALES={'thousand':1000,'million':10**6,'billion':10**9}
UNPARSED_SCALES={'hundred',*SCALES,'trillion','quadrillion','quintillion','sextillion','septillion','octillion','nonillion','decillion'}
FRACTIONS={'half','halves','quarter','quarters',*(word for word in ORDINALS if 3<=ORDINALS[word]<=20),*(word+'s' for word in ORDINALS if 3<=ORDINALS[word]<=20)}
MONTH_LIMITS={name.lower():monthrange(2000,index)[1] for index,name in enumerate(MONTHS) if name}
LITERAL_DIGIT_RUN=re.compile(
    r'\b((?:dollar|euro|pound) sign)\s*,?\s+followed by\s+'
    r'([3-9]|1[012]|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+'
    r'([0-9][’\']?s|zero(?:e)?s|ones|twos|threes|fours|fives|sixes|sevens|eights|nines)\b',re.I)
DIGIT_PLURALS={'zeroes':'0','sixes':'6',**{word+'s':str(i) for i,word in enumerate(SMALL[:10])}}
PRINTED_AMOUNT=r'(?:[1-9][0-9]{0,2}(?:,[0-9]{3}){1,3}|[0-9]{1,12})(?:\.[0-9]{1,12})?'
PRINTED_CURRENCY=re.compile(rf'(?<![\w$£€¥])([+−-]?)([£¥])[ \t]*({PRINTED_AMOUNT})(?!\w|[.,][0-9]|[$£€¥])')
GLUED_DOLLAR=re.compile(rf'(?<=[^\W\d_])\$[ \t]*{PRINTED_AMOUNT}(?!\w|[.,][0-9]|[$£€¥])')
DOLLAR_BEFORE_YEN=re.compile(rf'(?<![\w$£€¥])\$[ \t]*({PRINTED_AMOUNT})(?=[ \t]+yen\b)',re.I)
ELAPSED_PAIR=re.compile(r'(\belapsed?[ \t]+time[ \t]*[:,]?[ \t]+)([0-9]{1,3})[.:]([0-5][0-9])(?!\w|[.:][0-9])',re.I)
ISBN=re.compile(r'\bisbn(?:-1[03])?[\s:]+((?:[0-9][ -]?){9,12}[0-9x])(?!\w|[ -][0-9])',re.I)
AMENDMENT_LABEL=re.compile(r'([\[(](?i:amendment)[ \t]+)([IVXLCDM]{1,15})(?=[ \t]*(?:[\])]|[-–—][ \t]+))')
YEAR_TEXT=re.compile(r'(?<!\w)(?:1[1-9]|20)\d{2}s?(?!\w)',re.I)
YEAR_PREFIX='|'.join(SMALL[11:20]+['twenty'])
YEAR_TENS=('twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety')
YEAR_DECADES=('twenties','thirties','forties','fifties','sixties','seventies','eighties','nineties')
YEAR_ONES='|'.join(SMALL[1:10])
YEAR_TAIL='|'.join(SMALL[10:20])+r'|(?:'+ '|'.join(YEAR_TENS)+r')(?:[ -]+(?:'+YEAR_ONES+r'))?|oh[ -]+(?:'+YEAR_ONES+r')'
SPOKEN_YEAR=re.compile(r'\b('+YEAR_PREFIX+r')[ -]+('+ '|'.join(YEAR_DECADES)+r'|'+YEAR_TAIL+r')\b',re.I)
DOMAIN_START=r'(?<![\w@.])'
DOMAIN_END=r'(?![\w-]|\.[a-z0-9])'
WWW_DOMAIN=re.compile(DOMAIN_START+r'www\.(?:[a-z0-9-]+\.){1,6}[a-z]{2,20}'+DOMAIN_END,re.I)
SPOKEN_PADDED_CODE=re.compile(r'\bzero(?:[ \t]+(?:'+'|'.join(SMALL[:10])+r')){1,7}\b',re.I)
PRINTED_PADDED_CODE=re.compile(r'(?<![\w/.:+−-])0[0-9]{1,7}(?![\w/:-]|\.\d)')


def isbn_notation(text):
    def joined(match):
        digits = re.sub(r'[ -]','',match[1])
        canonical = re.fullmatch(r'(?:[0-9]{13}|[0-9]{9}[0-9x])',digits,re.I)
        if canonical:
            return match[0][:match.start(1)-match.start()]+digits
        return match[0]

    return ISBN.sub(joined,text)


def year_context(text,start,end):
    def adjacent(value,last):
        items = LEX.findall(value)
        return re.sub(r'\W','',items[-1 if last else 0]).casefold() if items else ''

    return adjacent(text[max(0,start-90):start],True),adjacent(text[end:end+90],False)


def contextual_years(expected,observed):
    written = {}
    for match in YEAR_TEXT.finditer(expected):
        context = year_context(expected,*match.span())
        if any(context):
            written.setdefault(match[0].lower(),set()).add(context)
    if not written:
        return observed

    def year(match):
        first,second = match[1].lower(),match[2].lower()
        century = TENS_VALUES.get(first) or SMALL_VALUES.get(first)
        if second in YEAR_DECADES:
            year = f'{century}{20+10*YEAR_DECADES.index(second):02d}s'
        else:
            parts = second.replace('-',' ').split()
            value = sum(TENS_VALUES.get(part,SMALL_VALUES.get(part,0)) for part in parts)
            year = f'{century}{value:02d}'
        context = year_context(observed,*match.span())
        return year if context in written.get(year,()) else match[0]

    return SPOKEN_YEAR.sub(year,observed)


def domain_notation(expected,observed):
    separator = r'(?:\s*\.\s*|\s+(?:dot\s+)?)'
    for spelling in dict.fromkeys(WWW_DOMAIN.findall(expected)):
        # Recognizers sometimes separate the letters and digits inside a domain
        # label. Recover precisely the printed spelling, including leading zeros.
        labels = [r'\s*'.join(map(re.escape,re.findall(r'[a-z]+|[0-9]+|-',label,re.I)))
                  for label in spelling.split('.')]
        observed = re.sub(DOMAIN_START+separator.join(labels)+DOMAIN_END,lambda _:spelling,observed,flags=re.I)
    return observed


def notation_observation(expected, observed):
    """Resolve ASR formatting that represents the chosen spoken reading."""
    observed=spoken_currency_observation(expected,observed)
    observed=contextual_years(expected,observed)
    observed=domain_notation(expected,observed)
    # Recognizers write spoken identifiers as digits. Keep every leading zero
    # and only recover a spelling that the planned request actually uses.
    forms = {''.join(str(SMALL_VALUES[word]) for word in match[0].casefold().split()):match[0]
             for match in SPOKEN_PADDED_CODE.finditer(expected)}
    if forms:
        observed=PRINTED_PADDED_CODE.sub(lambda match:forms.get(match[0],match[0]),observed)
    if 'elapsed time' not in expected.casefold():return observed

    def elapsed(match):
        spoken=duration_pair(match[2],match[3])
        if re.search(r'\belapsed time[ \t]*:[ \t]*'+re.escape(spoken)+r'(?=[ \t]*[.,;!?]|[ \t]*$)',expected,re.I):
            return 'elapsed time '+spoken
        return match[0]

    return ELAPSED_PAIR.sub(elapsed,observed)


def spoken_currency_observation(expected, observed):
    """Resolve ASR currency glyphs using the rendering they describe."""
    if '$' in observed and re.search(r'\bdollars?\b',expected,re.I):
        # A recognizer can omit the space before a complete printed amount.
        # Bound the repair to a reading that still matches all planned words.
        spaced = GLUED_DOLLAR.sub(r' \g<0>',observed)
        if spaced!=observed and comparison_tokens(spaced)==comparison_tokens(expected):
            return spaced
    labels={}
    if '£' in observed and re.search(r'\bpounds?\b',expected,re.I) and not re.search(r'\bbritish\s+pounds?\b',expected,re.I):
        labels['£']='GBP'
    if ('¥' in observed or '$' in observed) and re.search(r'\byen\b',expected,re.I) and not re.search(r'\byuan\b',expected,re.I):
        if '¥' in observed:labels['¥']='JPY'
        # ASR may insert a currency glyph alongside the spoken unit, as in $700 yen.
        observed=DOLLAR_BEFORE_YEN.sub(r'\1',observed)
    if not labels:return observed

    def read(match):
        sign,symbol,amount=match.groups()
        return money_words(sign+amount.replace(',',''),labels[symbol]) if symbol in labels else match[0]

    return PRINTED_CURRENCY.sub(read,observed)


def literal_digit_run(match):
    sign,count,surface=match.groups()
    count=count.lower()
    width=int(count) if count.isdigit() else SMALL_VALUES[count]
    surface=surface.lower()
    digit=surface[0] if surface[0].isdigit() else DIGIT_PLURALS[surface]
    return sign+' '+' '.join([digit]*width)


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
    if ts[i]=='second' and i and ts[i-1] in ('per','square','cubic'):
        return None
    if ts[i] in ORDINALS and not (previous or '').startswith('#'):return ORDINALS[ts[i]],i+1
    if i+1<len(ts) and ts[i] in TENS_VALUES and ts[i+1] in ORDINALS and 0<ORDINALS[ts[i+1]]<10:return TENS_VALUES[ts[i]]+ORDINALS[ts[i+1]],i+2
    if i+1<len(ts) and ts[i+1] in ('st','nd','rd','th') and len(ts[i])<=12 and re.fullmatch(r'(?:0|[1-9]\d*)',ts[i]):
        value=int(ts[i]);suffix='th' if 10<=value%100<=20 else {1:'st',2:'nd',3:'rd'}.get(value%10,'th')
        if ts[i+1]==suffix:return value,i+2
    return None


def repeated_decimal_digit(ts,i):
    if i+1>=len(ts) or ts[i] not in ('double','triple'):
        return None
    token=ts[i+1]
    digit=SMALL_VALUES.get(token,integer_value(token))
    if digit is None or digit>9:
        return None
    return str(digit)*(2 if ts[i]=='double' else 3)


def cardinal(ts,i):
    if i>=len(ts):return None
    literal=numeric_literal(ts[i]);negative=bool(literal and literal.startswith('-'))
    if literal is not None and integer_value(literal.removeprefix('-')) is None:
        if re.fullmatch(r'-?[0-9]+\.[0-9]+',literal) and (repeat:=repeated_decimal_digit(ts,i+1)):
            return literal+repeat,i+3
        return literal,i+1
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
        while position<len(ts):
            if ts[position] in SMALL_VALUES and SMALL_VALUES[ts[position]]<10:
                digits.append(str(SMALL_VALUES[ts[position]]));position+=1
            elif digits and (repeat:=repeated_decimal_digit(ts,position)):
                digits.append(repeat);position+=2
            else:break
        if digits:value+='.'+''.join(digits);end=position
    return value,end


def calendar_date(tokens,start):
    """Align a named calendar date's date words and ASR date notation."""
    if start+2>=len(tokens):
        return None
    read_day=ordinal(tokens,start+1)
    if read_day is None:
        plain=integer_value(tokens[start+1])
        if plain is None or tokens[start+2]!='|number-boundary|':
            return None
        read_day=plain,start+2
    day,index=read_day
    if not 1<=day<=MONTH_LIMITS[tokens[start]]:
        return None
    if tokens[index:index+1]==['|number-boundary|']:
        index+=1
    if tokens[index:index+1]==['oh'] and index+1<len(tokens) and tokens[index+1] in SMALL_VALUES:
        digit=SMALL_VALUES[tokens[index+1]]
        if digit<10:return [tokens[start],'#ordinal:'+str(day),'#0'+str(digit)],index+2
    year=cardinal(tokens,index)
    if not year or not year[0].isdigit():
        return None
    value,end=year
    if 11<=int(value)<=20 and tokens[index] in SMALL_VALUES:
        tail=end+1 if tokens[end:end+1]==['oh'] else end
        remainder=cardinal(tokens,tail)
        if remainder and remainder[0].isdigit() and int(remainder[0])<100:
            value+=remainder[0].zfill(2)
            end=remainder[1]
    if len(value) not in (2,4):
        return None
    return [tokens[start],'#ordinal:'+str(day),'#'+value],end


MONEY_UNITS = {
    ('us','dollar'): ('us','dollars'),
    ('canadian','dollar'): ('canadian','dollars'),
    ('australian','dollar'): ('australian','dollars'),
    ('new','zealand','dollar'): ('new','zealand','dollars'),
    ('british','pound'): ('british','pounds'),
    ('euro',): ('euros',),
    ('dollar',): ('dollars',),
}
MONEY_AMOUNT=re.compile(r'#-?[0-9]{1,12}(?:\.[0-9]{1,12})?')


def money_tokens(tokens):
    """Compare monetary notation and major/minor wording at equal value."""
    comparable=[]
    i=0
    while i<len(tokens):
        amount_at=i+2 if tokens[i:i+2]==['dollar','sign'] else i
        if amount_at>=len(tokens) or not MONEY_AMOUNT.fullmatch(tokens[amount_at]):
            comparable.append(tokens[i]);i+=1
            continue
        amount=tokens[amount_at]
        end=amount_at+1
        currency=('dollars',) if amount_at>i else None
        for unit,canonical in MONEY_UNITS.items():
            spelling=tuple(tokens[end:end+len(unit)])
            if currency is None and spelling in (unit,canonical):
                currency=canonical
                end+=len(unit)
                break
        if currency is None:
            comparable.append(tokens[i]);i+=1
            continue
        tail=tokens[end:end+3]
        small=('penny','pence') if currency==('british','pounds') else ('cent','cents')
        if '.' not in amount and len(tail)==3 and tail[0]=='and' and re.fullmatch(r'#[0-9]{1,2}',tail[1]) and tail[2] in small:
            amount+='.'+tail[1][1:].zfill(2)
            end+=3
        comparable.extend((amount.rstrip('0').rstrip('.') if '.' in amount else amount,*currency))
        i=end
    return comparable


def comparison_tokens(text):
    from .abbreviations import expand
    from .linguistic import comparison_text
    def amendment(match):
        value = roman(match[2])
        return match[1]+str(value) if value is not None else match[0]

    text=AMENDMENT_LABEL.sub(amendment,text)
    text=comparison_text(LITERAL_DIGIT_RUN.sub(literal_digit_run,isbn_notation(text)))
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
        date=calendar_date(ts,i) if t in MONTH_LIMITS else None
        if date:out.extend(date[0]);i=date[1];continue
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
        elif t=="it's":out.append('its')
        elif t=='alright':out.extend(('all','right'))
        elif t in COMPOUND_SPELLINGS:out.extend(COMPOUND_SPELLINGS[t])
        elif re.fullmatch('n+o+',t):out.append('no')
        else:out.append(SPELLING_VARIANTS.get(t,t))
        i+=1
    return money_tokens(out)

def quantities(ts):return [t[1:] for t in ts if t.startswith('#')]
def negations(ts):return sum(t in ('not','never','no',"ain't") for t in ts)


UNITS=r'(?:centi|milli|kilo)?met(?:er|re)s?|milligrams?|kilograms?|grams?|pounds?|yards?|seconds?|degrees?|inch(?:es)?|feet|foot|cm|mm|mg|kg|lbs?|(?:milli|micro)?volts?|kilopascals?|megabytes?|megabits?|kilobytes?|kilobits?|cups?|teaspoons?|tablespoons?|lit(?:er|re)s?|millilit(?:er|re)s?|celsius|fahrenheit|kelvin|ounces?|hours?|minutes?'
UNIT_WORD=re.compile(UNITS)


def unit_name(word):
    word=word.replace('metre','meter').replace('litre','liter')
    return {'cm':'centimeter','mm':'millimeter','mg':'milligram','kg':'kilogram','lb':'pound',
            'lbs':'pound','inches':'inch','feet':'foot','celsius':'celsius'}.get(word,word.rstrip('s'))


def unit_sites(ref,hyp):
    """Bind a measured value to its adjacent unit and rate denominator."""
    if quantities(ref)!=quantities(hyp):return set()

    def readings(tokens):
        def unit(index):
            power=tokens[index] if index<len(tokens) and tokens[index] in ('square','cubic') else ''
            index+=bool(power)
            if index<len(tokens) and UNIT_WORD.fullmatch(tokens[index]):return (power,unit_name(tokens[index])),index+1
            return (),index

        for index,token in enumerate(tokens):
            if not token.startswith('#'):continue
            numerator,end=unit(index+1)
            denominator=unit(end+1)[0] if numerator and tokens[end:end+1]==['per'] else ()
            yield index,token,(numerator,denominator)

    return {(index,value,expected,observed) for (index,value,expected),(_,_,observed) in zip(readings(ref),readings(hyp))
            if expected!=observed}


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
    # Observers often transcribe a dimension's “by” as x. A written
    # length unit and two complete values own this equivalence.
    span=r'(?<![\w.])[0-9]{1,9}(?:\.[0-9]+)?(?:\s*[x×]\s*[0-9]{1,9}(?:\.[0-9]+)?){1,2}(?=\s*(?:mm|cm|km|m|(?:milli|centi|kilo)?met(?:er|re)s?)\b)'
    text=re.sub(span,lambda match:re.sub(r'\s*[x×]\s*',' by ',match[0]),text)
    text,_,_=expand(text)
    text=text.lower().replace('²',' squared ').replace('³',' cubed ').replace('°',' degrees ')
    text=text.replace('½',' half ').replace('¼',' quarter ').replace('¾',' three quarters ')
    markers=[];ts=comparison_tokens(text)
    markers += ['plus-or-minus']*len(re.findall(r'plus\s+(?:or\s+)?minus|±|\+\s*/\s*-',text))
    markers += ['last-digit-uncertainty']*len(re.findall(
        r'\buncertainty (?:of )?(?:#[0-9]+ )+in (?:the )?(?:final|last) digits?\b',' '.join(ts)))
    markers += ['fraction:'+a+'/'+b for a,b in re.findall(r'\bnumerator #([0-9]+) and denominator #([0-9]+)\b',' '.join(ts))]
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
    markers += ['unit:'+unit_name(w) for w in re.findall(r'\b('+UNITS+r')\b',text)]
    markers += ['denominator:'+unit_name(w) for w in re.findall(r'\bper\s+(?:square\s+|cubic\s+)?('+UNITS+r')\b',text)]
    rates={'percent':r'\b(?:percent|per cent)\b|%', 'per_mille':r'\bper\s+mille\b|‰', 'basis_point':r'\bbasis\s+points?\b'}
    for label,pattern in rates.items():markers += ['rate:'+label]*len(re.findall(pattern,text))
    financial={'dollar sign':'unassigned-dollar','us dollar':'usd','canadian dollar':'cad','australian dollar':'aud',
               'new zealand dollar':'nzd','euro':'eur','british pound':'gbp','dollar':'unassigned-dollar',
               'yen':'jpy','yuan':'cny','penny':'gbp-minor','pence':'gbp-minor'}
    markers += ['currency:'+financial[m[1]] for m in re.finditer(r'\b('+'|'.join(financial)+r')s?\b',text)]
    # Inspect operators independently of matching numbers or aggregate WER.
    phrases={'greater than or equal to':'ge','less than or equal to':'le','greater than':'gt','less than':'lt'}
    operators=re.compile('|'.join(phrases))
    markers += ['comparison:'+phrases[m[0]] for m in operators.finditer(text)]
    for i,t in enumerate(ts):
        if t=='power' and i+1<len(ts) and ts[i+1].startswith('#'):markers.append('exponent:'+ts[i+1][1:])
        if t in ('subscript','subscripts','superscript','superscripts') and i+1<len(ts):
            value=ts[i+1]
            role=t.removesuffix('s')
            if value.startswith('#') or re.fullmatch('[a-z]',value):markers.append(role+':'+value.removeprefix('#'))
            if value=='capital' and i+2<len(ts) and re.fullmatch('[a-z]',ts[i+2]):markers.append(role+':capital:'+ts[i+2])
        if t=='bar' and i and i+1<len(ts) and ts[i+1] in ('slash','subscript','subscripts','superscript','superscripts'):
            markers.append('bar:'+ts[i-1])
        if t=='to' and i and i+1<len(ts) and ts[i-1].startswith('#') and ts[i+1].startswith('#'):markers.append('numeric-range')
        if t=='by' and i and i+2<len(ts) and ts[i-1].startswith('#') and ts[i+1].startswith('#') and UNIT_WORD.fullmatch(ts[i+2]):markers.append('dimension-axis')
        if t in ('a','p') and i and ts[i+1:i+2]==['m']:
            previous=ts[i-1] if ts[i-1]!="o'clock" or i<2 else ts[i-2]
            if previous.startswith('#'):markers.append('meridiem:'+t)
    return dict(sorted(Counter(markers).items()))
