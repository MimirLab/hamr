"""Bounded candidates for exact written forms. Scoring lives outside the grammar."""
from dataclasses import dataclass
from datetime import date
import re

from . import abbreviations, boundaries
from .linguistic import UNITS as TECHNICAL_UNITS
from .spoken_forms import identifier, number, ordinal
from .typed_readings import clock_words, decimal

LEFT = r'(?<![\w./:@#$£€+-])(?<![0-9],)'
RIGHT = r'(?!\w|[.,:/@-]\w|[$£€])'
AMOUNT = r'[+−-]?[0-9]{1,12}(?:[.,][0-9]{1,12})?'
UNITS = {**TECHNICAL_UNITS, 'mL': 'milliliters', '°C': 'degrees Celsius', '°F': 'degrees Fahrenheit'}
CURRENCIES = {'USD': 'U S dollar', 'CAD': 'Canadian dollar', 'AUD': 'Australian dollar',
              'NZD': 'New Zealand dollar', 'EUR': 'euro', 'GBP': 'British pound'}
SYMBOLS = {'$': 'dollar sign', '€': 'euro sign', '£': 'pound sign'}
MONTHS = ('', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December')
PATTERNS = {
    'money': LEFT + r'(?:[+−-]?[$£€][ \t]*[0-9]{1,12}(?:[.,][0-9]{1,12})?)' + RIGHT,
    'date_iso': LEFT + r'[0-9]{4}-[0-9]{2}-[0-9]{2}' + RIGHT,
    'slash': LEFT + r'[0-9]{1,2}/[0-9]{1,2}(?:/[0-9]{4})?' + RIGHT,
    'colon': LEFT + r'[0-9]{1,2}:[0-9]{2}(?:[ \t]+(?:UTC|GMT))?' + RIGHT,
    'parentheses': r'\([ \t]*[0-9]{1,9}(?:[.,][0-9]{1,12})?[ \t]*\)',
    'measurement': LEFT + AMOUNT + r'[ \t]*(?:' + '|'.join(map(re.escape, sorted(UNITS, key=len, reverse=True))) + ')' + RIGHT,
    'separators': LEFT + r'[+−-]?[0-9]{1,12}[.,][0-9]{1,12}' + RIGHT,
    'abbreviation': r'(?<![\w.@/])(?i:{lexicon})(?![\w./@-])',
    'integer': LEFT + r'[0-9]{1,12}' + RIGHT,
}

def expressions(lexicon):
    fields={**PATTERNS,'abbreviation':PATTERNS['abbreviation'].format(lexicon='|'.join(map(re.escape,lexicon)))}
    return re.compile('|'.join(f'(?P<{kind}>{pattern})' for kind, pattern in fields.items()))


EVENTS = {'money':'CR-N012', 'date_iso':'CR-N023', 'slash':'CR-N008', 'colon':'CR-N021',
          'parentheses':'CR-N007', 'measurement':'CR-N017', 'separators':'CR-N004',
          'abbreviation':'CR-L002', 'integer':'CR-N001'}
PROTECTED_PATH = re.compile(r'(?<!\w)(?:\.{0,2}/|[A-Za-z]:\\)[^\s<>]+|\b[vV][0-9]+(?:\.[0-9]+)+\b')


@dataclass(frozen=True)
class Candidate:
    kind: str
    fields: dict
    spoken: str | None
    evidence: tuple = ()


def literal(raw, spoken=None, *, fields=None, kind='literal'):
    return Candidate(kind, {'surface': raw, **(fields or {})}, raw if spoken is None else spoken, ('fallback',))


def numeric_fields(raw):
    clean = raw.replace('−','-')
    digits = clean.lstrip('+-')
    return {'source_amount':raw,'sign':clean[0] if clean.startswith(('+','-')) else '',
            'source_digits':digits}


def number_spoken(raw):
    return decimal(raw.replace('−','-'))


def exact_components(raw, separator, spoken_separator):
    return (' '+spoken_separator+' ').join(identifier(field) for field in raw.split(separator))


def date_candidate(fields, order, evidence):
    a, b, *tail = fields
    month, day = (a,b) if order=='MD' else (b,a)
    year = tail[0] if tail else None
    try:
        date(int(year) if year is not None else 2000, int(month), int(day))
    except ValueError:
        return None
    said = MONTHS[int(month)]+' '+ordinal(int(day))
    if year is not None:
        said += ' '+number_spoken(year)
    return Candidate('date',{'components':fields,'date_order':order,'month':month,'day':day,'year':year},said,evidence)


def monetary(raw, env):
    match = re.fullmatch(r'([+−-]?)([$£€])\s*(.+)',raw)
    sign, symbol, digits = match.groups()
    if ',' in digits:
        return [literal(raw)]
    fields = {**numeric_fields(sign+digits),'amount':digits,'currency_symbol':symbol}
    sign_spoken = 'minus ' if sign in ('−','-') else 'plus ' if sign else ''
    candidates = [literal(raw,sign_spoken+SYMBOLS[symbol]+' '+number_spoken(digits),
                          fields={**fields,'currency':None},kind='money_symbol')]
    labelled=env.finance() and env.currency_label in ('USD','CAD','AUD','NZD')
    currency = {'€':'EUR','£':'GBP'}.get(symbol) or (env.currency_label if labelled else env.setting('dollar_currency'))
    if currency:
        singular = digits == '1'
        origin='symbol' if symbol!='$' else 'source_label' if labelled else 'profile'
        candidates.append(Candidate('money', {**fields,'currency':currency,'currency_origin':origin},
                                   number_spoken(sign+digits)+' '+CURRENCIES[currency]+('' if singular else 's'),
                                   (('column' if env.header else 'governor') if labelled and symbol=='$' else 'convention' if symbol=='$' else 'syntax',)))
    return candidates


def candidates(kind, raw, env):
    if kind=='money':
        return monetary(raw,env)
    if kind=='date_iso':
        year,month,day=raw.split('-')
        if env.cue('identifier'):
            return [Candidate('component_identifier',{'components':[year,month,day]},
                              exact_components(raw,'-','dash'),('governor',))]
        reading = date_candidate([month,day,year],'MD',('syntax',))
        fields = {'components':[year,month,day],'date_order':'YMD','year':year,'month':month,'day':day,
                  'valid_calendar_date':reading is not None}
        # An ISO-shaped build could be an identifier. With no owning date cue
        # retain components and avoid assigning a calendar meaning by shape alone.
        if env.cue('date') or env.header_cue('date_column') or env.setting('date_order')=='YMD':
            return [Candidate('date',fields,reading.spoken if reading else exact_components(raw,'-','dash'),('governor',))]
        return [literal(raw)]
    if kind=='slash':
        fields=raw.split('/')
        fallback=literal(raw,exact_components(raw,'/','slash'),fields={'components':fields})
        if env.cue('identifier'):
            return [Candidate('identifier',{'components':fields,'separator':'/'},fallback.spoken,('governor',))]
        options=[fallback]
        if len(fields)==2 and int(fields[1])>0 and env.cue('fraction_unit', side='right'):
            numerator,denominator=map(int,fields)
            units={2:('half','halves'),3:('third','thirds'),4:('quarter','quarters'),8:('eighth','eighths')}
            said=number(numerator)+' '+units[denominator][numerator!=1] if denominator in units else number(numerator)+' divided by '+number(denominator)
            options.append(Candidate('fraction',{'numerator':fields[0],'denominator':fields[1]},said,('governor',)))
        nearby_date=env.cue('date_link') and env.nearby('date_topic')
        if env.cue('date') or env.header_cue('date_column') or nearby_date:
            role='column' if env.header_cue('date_column') else 'nearby' if nearby_date and not env.cue('date') else 'governor'
            order=env.setting('date_order')
            inferred=env.inferred_order
            valid=[value for value in ('MD','DM') if date_candidate(fields,value,())]
            for value in valid:
                supports=(role, *(('convention',) if order==value else ()), *(('anchors',) if inferred==value else ()))
                if len(valid)==1:
                    supports += ('syntax',)
                options.append(date_candidate(fields,value,supports))
        return options
    if kind=='colon':
        pieces=raw.split()
        first,second=pieces[0].split(':')
        base={'components':[first,second],'separator':':'}
        fallback=literal(raw,exact_components(pieces[0],':','colon')+(' '+' '.join(pieces[1]) if len(pieces)>1 else ''),fields=base)
        if env.cue('reference'):
            return [Candidate('reference',base,fallback.spoken,('governor',))]
        if env.cue('duration') or env.header_cue('duration'):
            duration=env.setting('duration_fields')
            if duration and int(second)<60 and len(pieces)==1:
                units=('minutes','seconds') if duration=='minutes_seconds' else ('hours','minutes')
                return [Candidate('duration',{**base,'field_order':duration},number(int(first))+' '+units[0]+' '+number(int(second))+' '+units[1],('convention',))]
            return [Candidate('duration',base,fallback.spoken,('governor',))]
        if (len(pieces)>1 or env.cue('clock')) and int(first)<24 and int(second)<60:
            # Leave known Breeze clock formatting to the existing renderer.
            return [Candidate('clock_time',{'hour':first,'minute':second,'zone':pieces[1] if len(pieces)>1 else None},None,('syntax',))]
        return [fallback]
    if kind=='parentheses':
        amount=raw[1:-1].strip()
        if ',' in amount:
            return [literal(raw)]
        if env.setting('accounting_parentheses') is True and env.finance():
            return [Candidate('accounting_negative',{'amount':amount,'sign':'negative','notation':'parentheses','currency':env.currency_label},'minus '+number_spoken(amount),('convention','column' if env.header else 'governor'))]
        return [Candidate('parenthetical_quantity',{'amount':amount,'integer_digits':amount if amount.isdigit() else None,'aside':True,'sign':'not_negative'},None,('syntax',))]
    if kind=='measurement':
        amount,unit=re.fullmatch('('+AMOUNT+r')\s*(.+)',raw).groups()
        return [Candidate('measurement',{'amount':amount,'unit':unit,'reading':UNITS[unit]},None,('syntax',))]
    if kind=='separators':
        sign, first, separator, second=re.fullmatch(r'([+−-]?)([0-9]+)([.,])([0-9]+)',raw).groups()
        if separator=='.' and len(second)!=3:
            return []
        locale=env.setting('number_locale')
        decimal_separator='.' if locale in ('en-US','en-GB') else ',' if locale else None
        option=Candidate('decimal_quantity',{'whole':first,'fraction':second,'sign':sign,'separator':separator},
                         number_spoken(sign+first+'.'+second),('convention',) if separator==decimal_separator else ())
        readings=[literal(raw),option]
        if len(first)<=3 and len(second)==3 and not first.startswith('0'):
            readings.append(Candidate('grouped_integer',{'groups':[first,second],'sign':sign,'separator':separator},
                                      number_spoken(sign+first+second),('convention',) if decimal_separator and separator!=decimal_separator else ()))
        if not locale and separator=='.' and second.endswith('0'):
            # The earlier qualified path protects visible decimal scale when
            # the user has not selected a competing number convention.
            return [Candidate('decimal_quantity',option.fields,None,('fallback',))]
        return readings
    if kind=='integer':
        if env.cue('identifier') or raw.startswith('0') and len(raw)>1:
            role=next((cue[2].casefold() for cue in sorted(env.governors) if cue[0]=='identifier'),None)
            return [Candidate('identifier',{'digits':raw,'role':role},identifier(raw),('governor' if role else 'syntax',))]
        if env.cue('count', side='right'):
            return [Candidate('cardinal_quantity',{'integer_digits':raw},None,('governor',))]
        return []
    if kind=='abbreviation':
        word=raw.casefold()
        before,after=env.before,env.continuation
        address=abbreviations.ADDRESS.search(before)
        following=boundaries.following(after)
        name=following and following[0].isupper() and following not in boundaries.STARTERS
        fallback=literal(raw,' '.join(raw[:-1].upper())+'.')
        old=boundaries.expand(before+raw+after)[1]
        legacy=next((edit['spoken'] for edit in old if edit['input_start']==len(before)),None)
        stop=boundaries.period_stop(before+raw+after,len(before)+len(raw),raw)
        if word not in ('dr.','st.') or env.definitions:
            stop=not after.strip() or following in boundaries.STARTERS
        options=[fallback]
        for cue,reading in env.resolver.config['lexicon'][word].items():
            intrinsic=cue=='name' and name and not address or cue=='address' and address
            evidence=tuple(fact for condition,fact in ((intrinsic,'syntax'),(env.cue(cue),'governor')) if condition)
            voiced=reading+('.' if stop else '')
            options.append(Candidate('abbreviation',{'reading':reading},None if voiced==legacy else voiced,evidence))
        for reading in sorted({row['reading'] for row in env.definitions}):
            options.append(Candidate('authored_abbreviation',{'reading':reading},reading+('.' if stop else ''),('definition',)))
        return options
    return []
