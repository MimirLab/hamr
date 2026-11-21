"""Speak bounded scientific table cells from source and typographic evidence."""

import re
import unicodedata

from .context_candidate import number_spoken
from .context_forms import UNIT_NAMES, singular_unit


SPACING = r'[ \t\u00a0\u2009\u202f]'
VALUE = re.compile(rf'(?P<coefficient>[+−-]?(?:[0-9]{{1,12}}|[0-9]{{1,3}}(?:{SPACING}[0-9]{{3}}){{1,3}})'
                   rf'(?:\.[0-9]{{1,15}}(?:{SPACING}[0-9]{{3}}){{0,4}}(?:{SPACING}[0-9]{{1,4}})?)?)'
                   r'(?:\((?P<uncertainty>[0-9]{1,8})\))?(?P<ellipsis>\s*\.\s*\.\s*\.|\s*…)?'
                   r'(?:\s*×\s*10\s*(?P<exponent>[+−-]?[0-9]{1,4}))?(?P<exact>\s*\(exact\))?')
AUTHOR_UNCERTAINTY = re.compile(
    r'\b(?:a number|digits?) in parentheses[^.!?]{0,150}\buncertainty\b[^.!?]{0,52}\b(?:last|final)\b[^.!?]{0,20}\bdigits?\b',
    re.I)
UNIT_LABELS = {**UNIT_NAMES, 'Hz':'hertz','MHz':'megahertz','J':'joules','N':'newtons','F':'farads',
               'C':'coulombs','T':'teslas','Wb':'webers','eV':'electron volts','MeV':'megaelectron volts',
               'W':'watts','A':'amperes','mol':'moles','u':'unified atomic mass units','Ω':'ohms'}
UNIT = re.compile('|'.join(re.escape(u) for u in sorted(UNIT_LABELS, key=len, reverse=True)))
SCRIPT_LABELS = {'superscript':'superscript','subscript':'subscript'}


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


def typed_units(cell):
    source = cell['text']
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
    operators = {'/':'over','−':'minus','+':'plus','′':'prime','\'':'prime'}
    for char in value:
        if char.isspace():
            continue
        if char in operators:
            tokens.append(operators[char])
        elif char.isascii() and char.isalpha():
            tokens.append(('capital ' if char.isupper() else '')+char.upper())
        elif char.isascii() and char.isdigit():
            tokens.append(number_spoken(char))
        else:
            name = unicodedata.name(unicodedata.normalize('NFKC',char), '') if char == 'µ' else unicodedata.name(char, '')
            match = re.fullmatch('GREEK (SMALL|CAPITAL) LETTER ([A-Z]+)', name)
            if not match:
                return None
            tokens.append(('capital ' if match[1] == 'CAPITAL' else '')+match[2].lower())
    return ' '.join(tokens)


def typed_symbol(cell):
    source = cell['text']
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


def readings(headers, cells, uncertainty_basis):
    normalized = [h.casefold() for h in headers]
    scientific = len(normalized)==4 and set(normalized)=={'quantity','symbol','numerical value','unit'}
    parsers = {'numerical value':lambda value:typed_value(value,uncertainty_basis),
               'symbol':typed_symbol,'unit':typed_units} if scientific else {}
    changes = []
    for column, (name, cell) in enumerate(zip(normalized,cells)):
        if not cell['text']:
            continue
        parser = parsers.get(name)
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
