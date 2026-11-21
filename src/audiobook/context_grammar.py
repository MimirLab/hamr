"""Bounded candidates for exact written forms. Scoring lives outside the grammar."""
from datetime import date
import re

from . import abbreviations, boundaries, context_forms, context_lexical, context_numbers
from .context_candidate import Candidate, Component, exact_components, literal, number_spoken, numeric_fields
from .context_forms import MONTHS, SYMBOLS, UNIT_NAMES, currency_name, money_words
from .linguistic import UNITS as TECHNICAL_UNITS
from .spoken_forms import identifier, number, ordinal
from .typed_readings import clock_words

LEFT = r'(?<![\w./:@#$£€+-])(?<![0-9],)'
RIGHT = r'(?!\w|[.,:/@-]\w|[$£€])'
AMOUNT = r'[+−-]?[0-9]{1,12}(?:[.,][0-9]{1,12})?'
SLASH_DATE = r'[0-9]{1,2}/[0-9]{1,2}/(?:[0-9]{4}|[0-9]{2})'
CLOCK_SUFFIX = r'(?:UTC|GMT)(?![+−-][0-9])|[AP]M|[aApP]\.[ \t]*[mM]\.?(?!\.?\w)'
CLOCK_CORE = r'([0-9]{1,3}):([0-9]{1,3})(?:[ \t]*(' + CLOCK_SUFFIX + '))?'
CLOCK = re.compile(CLOCK_CORE)
CLOCK_ZONE = re.compile(r'\s+(?:UTC|GMT|[ECMP][SD]T)\b')
UNITS = {**TECHNICAL_UNITS, **UNIT_NAMES}
COMPOSITES = context_forms.patterns(LEFT, RIGHT, MONTHS)
MEASUREMENT = re.compile(rf'({context_numbers.MEASUREMENT_AMOUNT})[^\S\r\n]*(.+)')
MEASUREMENT_PEER = re.compile(rf'^[ \t]*,[ \t]*{context_numbers.MEASUREMENT_AMOUNT}[^\S\r\n]+' +
                              '(?:' + '|'.join(context_numbers.SI_SYMBOLS) + '|°[CF])' + RIGHT)
MEASUREMENT_PREVIOUS = re.compile(LEFT + context_numbers.MEASUREMENT_AMOUNT + r'[^\S\r\n]+(?:m|' +
                                  '|'.join(context_numbers.SI_SYMBOLS) + r'|°[CF])[ \t]*,[ \t]*(?:and[ \t]+)?$')
DURATION_UNITS = {'minutes_seconds':('minutes','seconds'),'hours_minutes':('hours','minutes')}
DURATION_FIELDS = {'mm:ss':'minutes_seconds','minutes:seconds':'minutes_seconds',
                   'hh:mm':'hours_minutes','hours:minutes':'hours_minutes'}
DURATION_DECLARATION = re.compile(r'[ \t]+\(('+'|'.join(DURATION_FIELDS)+r')\)$',re.I)
LITERAL_SIGNS = {'$':'dollar sign','€':'euro sign','£':'pound sign','/':'slash','+':'plus sign',
                 '-':'hyphen','−':'minus sign',',':'comma','.':'dot'}
PATTERNS = {
    'date_iso': LEFT + r'[0-9]{4}-[0-9]{2}-[0-9]{2}' + RIGHT,
    **{name: expression for name, (_, expression) in COMPOSITES.items() if name!='signed'},
    'money_modifier': LEFT + r'[$£€][0-9]{1,12}(?:\.[0-9]{1,12})?[ \t]+(?i:{price_nouns})' + RIGHT,
    'money': LEFT + r'(?:[+−-]?[$£€][ \t]*[0-9]{1,12}(?:[.,][0-9]{1,12})?)' + RIGHT,
    'slash': LEFT + r'(?:' + SLASH_DATE + r'|[0-9]{1,2}/[0-9]{1,2})' + RIGHT,
    'colon': LEFT + CLOCK_CORE + RIGHT,
    'parentheses': r'\([ \t]*[0-9]{1,9}(?:[.,][0-9]{1,12})?[ \t]*\)',
    'measurement': LEFT + context_numbers.MEASUREMENT_AMOUNT + r'(?:[^\S\r\n]+(?:'+context_forms.UNIT_PATTERN+r')|[^\S\r\n]*(?:' + '|'.join(map(re.escape, TECHNICAL_UNITS)) + '))' + RIGHT,
    'separators': LEFT + context_numbers.PUNCTUATED + RIGHT,
    'signed': COMPOSITES['signed'][1],
    'abbreviation': r'(?<![\w.@/])(?i:{lexicon})(?![\w./@-])',
    'integer': LEFT + r'[0-9]{1,12}' + RIGHT,
}

def expressions(lexicon, initialisms, price_nouns, native_names=()):
    fields=dict(context_lexical.expressions(LEFT,RIGHT,initialisms,native_names))
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


def literal_notation(raw):
    fields={'reading_mode':'characters','exact_characters':raw,'metalinguistic':True}
    if '$' in raw:
        fields['dollar_sign_is_named']=True
    if repeated:=re.fullmatch(r'([$£€])([0-9])\2{2,}',raw):
        sign,digit=repeated.groups()
        word=identifier(digit)
        plural=word+('es' if word=='six' else 's')
        return literal(raw,f'{LITERAL_SIGNS[sign]}, followed by {number(len(raw)-1)} {plural}',fields=fields)
    pieces=re.findall(r'[^\W\d_]+|[^\s]',raw)
    return literal(raw,' '.join(identifier(c) if c.isascii() and c.isdigit() else LITERAL_SIGNS.get(c,c)
                                for c in pieces),fields=fields)


def date_candidate(fields, order, evidence, technical=False):
    a, b, *tail = fields
    month, day = (a,b) if order=='MD' else (b,a)
    year = tail[0] if tail else None
    try:
        calendar_year=2000 if year is None else 2000+int(year) if len(year)==2 else int(year)
        date(calendar_year, int(month), int(day))
    except ValueError:
        return None
    said = MONTHS[int(month)]+' '+ordinal(int(day))
    if year is not None:
        said += ' '+context_forms.year_words(year,technical)
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
    technical=env.setting('narration_style')=='technical'
    sign_spoken = 'minus ' if sign in ('−','-') else 'plus ' if sign else ''
    fallback=(sign_spoken+SYMBOLS[symbol]+' '+number_spoken(digits) if technical or symbol!='$'
              else money_words(sign+digits,None))
    candidates = [literal(raw,fallback,
                          fields={**fields,'currency':None},kind='money_symbol')]
    labelled=env.finance() and env.currency_label in ('USD','CAD','AUD','NZD')
    currency = {'€':'EUR','£':'GBP'}.get(symbol) or (env.currency_label if labelled else env.setting('dollar_currency'))
    if currency:
        origin='symbol' if symbol!='$' else 'source_label' if labelled else 'profile'
        candidates.append(Candidate('money', {**fields,'currency':currency,'currency_origin':origin},
                                   money_words(sign+digits,currency,technical),
                                   (('column' if env.header else 'governor') if labelled and symbol=='$' else 'convention' if symbol=='$' else 'syntax',)))
    return candidates


def money_modifier(raw, env):
    amount,noun=raw.split(maxsplit=1)
    separator=raw[len(amount):len(raw)-len(noun)]
    noun_phrase=env.cue('article')
    options=[]
    for option in monetary(amount,env):
        if noun_phrase and (option.kind=='money' or option.kind=='money_symbol' and env.setting('narration_style')!='technical'):
            label=currency_name(option.fields['currency'],env.setting('narration_style')=='technical')
            fields={**option.fields,'noun':noun,'currency_grammar':'attributive_singular'}
            spoken=number_spoken(option.fields['source_amount'])+' '+label+' '+noun
            options.append(Candidate('money_modifier' if option.kind=='money' else 'money_symbol',fields,spoken,option.evidence))
        else:
            options.append(Candidate(option.kind,{**option.fields,'noun':noun},(option.spoken or amount)+separator+noun,option.evidence))
    return options


def measurement(raw, env):
    amount, unit = MEASUREMENT.fullmatch(raw).groups()
    if env.cue('identifier') or env.header_cue('identifier'):
        return [literal(raw)]
    peer = MEASUREMENT_PEER.match(env.after) if not env.cue('duration') else None
    previous = MEASUREMENT_PREVIOUS.search(env.before) if unit in context_numbers.SI_SYMBOLS else None
    local = env.cue('measurement') or env.cue('temperature') or unit == 'm' and env.cue('length')
    if unit not in TECHNICAL_UNITS and unit not in context_numbers.SI_SYMBOLS and not (unit=='m' and peer or local):
        return []
    joined = any(c in context_numbers.SPACES for c in raw[len(amount):])
    expanded = unit in ('m','K') or bool(peer or previous or local or joined)
    reading = UNITS[unit]
    unit_name = context_forms.singular_unit(reading)
    if peer:
        env.governors.add(('measurement_peer','right',peer[0],env.after_start+peer.start(),env.after_start+peer.end()))
    if previous:
        env.governors.add(('measurement_peer','left',previous[0],env.before_start+previous.start(),env.before_start+previous.end()))

    base = {'amount': amount, 'value': amount, 'unit': unit_name, 'case': unit, 'reading': reading}
    unit_evidence = 'governor' if local or peer or previous else 'syntax'
    if not re.search(r'[.,\u00a0\u2009\u202f]', amount):
        return [Candidate('measurement', base, context_forms.quantity_words(amount,reading) if expanded else None, (unit_evidence,))]
    options=[]
    for choice in context_numbers.candidates(amount, env, measurement=True):
        if choice.kind == 'literal':
            options.append(literal(raw))
            continue
        fields = {**base, 'value':choice.fields['value']}
        part = Component('CR-N004', choice.kind, (0,len(amount)), dict(choice.fields))
        # Amount evidence must settle punctuation before a unit can be voiced.
        evidence = (*choice.evidence, unit_evidence) if choice.evidence else ()
        legacy = (unit=='mL' and choice.kind=='decimal_quantity' and choice.fields['decimal_separator']=='.'
                  and choice.fields.get('group_separator') in (None, ',') and not env.setting('number_locale'))
        spoken = context_forms.quantity_words(fields['value'],reading) if not legacy and (expanded or fields['value']!=amount or env.setting('number_locale')) else None
        options.append(Candidate('measurement', fields, spoken, evidence, (part,)))
    return options


def colon_duration(first, second, base, fallback, env):
    label=env.header if env.header is not None else env.before.rstrip(' \t:')
    match=DURATION_DECLARATION.search(label)
    declared=None
    if match and env.resolver.cues['duration']['left'].search(label[:match.start()]):
        declared=DURATION_FIELDS[match[1].casefold()]
        if env.header is None:
            env.governors.add(('duration_field_order','left',match[1],
                               env.before_start+match.start(1),env.before_start+match.end(1)))
        env.duration_label=(env.before_start+match.start(),env.before_start+match.end()) if env.header is None else match.span()
    origin = ('column' if env.header is not None else 'governor') if declared else (
        'column' if env.header_cue('duration') else 'governor' if env.cue('duration') else None)
    if not origin:
        return []
    neutral=Candidate('duration',{**base,'field_order':None},fallback.spoken,(origin,))
    if int(second)>=60:
        return [neutral]
    convention=env.setting('duration_fields')
    supported = {declared:origin} if declared else {convention:'convention'} if convention else {}
    options = []
    for order in supported or DURATION_UNITS:
        units = dict(zip(DURATION_UNITS[order],(first,second)))
        options.append(Candidate('duration',{**base,'field_order':order,**units},
                                 context_forms.duration_words(units),(supported.get(order,origin),)))
    return options if len(supported)==1 else [neutral,*options]


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
            return [Candidate('component_identifier',{'components':[year,month,day],'exact':raw},
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
        if len(fields)==2 and int(fields[1])>0 and (unit:=env.cue('fraction_unit', side='right')):
            numerator,denominator=map(int,fields)
            units={2:('half','halves'),3:('third','thirds'),4:('quarter','quarters'),8:('eighth','eighths')}
            said=number(numerator)+' '+units[denominator][numerator!=1] if denominator in units else number(numerator)+' divided by '+number(denominator)
            if 1<numerator<denominator and denominator in units and not unit.casefold().endswith('s') and env.setting('narration_style')!='technical':
                said+=' of an' if unit[0].casefold() in 'aeiou' else ' of a'
            options.append(Candidate('fraction',{'numerator':fields[0],'denominator':fields[1],'bound_unit':unit},said,('governor',)))
        quote=env.quoted_date_order()
        nearby_date=env.cue('date_link') and env.nearby('date_topic')
        if env.cue('date') or env.header_cue('date_column') or nearby_date or quote or env.chronology:
            role=('column' if env.header_cue('date_column') else
                  'nearby' if nearby_date and not env.cue('date') and not env.chronology else 'governor')
            order=env.setting('date_order')
            inferred=env.inferred_order
            valid=[value for value in ('MD','DM') if date_candidate(fields,value,())]
            technical=env.setting('narration_style')=='technical'
            if quote in valid:
                entry=date_candidate(fields,quote,('definition',),technical)
                return [Candidate(entry.kind,{**entry.fields,'date_order_evidence':'explicit_quote_scope'},entry.spoken,entry.evidence)]
            if not any(option.kind=='fraction' for option in options):
                options=[literal(raw,fallback.spoken,kind='date',fields={**fallback.fields,'class':'date',
                                 'day_month_order':'retain_alternatives_if_not_established','month':None,'day':None,
                                 'year':fields[2] if len(fields)==3 else None,'class_confidence_separate_from_field_confidence':True})]
            for value in valid:
                supports=(role, *(('convention',) if order==value else ()), *(('anchors',) if inferred==value else ()))
                if len(valid)==1:
                    supports += ('syntax',)
                options.append(scoped_date(date_candidate(fields,value,supports,technical),raw,env))
        elif len(fields)==3 and len(fields[-1])==2:
            return []
        return options
    if kind=='colon':
        clock=CLOCK.fullmatch(raw)
        first,second,written_suffix=clock.groups()
        suffix=written_suffix.translate(str.maketrans('', '', '. \t')).upper() if written_suffix else None
        numeric=raw[:clock.end(2)]
        base={'components':[first,second],'separator':':'}
        fallback=literal(raw,exact_components(numeric,':','colon')+((' '+written_suffix) if written_suffix else ''),fields=base)
        if env.cue('ratio') or env.cue('ratio',side='right'):
            return [Candidate('ratio',{'left':first,'right':second},number_spoken(first)+' to '+number_spoken(second),('governor',))]
        if env.cue('reference'):
            return [Candidate('reference',base,fallback.spoken,('governor',))]
        if suffix is None and (durations:=colon_duration(first,second,base,fallback,env)):
            return durations
        if (suffix or env.cue('clock')) and len(second)==2 and int(first)<24 and int(second)<60 and (suffix not in ('AM','PM') or 1<=int(first)<=12):
            fields={'hour':first,'minute':second,'zone':suffix if suffix in ('UTC','GMT') else None}
            if suffix in ('AM','PM'):
                fields['meridiem']=suffix
                part=Component('CR-N016','clock_time',(0,len(numeric)),dict(fields))
                following=env.after.lstrip()
                stop=written_suffix.endswith('.') and (not following or following[0].isupper() or following[0] in '"”’)]')
                punctuation='.' if stop and not CLOCK_ZONE.match(env.after) else ''
                return [Candidate('clock_time',fields,clock_words(first,second)+' '+' '.join(suffix)+punctuation,('syntax',),(part,))]
            # The existing formatter still owns unsuffixed clocks and UTC/GMT.
            return [Candidate('clock_time',fields,None,('syntax',))]
        return [fallback]
    if kind=='parentheses':
        amount=raw[1:-1].strip()
        if ',' in amount:
            return [literal(raw)]
        if env.setting('accounting_parentheses') is True and env.finance():
            fields={**numeric_fields(amount),'amount':amount,'sign':'negative','notation':'parentheses',
                    'currency':env.currency_label,'accounting_convention':'parentheses_negative'}
            fields['integer_digits'],_,fields['fraction_digits']=amount.partition('.')
            return [Candidate('accounting_negative',fields,'minus '+number_spoken(amount),('convention','column' if env.header else 'governor'))]
        return [Candidate('parenthetical_quantity',{'amount':amount,'integer_digits':amount if amount.isdigit() else None,'aside':True,'sign':'not_negative'},None,('syntax',))]
    if kind=='measurement':
        return measurement(raw,env)
    if kind=='separators':
        dotted=context_numbers.DECIMAL_SURFACE.fullmatch(raw)
        if env.cue('version'):
            if not dotted or not dotted[1] or dotted[2]!='.':
                return [literal(raw)]
            first,_,second=dotted.groups()
            said=number_spoken(first)+' point '+(identifier(second) if second.startswith('0') else number_spoken(second))
            fields={'components':[first,second],'role':'version','preserve_component_zeroes':True}
            kind='version' if env.cue('version_label') else 'component_identifier'
            return [Candidate(kind,fields,said,('governor',))]
        if env.header_cue('identifier') or env.cue('identifier') and not context_numbers.PLAIN_POINT.fullmatch(raw):
            return [literal(raw)]
        return context_numbers.candidates(raw,env)
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
            unit=currency_name(currency,env.setting('narration_style')=='technical',plural=True) if currency else ''
            return [Candidate('scaled_quantity',fields,' '.join(s for s in (number_spoken(raw),word,unit) if s),
                              ('column',),components)]
        if env.header and env.finance() and env.currency_label:
            return [Candidate('money',{'amount':raw,'currency':env.currency_label,'currency_origin':'column_header'},
                              money_words(raw,env.currency_label,env.setting('narration_style')=='technical'),('column',))]
        if env.header_cue('identifier'):
            role=env.header.casefold().strip().replace(' ','_')
            return [Candidate('identifier',{'digits':raw,'role':role},identifier(raw),('column',))]
        if env.cue('identifier') or raw.startswith('0') and len(raw)>1:
            role=next((cue[2].casefold() for cue in sorted(env.governors) if cue[0]=='identifier'),None)
            return [Candidate('identifier',{'digits':raw,'role':role},identifier(raw),('governor' if role else 'syntax',))]
        if noun:=env.cue('count', side='right'):
            return [Candidate('cardinal_quantity',{'integer_digits':raw,'noun':noun},None,('governor',))]
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
