"""Composite written forms that must be claimed before their component tokens."""
import re

from calendar import monthrange

from . import context_numbers
from .context_candidate import Candidate, Component, exact_components, literal, number_spoken
from .spoken_forms import DIGITS as DIGIT_WORDS, ONES, TENS, identifier, ordinal, roman

CURRENCIES = {'USD': 'U S dollar', 'CAD': 'Canadian dollar', 'AUD': 'Australian dollar',
              'NZD': 'New Zealand dollar', 'EUR': 'euro', 'GBP': 'British pound'}
SYMBOLS = {'$': 'dollar sign', '€': 'euro sign', '£': 'pound sign'}
MONTHS = ('', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December')

DIGITS = r'[0-9]{1,9}'
DECIMAL = DIGITS + r'(?:\.[0-9]{1,12})?'
SIGNED = r'[+−-]?' + DECIMAL
OPERATORS = {'+':'plus','−':'minus','-':'minus','×':'times','÷':'divided by'}
BOUNDS = {'≥':('greater_or_equal','greater than or equal to'),'>=':('greater_or_equal','greater than or equal to'),
          '≤':('less_or_equal','less than or equal to'),'<=':('less_or_equal','less than or equal to'),
          '>':('greater','greater than'),'<':('less','less than'),'≈':('approximately','approximately')}
RATES = {'%':('percent','percent'),'‰':('per_mille','per mille'),'bp':('basis_point','basis points')}
UNIT_NAMES = {'kg':'kilograms','g':'grams','mg':'milligrams','km':'kilometers','cm':'centimeters',
              'mm':'millimeters','m':'meters','s':'seconds','h':'hours','L':'liters','K':'kelvin',
              'mL':'milliliters','°C':'degrees Celsius','°F':'degrees Fahrenheit','°':'degrees',
              'MB':'megabytes','Mb':'megabits','kB':'kilobytes','kb':'kilobits','V':'volts','mV':'millivolts',
              'µV':'microvolts','μV':'microvolts','kPa':'kilopascals'}
UNIT_PATTERN = '|'.join(map(re.escape, sorted(UNIT_NAMES,key=len,reverse=True)))
UNCERTAINTY = re.compile(rf'({context_numbers.MEASUREMENT_AMOUNT})\(([0-9]{{1,6}})\)'
                         rf'(?:[^\S\r\n]+({UNIT_PATTERN})(?![\w/·^²³]))?')
NAMED_CURRENCIES = r'USD|CAD|AUD|NZD|EUR|GBP'
ELEMENT = r'Na|Cl|Mg|Ca|Fe|Cu|Zn|Si|He|Ne|Ar|Li|Al|K|I|F|H|B|C|N|O|P|S'
ATOMS = re.compile(r'('+ELEMENT+r')([0-9₀-₉]{0,3})')
SUBSCRIPT_DIGITS = str.maketrans('₀₁₂₃₄₅₆₇₈₉','0123456789')
HAN_DIGITS = dict(zip('零一二三四五六七八九','0123456789'))
SCALE_WORDS = {'thousand':1000,'million':10**6,'billion':10**9,'trillion':10**12}
SMALL_WORDS = {word:i for i,word in enumerate(ONES)}
TENS_WORDS = {word:i*10 for i,word in enumerate(TENS) if word}
COEFFICIENT = ('(?:a|'+'|'.join(ONES[1:])+'|(?:'+'|'.join(TENS_WORDS)+
               r')(?:[ -](?:'+'|'.join(DIGIT_WORDS[1:])+'))?)')
OPERATION = re.compile('('+DECIMAL+r')\s*([+−×÷-])\s*('+DECIMAL+r')(?:\s*=\s*('+SIGNED+'))?')


def money_words(amount, currency, technical=False):
    primary=CURRENCIES.get(currency,'dollar')
    singular=amount.lstrip('+−-')=='1'
    major,point,minor=amount.partition('.')
    if not technical and point and len(minor)==2:
        singular=major.lstrip('+−-')=='1'
        spoken=number_spoken(major)+' '+primary+('' if singular else 's')
        cents=int(minor)
        if cents:
            small=('penny' if cents==1 else 'pence') if currency=='GBP' else ('cent' if cents==1 else 'cents')
            spoken+=' and '+number_spoken(str(cents))+' '+small
        return spoken
    return number_spoken(amount)+' '+primary+('' if singular else 's')


def year_words(year, technical=False):
    if len(year)==2:
        return ('oh '+identifier(year[1])) if year.startswith('0') else number_spoken(year)
    value=int(year)
    if not technical and (1100<=value<2000 or 2010<=value<2100):
        century,within=divmod(value,100)
        tail='hundred' if within==0 else ('oh ' if within<10 else '')+number_spoken(str(within))
        return number_spoken(str(century))+' '+tail
    return number_spoken(year)


def singular_unit(label):
    if label.startswith('degrees '):
        return label.replace('degrees ', 'degree ', 1)
    head, sep, tail = label.partition(' per ')
    return head.removesuffix('s') + sep + tail


def quantity_words(value, unit):
    singular = re.fullmatch(r'[+−-]?1(?:\.0+)?', value)
    return number_spoken(value) + ' ' + (singular_unit(unit) if singular else unit)


def duration_words(fields):
    return ' '.join(quantity_words(str(int(value)), unit) for unit, value in fields.items())


def patterns(left,right,months):
    """Order wider owners ahead of their numeric or lexical children."""
    units='(?:'+UNIT_PATTERN+')'
    month='(?:'+'|'.join(months[1:])+')'
    entries=(
        ('date_time','CR-N045',r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})'),
        ('timezone_offset','CR-N026',r'(?:UTC|GMT)[+−-][0-9]{1,2}:[0-9]{2}'),
        ('money_rate','CR-N039',r'\(\s*[$£€]'+DECIMAL+r'\s*[–—-]\s*[$£€]?'+DECIMAL+r'\s*\)\s*/\s*(?:h|hr|min|s)'),
        ('money_range','CR-N042',r'[$£€]'+DECIMAL+r'\s*[–—-]\s*[$£€]?'+DECIMAL),
        ('historical_money','CR-N014',r'£[0-9]{1,6}[ \t]+[0-9]{1,2}s[ \t]+[0-9]{1,2}d'),
        ('coded_money','CR-N013',r'(?:'+NAMED_CURRENCIES+r')[ \t]+'+SIGNED),
        ('telephone','CR-N027',r'[0-9]{3}-[0-9]{3}-[0-9]{4}(?:[ \t]+(?:ext\.?|extension)[ \t]+[0-9]{1,6})?'),
        ('date_range','CR-N044',month+r'[ \t]+[0-9]{1,2}\s*[–—-]\s*[0-9]{1,2}'),
        ('named_date','CR-N025',month+r'[ \t]+[0-9]{1,2}(?:st|nd|rd|th)?(?:,[ \t]+[1-9][0-9]{3})?'),
        ('uncertainty','CR-N034',UNCERTAINTY.pattern),
        ('scientific','CR-N051',SIGNED+r'[eE][+−-]?[0-9]{1,4}'),
        ('duration_units','CR-N022',r'[0-9]{1,5}h[0-9]{1,2}m(?:[0-9]{1,2}s)?'),
        ('coordinate','CR-N033',SIGNED+r'°[NSEW]'),
        ('dimension','CR-N019',DECIMAL+r'(?:\s*×\s*'+DECIMAL+r'){1,2}(?:[ \t]*'+units+r')?'),
        ('measurement_range','CR-N043',SIGNED+r'\s*[–—]\s*'+SIGNED+r'[ \t]*'+units),
        ('compound_unit','CR-N018',SIGNED+r'[ \t]*'+units+r'/'+units),
        ('range','CR-N010',DECIMAL+r'\s*[–—]\s*'+DECIMAL),
        ('bound','CR-N011',r'(?:>=|<=|≥|≤|>|<|≈)[ \t]*'+SIGNED),
        ('formula','CR-N035',OPERATION.pattern),
        ('missing','CR-N037',r'N/A'),
        ('symbolic_fraction','CR-N035',r'[A-Za-z]/[A-Za-z]'),
        ('tuple','CR-N046',r'[\[(]\s*'+SIGNED+r'\s*,\s*'+SIGNED+r'\s*[\])]'),
        ('mixed_fraction','CR-N009',r'(?:[0-9]{1,9}[ \t]+[0-9]{1,2}/[0-9]{1,2}|[0-9]{0,9}[½¼¾⅓⅔⅛⅜⅝⅞])'),
        ('temperature','CR-N020',SIGNED+r'[^\S\r\n]*°[CF]?'),
        ('rate','CR-N015',SIGNED+r'[ \t]*(?:%|‰|bp)'),
        ('radix','CR-N032',r'(?:0[xX][0-9A-Fa-f]{1,32}|0[bB][01]{1,32}|0[oO][0-7]{1,24})'),
        ('version','CR-N029',r'(?:[vV][0-9]{1,8}(?:\.[0-9]{1,8}){1,5}|[0-9]{1,8}(?:\.[0-9]{1,8}){2,5})'),
        ('ordinal','CR-N002',r'[0-9]{1,3}(?:st|nd|rd|th)'),
        ('section_ref','CR-N030',r'§[ \t]*[0-9]{1,8}(?:\.[0-9]{1,8}){0,5}(?:\([A-Za-z0-9]{1,8}\)){0,5}'),
        ('chemical','CR-N054',r'(?=[A-Z][A-Za-z0-9₀-₉]{0,25}[0-9₀-₉])(?:(?:'+ELEMENT+r')[0-9₀-₉]{0,3}){2,10}'),
        ('scale_suffix','CR-N052',DECIMAL+r'[KMB]'),
        ('signed','CR-N006',r'[+−-]'+DECIMAL),
        ('roman','CR-N031',r"[IVXLCDM]{1,12}(?!['’][A-Za-z])"),
        ('lexical_scale','CR-N048',r'(?i:'+COEFFICIENT+r'[ \t]+(?:'+'|'.join(SCALE_WORDS)+'))'),
        ('unknown_number','CR-N049',DIGITS+r'¤(?:[A-Za-z0-9]+)?'),
        ('han_digit','CR-N053','['+''.join(HAN_DIGITS)+']'),
    )
    return {name:(event,left+pattern+right) for name,event,pattern in entries}


def expand(kind, raw, env):
    """Interpret an admitted form using a source-bound role or explicit convention."""
    owns_identifier=env.cue('identifier') or kind=='uncertainty' and env.header_cue('identifier')
    if owns_identifier and kind in ('telephone','uncertainty','scientific','date_time','mixed_fraction','chemical','version'):
        return [Candidate('label' if kind=='uncertainty' else 'identifier',{'exact':raw},None,('governor',))] if kind=='uncertainty' else [Candidate('identifier',{'exact':raw},raw,('governor',))]
    if kind in ('money_range','money_rate'):
        components=re.fullmatch(r'\(?\s*([$£€])('+DECIMAL+r')\s*[–—-]\s*([$£€]?)('+DECIMAL+r')\s*\)?\s*(?:/\s*(h|hr|min|s))?',raw)
        first,start,second,end,denominator=components.groups()
        if second and first!=second:
            return [literal(raw)]
        currency={'€':'EUR','£':'GBP'}.get(first) or env.setting('dollar_currency')
        label=CURRENCIES[currency]+'s' if currency else SYMBOLS[first]
        amount=number_spoken(start)+' to '+number_spoken(end)
        spoken=(amount+' '+label if currency else label+' '+amount)
        if env.setting('narration_style')!='technical':
            # One unit can serve both whole endpoints. State both monetary
            # amounts when minor units need their own words.
            if '.' in start or '.' in end:
                spoken=money_words(start,currency)+' to '+money_words(end,currency)
            else:
                spoken=amount+' '+CURRENCIES.get(currency,'dollar')+'s'
        fields={'start':start,'end':end,'currency':currency,'shared_currency':not bool(second),'currency_symbol':first}
        if denominator:
            unit={'h':'hour','hr':'hour','min':'minute','s':'second'}[denominator]
            fields.update(denominator_unit=unit,parent='money_range_per_unit');spoken+=' per '+unit
        return [Candidate('compound_expression' if denominator else 'money_range',fields,spoken,('syntax',))]
    if kind=='historical_money':
        pounds,shillings,pence=re.fullmatch(r'£([0-9]+)\s+([0-9]+)s\s+([0-9]+)d',raw).groups()
        spoken=f'{number_spoken(pounds)} pound{"" if pounds=="1" else "s"} {number_spoken(shillings)} shilling{"" if shillings=="1" else "s"} {number_spoken(pence)} {"penny" if pence=="1" else "pence"}'
        return [Candidate('historical_money',dict(pounds=pounds,shillings=shillings,pence=pence,system='GBP_predecimal'),spoken,('syntax',))]
    if kind=='coded_money':
        currency,value=raw.split();whole,_,fraction=value.lstrip('+−-').partition('.')
        fields={'amount':value,'currency':currency,'integer_digits':whole,'fraction_digits':fraction,'scale':len(fraction),'currency_origin':'explicit_code'}
        return [Candidate('money',fields,money_words(value,currency,env.setting('narration_style')=='technical'),('syntax',))]
    if kind in ('date_time','timezone_offset'):
        if kind=='date_time':
            date_text,time,offset=re.fullmatch(r'(.*)T([0-9]{2}:[0-9]{2})(.*)',raw).groups()
            return [Candidate('date_time',{'date':date_text,'time':time,'offset':offset,'convert_time':False},
                      exact_components(date_text,'-','dash')+' at '+exact_components(time,':','colon')+' '+offset_words(offset),('syntax',))]
        zone,sign,hours,minutes=re.fullmatch(r'(UTC|GMT)([+−-])([0-9]+):([0-9]+)',raw).groups()
        return [Candidate('timezone_offset',{'zone_base':zone,'offset_sign':'positive' if sign=='+' else 'negative','offset_hours':hours,
                                      'offset_minutes':minutes,'convert_time':False},' '.join(zone)+' '+offset_words(sign+hours+':'+minutes),('syntax',))]
    if kind=='telephone':
        match=re.fullmatch(r'([0-9-]+)(?:\s+(?:ext\.?|extension)\s+([0-9]+))?',raw)
        base,extension=match.groups()
        if not (env.cue('telephone') or extension):
            return [literal(raw)]
        digits=base.replace('-','');spoken=', '.join(identifier(part) for part in base.split('-'))
        return [Candidate('telephone',{'digits':digits,'extension':extension},spoken+(' extension '+identifier(extension) if extension else ''),('governor' if not extension else 'syntax',))]
    if kind in ('named_date','date_range'):
        month,first,tail=re.fullmatch(r'([A-Za-z]+)\s+([0-9]+)(.*)',raw).groups()
        month_num=str(MONTHS.index(month))
        year=tail.partition(',')[2].strip() or None
        limit=monthrange(int(year or 2000),int(month_num))[1]
        if kind=='date_range':
            end=tail.strip().lstrip('–—-').strip()
            if not 1<=int(first)<=int(end)<=limit:return [literal(raw)]
            return [Candidate('date_range',{'start_month':month_num,'end_month':month_num,'start_day':first,'end_day':end,'year':None},
                      month+' '+ordinal(int(first))+' through '+ordinal(int(end)),('syntax',))]
        if not 1<=int(first)<=limit or month=='May' and not year and not env.cue('date') and not env.cue('date_link') and not env.header_cue('date_column'):
            return []
        if year and env.literal_mention():
            return [literal(raw)]
        spoken=month+' '+ordinal(int(first))
        if year:spoken+=', '+year_words(year,env.setting('narration_style')=='technical')
        return [Candidate('date' if year else 'partial_date',{'month':month_num,'day':first,'year':year},spoken,('syntax',))]
    if kind=='version':
        components=re.sub(r'^[vV]','',raw).split('.')
        if not env.cue('version'):
            return [literal(raw)]
        return [Candidate('version',{'components':components,'preserve_component_zeroes':True},raw,('governor',))]
    if kind=='ordinal':
        value,suffix=raw[:-2],raw[-2:];n=int(value)
        if suffix!=('th' if 11<=n%100<=13 else {1:'st',2:'nd',3:'rd'}.get(n%10,'th')) or not n:
            return [literal(raw)]
        return [Candidate('ordinal',{'integer_digits':value,'rank':True},ordinal(n),('syntax',))]
    if kind=='radix':
        digits=raw[2:];base={'x':16,'b':2,'o':8}[raw[1].lower()]
        said=' '.join(identifier(c) if c.isdigit() else c.upper() for c in digits)
        return [Candidate('radix_number',{'radix':base,'digits':digits,'convert_base':False}, {16:'hexadecimal',2:'binary',8:'octal'}[base]+' '+said,('syntax',))]
    if kind=='signed':
        value=raw[1:]; sign='positive' if raw[0]=='+' else 'negative'
        return [Candidate('signed_quantity',{'sign':sign,'integer_digits':value} if value.isdigit() else {'sign':sign,'digits':value},None,('syntax',))]
    if kind in ('rate','temperature','compound_unit','measurement_range','range','dimension','bound','formula','tuple','coordinate','duration_units','scientific','uncertainty','mixed_fraction'):
        return lab_forms(kind,raw,env)
    if kind=='section_ref':
        fields=re.findall(r'[A-Za-z]+|[0-9]+',raw)
        from .spoken_forms import expand as established
        return [Candidate('reference',{'relation':'section','components':fields},established(raw),('syntax',))]
    if kind=='roman':
        value=roman(raw)
        if value and env.cue('chapter'):
            return [Candidate('roman_numeral',{'roman':raw,'value':str(value),'role':'chapter'},number_spoken(str(value)),('governor',))]
        return []
    if kind=='chemical':
        if not env.cue('formula'):
            return []
        atoms=list(ATOMS.finditer(raw))
        parts=[(atom[1],atom[2].translate(SUBSCRIPT_DIGITS)) for atom in atoms]
        if len({symbol for symbol,_ in parts})!=len(parts):return [literal(raw)]
        spoken=' '.join(' '.join(symbol.upper())+(' '+identifier(digits) if digits else '') for symbol,digits in parts)
        counts=tuple(Component('CR-N036','chemical_count',atom.span(2),{'element':atom[1],'atom_count':count})
                     for atom,(_,count) in zip(atoms,parts) if count and atom[2]!=count)
        return [Candidate('chemical_formula',{'source_formula':raw,'atom_counts':{symbol:digits or '1' for symbol,digits in parts},
                                     'replace_with_common_name':False},spoken,('governor',),counts)]
    if kind=='scale_suffix':
        if noun:=env.cue('count',side='right'):
            scale={'K':'thousand','M':'million','B':'billion'}[raw[-1]]
            return [Candidate('scaled_quantity',{'coefficient':raw[:-1],'scale':scale,'scope':noun},number_spoken(raw[:-1])+' '+scale,('governor',))]
        if raw.endswith('K') and env.cue('temperature'):
            return [Candidate('measurement',{'value':raw[:-1],'unit':'kelvin'},number_spoken(raw[:-1])+' kelvin',('governor',))]
        return [literal(raw)]
    if kind=='missing':
        return [Candidate('missing_value',{'missing':True,'source_marker':raw,'equals_zero':False},'N A',('governor',))] if env.cue('result') else [literal(raw)]
    if kind=='symbolic_fraction' and env.cue('math'):
        numerator,denominator=raw.split('/')
        return [Candidate('formula',{'numerator':numerator,'denominator':denominator,'correct_source':False},
                          numerator+' divided by '+denominator,('governor',))]
    if kind=='lexical_scale':
        if env.cue('work_title'):
            return [Candidate('name',{'exact':raw,'rewrite_source_words':False},None,('governor',))]
        *words,scale=raw.casefold().replace('-',' ').split()
        value=1 if words==['a'] else sum(SMALL_WORDS.get(word,TENS_WORDS.get(word,0)) for word in words)
        if raw[0].islower() or env.cue('quantity'):
            return [Candidate('lexical_quantity',{'coefficient':str(value),'scale':scale,
                              'exact_value':str(value*SCALE_WORDS[scale]),'rewrite_source_words':False},None,('syntax',))]
    if kind=='unknown_number' and not owns_identifier:
        return [literal(raw,kind='unknown_numeric_form',fields={'raw':raw,'preserve_source':True})]
    if kind=='han_digit' and (env.cue('numeral_role') or env.header_cue('numeral_role')):
        value=HAN_DIGITS[raw]
        return [Candidate('numeral',{'system':'han_decimal','value':value,'source':raw},number_spoken(value),
                          ('column' if env.header else 'governor',))]
    return []


def offset_words(offset):
    if offset=='Z':return 'U T C'
    hour,minute=offset[1:].split(':')
    return ('plus' if offset[0]=='+' else 'minus')+' '+number_spoken(hour)+' hours '+number_spoken(minute)+' minutes'


def lab_forms(kind,raw,env):
    parts=re.findall(SIGNED,raw)
    learned=('syntax',)
    if kind=='rate':
        amount=parts[0];unit=raw[len(amount):].strip();scale,spoken=RATES[unit]
        if unit=='bp' and amount.lstrip('+−-')=='1':spoken='basis point'
        return [Candidate('rate',{'coefficient':amount,'scale':scale},number_spoken(amount)+' '+spoken,learned)]
    if kind=='temperature':
        amount=parts[0];symbol=raw[len(amount):].strip()
        unit={'°C':'celsius','°F':'fahrenheit','°':'degree'}[symbol]
        return [Candidate('angle' if symbol=='°' else 'temperature',{'value':amount,'unit':unit},number_spoken(amount)+' '+UNIT_NAMES[symbol],learned)]
    if kind=='compound_unit':
        amount=parts[0];a,b=raw[len(amount):].strip().split('/')
        denominator={'meters':'meter','seconds':'second','hours':'hour','liters':'liter','milliliters':'milliliter'}.get(UNIT_NAMES[b],UNIT_NAMES[b].removesuffix('s'))
        return [Candidate('compound_measurement',{'value':amount,'numerator_unit':a,'denominator_unit':b},number_spoken(amount)+' '+UNIT_NAMES[a]+' per '+denominator,learned)]
    if kind in ('range','measurement_range'):
        a,b=parts;unit=raw[raw.rfind(b)+len(b):].strip()
        fields={'start':a,'end':b};spoken=number_spoken(a)+' to '+number_spoken(b)
        if unit:fields['shared_unit']=unit;spoken+=' '+UNIT_NAMES[unit]
        return [Candidate('measurement_range' if unit else 'numeric_range',fields,spoken if unit else None,learned)]
    if kind=='dimension':
        unit=raw[raw.rfind(parts[-1])+len(parts[-1]):].strip()
        if unit:return [Candidate('dimensions',{'axes':parts,'shared_unit':unit},' by '.join(map(number_spoken,parts))+' '+UNIT_NAMES[unit],learned)]
        return [Candidate('multiplication',{'operands':parts,'operator':'multiply'},' times '.join(map(number_spoken,parts)),learned)]
    if kind=='bound':
        amount=parts[-1];operator=raw[:raw.rfind(amount)].strip();relation,words=BOUNDS[operator]
        return [Candidate('bound',{'relation':relation,'value':amount},words+' '+number_spoken(amount) if operator=='≈' else None,learned)]
    if kind=='formula':
        operation=OPERATION.fullmatch(raw)
        a,operator,b,rhs=operation.groups()
        if not rhs and operator=='-' and not env.cue('math'):
            return [literal(raw,exact_components(raw,'-','dash') if raw.replace('-','').isdigit() else raw)]
        verb={'+':'add','−':'subtract','-':'subtract','×':'multiply','÷':'divide'}[operator]
        fields={'left':a,'right':b,'left_integer':a,'right_integer':b,'operator':verb}
        spoken=number_spoken(a)+' '+OPERATORS[operator]+' '+number_spoken(b)
        if rhs:
            fields.update(operands=[a,b],equality_rhs=rhs,correct_source=False,
                          ordered_operands=[a,b,rhs],operators=[operator,'='],preserve_authored_claim=True)
            spoken+=' equals '+number_spoken(rhs)
        parts=()
        if operator in ('-','−') and operation.end(2)==operation.start(3):
            parts=(Component('CR-N006','binary_operation',(operation.start(2),operation.end(3)),dict(fields)),)
        return [Candidate('formula' if rhs else 'binary_operation',fields,spoken,learned,parts)]
    if kind=='tuple':
        a,b=parts
        if env.cue('interval'):
            fields={'lower':a,'upper':b,'lower_inclusive':raw[0]=='[','upper_inclusive':raw[-1]==']'}
            spoken=f'interval from {number_spoken(a)} {"inclusive" if fields["lower_inclusive"] else "exclusive"} to {number_spoken(b)} {"inclusive" if fields["upper_inclusive"] else "exclusive"}'
            return [Candidate('interval',fields,spoken,('governor',))]
        if env.cue('pair'):
            return [Candidate('ordered_tuple',{'ordered_components':parts},number_spoken(a)+', '+number_spoken(b),('governor',))]
        return [literal(raw)]
    if kind=='coordinate':
        amount=parts[0];hemisphere=raw[-1];direction={'N':'north','S':'south','E':'east','W':'west'}[hemisphere]
        return [Candidate('coordinate',{'axis':'latitude' if hemisphere in 'NS' else 'longitude','value':amount,'hemisphere':hemisphere},number_spoken(amount)+' degrees '+direction,learned)]
    if kind=='duration_units':
        units={'h':'hours','m':'minutes','s':'seconds'}
        fields={units[k]:v for v,k in re.findall(r'([0-9]+)([hms])',raw)}
        return [Candidate('duration',fields,duration_words(fields),learned)]
    if kind=='scientific':
        value,exponent=re.split('[eE]',raw)
        return [Candidate('scientific_quantity',{'mantissa':value,'exponent':exponent,'base':'10'},number_spoken(value)+' times ten to the power of '+number_spoken(exponent),learned)]
    if kind=='uncertainty':
        return uncertainty(raw,env)
    if kind=='mixed_fraction':
        from .linguistic import FRACTIONS, technical
        if raw[-1] in FRACTIONS:
            whole=raw[:-1];return [Candidate('fraction',{'whole':whole or None,'fraction_symbol':raw[-1]},technical(raw).strip(),learned)]
        whole,ratio=raw.split();a,b=ratio.split('/')
        if not int(b):return [literal(raw)]
        words={'2':('half','halves'),'3':('third','thirds'),'4':('quarter','quarters'),'8':('eighth','eighths')}
        fraction=number_spoken(a)+' '+words[b][a!='1'] if b in words else number_spoken(a)+' divided by '+number_spoken(b)
        return [Candidate('fraction',{'whole':whole,'numerator':a,'denominator':b},number_spoken(whole)+' and '+fraction,learned)]
    return []


def uncertainty(raw, env):
    amount, digits, unit = UNCERTAINTY.fullmatch(raw).groups()
    role = 'column' if env.header_cue('measurement') else 'governor' if env.cue('measurement') else None
    if role is None:
        return [literal(raw)]
    options = []
    fallback = False
    choices = ([Candidate('integer_quantity', {'value':amount}, None, ('syntax',))]
               if amount.lstrip('+−-').isdigit() else context_numbers.candidates(amount, env, measurement=True))
    for choice in choices:
        if choice.kind == 'literal':
            fallback = True
            continue
        evidence = choice.evidence
        # In this English scientific notation a plain decimal point is the
        # default. A scoped locale or witnessed separator takes precedence.
        if (choice.kind == 'decimal_quantity' and not evidence and not env.setting('number_locale')
                and not env.number_separator() and context_numbers.PLAIN_POINT.fullmatch(amount)):
            evidence = ('syntax',)
        value = choice.fields['value']
        fields = {'coefficient':amount,'coefficient_digits':value,'parenthesized_digits':digits,
                  'uncertainty_last_digits':digits,'uncertainty_scale':'final_digits',
                  'decimal_places':choice.fields.get('scale',0),'convention':'last_digit_uncertainty','unit':unit}
        spoken = quantity_words(value, UNIT_NAMES[unit]) if unit else number_spoken(value)
        supported = (role,*evidence) if evidence else ()
        options.append(Candidate('measurement_with_uncertainty',fields,
                                 spoken+' with an uncertainty of '+identifier(digits)+' in the final digits',supported))
    return [literal(raw),*options] if fallback or not options else options
