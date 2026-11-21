"""Exact positional numbers and evidence from physically written separators."""
import re

from .context_candidate import Candidate, literal, number_spoken, numeric_fields


SPACES = '\u00a0\u2009\u202f'
GROUP_SIZE = r'[1-9][0-9]{0,2}'
TRIPLE = r'[0-9]{3}'
SPACE_GROUPS = rf'{GROUP_SIZE}(?:[{SPACES}]{TRIPLE}){{1,3}}'
SPACE_FRACTION = rf'{TRIPLE}(?:[{SPACES}]{TRIPLE}){{0,2}}[{SPACES}][0-9]{{1,3}}'
PLAIN = r'[0-9]{1,12}'
SIGNED = r'[+−-]?'
GROUPING = {'en-US': ('.', ','), 'en-GB': ('.', ','), 'de-DE': (',', '.'), 'fr-FR': (',', None)}


def grouped(mark):
    return rf'{GROUP_SIZE}(?:{re.escape(mark)}{TRIPLE}){{1,3}}'


WHOLE_GROUP = {mark: re.compile(grouped(mark)) for mark in '.,'}
SPACED_WHOLE = re.compile(SPACE_GROUPS)
FRACTION = re.compile(rf'(?:{PLAIN}|{SPACE_FRACTION})')
PUNCTUATED = SIGNED + '(?:' + '|'.join((
    rf'(?:{SPACE_GROUPS}|{PLAIN})[.,]{SPACE_FRACTION}',
    rf'{SPACE_GROUPS}(?:[.,]{PLAIN})?',
    rf'{grouped(",")}\.{PLAIN}',
    rf'{grouped(".")},{PLAIN}',
    rf'{GROUP_SIZE}(?:,{TRIPLE}){{2,3}}',
    rf'{GROUP_SIZE}(?:\.{TRIPLE}){{2,3}}',
    rf'(?:{PLAIN})?[.,]{PLAIN}',
)) + ')'
MEASUREMENT_AMOUNT = rf'(?:{PUNCTUATED}|{SIGNED}{PLAIN})'
SI_SYMBOLS = ('kg', 'mg', 'km', 'cm', 'mm', 'mL')
DECIMAL_SURFACE = re.compile(r'([0-9]{0,12})([.,])([0-9]{1,12})')
PLAIN_POINT = re.compile(rf'{SIGNED}{PLAIN}\.{PLAIN}')
QUOTATIONS = re.compile(r'“[^”]*(?:”|$)|«[^»]*(?:»|$)|"[^"]*(?:"|$)|`[^`]*(?:`|$)')


def witness_pattern(left, right, units):
    unit_names = '|'.join(map(re.escape, sorted(units, key=len, reverse=True)))
    if not unit_names:
        return re.compile(r'(?!)')
    return re.compile(left + rf'(?P<number>{SIGNED}(?:{PLAIN})?[.,]{PLAIN})[^\S\r\n]+(?:{unit_names})' + right, re.I)


def witnesses(source, pattern, excluded):
    """Yield clear decimal punctuation only from complete written measurements."""
    for match in pattern.finditer(source):
        a, b = match.span()
        if any(a < end and start < b for start, end in excluded):
            continue
        number = match['number']
        whole, mark, fraction = DECIMAL_SURFACE.fullmatch(number.lstrip('+−-')).groups()
        if len(fraction) != 3 or not whole or whole == '0' or len(whole) > 3:
            yield mark, number, [a, b]


def digits(value):
    return value.translate(str.maketrans('', '', SPACES))


def decimal_candidate(raw, sign, whole, fraction, mark, group):
    fields = {**numeric_fields(raw), 'whole': whole, 'fraction': fraction, 'integer_digits': whole,
              'fraction_digits': fraction, 'scale': len(fraction), 'separator': mark,
              'decimal_separator': mark, 'value': sign + whole + '.' + fraction}
    if group:
        fields['group_separator'] = group
    return Candidate('decimal_quantity', fields, number_spoken(fields['value']))


def integer_candidate(raw, sign, parts, mark):
    integer = ''.join(parts)
    fields = {**numeric_fields(raw), 'groups': parts, 'separator': mark, 'group_separator': mark,
              'integer_digits': integer, 'value': sign + integer}
    return Candidate('grouped_integer', fields, number_spoken(fields['value']))


def shapes(raw):
    """Generate only parses that account for every source digit and separator."""
    cleaned = raw.replace('−', '-')
    unsigned = cleaned.lstrip('+-')
    sign = cleaned[:len(cleaned)-len(unsigned)]
    choices = []
    for mark, grouping in WHOLE_GROUP.items():
        if grouping.fullmatch(unsigned):
            choices.append(integer_candidate(raw, sign, unsigned.split(mark), mark))
    if SPACED_WHOLE.fullmatch(unsigned):
        choices.append(integer_candidate(raw, sign, re.split(f'[{SPACES}]', unsigned), next(c for c in unsigned if c in SPACES)))
    for mark, grouping in (('.', ','), (',', '.')):
        head, point, tail = unsigned.partition(mark)
        if not point or not FRACTION.fullmatch(tail):
            continue
        if not head or head.isascii() and head.isdigit():
            whole, group = head or '0', None
        elif WHOLE_GROUP[grouping].fullmatch(head):
            whole, group = head.replace(grouping, ''), grouping
        elif SPACED_WHOLE.fullmatch(head):
            whole, group = digits(head), next(c for c in head if c in SPACES)
        else:
            continue
        choices.append(decimal_candidate(raw, sign, whole, digits(tail), mark, group))
    return choices


def candidates(raw, env, *, measurement=False):
    choices = shapes(raw)
    locale = env.setting('number_locale')
    decimal, group = GROUPING.get(locale, (None, None))
    quantity = measurement or env.cue('quantity_unit', side='right') or env.cue('measurement')
    if not locale and quantity:
        decimal = env.number_separator()
        if decimal:
            group = ',' if decimal == '.' else None
    if len(choices) == 1 and not locale:
        choice = choices[0]
        # A comma with one or two trailing digits can also separate list items.
        if choice.kind == 'grouped_integer' or choice.fields['separator'] == '.' or choice.fields.get('group_separator'):
            spoken = None if not quantity and PLAIN_POINT.fullmatch(raw) else choice.spoken
            return [Candidate(choice.kind, choice.fields, spoken, ('syntax',))]
    result = [literal(raw)]
    for choice in choices:
        if choice.kind == 'decimal_quantity':
            supported = choice.fields['decimal_separator'] == decimal
            explicit_group = choice.fields.get('group_separator')
            supported = supported and (explicit_group is None or explicit_group == group or explicit_group in SPACES)
        else:
            mark = choice.fields['group_separator']
            supported = mark == group or mark in SPACES
        evidence = ('convention' if locale else 'anchors',) if supported else ()
        result.append(Candidate(choice.kind, choice.fields, choice.spoken, evidence))
    return result
