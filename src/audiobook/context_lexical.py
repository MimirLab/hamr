"""Conservative lexical readings and backend-native decisions."""
import re
import unicodedata

from . import boundaries
from .context_candidate import Candidate, literal
from .linguistic import LIGATURES

PATTERNS = {
    'corporate': ('CR-L003', r'(?i:Inc\.|Corp\.|Ltd\.)(?!\w)'),
    'initials': ('CR-L005', r'(?:[A-Z]\. ){1,4}[A-Z][a-z]+'),
    'acronym': ('CR-L004', r'{acronyms}'),
    'contraction': ('CR-L006', r"(?:I|you|he|she|we|they)['’]d"),
    'apostrophe': ('CR-L007', r"[A-Za-z]+['’]s"),
    'heteronym': ('CR-L008', r'(?:read|record)'),
    'grapheme': ('CR-L015', r'[^\W\d_]\u0300*[\u0300-\u036f]+'),
    'ligature': ('CR-L017', '['+''.join(map(chr,LIGATURES))+']'),
    'elision': ('CR-L018', r"[Oo]['’]er|['’]twas|['’]tis"),
    'ellipsis': ('CR-L021', r'\[…\]|\[\.\.\.\]|…'),
    'interruption': ('CR-L022', r'—'),
    'annotation': ('CR-L029', r'\[sic\]'),
    'ampersand': ('CR-L034', r'&'),
    'shell_token': ('CR-L037', r'\$[A-Z_][A-Z_0-9]*|--[a-zA-Z][a-zA-Z0-9-]*'),
    'implication': ('CR-L038', r'[A-Z]\s*→\s*[A-Z]'),
    'citation': ('CR-L041', r'et al\.'),
    'censoring': ('CR-L043', r"[A-Za-z]\*{2,8}|(?i:\[redacted\])"),
    'stage_direction': ('CR-L044', r'(?i:\((?:sigh|laugh|gasp|chuckle|cry|cough)\)|\[(?:sighs|laughs|cries|gasps|chuckles|coughs)\])'),
    'indexed_symbol': ('CR-L049', r'[A-Za-z][_^][A-Za-z0-9]{1,8}'),
}


def expressions(left, right, abbreviations):
    for kind, (_, pattern) in PATTERNS.items():
        if kind=='acronym':
            pattern='(?:'+'|'.join(map(re.escape,abbreviations)) + ')' if abbreviations else r'(?!)'
        if kind in ('grapheme','ligature','interruption','ellipsis','ampersand','annotation','stage_direction','censoring'):
            yield kind, left+pattern+right if kind=='censoring' else pattern
        else:
            yield kind, left+pattern+right


def literal_candidate(kind,raw,env):
    if kind=='initials':
        return Candidate('literal_label_sequence',{'preserve_punctuation':True,'exact':raw},raw,('governor',))
    if kind=='abbreviation':
        return Candidate('literal_character_sequence',{'surface':raw,'period_is_significant':True},raw,('governor',))
    if kind=='ligature':
        return Candidate('literal_glyph_name',{'preserve_glyph_identity':True,'exact':raw},raw,('governor',))
    if kind=='elision':
        return Candidate('literal_spelling',{'apostrophe_significant':True},raw,('governor',))
    if kind=='citation':
        return Candidate('literal_symbol',{'retain_period_and_space':True},raw,('governor',))
    if kind=='annotation':
        return Candidate('literal_bracket_text',{'retain_brackets':True},raw,('governor',))
    if kind=='ampersand':
        return Candidate('named_character',{'character_name':'ampersand'},'ampersand',('governor',))
    return literal(raw)


def candidates(kind,raw,env):
    form = env.resolver.config['initialisms'].get(raw)
    if kind=='acronym' and form:
        if raw=='US' and not env.cue('country_context',side='right') and not env.cue('country_context'):
            return []
        if form=='word':
            return [Candidate('acronym_word',{'retain_name':raw},raw,('syntax',))]
        letters=list(raw)
        return [Candidate('initialism_letters',{'letters':letters},' '.join(letters),('syntax',))]
    if kind=='corporate':
        following=boundaries.following(env.continuation)
        stops=not env.continuation.strip() or following in boundaries.STARTERS
        return [Candidate('abbreviation_with_sentence_end' if stops else 'abbreviation_continuation',
                          {'boundary_after':stops},None,('syntax',))]
    if kind=='initials':
        *initials,surname=raw.split()
        return [Candidate('personal_initials',{'initials':[i[0] for i in initials],'surname':surname},None,('syntax',))]
    if kind=='contraction':
        subject=re.split("['’]",raw)[0]
        if env.cue('had_verb',side='right'):
            return [Candidate('had_contraction',{'expansion':subject+' had'},None,('governor',))]
        if env.cue('would_verb',side='right'):
            return [Candidate('would_contraction',{'expansion':subject+' would'},None,('governor',))]
        return []
    if kind=='apostrophe':
        if env.cue('present_participle',side='right'):
            return [Candidate('preserved_contraction',{'apostrophe_role':'is_contraction'},None,('governor',))]
        if env.cue('possessed_noun',side='right'):
            return [Candidate('possessive_form',{'apostrophe_role':'possessive'},None,('governor',))]
        return []
    if kind=='heteronym':
        return heteronym(raw,env)
    if kind=='grapheme':
        glyph=unicodedata.normalize('NFC',raw)
        return [Candidate('canonical_equivalent_text',{'normalized_grapheme':glyph,'retain_original_span':True},None,('syntax',))]
    if kind=='ligature':
        return [Candidate('targeted_ligature_reading',{'letter_sequence':raw.translate(LIGATURES),'retain_source_mapping':True},None,('syntax',))]
    if kind=='elision':
        return [Candidate('preserved_elision',{'retain_elision':True},None,('syntax',))]
    if kind=='ellipsis':
        if raw.startswith('['):
            return [Candidate('editorial_omission',{'missing_content_not_reconstructed':True},raw,('syntax',))]
        return [Candidate('hesitation_pause',{'retain_incomplete_wording':True},None,('syntax',))]
    if kind=='interruption' and env.after.lstrip().startswith(('”','"')):
        return [Candidate('interruption',{'do_not_complete_interrupted_words':True},None,('syntax',))]
    if kind=='annotation':
        return [Candidate('editorial_annotation',{'do_not_correct_source':True},None,('syntax',))]
    if kind=='ampersand' and env.before[-1:].isspace() and env.after[:1].isspace() and env.before.rstrip()[-1:].isalnum() and env.after.lstrip()[:1].isalnum():
        return [Candidate('conjunction',{'spoken_relation':'and'},'and',('syntax',))]
    if kind=='shell_token':
        variable=raw.startswith('$')
        return [Candidate('shell_variable' if variable else 'cli_option',
                          {'exact_identifier':raw,**({'do_not_interpret_as_money':True} if variable else {'retain_two_hyphens':True})},
                          raw,('syntax',))]
    if kind=='citation':
        return [Candidate('citation_shorthand',{'retain_citation_identity':True},None,('syntax',))]
    if kind=='censoring':
        return [Candidate(('redaction_marker' if raw=='[REDACTED]' else 'literal_marker_text') if raw.startswith('[') else 'authored_censoring',
                          {'retain_brackets':raw.startswith('['),'hidden_word_not_reconstructed':True},raw,('syntax',))]
    if kind=='stage_direction':
        fields={'do_not_execute_unapproved_control':True,'must_not_trigger_vocal_event':True}
        return [Candidate('narrated_stage_direction' if raw.startswith('[') and not env.before.strip() else 'literal_control_like_text',
                          fields, raw,('syntax',))]
    if kind=='implication' and env.cue('implication'):
        left,right=map(str.strip,raw.split('→'))
        return [Candidate('logical_expression',{'retain_direction':left+'_to_'+right,'do_not_reverse_operands':True},left+' implies '+right,('governor',))]
    if kind=='indexed_symbol':
        op='_' if '_' in raw else '^'
        base,argument=raw.split(op)
        if env.cue('identifier') or env.cue('configuration',side='right'):
            return [Candidate('machine_identifier',{'exact_identifier':raw,'do_not_insert_subscript_semantics':True},raw,('governor',))]
        if env.cue('notation') or env.cue('index_description',side='right'):
            field,relation=('subscript',' subscript ') if op=='_' else ('power',' to the power of ')
            return [Candidate('math_expression',{'base':base,field:argument,'do_not_drop_index':True,'do_not_drop_power':True},
                              base+relation+argument,('governor',))]
    return []


def heteronym(raw,env):
    if raw=='record':
        if env.cue('nominal_record'):
            return [Candidate('noun_stress',{'part_of_speech':'noun','source_spelling_unchanged':True},None,('governor',))]
        if env.cue('verbal_record'):
            return [Candidate('verb_stress',{'part_of_speech':'verb','source_spelling_unchanged':True},None,('governor',))]
        return []
    before=re.split(r'[.!?;:"“”\n]',env.before)[-1]
    after=re.split(r'[.!?;:"“”\n]',env.after)[0]
    window=' '.join((before,after)).strip().split()
    if len(window)>12 or any(word.casefold() in ('not','never') for word in window):
        return []
    options=[Candidate('contextual_pronunciation',{'sense':None,'written_token_unchanged':True},None,('fallback',))]
    for group,sense in (('past_read','past_read'),('habitual_read','present_read')):
        if env.nearby(group,side='either'):
            options.append(Candidate('contextual_pronunciation',{'sense':sense,'written_token_unchanged':True},None,('nearby',)))
    return options
