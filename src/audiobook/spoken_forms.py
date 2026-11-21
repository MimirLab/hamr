"""Conservative, source-preserving readings of explicitly supported forms."""
import re

DIGITS=('zero','one','two','three','four','five','six','seven','eight','nine')
ONES=(*DIGITS,'ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen')
TENS=('','','twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety')
REGNAL_NAME=r'Louis|Henry|Edward|George|Charles|Elizabeth|Victoria|Philip|Pope [A-Z][a-z]+'
REGNAL_NUMBER=re.compile(r'\b('+REGNAL_NAME+r')\s+([IVXLCDM]+)\b')


def number(n):
    if n<20:return ONES[n]
    if n<100:return TENS[n//10]+(' '+ONES[n%10] if n%10 else '')
    if n<1000:return ONES[n//100]+' hundred'+(' '+number(n%100) if n%100 else '')
    return ' '.join(DIGITS[int(c)] for c in str(n))


def identifier(s):return ' '.join(DIGITS[int(c)] for c in s)


def roman(s):
    if not re.fullmatch(r'M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})',s) or not s:return None
    values={'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}
    return sum(-values[c] if i+1<len(s) and values[c]<values[s[i+1]] else values[c] for i,c in enumerate(s))


def ordinal(n):
    fixed={1:'first',2:'second',3:'third',5:'fifth',8:'eighth',9:'ninth',12:'twelfth'}
    if n in fixed:return fixed[n]
    if n<20:return number(n)+'th'
    if n<100:return number(n)[:-1]+'ieth' if n%10==0 else TENS[n//10]+' '+ordinal(n%10)
    return None


def expand(text, *, literal_clocks=True):
    # Match supported context, not every colon or slash in a command/URL.
    text=re.sub(r'\b(Chapter|CHAPTER)\s+([IVXLCDM]+)\b',lambda m:m[1]+' '+number(roman(m[2])) if roman(m[2]) else m[0],text)
    def royal(m):
        n=roman(m[2]);spoken=ordinal(n) if n is not None else None
        return m[1]+' the '+spoken if spoken else m[0]
    text=REGNAL_NUMBER.sub(royal,text)
    text=re.sub(r'(?<![\w/:])(\d{1,3}):(\d{1,3})(?=\s+ratio\b)',lambda m:number(int(m[1]))+' to '+number(int(m[2])),text)
    def clock(m):
        h,minute=int(m[1]),int(m[2]);zone=m[3]
        return number(h)+(' oh '+number(minute) if 0<minute<10 else ' '+number(minute) if minute else ' hundred')+' '+('U T C' if zone=='UTC' else 'G M T')
    text=re.sub(r'(?<![\w/:])([01]\d|2[0-3]):([0-5]\d)\s+(UTC|GMT)\b',clock,text)
    if literal_clocks:
        text=re.sub(r'(?<![\w/:])([01]\d|2[0-3]):([0-5]\d)(?![\w/:])',lambda m:identifier(m[1])+' colon '+identifier(m[2]),text)
    # A measurement unit disambiguates these fractions from calendar dates.
    fractions={2:('half','halves'),3:('third','thirds'),4:('quarter','quarters'),8:('eighth','eighths')}
    def fraction(m):
        numerator,denominator=int(m[1]),int(m[2])
        if denominator not in fractions:return number(numerator)+' divided by '+number(denominator)
        return number(numerator)+' '+fractions[denominator][numerator!=1]
    text=re.sub(r'(?<![\w/])(\d{1,2})/([1-9]\d?)(?=\s+(?:cups?|teaspoons?|tablespoons?|liters?|litres?|pounds?|inches|inch)\b)',fraction,text)
    # Two-part dates stay literal; language alone never chooses month/day.
    text=re.sub(r'(?<![\w/])(\d{1,2})/(\d{1,2})(?![\w/])',lambda m:identifier(m[1])+' slash '+identifier(m[2]),text)
    def section(m):
        components=m[1].split('.');suffix=' '.join('open parenthesis '+s+' close parenthesis' for s in re.findall(r'\(([A-Za-z0-9]+)\)',m[2]))
        return 'section '+' point '.join(number(int(s)) if not (len(s)>1 and s.startswith('0')) else identifier(s) for s in components)+(' '+suffix if suffix else '')
    text=re.sub(r'§\s*(\d+(?:\.\d+)*)(\([A-Za-z0-9]+\)(?:\([A-Za-z0-9]+\))*)?',lambda m:section([m[0],m[1],m[2] or '']),text)
    return text
