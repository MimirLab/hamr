"""Bounded English spoken readings, with the source and each decision retained.

This is deliberately a contextual vocabulary, not a general acronym expander.
Unresolved ambiguous forms stay literal and require a book-local reading.
"""
import re

TITLES = {
    'dr': 'Doctor', 'mr': 'Mister', 'mrs': 'Missus', 'ms': 'Ms',
    'prof': 'Professor', 'rev': 'Reverend', 'fr': 'Father',
    'capt': 'Captain', 'lt': 'Lieutenant', 'sgt': 'Sergeant',
    'gen': 'General', 'col': 'Colonel', 'maj': 'Major',
    'gov': 'Governor', 'sen': 'Senator', 'rep': 'Representative',
    'pres': 'President', 'hon': 'Honorable', 'supt': 'Superintendent',
}
LABELS = {'no': 'number', 'nos': 'numbers', 'p': 'page', 'pp': 'pages',
          'fig': 'figure', 'figs': 'figures', 'vol': 'volume',
          'vols': 'volumes', 'ch': 'chapter', 'chap': 'chapter',
          'sec': 'section', 'eq': 'equation', 'eqs': 'equations'}
UNITS = {'cm': 'centimeters', 'mm': 'millimeters', 'km': 'kilometers',
         'm': 'meters', 'mg': 'milligrams', 'kg': 'kilograms', 'g': 'grams',
         'lb': 'pounds', 'lbs': 'pounds', 'oz': 'ounces', 'ft': 'feet',
         'in': 'inches'}
PHRASES = {'e.g': 'for example', 'i.e': 'that is', 'etc': 'et cetera',
           'vs': 'versus', 'cf': 'compare'}
WORDS = set(TITLES) | set(LABELS) | set(UNITS) | set(PHRASES) | {
    'st', 'mt', 'jr', 'sr', 'a.m', 'p.m', 'inc', 'ltd', 'dept', 'approx'}
TOKEN = r'(?<![\w.@/])(?:' + '|'.join(re.escape(x) for x in sorted(WORDS, key=len, reverse=True)) + r')\.(?![\w./@-])'
# Try the local part once at the start of a run, including long non-email tokens.
EMAIL_LOCAL = r'(?<![\w.+-])[\w.+-]++'
EMAIL_HEAD = EMAIL_LOCAL+'@'
PROTECTED = re.compile(r'\b(?:https?://|www\.)\S+|'+EMAIL_HEAD+
                       r'[\w.-]+\.[A-Za-z]{2,}|\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|edu)\b\S*',re.I)
NAME = re.compile(r'''\s*[\[\]().{}“”"'’]*([^\W\d_])''')
ADDRESS = re.compile(r"\b\d+\s+(?:[A-Z][\w’'-]*\s+){1,5}$")
NUMBER_BEFORE = re.compile(r'(?<!\w)[+-]?\d+(?:[.,:/]\d+)*\s*$')
NAME_BEFORE = re.compile(r'\b[A-Z][\w’\'-]*[,]?\s+$')


def reading(token, before, after):
    word = token[:-1].lower()
    name = NAME.match(after)
    proper = bool(name and name[1].isupper())
    # Address evidence takes priority over title evidence: 12 Lake Dr.
    if word in ('dr', 'st') and ADDRESS.search(before):
        return ('Drive' if word == 'dr' else 'Street'), 'Numbered street address', None
    if word in TITLES:
        if proper:
            return TITLES[word], 'Title before a name or initial', None
        return token, None, 'Title or address abbreviation needs a contextual reading'
    if word == 'st':
        return token, None, 'St. may mean Saint or Street; choose a book-local reading'
    if word == 'mt' and proper:
        return 'Mount', 'Geographic title before a name', None
    if word in ('jr', 'sr') and NAME_BEFORE.search(before):
        return ('Junior' if word == 'jr' else 'Senior'), 'Name suffix', None
    if word in LABELS and re.match(r'\s*\d', after):
        return LABELS[word], 'Numbered source label', None
    if word in ('a.m', 'p.m') and NUMBER_BEFORE.search(before):
        return ('A M' if word == 'a.m' else 'P M'), 'Time following a numeric clock reading', None
    if word in UNITS and token[:-1].islower() and NUMBER_BEFORE.search(before):
        return UNITS[word], 'Dotted physical unit after a numeric quantity', None
    if word in PHRASES:
        return PHRASES[word], 'Conventional prose abbreviation', None
    if word in ('inc', 'ltd') and NAME_BEFORE.search(before):
        return ('Incorporated' if word == 'inc' else 'Limited'), 'Company suffix', None
    if word == 'dept' and proper:
        return 'Department', 'Department label before a name', None
    if word == 'approx' and re.match(r'\s*\d', after):
        return 'approximately', 'Approximation before a numeric quantity', None
    # A p., m., etc. outside these contexts may be a personal initial or a
    # technical label. Do not invent its meaning or erase a scientific name.
    return token, None, None


def expand(text, aliases=None):
    aliases = aliases or {}
    alternatives = [r'(?<!\w)' + re.escape(src) + r'(?!\w)' for src in sorted(aliases, key=len, reverse=True)]
    pattern = re.compile('|'.join(alternatives + [TOKEN]), re.I if not aliases else 0)
    # Alias matching retains the existing case-sensitive contract. The second
    # branch is independently case-insensitive when aliases are present.
    if aliases:
        pattern = re.compile('|'.join(alternatives + ['(?i:' + TOKEN + ')']))
    protected = [m.span() for m in PROTECTED.finditer(text)]
    edits = []; pending = []
    def replace(match):
        literal = match[0]
        if literal in aliases:
            return aliases[literal]
        if any(start <= match.start() < end for start, end in protected):
            return literal
        before, after = text[:match.start()], text[match.end():]
        spoken, reason, uncertain = reading(literal, before, after)
        if uncertain:
            pending.append({'source': literal, 'input_start': match.start(), 'input_end': match.end(), 'reason': uncertain})
        if spoken != literal:
            # A terminal abbreviation period can also terminate its sentence.
            # Titles/numbered labels introduce the following name/value instead.
            word = literal[:-1].lower()
            if word not in TITLES and word not in LABELS and word not in ('mt', 'e.g', 'i.e', 'vs', 'cf', 'dept', 'approx'):
                if not after.strip() or re.match(r'''\s+[“"']?[A-Z]''', after):
                    spoken += '.'
            edits.append({'source': literal, 'spoken': spoken, 'input_start': match.start(), 'input_end': match.end(), 'reason': reason})
        return spoken
    return pattern.sub(replace, text), edits, pending


def sentence_parts(text):
    """Keep unexpanded abbreviations, dotted acronyms and initials attached."""
    start = 0
    for match in re.finditer(r'(?<=[.!?;])\s+', text):
        prefix = text[:match.start()]
        token = prefix.split()[-1] if prefix.split() else ''
        bare = token.lstrip('([\"“')
        if prefix.endswith('.') and (re.fullmatch(TOKEN, bare, re.I) or re.fullmatch(r'(?:[A-Za-z]\.)+', bare)):
            continue
        yield text[start:match.start()]
        start = match.end()
    if start < len(text):
        yield text[start:]


def attached_boundary(tokens, end):
    """Move a word-budget cut before a title/initial rather than after it."""
    titles = set(TITLES.values()) | {'Mount'}
    while end > 1 and (tokens[end-1] in titles or re.fullmatch(r'(?:[A-Z]\.)+', tokens[end-1]) or
                       re.fullmatch(TOKEN, tokens[end-1], re.I) or
                       (end < len(tokens) and tokens[end].rstrip('.,') in ('Junior', 'Senior'))):
        end -= 1
    return end
