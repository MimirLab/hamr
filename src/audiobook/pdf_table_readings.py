"""Speak bounded scientific table cells from source and typographic evidence."""

import re
from dataclasses import replace

from .context_candidate import number_spoken
from .context_forms import UNIT_NAMES, singular_unit
from .core import Error
from .pdf_formula import formula_suffix,letter,read_formula


SPACING = r'[ \t\u00a0\u2009\u202f]'
ROW_READING_ISSUE = 'Scientific notation in this table row requires a verified reading.'
VALUE = re.compile(rf'(?P<coefficient>[+−-]?(?:[0-9]{{1,12}}|[0-9]{{1,3}}(?:{SPACING}[0-9]{{3}}){{1,3}})'
                   rf'(?:\.[0-9]{{1,15}}(?:{SPACING}[0-9]{{3}}){{0,4}}(?:{SPACING}[0-9]{{1,4}})?)?)'
                   r'(?:\((?P<uncertainty>[0-9]{1,8})\))?(?P<ellipsis>\s*\.\s*\.\s*\.|\s*…)?'
                   r'(?:\s*×\s*10\s*(?P<exponent>[+−-]?[0-9]{1,4}))?(?P<exact>\s*\(exact\))?')
AUTHOR_UNCERTAINTY = re.compile(
    r'\b(?:a number|digits?) in parentheses[^.!?]{0,150}\buncertainty\b[^.!?]{0,52}\b(?:last|final)\b[^.!?]{0,20}\bdigits?\b',
    re.I)
BRACKETED_UNIT_DECLARATION = re.compile(
    r'\bFor units with square brackets[^.!?]{0,72}\[(?P<unit>[A-Za-z]+)\](?P<power>[−-][0-9])'
    r'\s+and\s+\[(?P=unit)\]\s+are\s+(?P<inverse>[a-z]+ per [a-z]+)\s+and\s+'
    r'(?P<positive>[a-z]+ per [a-z]+),\s+respectively\b',re.I)
UNIT_LABELS = {**UNIT_NAMES, 'Hz':'hertz','MHz':'megahertz','J':'joules','N':'newtons','F':'farads',
               'C':'coulombs','T':'teslas','Wb':'webers','eV':'electron volts','MeV':'megaelectron volts',
               'W':'watts','A':'amperes','mol':'moles','u':'unified atomic mass units','Ω':'ohms','Ω':'ohms'}
UNIT = re.compile('|'.join(re.escape(u) for u in sorted(UNIT_LABELS, key=len, reverse=True)))
SCRIPT_LABELS = {'superscript':'superscript','subscript':'subscript'}
CONDITION_VALUE = re.compile(r'(?:(?P<variable>[A-Za-z])\s*=\s*)?(?P<value>[+−-]?[0-9]{1,12}(?:\.[0-9]{1,12})?)\s*(?P<unit>[◦°]?[A-Za-z]+)')
ISOTOPE = re.compile(r'\((?P<mass>[0-9]{1,3})(?P<element>H|He|C|N|O) nucleus\)')
ELEMENT_NAMES = {'H':'hydrogen','He':'helium','C':'carbon','N':'nitrogen','O':'oxygen'}


def typed_value(cell, uncertainty_basis):
    source = cell['text']
    match = VALUE.fullmatch(source)
    if not match:
        return None
    coefficient = re.sub(SPACING, '', match['coefficient'])
    _, _, decimals = coefficient.partition('.')
    uncertainty = match['uncertainty']
    exponent = match['exponent']
    if uncertainty and (not uncertainty_basis or match['exact'] or match['ellipsis']):
        return None
    expected = [{'kind':'superscript','span':list(match.span('exponent'))}] if exponent else []
    if cell['scripts'] != expected:
        return None
    reading = number_spoken(coefficient)
    if len(decimals)>=8 and (tail:=re.search(r'(.)\1+$',decimals)) and 2<=len(tail[0])<=3:
        repetition='double' if len(tail[0])==2 else 'triple'
        reading=number_spoken(coefficient[:-len(tail[0])])+' '+repetition+' '+number_spoken(tail[1])
    fields = {'coefficient':coefficient, 'decimal_places':len(decimals),'exponent':exponent,
              'uncertainty_last_digits':uncertainty,'continuing_digits':bool(match['ellipsis']),
              'declared_exact':bool(match['exact'])}
    if match['ellipsis']:
        reading += ', followed by an ellipsis,'
    if exponent:
        reading += ' times ten to the power of '+number_spoken(exponent)
    if uncertainty:
        fields['uncertainty_convention'] = 'last_digit_uncertainty'
        fields['author_evidence'] = uncertainty_basis
        reading += ', with an uncertainty of '+number_spoken(uncertainty)+' in the final digits'+(' of the coefficient' if exponent else '')
    if match['exact']:
        reading += ', exactly'
    event = 'CR-N034' if uncertainty else 'CR-N051' if exponent else 'CR-N004'
    return reading, event, 'measurement_with_uncertainty' if uncertainty else 'scientific_quantity' if exponent else 'decimal_quantity', fields


def unit_word(unit, exponent):
    name = singular_unit(UNIT_LABELS[unit])
    if exponent is None:
        return name
    if exponent == '2':
        return 'square '+name
    if exponent == '3':
        return 'cubic '+name
    return name+' to the power of '+number_spoken(exponent)


def typed_units(cell, unit_definitions=None):
    source = cell['text']
    if unit_definitions and (author:=unit_definitions.get(source)):
        if cell['scripts']==author['scripts']:
            return author['reading'],'CR-N017','authored_unit',{'source_unit':source,'author_evidence':author['evidence']}
    if source.startswith('[') and source.endswith(']') and unit_definitions:
        if inner:=typed_units(cell_slice(cell,1,len(source)-1)):
            audible,_,_, fields=inner
            return 'bracketed '+audible,'CR-N018','bracketed_unit_notation',{**fields,'source_unit':source,'notation_only':True}
    scripts = {s['span'][0]:(s['span'][1],s['kind']) for s in cell['scripts']}
    fields = []
    position = 0
    while position < len(source):
        match = UNIT.match(source, position)
        if not match:
            return None
        position = match.end()
        exponent = None
        if position in scripts:
            end, kind = scripts.pop(position)
            exponent = source[position:end]
            if kind != 'superscript' or not re.fullmatch(r'[−+-]?[0-9]{1,3}', exponent):
                return None
            position = end
        if position < len(source) and not source[position].isspace() and exponent is None:
            return None
        fields.append({'symbol':match[0],'exponent':exponent})
        while position < len(source) and source[position].isspace():
            position += 1
    if scripts or not fields:
        return None
    powers = [part['exponent'] or '' for part in fields]
    denominator = [power[:1] in ('-','−') for power in powers]
    use_per = denominator == sorted(denominator) and all(
        power[1:] in ('1','2','3') for power, negative in zip(powers,denominator) if negative)
    pieces = []
    for part in fields:
        exponent = part['exponent']
        if use_per and exponent and exponent.startswith(('-', '−')):
            pieces.append('per '+unit_word(part['symbol'], None if exponent[1:] == '1' else exponent[1:]))
        else:
            pieces.append(('times ' if pieces else '')+unit_word(part['symbol'], exponent))
    reading = ' '.join(pieces)
    return reading, 'CR-N018', 'compound_measurement', {'units':fields}


def symbol_text(value):
    tokens = []
    operators = {'/':'over','−':'minus','+':'plus','′':'prime','\'':'prime','∞':'infinity'}
    for char in value:
        if char.isspace():
            continue
        if char in operators:
            tokens.append(operators[char])
        elif spoken:=letter(char):
            tokens.append(spoken)
        elif char.isascii() and char.isdigit():
            tokens.append(number_spoken(char))
        else:
            return None
    return ' '.join(tokens)


def typed_symbol(cell):
    source = cell['text']
    if cell.get('math_marks') or any(ch in source for ch in '()[]'):
        spoken = read_formula(cell)
        if spoken:
            fields={'notation':source,'scripts':cell['scripts'],'marks':cell.get('math_marks',[])}
            return spoken,'CR-L030','authored_symbol',fields
        return None
    cursor = 0
    pieces = []
    for item in cell['scripts']:
        start, end = item['span']
        base, script = symbol_text(source[cursor:start]), symbol_text(source[start:end])
        if base is None or not script:
            return None
        pieces.extend((base, SCRIPT_LABELS[item['kind']], script))
        cursor = end
    tail = symbol_text(source[cursor:])
    if tail is None:
        return None
    pieces.append(tail)
    return ' '.join(filter(None, pieces)), 'CR-L030', 'authored_symbol', {'notation':source,'scripts':cell['scripts']}


def cell_slice(cell,start,end):
    return {
        'text':cell['text'][start:end],
        'scripts':[{'kind':s['kind'],'span':[a-start,b-start]} for s in cell['scripts']
                   for a,b in (s['span'],) if start<=a<=b<=end],
        'math_font_spans':[[max(a,start)-start,min(b,end)-start] for a,b in cell.get('math_font_spans',()) if a<end and start<b],
    }


def equation_left(cell,unit_definitions):
    source=cell['text']
    group=re.match(r'\(([0-9]{1,4})\s+([^)]+)\)|\[([0-9]{1,4})\s+([^\]]+)\]',source)
    if not group:
        return read_formula(cell)
    amount=group[1] or group[3]
    a,b=group.span(2 if group[1] else 4)
    unit=cell_slice(cell,a,b)
    heading='parenthesis'
    if group[3]:
        unit['text']='['+unit['text']+']'
        for item in unit['scripts']:item['span']=[v+1 for v in item['span']]
        heading='bracket'
    resolved=typed_units(unit,unit_definitions)
    rest=cell_slice(cell,group.end(),len(source))
    suffix=read_formula(rest) if rest['text'].strip() else ''
    if not resolved or suffix is None:
        return read_formula(cell)
    reading=singular_unit(resolved[0]) if amount=='1' else resolved[0]
    return f'open {heading} {number_spoken(amount)} {reading} close {heading}'+(' '+suffix if suffix else '')


def typed_equation(cell,uncertainty_basis,unit_definitions):
    source=cell['text']
    separator=source.index('=')
    left=cell_slice(cell,0,separator)
    lhs=equation_left(left,unit_definitions)
    if not lhs:
        return None
    # Typeset units may start immediately after a raised numeric exponent.
    start=separator+1
    while start<len(source) and source[start].isspace():start+=1
    options=[]
    for match in re.finditer(r'[A-Za-zµμΩΩ°\[]',source[start:]):
        unit_start=start+match.start()
        value_end=unit_start
        while value_end>start and source[value_end-1].isspace():value_end-=1
        if any(a<unit_start<b for a,b in (s['span'] for s in cell['scripts'])):
            continue
        unit=typed_units(cell_slice(cell,unit_start,len(source)),unit_definitions)
        value=typed_value(cell_slice(cell,start,value_end),uncertainty_basis)
        if unit and value:
            options.append((value,unit))
    if len(options)!=1:
        return None
    value,unit=options[0]
    spoken=lhs+' equals '+value[0]+', '+unit[0]
    fields={'notation':source,'left':left['text'].rstrip(),'relation':'=','value':value[3],
            'unit':unit[3],'scripts':cell['scripts']}
    return {'reading':spoken+'.','fields':fields}


def condition(cell):
    source = cell['text']
    if not source.startswith('(') or not source.endswith(')') or ',' not in source:
        return None
    parts = list(re.finditer(r'[^,]+',source[1:-1]))
    if len(parts)!=source.count(',')+1:
        return None
    spoken, fields = [], []
    for part in parts:
        text = part[0].strip()
        a = 1+part.start()+len(part[0])-len(part[0].lstrip())
        z = a+len(text)
        scripts = [s for s in cell['scripts'] if a <= s['span'][0] and s['span'][1] <= z]
        if re.fullmatch(r'[a-z][a-z -]*',text) and not scripts:
            spoken.append(text)
            fields.append({'source':text})
        elif text=='H2O' and scripts==[{'kind':'subscript','span':[a+1,a+2]}]:
            spoken.append('H two O')
            fields.append({'source':text,'formula':{'H':'2','O':'1'}})
        elif measurement:=CONDITION_VALUE.fullmatch(text):
            unit=measurement['unit'].replace('◦','°')
            expected=[{'kind':'superscript','span':[a+measurement.start('unit'),a+measurement.start('unit')+1]}]
            if unit not in UNIT_LABELS or scripts and (not unit.startswith('°') or scripts!=expected):
                return None
            value=measurement['value']
            prefix=letter(measurement['variable'])+' equals ' if measurement['variable'] else ''
            spoken.append(prefix+number_spoken(value)+' '+UNIT_LABELS[unit])
            fields.append({'source':text,'value':value,'unit':unit,'variable':measurement['variable']})
        else:
            return None
    return ', '.join(spoken),fields


def typed_quantity(cell):
    source = cell['text']
    if source.startswith('in '):
        unit_cell = cell_slice(cell,3,len(source))
        if reading:=typed_units(unit_cell):
            spoken,event,kind,fields=reading
            return 'in '+spoken,event,kind,fields
    if parsed:=condition(cell):
        spoken,fields=parsed
        return spoken,'CR-N035','authored_conditions',{'notation':source,'conditions':fields}
    if isotope:=ISOTOPE.search(source):
        if cell['scripts']==[{'kind':'superscript','span':list(isotope.span('mass'))}]:
            mass,element=isotope['mass'],isotope['element']
            label=ELEMENT_NAMES[element]+' '+number_spoken(mass)+' nucleus'
            reading=source[:isotope.start()].rstrip()+', '+label+', '+source[isotope.end():].lstrip()
            return reading,'CR-N035','isotope_notation',{'notation':source,'mass_number':mass,'element':element}
    if compiled:=formula_suffix(cell):
        start, spoken = compiled
        label = source[:start].rstrip(' :')
        reading = label+', formula, '+spoken if label else spoken
        fields = {'label':label,'notation':source[start:],'formula_span':[start,len(source)],
                  'scripts':cell['scripts'],'marks':cell.get('math_marks',[])}
        return reading,'CR-N035','formula_notation',fields
    return None


def readings(headers,cells,uncertainty_basis,unit_definitions=None):
    normalized = [h.casefold() for h in headers]
    scientific = len(normalized)==4 and set(normalized)=={'quantity','symbol','numerical value','unit'}
    parsers = {'numerical value':lambda value:typed_value(value,uncertainty_basis),
               'symbol':typed_symbol,'unit':lambda value:typed_units(value,unit_definitions)} if scientific else {}
    changes = []
    for column, (name, cell) in enumerate(zip(normalized,cells)):
        if not cell['text']:
            continue
        annotated = cell['scripts'] or cell.get('math_font_spans') or cell.get('math_marks')
        parser = typed_quantity if scientific and name=='quantity' and (annotated or re.search(r'[/×=¯]|\b[0-9]+[A-Za-z]+\b',cell['text'])) else parsers.get(name)
        if parser is None:
            if cell['scripts'] or scientific and re.search(r'[/×=¯]|\b[0-9]+[A-Za-z]+\b',cell['text']):
                return None
            continue
        result = parser(cell)
        if result is None:
            return None
        reading,event,kind,fields = result
        changes.append({'row':1,'column':column,'source':cell['text'],'reading':reading,
                        'event_id':event,'kind':kind,'fields':fields})
    return changes


def apply_glyph_aliases(doc, aliases):
    """Recompile affected cells while retaining the immutable source text and ranges."""
    if not isinstance(aliases,dict) or doc.format!='pdf':
        raise Error('pdf_glyph_alias','PDF cell glyph aliases require a source-bound PDF policy.')
    replacements={}
    known={b.id:b for b in doc.blocks}
    for block_id, fixes in aliases.items():
        block=known.get(block_id)
        if (block is None or not isinstance(fixes,list) or not fixes or 'pdf_conventions' not in block.locator
                or block.issue!=ROW_READING_ISSUE):
            raise Error('pdf_glyph_alias','A glyph alias must name an unresolved scientific table row in this PDF.')
        original=block.locator['pdf_table_cells']
        cells=[dict(cell) for cell in original]
        affected=set()
        for fix in fixes:
            if not isinstance(fix,dict) or set(fix)!={'column','offset','source','reading','reason','verified'}:
                raise Error('pdf_glyph_alias','A glyph alias requires column, offset, source, reading, reason and verified.')
            column,offset=fix['column'],fix['offset']
            raw,reading=fix['source'],fix['reading']
            if (type(column) is not int or not 0<=column<len(cells) or type(offset) is not int or offset<0
                    or (column,offset) in affected or not isinstance(raw,str) or len(raw)!=1 or not raw.isalpha()
                    or not isinstance(reading,str) or len(reading)!=1 or not letter(reading)
                    or fix['verified'] is not True or not isinstance(fix['reason'],str) or not fix['reason'].strip()
                    or offset>=len(original[column]['text']) or original[column]['text'][offset]!=raw):
                raise Error('pdf_glyph_alias','A glyph alias needs one exact source letter, one mathematical letter, a unique cell offset and a verified reason.')
            text=cells[column]['text']
            cells[column]['text']=text[:offset]+reading+text[offset+1:]
            affected.add((column,offset))
        conventions=block.locator['pdf_conventions']
        selected=readings(block.locator['rows'][0],cells,conventions['uncertainty'],conventions['units'])
        if selected is None:
            continue  # Leave any remaining source issue in place.
        for cell in selected:
            column=cell['column']
            cell['source']=original[column]['text']
            evidence=[fix for fix in fixes if fix['column']==column]
            if evidence:cell['fields']['glyph_aliases']=evidence
        if any(column not in {cell['column'] for cell in selected} for column,_ in affected):
            continue
        location={**block.locator,'pdf_cell_readings':selected}
        replacements[block_id]=replace(block,locator=location,issue=None)
    return replace(doc,blocks=[replacements.get(b.id,b) for b in doc.blocks])
