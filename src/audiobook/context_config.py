"""Validate explicit, data-only contextual reading settings."""
import json
import re
from pathlib import Path

from .core import Error, filehash, key

DEFAULT_PATH = Path(__file__).with_name('context_rules.json')
DEFAULT = json.loads(DEFAULT_PATH.read_text(encoding='utf-8'))
CONVENTIONS = {
    'date_order': {'MD', 'DM', 'YMD'},
    'number_locale': {'en-US', 'en-GB', 'de-DE', 'fr-FR'},
    'dollar_currency': {'USD', 'CAD', 'AUD', 'NZD'},
    'accounting_parentheses': {True, False},
    'duration_fields': {'minutes_seconds', 'hours_minutes'},
    'notation': {'propositional_implication','ASCII_subscript','ASCII_power','chess','inventory'},
    'emoji_speech': {'literal','localized_name'},
    'ditto_marker': {'double_quote','ditto'},
    'expand_contractions': {True,False},
    'expand_citations': {True,False},
    'narration_style': {'natural','technical'},
}
BOOLEAN_CONVENTIONS={'accounting_parentheses','expand_contractions','expand_citations'}
KEYBOARD_CUES={'keyboard_context','keyboard_action','pattern_syntax'}


def invalid(message):
    raise Error('context_config', message, 'Check the contextual reading settings and try again.')


def read(path):
    with path.open('rb') as file:
        data = file.read(128 * 1024 + 1)
    if len(data) > 128 * 1024:
        invalid('Context settings or review data exceed 128 KiB.')
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise Error('context_config', f'Invalid context JSON in {path.name}.') from exc


def conventions(value):
    if not isinstance(value, dict) or value.keys() - CONVENTIONS.keys():
        invalid('Unknown context convention.')
    for name, option in value.items():
        if type(option) is not (bool if name in BOOLEAN_CONVENTIONS else str) or option not in CONVENTIONS[name]:
            invalid(f'Unsupported value for {name}.')
    return dict(value)


def effective(value=None, *, keyboard=True, narrative_clocks=True, character_inventories=True):
    value = {} if value is None else value
    if not isinstance(value, dict) or value.keys() - DEFAULT.keys() or value.get('schema_version', 1) != 1:
        invalid('Unknown context settings key or schema version.')
    config = {**DEFAULT, **value}
    if config['mode'] not in ('automatic', 'strict'):
        invalid('Context mode must be automatic or strict.')
    margin = config['minimum_margin']
    if type(margin) is not int or not 1 <= margin <= 100:
        invalid('Context minimum_margin must be an integer from 1 through 100.')
    weights, cues = config['weights'], config['cues']
    if not isinstance(weights, dict) or weights.keys() - DEFAULT['weights'].keys():
        invalid('Unknown context weight.')
    if any(type(weight) is not int or not 0 <= weight <= 100 for weight in weights.values()):
        invalid('Context weights must be integers from 0 through 100.')
    inactive = set() if keyboard else KEYBOARD_CUES.copy()
    if not narrative_clocks:
        inactive.add('clock_narrative')
    if not character_inventories:
        inactive.add('character_inventory')
    cue_defaults = {name:phrases for name,phrases in DEFAULT['cues'].items() if name not in inactive}
    if not isinstance(cues, dict) or cues.keys() - DEFAULT['cues'].keys():
        invalid('Unknown context cue group.')
    for group, phrases in cues.items():
        if (not isinstance(phrases, list) or len(phrases) > 100 or any(
                not isinstance(phrase, str) or not re.fullmatch(r"[\w][\w '\u2019-]{0,79}", phrase, re.UNICODE)
                or not phrase[-1].isalnum() for phrase in phrases)):
            invalid(f'Context cue group {group} must contain short literal phrases.')
    merged_cues={name: sorted(set(p.casefold() for p in cues.get(name,phrases)))
                 for name,phrases in cue_defaults.items()}
    lexicon=config['lexicon']
    if not isinstance(lexicon,dict) or len(lexicon)>128:
        invalid('Context lexicon must map short dotted tokens to cue-based readings.')
    for token,readings in lexicon.items():
        if (not isinstance(token,str) or not re.fullmatch(r'[a-z]{1,10}\.',token)
                or not isinstance(readings,dict) or not readings or readings.keys()-merged_cues.keys()
                or any(not isinstance(s,str) or not re.fullmatch(r"[A-Za-z]+(?:[ '-][A-Za-z]+){0,7}",s) for s in readings.values())):
            invalid('A context lexicon entry needs a lowercase dotted token and plain-word readings keyed by known cue groups.')
    merged_lexicon={token:dict(sorted(readings.items())) for token,readings in {**DEFAULT['lexicon'],**lexicon}.items()}
    initialisms=config['initialisms']
    if (not isinstance(initialisms,dict) or len(initialisms)>128 or any(
            not isinstance(token,str) or not re.fullmatch(r'[A-Z]{2,10}',token) or style not in ('word','letters')
            for token,style in initialisms.items())):
        invalid('Context initialisms must map uppercase tokens to word or letters.')
    native_names=config['native_names']
    if not isinstance(native_names,list) or len(native_names)>128 or any(
            not isinstance(name,str) or not re.fullmatch(r"[^\W\d_][^\W\d_ '\u2019-]{0,79}",name)
            for name in native_names):
        invalid('Context native_names must contain short exact names.')
    return {**config, 'weights': {**DEFAULT['weights'], **weights}, 'cues': merged_cues, 'lexicon':merged_lexicon,
            'conventions': conventions(config['conventions']), 'native_names': sorted(set(native_names))}


def scopes(value):
    if not isinstance(value, dict) or value.keys() - {'conventions', 'chapters', 'tables'}:
        invalid('Source-bound context accepts conventions, chapters and tables.')

    def scoped(group):
        entries = value.get(group, {})
        if not isinstance(entries, dict) or any(not isinstance(name,str) or not name or len(name) > 200 for name in entries):
            invalid(f'Context {group} must map exact source IDs or chapter names to conventions.')
        return {name: conventions(values) for name, values in entries.items()}

    return conventions(value.get('conventions', {})), scoped('chapters'), scoped('tables')


def implementation(*, unified=True, rule_revision=None):
    if not unified:
        # The contextual implementation shipped with faithful-17 and dev7.
        return '163ddcdc598df9b419aba3de219f26bd20bd5912c331e729ed921fd98c579287'
    if rule_revision=='faithful-18':
        return 'dae46ffba0f49f2a1ba6f415a8f6c878a3829bf188ebdce5936d99fc9529415c'
    if rule_revision=='faithful-19':
        return 'd131d2ad28edf7394449cebfea37a43cd5440fe11db06417488e0d3074a6f036'
    if rule_revision=='faithful-20':
        return '996d510e1617f02aa68f5539c9a48d2d603b3d19580fb1f7a0d385dcf2066287'
    if rule_revision=='faithful-21':
        return 'eb6ca8979e18251cfd982133424cb7ea2f26175d7bd0969c52426cfa3329b473'
    if rule_revision=='faithful-22':
        return '152e0fdc935a0f515685b688feda123be0e509c6c22af9b63d09c59a29a0079d'
    if rule_revision in ('faithful-23','faithful-24','faithful-25'):
        return 'f6fc8d74d0bf3e14fc816ca16bb7ea1e80a880044d21dc9b7f6a51591b800808'
    if rule_revision=='faithful-26':
        return '7a7ccafaac0593f93185a12edb3967d466ee90517593bcbdbb935d55ebcd0a4d'
    if rule_revision=='faithful-27':
        return 'e77ddecde1fe3dfb9ff76f5c45b1cea0f180009ef05047f8c045c3e2b764b344'
    if rule_revision=='faithful-28':
        return 'df772e6534764093c6bd95de9ac806fad6c9f617b2791f7f4369a9bd586faf8d'
    if rule_revision=='faithful-29':
        return 'badb69c8d1a7854caec4656673fde7b12efee39930e3f2a10c674ff92df6b925'
    if rule_revision=='faithful-32':
        return '2054a5539eea86174aa51033fc7776137e7e16b73317e88debe0257a6d4f2492'
    if rule_revision=='faithful-31':
        return 'cad5c7c3d09fed2d8ca3f1957b9d354dec6f0fbe5f0b7cca54cea6358b066ad7'
    if rule_revision=='faithful-30':
        return '62a3240b8f66419b85dad0c354292293032bd00e20f41f89a9064de6c177c31e'
    if rule_revision=='faithful-33':
        return '8afaf28d089c87fdf3ff603430a990aec513f41cd0d8c14faf42e4d91a309ed1'
    if rule_revision=='faithful-34':
        return '006f24afd267d795b8884a1b7df755bbc3698cfc810e8f66de8047417c564a50'
    if rule_revision=='faithful-35':
        return 'e6ad083248b88811a5abfaa8219b0fc519d1632b4851e686829bd3842b50484b'
    return key({name: filehash(Path(__file__).with_name(name))
                for name in ('context_config.py','contextual.py','context_grammar.py','context_candidate.py','context_forms.py','context_lexical.py','context_numbers.py',
                             'context_structure.py','context_reviewer.py','context_rules.json','abbreviations.py','boundaries.py','epub_semantics.py','epub_styles.py','linguistic.py','pronunciation.py',
                             'markdown_notes.py','pdf_formula.py','pdf_geometry.py','pdf_layout.py','pdf_overlays.py','pdf_rules.py','pdf_tables.py','pdf_table_readings.py',
                             'pdf_words.py','pdf_reading_spans.py','pdf_scripts.py','pdf_literals.py','source_passages.py','source_numbering.py',
                             'source_math.py','source_arithmetic.py','source_notes.py','source_scripts.py','tex_math.py',
                             'epub_notes.py','html_notes.py','keyboard_readings.py','spoken_forms.py','table_readings.py','typed_readings.py')})
