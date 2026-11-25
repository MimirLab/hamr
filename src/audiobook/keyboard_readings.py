"""Describe written keyboard combinations and compare the keys in speech."""

import re

from .spoken_forms import identifier, number


MODIFIERS = {
    'ctrl': 'Control', 'control': 'Control', 'cmd': 'Command', 'command': 'Command',
    'alt': 'Alt', 'option': 'Option', 'shift': 'Shift', 'super': 'Super', 'meta': 'Meta',
}
MODIFIER = '(?i:' + '|'.join(MODIFIERS) + ')'
KEY_NAMES = {
    'esc': 'Escape', 'del': 'Delete', 'ins': 'Insert',
    'pgup': 'Page Up', 'pgdn': 'Page Down',
}
KEY = (r'(?i:page[ \t]+(?:up|down)|backspace|delete|insert|escape|return|enter|'
       r'space|home|end|tab|left|right|up|down|esc|del|ins|pgup|pgdn|F(?:2[0-4]|1[0-9]|[1-9])|[a-z0-9])')
FUNCTION_KEYS = '|'.join(number(index) for index in range(24,0,-1))
SPOKEN_KEY = rf'(?:(?i:F[ \t]+(?:{FUNCTION_KEYS}))|{KEY})'
CHORD_JOIN = r'(?:[ \t]*\+[ \t]*|[-‐‑])'
CHORD = rf'(?:{MODIFIER}{CHORD_JOIN}){{1,3}}{KEY}'
# These four legacy Control prefixes take a subsequent key in authoring
# manuals that use the caret convention. The letters must be printed uppercase.
CARET = r'(?<![\\\[\^])\^(?:[KOPQ][A-Z0-9]|[A-Z])'
EXPRESSION = rf'(?:{CHORD}|{CARET})(?![|+*^=])'

CHORD_PART = re.compile(CHORD_JOIN)
SPOKEN_SEPARATOR = rf'(?:[ \t]+|{CHORD_JOIN})'
SPOKEN_MODIFIER = rf'(?P<modifiers>{MODIFIER}(?:{SPOKEN_SEPARATOR}{MODIFIER}){{0,2}})'
WORD_START = r'(?<![\w@./])'
MODIFIER_SITE = re.compile(WORD_START+MODIFIER+SPOKEN_SEPARATOR)
SPOKEN_CHORD = re.compile(rf'{WORD_START}{SPOKEN_MODIFIER}{SPOKEN_SEPARATOR}(?P<key>{SPOKEN_KEY})'
                          r'(?![\w+/]|[-‐‑.][a-zA-Z0-9])')


def key_name(value):
    word = ' '.join(value.split()).casefold()
    if len(word)==1:
        return identifier(word) if word.isdigit() else word.upper()
    if word.startswith('f') and word[1:].isdigit():
        return 'F '+number(int(word[1:]))
    if word.startswith('f '):
        return 'F '+word[2:]
    return KEY_NAMES.get(word,word.title())


def describe(surface):
    if surface.startswith('^'):
        strokes = [{'modifiers':['Control'],'key':surface[1]}]
        if len(surface)==3:
            strokes.append({'modifiers':[],'key':surface[2]})
    else:
        *modifiers,key = CHORD_PART.split(surface)
        modifiers = [MODIFIERS[word.casefold()] for word in modifiers]
        if len(set(modifiers))!=len(modifiers):
            return None
        strokes = [{'modifiers':modifiers,'key':key}]
    spoken = ', then '.join(' '.join((*step['modifiers'],key_name(step['key']))) for step in strokes)
    return strokes,spoken


def observation(expected, observed, word_context):
    """Reconcile printed ASR shortcuts only at a matching spoken key site."""
    if not MODIFIER_SITE.search(observed):
        return observed

    def signature(match):
        mods = tuple(MODIFIERS[word.casefold()] for word in re.findall(MODIFIER,match['modifiers']))
        return mods,key_name(match['key'])

    forms = {(signature(match),context):match[0] for match in SPOKEN_CHORD.finditer(expected)
             if any(context:=word_context(expected,*match.span()))}

    def replace(match):
        location = signature(match),word_context(observed,*match.span())
        return forms.get(location,match[0])

    return SPOKEN_CHORD.sub(replace,observed) if forms else observed
