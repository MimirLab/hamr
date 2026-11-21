"""Bounded written-to-spoken technical forms; no compatibility normalization.

Keep source/raw ASR outside this module. NFC composes accents; explicit rules
handle typography whose mathematical/case distinctions must survive comparison.
"""
import re
import unicodedata

LIGATURES=str.maketrans({'ﬀ':'ff','ﬁ':'fi','ﬂ':'fl','ﬃ':'ffi','ﬄ':'ffl','ﬅ':'st','ﬆ':'st'})
SUPER=str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻','0123456789+-')
SUB=str.maketrans('₀₁₂₃₄₅₆₇₈₉','0123456789')
FRACTIONS={'½':'one half','¼':'one quarter','¾':'three quarters',
           '⅓':'one third','⅔':'two thirds','⅛':'one eighth','⅜':'three eighths',
           '⅝':'five eighths','⅞':'seven eighths'}
UNITS={'MB/s':'megabytes per second','Mb/s':'megabits per second',
       'MB':'megabytes','Mb':'megabits','kB':'kilobytes','kb':'kilobits',
       'mV':'millivolts','µV':'microvolts','μV':'microvolts','kPa':'kilopascals'}
COMPARATORS={'>=':'greater than or equal to','<=':'less than or equal to',
             '≥':'greater than or equal to','≤':'less than or equal to',
             '>':'greater than','<':'less than'}
NUMBER=r'[+-]?\d+(?:\.\d+)?'
CONTROL_CUES=re.compile(r'\((sigh|laugh|chuckle|gasp|cough|cry)\)',re.I)


def literal_cues(text):
    return CONTROL_CUES.sub(lambda match:'open parenthesis '+match[1]+' close parenthesis',text)


def safe_unicode(text):
    return unicodedata.normalize('NFC',text).translate(LIGATURES)


def technical(text,preserve_lines=False):
    """Explicit reversible readings of supported notation, before lowercasing.

    Unknown notation stays inspectable. This does not infer chemistry names,
    locale, an unlinked footnote's role, or the meaning of styled superscripts.
    """
    text=safe_unicode(text)
    # Superscripts must be interpreted before any generic digit processing.
    text=re.sub(r'(?<=[\w)])([⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)',
                lambda m:' to the power of '+m[0].translate(SUPER),text)
    text=re.sub(r'([A-Z][A-Za-z]*)([₀₁₂₃₄₅₆₇₈₉]+)',
                lambda m:' '.join(re.findall(r'[A-Z][a-z]?|[a-z]+',m[1]))+' subscript '+m[2].translate(SUB),text)
    text=re.sub('['+''.join(FRACTIONS)+']',
                lambda m:(' and ' if m.start() and text[m.start()-1].isdigit() else ' ')+FRACTIONS[m[0]]+' ',text)
    # Unicode minus is an operator/sign, unlike a lexical or CLI hyphen.
    text=text.replace('−',' minus ')
    text=re.sub(r'(?<![<>=])(?:>=|<=|≥|≤|>|<)(?=\s*(?:[+-]|minus\s+)?\d)',
                lambda m:' '+COMPARATORS[m[0]]+' ',text)
    text=re.sub(r'(?<=\d)[–—](?=[+-]?\d)', ' to ',text)
    units='|'.join(re.escape(x) for x in sorted(UNITS,key=len,reverse=True))
    text=re.sub(r'(?<!\w)('+NUMBER+r')\s*('+units+r')(?![\w/])',
                lambda m:m[1]+' '+UNITS[m[2]],text)
    return re.sub(r'[^\S\n]+' if preserve_lines else r'\s+',' ',text).strip()


def comparison_text(text):
    from .spoken_forms import expand
    from .typed_readings import clock_observation, expand as interpret
    from .typed_readings import clock_words
    meridiem=re.compile(r'(?<![\w.,])([0-9]{1,2})([.:])([0-9]{2})\s*([AP])(?:\.\s*|[ \t]*)M(?!\w)',re.I)
    clock_context=re.compile(r'\b(?:departs at|arrives at|departure|arrival|clock reads|time is|starts at|opens at|closes at)\s*[:=]?\s*$',re.I)
    def clock_with_meridiem(match):
        hour,delimiter,minute,period=match.groups()
        if not 1<=int(hour)<=12 or int(minute)>=60:
            return match[0]
        if delimiter=='.' and not clock_context.search(text[max(0,match.start()-48):match.start()]):
            return match[0]
        return clock_words(hour,minute)+' '+period.upper()+' M'
    text=meridiem.sub(clock_with_meridiem,text)
    text=re.sub(r'(?i)\bat[ \t]+([\w-]+(?:\.[\w-]+)*\.(?:com|org|net|edu|gov))\b',
                lambda match:'at '+match[1].replace('.',' dot '),text)
    # Compact clock digits need both a clock label and a timezone. Bare
    # identifiers remain distinct; no expected script is supplied to ASR.
    def compact_clock(m):
        value=m[2];hour=int(value[:-2]);minute=int(value[-2:])
        return m[1]+f'{hour:02}:{minute:02} '+m[3].upper() if hour<24 and minute<60 else m[0]
    text=re.sub(r'(\bclock\s+(?:reads\s+)?)(\d{3,4})\s+(UTC|GMT)\b',compact_clock,text,flags=re.I)
    def separated_clock(m):
        return m[1]+f'{int(m[2]):02}:{m[3]} '+m[4].upper() if int(m[2])<24 else m[0]
    text=re.sub(r'(\bclock\s+(?:reads\s+)?)(\d{1,2})[:-]([0-5]\d)\s*,?\s+(UTC|GMT)\b',separated_clock,text,flags=re.I)
    text=re.sub(r'(?<![\w/:])[vV]\s*(\d+\.\d+(?:\.\d+)*)(?!\w|\.\w)',r'version \1',text)
    digits={'zero':'0','one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9'}
    digit_words={value:word for word,value in digits.items()}
    text=re.sub(r'(?<![\w./])(\d{1,2})\s+slash\s+(\d{1,2})(?!\w|\.[\w/])',lambda m:' '.join(digit_words[c] for c in m[1])+' slash '+' '.join(digit_words[c] for c in m[2]),text,flags=re.I)
    text=re.sub(r'(?<![\w/])(\d{1,3})-(\d{1,3})(?=\s+ratio\b)',r'\1 to \2',text,flags=re.I)
    atom=r'(?:[0-9]|'+'|'.join(digits)+r')'
    def room(m):return m[1]+''.join(digits.get(t.lower(),t) for t in m[2].split())
    text=re.sub(r'(\bRoom\s+)('+atom+r'(?:\s+'+atom+r'){1,15})(?!\w)',room,text,flags=re.I)
    text=clock_observation(text)
    text,_=interpret(text,kinds={'measurement','currency_symbol'})
    # An observer may format a spoken euro/pound amount with its explicit
    # symbol. Bare dollars retain the existing symbol-only interpretation.
    currencies={'€':'euros','£':'British pounds'}
    text=re.sub(r'(?<![\w$£€])([€£])\s*([0-9]{1,12}(?:\.[0-9]{1,12})?)(?!\w|[.,]\d|[€£$])',
                lambda m:m[2]+' '+currencies[m[1]],text)
    text=re.sub(r'\b(?:U\s+S|US) dollars?\b','US dollars',text,flags=re.I)
    text=expand(text)
    text=technical(text)
    # These are notation variants, not homophone or approximate-value rules.
    text=re.sub(r'\b(?:raised\s+)?to\s+(?:the\s+)?power\s+(?:of\s+)?','power ',text,flags=re.I)
    return text
