"""Bounded candidates for exact written forms. Scoring lives outside the grammar."""
from datetime import date
import re

from . import abbreviations, boundaries, context_forms, context_lexical
from .context_candidate import Candidate, Component, exact_components, literal, number_spoken, numeric_fields
from .context_forms import CURRENCIES, MONTHS, SYMBOLS, UNIT_NAMES
from .linguistic import UNITS as TECHNICAL_UNITS
from .spoken_forms import identifier, number, ordinal
from .typed_readings import clock_words

LEFT = r'(?<![\w./:@#$£€+-])(?<![0-9],)'
RIGHT = r'(?!\w|[.,:/@-]\w|[$£€])'
AMOUNT = r'[+−-]?[0-9]{1,12}(?:[.,][0-9]{1,12})?'
CLOCK = re.compile(r'([0-9]{1,3}):([0-9]{1,3})(?:[ \t]*(UTC|GMT|AM|PM))?')
UNITS = {**TECHNICAL_UNITS, **UNIT_NAMES}
COMPOSITES = context_forms.patterns(LEFT, RIGHT, MONTHS)
PATTERNS = {
    'date_iso': LEFT + r'[0-9]{4}-[0-9]{2}-[0-9]{2}' + RIGHT,
    **{name: expression for name, (_, expression) in COMPOSITES.items() if name!='signed'},
    'money_modifier': LEFT + r'[$£€][0-9]{1,12}(?:\.[0-9]{1,12})?[ \t]+(?i:{price_nouns})' + RIGHT,
    'money': LEFT + r'(?:[+−-]?[$£€][ \t]*[0-9]{1,12}(?:[.,][0-9]{1,12})?)' + RIGHT,
    'slash': LEFT + r'[0-9]{1,2}/[0-9]{1,2}(?:/[0-9]{4})?' + RIGHT,
    'colon': LEFT + r'[0-9]{1,3}:[0-9]{1,3}(?:[ \t]*(?:(?:UTC|GMT)(?![+−-][0-9])|AM|PM))?' + RIGHT,
    'parentheses': r'\([ \t]*[0-9]{1,9}(?:[.,][0-9]{1,12})?[ \t]*\)',
    'measurement': LEFT + AMOUNT + r'(?:[ \t]+(?:'+context_forms.UNIT_PATTERN+r')|[ \t]*(?:' + '|'.join(map(re.escape, TECHNICAL_UNITS)) + '))' + RIGHT,
    'separators': LEFT + r'[+−-]?[0-9]{1,12}[.,][0-9]{1,12}' + RIGHT,
    'signed': COMPOSITES['signed'][1],
    'abbreviation': r'(?<![\w.@/])(?i:{lexicon})(?![\w./@-])',
    'integer': LEFT + r'[0-9]{1,12}' + RIGHT,
}

def expressions(lexicon, initialisms, price_nouns):
    fields=dict(context_lexical.expressions(LEFT,RIGHT,initialisms))
    # A dash between two numbers belongs to the numeric expression.
    fields={'date_iso':PATTERNS['date_iso'], **PATTERNS, **fields}
    fields['abbreviation']=PATTERNS['abbreviation'].format(lexicon='|'.join(map(re.escape,lexicon)))
    fields['money_modifier']=PATTERNS['money_modifier'].replace('{price_nouns}','|'.join(map(re.escape,price_nouns)) or r'(?!)')
    return re.compile('|'.join(f'(?P<{kind}>{pattern})' for kind, pattern in fields.items()))


EVENTS = {**{name: event for name, (event, _) in (COMPOSITES|context_lexical.PATTERNS|context_lexical.OPAQUE_PATTERNS).items()}, 'money':'CR-N012', 'money_modifier':'CR-N041', 'date_iso':'CR-N023', 'slash':'CR-N008', 'colon':'CR-N021',
          'parentheses':'CR-N007', 'measurement':'CR-N017', 'separators':'CR-N004',
          'abbreviation':'CR-L002', 'integer':'CR-N001'}
PROTECTED_PATH = re.compile(context_lexical.PATH)
KIND_EVENTS = {'explicit_cardinal':'CR-N003','signed_quantity':'CR-N006','ratio':'CR-N016',
               'year':'CR-N024','address_component':'CR-N028','version':'CR-N029',
               'money_modifier':'CR-N041','literal_character_sequence':'CR-L001','noun_stress':'CR-L009',
               'verb_stress':'CR-L009','literal_cli_syntax':'CR-L022'}
RELATED_EVENTS = {'CR-N003','CR-N005','CR-N006','CR-N010','CR-N016','CR-N024','CR-N025','CR-N029','CR-N038','CR-N040','CR-N047','CR-N052'}
COMPONENT_EVENTS = {'CR-N006','CR-N016','CR-N036','CR-N038','CR-N050','CR-L001','CR-L006','CR-L019','CR-L022','CR-L038'}


def related_events(kind,raw,env,selected):
    form,fields=selected['kind'], selected['fields']
    events={KIND_EVENTS[form]} if form in KIND_EVENTS else set()
    if kind=='integer' and len(raw)>1 and raw.startswith('0'):events.add('CR-N003')
    if kind=='separators' and '.' in raw:
        events.update(('CR-N005','CR-N029'))
    if form=='binary_operation' and fields['operator']=='subtract':
        events.add('CR-N006')
        if '-' in raw:events.add('CR-N010')
    if form=='clock_time' and fields.get('meridiem'):events.add('CR-N016')
    if kind=='slash' and form=='identifier':events.add('CR-N025')
    if kind=='integer' and env.header and (form=='money' or env.header_cue('identifier')):events.add('CR-N038')
    if fields.get('reading_mode')=='characters' and kind in ('money','money_modifier','slash'):events.add('CR-N040')
    if fields.get('origin')=='header' and 'scale' in fields:events.add('CR-N047')
    if form=='measurement' and fields.get('unit')=='kelvin':events.add('CR-N052')
    if kind=='integer' and len(raw)==4 and form=='cardinal_quantity':events.add('CR-N024')
    return sorted(events-{EVENTS[kind]})


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


def scoped_date(candidate, raw, env):
    if len({value for _,value in env.date_conventions})<2:
        return candidate
    lineage=env.date_conventions
    scope=env.origins.get('date_order')
    precedence={'chapter policy':'explicit_section_over_book_default','table policy':'explicit_table_convention',
                'document policy':'explicit_document_convention'}[scope]
    fields={'selected_date_order':candidate.fields['date_order'],'evidence_precedence':precedence,
            'preserve_source':raw,'date_order_lineage':lineage}
    part=Component('CR-N050','best_supported_candidate',(0,len(raw)),fields)
    return Candidate(candidate.kind,candidate.fields,candidate.spoken,candidate.evidence,(part,))


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


def money_modifier(raw, env):
    amount,noun=raw.split(maxsplit=1)
    separator=raw[len(amount):len(raw)-len(noun)]
    noun_phrase=env.cue('article')
    options=[]
    for option in monetary(amount,env):
        if option.kind=='money' and noun_phrase:
            fields={**option.fields,'noun':noun,'currency_grammar':'attributive_singular'}
            spoken=number_spoken(option.fields['source_amount'])+' '+CURRENCIES[fields['currency']]+' '+noun
            options.append(Candidate('money_modifier',fields,spoken,option.evidence))
        else:
            options.append(Candidate(option.kind,{**option.fields,'noun':noun},(option.spoken or amount)+separator+noun,option.evidence))
    return options


def candidates(kind, raw, env):
    if kind in context_lexical.PATTERNS:
        return context_lexical.candidates(kind, raw, env)
    if kind in COMPOSITES:
        return context_forms.expand(kind, raw, env)
    if kind=='money':
        return monetary(raw,env)
    if kind=='money_modifier':
        return money_modifier(raw,env)
    if kind=='date_iso':
        year,month,day=raw.split('-')
        if env.cue('identifier'):
            return [Candidate('component_identifier',{'components':[year,month,day]},
                              exact_components(raw,'-','dash'),('governor',))]
        reading = date_candidate([month,day,year],'MD',('syntax',))
        fields = {'components':[year,month,day],'date_order':'YMD','year':year,'month':month,'day':day,
                  'valid_calendar_date':reading is not None,'preserve_authored_components':True,
                  'calendar_validity':'valid' if reading else 'invalid_authored_value'}
        # An ISO-shaped build could be an identifier. With no owning date cue
        # retain components and avoid assigning a calendar meaning by shape alone.
        if env.cue('date') or env.header_cue('date_column') or env.setting('date_order')=='YMD':
            return [Candidate('date',fields,reading.spoken if reading else exact_components(raw,'-','dash'),('governor',))]
        return [literal(raw)]
    if kind=='slash':
        fields=raw.split('/')
        fallback=literal(raw,exact_components(raw,'/','slash'),fields={'components':fields})
        if env.cue('identifier'):
            return [Candidate('identifier',{'components':fields,'separator':'/','exact':raw},fallback.spoken,('governor',))]
        options=[fallback]
        if len(fields)==2 and int(fields[1])>0 and env.cue('fraction_unit', side='right'):
            numerator,denominator=map(int,fields)
            units={2:('half','halves'),3:('third','thirds'),4:('quarter','quarters'),8:('eighth','eighths')}
            said=number(numerator)+' '+units[denominator][numerator!=1] if denominator in units else number(numerator)+' divided by '+number(denominator)
            unit=next(cue[2] for cue in env.governors if cue[0]=='fraction_unit')
            options.append(Candidate('fraction',{'numerator':fields[0],'denominator':fields[1],'bound_unit':unit},said,('governor',)))
        quote=env.quoted_date_order()
        nearby_date=env.cue('date_link') and env.nearby('date_topic')
        if env.cue('date') or env.header_cue('date_column') or nearby_date or quote:
            role='column' if env.header_cue('date_column') else 'nearby' if nearby_date and not env.cue('date') else 'governor'
            order=env.setting('date_order')
            inferred=env.inferred_order
            valid=[value for value in ('MD','DM') if date_candidate(fields,value,())]
            if quote in valid:
                entry=date_candidate(fields,quote,('definition',))
                return [Candidate(entry.kind,{**entry.fields,'date_order_evidence':'explicit_quote_scope'},entry.spoken,entry.evidence)]
            if not any(option.kind=='fraction' for option in options):
                options=[literal(raw,fallback.spoken,kind='date',fields={**fallback.fields,'class':'date',
                                 'day_month_order':'retain_alternatives_if_not_established','month':None,'day':None,
                                 'year':fields[2] if len(fields)==3 else None,'class_confidence_separate_from_field_confidence':True})]
            for value in valid:
                supports=(role, *(('convention',) if order==value else ()), *(('anchors',) if inferred==value else ()))
                if len(valid)==1:
                    supports += ('syntax',)
                options.append(scoped_date(date_candidate(fields,value,supports),raw,env))
        return options
    if kind=='colon':
        clock=CLOCK.fullmatch(raw)
        first,second,suffix=clock.groups()
        numeric=raw[:clock.end(2)]
        base={'components':[first,second],'separator':':'}
        fallback=literal(raw,exact_components(numeric,':','colon')+((' '+suffix) if suffix else ''),fields=base)
        if env.cue('ratio') or env.cue('ratio',side='right'):
            return [Candidate('ratio',{'left':first,'right':second},number_spoken(first)+' to '+number_spoken(second),('governor',))]
        if env.cue('reference'):
            return [Candidate('reference',base,fallback.spoken,('governor',))]
        if env.cue('duration') or env.header_cue('duration'):
            duration=env.setting('duration_fields')
            if duration and int(second)<60 and suffix is None:
                units=('minutes','seconds') if duration=='minutes_seconds' else ('hours','minutes')
                return [Candidate('duration',{**base,'field_order':duration},number(int(first))+' '+units[0]+' '+number(int(second))+' '+units[1],('convention',))]
            return [Candidate('duration',base,fallback.spoken,('governor',))]
        if (suffix or env.cue('clock')) and len(second)==2 and int(first)<24 and int(second)<60 and (suffix not in ('AM','PM') or 1<=int(first)<=12):
            fields={'hour':first,'minute':second,'zone':suffix if suffix in ('UTC','GMT') else None}
            if suffix in ('AM','PM'):
                fields['meridiem']=suffix
                part=Component('CR-N016','clock_time',(0,len(numeric)),dict(fields))
                return [Candidate('clock_time',fields,clock_words(first,second)+' '+' '.join(suffix),('syntax',),(part,))]
            # The existing formatter still owns unsuffixed clocks and UTC/GMT.
            return [Candidate('clock_time',fields,None,('syntax',))]
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
        if unit not in TECHNICAL_UNITS and not (env.cue('measurement') or env.cue('temperature') or unit in ('mL','kg','km','cm','mm','mg')):
            return []
        spoken=number_spoken(amount)+' kelvin' if unit=='K' else None
        reading=UNITS[unit]
        return [Candidate('measurement',{'amount':amount,'value':amount,'unit':reading.removesuffix('s'),'case':unit,'reading':reading},spoken,('syntax',))]
    if kind=='separators':
        sign, first, separator, second=re.fullmatch(r'([+−-]?)([0-9]+)([.,])([0-9]+)',raw).groups()
        if separator=='.' and not sign and env.cue('version'):
            said=number_spoken(first)+' point '+(identifier(second) if second.startswith('0') else number_spoken(second))
            fields={'components':[first,second],'role':'version','preserve_component_zeroes':True}
            kind='version' if env.cue('version_label') else 'component_identifier'
            return [Candidate(kind,fields,said,('governor',))]
        locale=env.setting('number_locale')
        decimal_separator='.' if locale in ('en-US','en-GB') else ',' if locale else None
        option=Candidate('decimal_quantity',{'whole':first,'fraction':second,'integer_digits':first,'fraction_digits':second,
                                              'scale':len(second),'sign':sign,'separator':separator},
                         number_spoken(sign+first+'.'+second),('convention',) if separator==decimal_separator else ())
        readings=[literal(raw),option]
        if len(first)<=3 and len(second)==3 and not first.startswith('0'):
            readings.append(Candidate('grouped_integer',{'groups':[first,second],'sign':sign,'separator':separator},
                                      number_spoken(sign+first+second),('convention',) if decimal_separator and separator!=decimal_separator else ()))
        if not locale and separator=='.' and (second.endswith('0') or len(second)!=3):
            # The earlier qualified path protects visible decimal scale when
            # the user has not selected a competing number convention.
            return [Candidate('decimal_quantity',option.fields,None,('fallback',))]
        return readings
    if kind=='integer':
        if env.cue('schema_count') and env.cue('decimal_schema',side='right'):
            return [Candidate('explicit_cardinal',{'integer_value':str(int(raw)),'source_digits':raw},number_spoken(str(int(raw))),('governor',))]
        if env.cue('address_label'):
            return [Candidate('address_component',{'digits':raw,'component':'building'},None,('governor',))]
        if len(raw)==4 and not raw.startswith('0') and env.cue('year'):
            return [Candidate('year',{'year':raw},None,('governor',))]
        scale=env.scale_label()
        if scale:
            currency,word,multiplier=scale
            fields={'coefficient':raw,'coefficient_digits':raw,'scale':word,'scale_multiplier':multiplier,
                    'currency':currency,'origin':'header','header':env.header,'header_source':env.header_source}
            components=(Component('CR-N038','money',(0,len(raw)),dict(fields)),) if currency else ()
            return [Candidate('scaled_quantity',fields,number_spoken(raw)+' '+word+((' '+CURRENCIES[currency]+'s') if currency else ''),
                              ('column',),components)]
        if env.header and env.finance() and env.currency_label:
            return [Candidate('money',{'amount':raw,'currency':env.currency_label,'currency_origin':'column_header'},
                              number_spoken(raw)+' '+CURRENCIES[env.currency_label]+('' if raw=='1' else 's'),('column',))]
        if env.header_cue('identifier'):
            role=env.header.casefold().strip().replace(' ','_')
            return [Candidate('identifier',{'digits':raw,'role':role},identifier(raw),('column',))]
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
