"""Small, explicit written forms with exact offsets into the text they edit."""
import re

from .spoken_forms import identifier, number

LEFT = r'(?<![\w./:@#$+-])(?<![0-9],)'
RIGHT = r'(?!\w|[./:@-]\w|\$)'
AMOUNT = r'[+-]?(?:[1-9][0-9]{0,2}(?:,[0-9]{3}){1,2}\.[0-9]{1,12}|[0-9]{1,9}(?:\.[0-9]{1,12})?)'
UNITS = {'mL': 'milliliter', '°C': 'degree Celsius', '°F': 'degree Fahrenheit'}
CLOCK_LABEL = r'(?:departure|arrival)(?: time)?|clock reads'
CLOCK = r'(?i:' + CLOCK_LABEL + r')(?:\s*:\s*|\s+)(?:[01]?[0-9]|2[0-3]):[0-5][0-9]'
CLOCK_END = RIGHT + r'(?!\s+(?:UTC|GMT|ratio|seconds?|minutes?|milliseconds?|hours?)\b)'
# A recognizer can put a sentence stop between an explicit clock label and
# its value. The cue must be immediately adjacent to the value in its text.
CLOCK_OBSERVATION = re.compile(r'\b((?i:departure time|arrival time|clock reads)(?:[,:]\s*|\.\s+|\s+))'
                               r'([01]?[0-9]|2[0-3])[.:]?([0-5][0-9])' + CLOCK_END)
READINGS = re.compile('|'.join(f'(?P<{kind}>{pattern})' for kind, pattern in (
    ('room_identifier', r'\b[Rr]oom\s+0[0-9]{1,11}' + RIGHT),
    ('clock_time', r'\b' + CLOCK + CLOCK_END),
    ('literal_clock', LEFT + r'(?:[01][0-9]|2[0-3]):[0-5][0-9]' + RIGHT),
    ('currency_symbol', LEFT + r'\$' + AMOUNT + RIGHT + r'(?![,.][0-9])'),
    ('measurement', LEFT + AMOUNT + r'[^\S\n]*(?:mL|°[CF])' + RIGHT),
    ('decimal_precision', LEFT + r'[+-]?[0-9]{1,9}\.[0-9]{0,11}0' + RIGHT + r'(?!,[0-9])'),
)))


def decimal(raw):
    sign = 'minus ' if raw.startswith('-') else 'plus ' if raw.startswith('+') else ''
    whole, dot, fraction = raw.lstrip('+-').replace(',', '').partition('.')
    if len(whole) > 1 and whole.startswith('0'):
        spoken = identifier(whole)
    else:
        value = int(whole)
        parts = []
        for scale, label in ((10**9, 'billion'), (10**6, 'million'), (10**3, 'thousand')):
            group, value = divmod(value, scale)
            if group:
                parts.append(number(group) + ' ' + label)
        if value or not parts:
            parts.append(number(value))
        spoken = ' '.join(parts)
    return sign + spoken + (' point ' + identifier(fraction) if dot else '')


def clock_words(hour, minute):
    minutes = int(minute)
    return number(int(hour)) + (' o’clock' if minutes == 0 else (' oh ' if minutes < 10 else ' ') + number(minutes))


def clock_observation(text):
    return CLOCK_OBSERVATION.sub(lambda m: m[1] + clock_words(m[2],m[3]), text)


def render(kind, raw):
    if kind == 'room_identifier':
        label, digits = raw.split()
        return label + ' ' + identifier(digits), {'digits': digits}
    if kind == 'clock_time':
        label, hour, minute = re.fullmatch(r'(.*?)([0-9]{1,2}):([0-9]{2})', raw).groups()
        cue = label.rstrip(' :')
        if cue.lower() in ('departure','arrival'):
            cue += ' time'
        return cue + (' ' if cue.lower()=='clock reads' else ', ') + clock_words(hour,minute), {'hour': hour, 'minute': minute}
    if kind == 'literal_clock':
        hour, minute = raw.split(':')
        return identifier(hour) + ' colon ' + identifier(minute), {'hour_field': hour, 'minute_field': minute}
    if kind == 'currency_symbol':
        return 'dollar sign ' + decimal(raw[1:]), {'amount': raw[1:], 'symbol': '$'}
    if kind == 'measurement':
        amount, unit = re.fullmatch(r'(' + AMOUNT + r')\s*(mL|°[CF])', raw).groups()
        first, space, rest = UNITS[unit].partition(' ')
        plural = '' if amount.lstrip('+-') == '1' else 's'
        return decimal(amount) + ' ' + first + plural + space + rest, {'amount': amount, 'unit': unit}
    return decimal(raw), {'number': raw}


def expand(text, protected=(), kinds=None):
    protected = [*protected, *(match.span() for match in re.finditer(r'(?i)\b(?:https?://|www\.)\S+', text))]
    out, edits = [], []
    cursor = output_pos = 0
    for match in READINGS.finditer(text):
        a, b = match.span()
        if (kinds is not None and match.lastgroup not in kinds) or any(a < end and b > start for start, end in protected):
            continue
        value, fields = render(match.lastgroup, match[0])
        out.extend((text[cursor:a], value))
        start = output_pos + (a - cursor)
        output_pos = start + len(value)
        edits.append({'kind': match.lastgroup, 'input_start': a, 'input_end': b,
                      'output_start': start, 'output_end': output_pos, 'source': match[0], 'spoken': value,
                      'fields': fields})
        cursor = b
    out.append(text[cursor:])
    return ''.join(out), edits
