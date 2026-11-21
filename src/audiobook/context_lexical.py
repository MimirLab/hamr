"""Conservative lexical readings and backend-native decisions."""
import re
import unicodedata

from . import boundaries
from .context_candidate import Candidate, Component, literal
from .linguistic import LIGATURES
from .spoken_forms import identifier

EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}"
PATH = r'(?<![\w)])(?:\.{0,2}/|[A-Za-z]:\\)[^\s<>"“”]+?(?=$|[\s<>"“”]|[,.!?;:](?=$|\s))'
FILENAME = r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:md|txt|pdf|epub|csv|json|toml|py|html|yaml|wav|m4b|zip)'
OPAQUE_PATTERNS = {'email':('CR-L035',EMAIL),'path':('CR-L036',PATH)}
OPAQUE = re.compile('|'.join(f'(?P<{kind}>{pattern})' for kind,(_,pattern) in OPAQUE_PATTERNS.items()))
PICTOGRAPH = r'[\U0001F300-\U0001FAFF]\ufe0f?[\U0001F3FB-\U0001F3FF]?'

PATTERNS = {
    'corporate': ('CR-L003', r'(?i:Inc\.|Corp\.|Ltd\.)(?!\w)'),
    'initials': ('CR-L005', r'(?:[A-Z]\. ){1,4}[A-Z][a-z]+'),
    'acronym': ('CR-L004', r'{acronyms}'),
    'contraction': ('CR-L006', r"(?:I|you|he|she|we|they)['’]d"),
    'apostrophe': ('CR-L007', r"[A-Za-z]+['’]s"),
    'heteronym': ('CR-L008', r'(?:read|record)'),
    'native_name': ('CR-L010', r'{names}'),
    'grapheme': ('CR-L015', r'[^\W\d_]\u0300*[\u0300-\u036f]+'),
    'mixed_script': ('CR-L016',r'(?=[A-Za-z\u0400-\u052f0-9]{0,63}[\u0400-\u052f])'
                              r'(?=[A-Za-z\u0400-\u052f0-9]{0,63}[A-Za-z])[A-Za-z\u0400-\u052f0-9]{2,64}'),
    'ligature': ('CR-L017', '['+''.join(map(chr,LIGATURES))+']'),
    'elision': ('CR-L018', r"[Oo]['’]er|['’]twas|['’]tis"),
    'ellipsis': ('CR-L021', r'\[…\]|\[\.\.\.\]|…'),
    'interruption': ('CR-L022', r'—'),
    'period': ('CR-L020', r'(?<=\s)\.(?![.\w])'),
    'annotation': ('CR-L029', r'\[sic\]'),
    'ampersand': ('CR-L034', r'&'),
    'shell_token': ('CR-L037', r'\$[A-Z_][A-Z_0-9]*|--[a-zA-Z][a-zA-Z0-9-]*'),
    'implication': ('CR-L038', r'[A-Z]\s*→\s*[A-Z]'),
    'citation': ('CR-L041', r'et al\.'),
    'pictograph': ('CR-L042',PICTOGRAPH+r'(?:\u200d'+PICTOGRAPH+r'){0,8}(?!\u200d)'),
    'censoring': ('CR-L043', r"[A-Za-z]\*{2,8}|(?i:\[redacted\])"),
    'stage_direction': ('CR-L044', r'(?i:\((?:sigh|laugh|gasp|chuckle|cry|cough)\)|\[(?:sighs|laughs|cries|gasps|chuckles|coughs)\])'),
    'indexed_symbol': ('CR-L049', r'[A-Za-z][_^][A-Za-z0-9]{1,8}'),
    'chess_move': ('CR-L040', r'[KQRBN][a-h][1-8]'),
    'unknown_symbol': ('CR-L048', r'[\u27c0-\u27ef\u2980-\u29ff]'),
    'filename': ('CR-L036',FILENAME),
}


def expressions(left, right, abbreviations, native_names=()):
    for kind, (_, pattern) in PATTERNS.items():
        if kind=='acronym':
            pattern='(?:'+'|'.join(map(re.escape,abbreviations)) + ')' if abbreviations else r'(?!)'
        if kind=='native_name':
            pattern='(?:'+'|'.join(map(re.escape,sorted(native_names,key=lambda value:(-len(value),value))))+')' if native_names else r'(?!)'
        if kind in ('grapheme','ligature','interruption','ellipsis','ampersand','annotation','stage_direction','censoring','pictograph'):
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
    if kind=='period':
        return Candidate('named_character',{'character_name':'period'},'period',('governor',))
    if kind=='chess_move':
        return Candidate('literal_identifier',{'retain_identifier':raw,'do_not_infer_a_chess_move':True},raw,('governor',))
    return literal(raw)


def candidates(kind,raw,env):
    form = env.resolver.config['initialisms'].get(raw)
    if kind=='acronym' and form:
        if raw=='US' and not env.cue('country_context',side='right') and not env.cue('country_context'):
            return []
        if form=='word':
            return [Candidate('acronym_word',{'retain_name':raw},raw,('syntax',))]
        letters=list(raw)
        parts=(Component('CR-L019','lexical_initialism',(0,len(raw)),{'letters':letters}),) if raw=='US' else ()
        return [Candidate('initialism_letters',{'letters':letters},' '.join(letters),('syntax',),parts)]
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
            return contraction(raw,subject+' had','had_contraction',env)
        if env.cue('would_verb',side='right'):
            return contraction(raw,subject+' would','would_contraction',env)
        return []
    if kind=='apostrophe':
        if env.cue('present_participle',side='right'):
            return [Candidate('preserved_contraction',{'apostrophe_role':'is_contraction'},None,('governor',))]
        if env.cue('possessed_noun',side='right'):
            return [Candidate('possessive_form',{'apostrophe_role':'possessive'},None,('governor',))]
        return []
    if kind=='heteronym':
        return heteronym(raw,env)
    if kind=='native_name':
        return [Candidate('backend_native_fallback',{'name':raw,'keep_complete_token':True},None,('convention',))]
    if kind=='grapheme':
        glyph=unicodedata.normalize('NFC',raw)
        return [Candidate('canonical_equivalent_text',{'normalized_grapheme':glyph,'retain_original_span':True},None,('syntax',))]
    if kind=='mixed_script':
        parts=[]
        for index,c in enumerate(raw):
            if '\u0400'<=c<='\u052f':
                fields={'retain_character':c,'script':'Cyrillic','unicode':f'U+{ord(c):04X}'}
                parts.append(Component('CR-L016','retain_authored_characters',(index,index+1),fields))
        fields={'scripts':['Cyrillic','Latin'],'exact_identifier':raw}
        return [Candidate('retain_authored_characters',fields,raw,('syntax',),tuple(parts))]
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
        component=() if variable else (Component('CR-L022','literal_cli_syntax',(0,2),{'retain_two_hyphens':True}),)
        return [Candidate('shell_variable' if variable else 'cli_option',
                          {'exact_identifier':raw,**({'do_not_interpret_as_money':True} if variable else {'retain_two_hyphens':True})},
                          raw,('syntax',),component)]
    if kind=='citation':
        return [Candidate('citation_shorthand',{'retain_citation_identity':True},None,('syntax',))]
    if kind=='pictograph' and env.setting('emoji_speech'):
        fields={'sequence':raw,'retain_complete_sequence':True,'do_not_infer_author_emotion':True}
        # Unicode names apply to individual symbols. A sequence can have a
        # meaning that cannot be derived from its component names.
        symbol=raw.removesuffix('\ufe0f')
        name=unicodedata.name(symbol,'').lower() if len(symbol)==1 else ''
        if env.setting('emoji_speech')=='localized_name' and name:
            return [Candidate('localized_symbol_name',{**fields,'name':name,'language':'en'},name,('convention',))]
        return [literal(raw,kind='literal_sequence',fields=fields)]
    if kind=='censoring':
        return [Candidate(('redaction_marker' if raw=='[REDACTED]' else 'literal_marker_text') if raw.startswith('[') else 'authored_censoring',
                          {'retain_brackets':raw.startswith('['),'hidden_word_not_reconstructed':True},raw,('syntax',))]
    if kind=='stage_direction':
        fields={'do_not_execute_unapproved_control':True,'must_not_trigger_vocal_event':True}
        return [Candidate('narrated_stage_direction' if raw.startswith('[') and not env.before.strip() else 'literal_control_like_text',
                          fields, raw,('syntax',))]
    if kind=='implication' and (env.cue('implication') or env.setting('notation')=='propositional_implication'):
        left,right=map(str.strip,raw.split('→'))
        evidence='convention' if env.setting('notation')=='propositional_implication' else 'governor'
        return [Candidate('logical_expression',{'retain_direction':left+'_to_'+right,'do_not_reverse_operands':True},left+' implies '+right,(evidence,))]
    if kind=='indexed_symbol':
        op='_' if '_' in raw else '^'
        base,argument=raw.split(op)
        if env.cue('identifier') or env.cue('configuration',side='right'):
            fields={'exact_identifier':raw,'retain_exact_identifier':True,'do_not_insert_subscript_semantics':True}
            if env.cue('code_language'):
                code_cue=next(s[2] for s in sorted(env.governors) if s[0]=='code_language')
                fields.update(exact_characters=raw,source_language=code_cue.split()[0].casefold())
            parts=(Component('CR-L001','literal_character_sequence',(0,len(raw)),dict(fields)),)
            return [Candidate('machine_identifier',fields,raw,('governor',),parts)]
        notation='ASCII_subscript' if op=='_' else 'ASCII_power'
        if env.cue('notation') or env.cue('index_description',side='right') or env.setting('notation')==notation:
            field,relation=('subscript',' subscript ') if op=='_' else ('power',' to the power of ')
            fields={'base':base,field:argument,'do_not_drop_index':True,'do_not_drop_power':True}
            parts=()
            if env.cue('tex'):
                parts=(Component('CR-L038','mathematical_expression',(0,len(raw)),
                                 {'base_symbol':base,field+'_symbol':argument,'source_language':'tex'}),)
            evidence='convention' if env.setting('notation')==notation else 'governor'
            return [Candidate('math_expression',fields,base+relation+argument,(evidence,),parts)]
    if kind=='chess_move':
        if env.setting('notation')=='inventory' or env.cue('identifier'):
            return [Candidate('literal_identifier',{'retain_identifier':raw,'do_not_infer_a_chess_move':True},raw,('governor',))]
        if env.setting('notation')=='chess' or env.cue('chess'):
            piece={'K':'king','Q':'queen','R':'rook','B':'bishop','N':'knight'}[raw[0]]
            fields={'piece':piece,'destination':raw[1:],'do_not_calculate_new_move':True}
            evidence='convention' if env.setting('notation')=='chess' else 'governor'
            return [Candidate('chess_move',fields,piece+' '+raw[1]+' '+raw[2],(evidence,))]
    if kind=='unknown_symbol' and env.cue('unclassified_symbol'):
        return [literal(raw,kind='literal_fallback',fields={'retain_original_symbol':raw,'record_unsupported_reading':True})]
    if kind=='filename' and env.cue('filename'):
        stem,extension=raw.rsplit('.',1)
        fields={'exact_identifier':raw,'retain_extension':extension}
        return [Candidate('filename',fields,stem+' dot '+' '.join(extension),('governor',))]
    return []


def opaque_reading(kind, raw, env):
    fields={'exact_identifier':raw}
    if kind=='path':
        if env.cue('path'):
            return Candidate('filesystem_path',fields,raw,('governor',))
        return None
    if env.cue('literal_tail',side='right') or env.literal_mention():
        return Candidate('literal_identifier',{**fields,'spell_policy':'characters'},raw,('governor',))
    if env.cue('email_address'):
        punctuation={'.':'dot','_':'underscore','-':'hyphen','+':'plus','@':'at'}
        fragments=re.split(r'([._+@-])',raw)
        spoken=' '.join(punctuation.get(part,identifier(part) if part.isascii() and part.isdigit() else part)
                        for part in fragments if part)
        return Candidate('email_reading',{**fields,'punctuation_owned_by_identifier':True},spoken,('governor',))
    return None


def contraction(raw, expansion, kind, env):
    fields={'expansion':expansion,'source_contraction_preserved':True,'optional_expansion_if_requested':expansion}
    choice=env.setting('expand_contractions')
    source_form=Component('CR-L006','preserved_contraction',(0,len(raw)),dict(fields))
    if choice is False:
        return [Candidate('preserved_contraction',fields,None,('governor','convention'))]
    return [Candidate(kind,fields,expansion if choice else None,('governor','convention') if choice else ('governor',),(source_form,))]


COORDINATOR = re.compile(r'\band\s+$',re.I)
RELATIVE_ASIDE = re.compile(r',\s+(?:who|whose|which)\b[^,]+$',re.I)
CLAUSE_SHIFT = re.compile(r'\b(?:to|when|while|unless|because|if|after|before|who|whose|which|that)\b',re.I)
NOUN_MODIFIER = re.compile(r'\b(?:a|an|the|my|your|his|her|our|their|its|this|that)\s+(?:[A-Za-z]+ly\s+)?$',re.I)


def coordinated_read(env):
    """Find tense evidence in a coordinated verb with an omitted subject."""
    origin,prefix=env.preceding_text(224)
    link=COORDINATOR.search(prefix)
    if not link:
        return None
    clause=re.split(r'[.!?;:\n]',prefix[:link.start()])[-1]
    start=origin+link.start()-len(clause)
    trimmed=clause.rstrip()
    head=trimmed.rstrip(',').rstrip()
    if trimmed.endswith(','):
        relative=RELATIVE_ASIDE.search(head)
        if relative:
            head=head[:relative.start()]
    for group,sense in (('read_coordination_past','past_read'),('read_coordination_base','present_read')):
        pattern=env.resolver.cues[group]['nearby']
        for verb in reversed(list(pattern.finditer(head))):
            before=head[:verb.start()].rsplit(',',1)[-1]
            after=head[verb.end():]
            if CLAUSE_SHIFT.search(after) or re.search(r'\b(?:not|never|if|whether)\b',before,re.I):
                continue
            if sense=='past_read' and NOUN_MODIFIER.search(before):
                continue
            governing=env.resolver.cues['read_base']['left'].search(before.rstrip())
            if bool(governing)!=(sense=='present_read'):
                continue
            env.governors.add((group,'left',verb[0],start+verb.start(),start+verb.end()))
            return sense
    return None


def heteronym(raw,env):
    if raw=='record':
        if env.cue('nominal_record'):
            return [Candidate('noun_stress',{'part_of_speech':'noun','source_spelling_unchanged':True},None,('governor',))]
        if env.cue('verbal_record'):
            return [Candidate('verb_stress',{'part_of_speech':'verb','source_spelling_unchanged':True},None,('governor',))]
        return []
    for group,sense in (('read_participle','past_read'),('read_base','present_read')):
        if env.cue(group):
            return [Candidate('contextual_pronunciation',{'sense':sense,'written_token_unchanged':True},None,('governor',))]
    sense=coordinated_read(env)
    if sense:
        fields={'sense':sense,'written_token_unchanged':True,'syntax_context':'coordinated_verb'}
        return [Candidate('contextual_pronunciation',fields,None,('governor',))]
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
