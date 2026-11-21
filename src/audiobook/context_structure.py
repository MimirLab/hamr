"""Map source-adapter relationships into contextual evidence without rewriting them."""
import re
import unicodedata

from .core import key
from .abbreviations import PROTECTED

EVENT_IDS = {'CR-N036','CR-L011','CR-L012','CR-L013','CR-L014','CR-L019','CR-L020','CR-L023',
             'CR-L024','CR-L025','CR-L026','CR-L027','CR-L030','CR-L031','CR-L032','CR-L033','CR-L045','CR-L046','CR-L047'}
BOUND_INLINE = {
    'emphasis': ('CR-L026','contrastive_emphasis'),
    'title_style': ('CR-L026','title_style'),
    'line_break': ('CR-L024','verse_break'),
    'math_vector': ('CR-L027','vector_symbol'),
    'generated_label': ('CR-L046','generated_structural_label'),
    'object_label': ('CR-L046','authored_description'),
}
RAISED = re.compile(r'[⁰¹²³⁴⁵⁶⁷⁸⁹]+')


def inline_reading(block, span, raw):
    kind=span['kind']
    if kind=='note_reference':
        href=span.get('href')
        link=next((link for link in block.links if link.get('kind')=='note' and link.get('source_start')==span['source_start']
                   and not link.get('issue')),None)
        if not href or not link:
            return
        target=link['href'].rsplit('#',1)[-1].removeprefix('markdown-note:')
        fields={'reference_target':target,'target_id':target,'target':target,'do_not_delete_note_content':True}
        marker=raw.strip('()[] ')
        if marker and all(c.isdigit() for c in marker):
            marker=''.join(str(unicodedata.digit(c)) for c in marker)
            fields.update(marker=marker,marker_value=marker)
        related=('CR-N036',) if RAISED.fullmatch(raw) else ()
        return 'CR-L030','note_reference',fields,related
    if kind=='math_script':
        power=span['relation']=='power'
        reading='mathematical_superscript' if power else 'mathematical_subscript'
        fields={'base':span['base'],'exponent' if power else 'index':span['value'],'do_not_route_as_footnote':True}
        return 'CR-L030',reading,fields,('CR-N036',)
    if kind=='hyperlink' and span.get('href'):
        href=span['href']
        if href.startswith('mailto:') and href[7:]==raw:
            return 'CR-L031','spoken_destination',{'retain_exact_destination':raw,'href':href},()
        return 'CR-L031','visible_link_label',{'label':raw,'href':href,'retain_target_in_companion':True},()
    if kind=='ruby':
        return 'CR-L014','base_reading',{'base':span.get('base',span['reading']),'annotation':span['annotation'],
                                         'reading':span['reading'],'speak_once':True},()
    if kind=='lexicon_hint' and span.get('reading') and not (span.get('shadowed_by_inline') or span.get('protected_overlap')):
        readings={s.get('reading') for s in block.locator.get('inline_semantics',[]) if s['kind']=='lexicon_hint'
                  and s['source_start']==span['source_start'] and s['source_end']==span['source_end'] and not s.get('shadowed_by_inline')}
        if len(readings)==1:
            return 'CR-L011','authored_alias',{'use_occurrence_alias':span['reading'],'retain_source_name':raw},()
    if kind=='language' and raw.strip() and span.get('language'):
        language=span['language']
        return 'CR-L012','configured_native_fallback',{'span_language':language,'retain_token':raw,'do_not_translate':True,
                                                     'qualified_speech':language.split('-')[0].casefold()=='en'},()
    if kind=='display_case' and raw.isascii():
        display=span['transform']
        if display=='uppercase' and raw!=raw.upper():
            fields={'word':raw,'underlying_text':raw,'display_case':display,'do_not_infer_initialism_from_display':True,
                    'display_transform_is_not_lexical_case':True}
            if raw.casefold() in ('us','i','we','me','my','you','they','them','he','him','she','her','it'):
                fields['lexical_role']='pronoun'
            return 'CR-L019','underlying_word_reading',fields,()
    if kind=='abbreviation_label':
        expansion=span['expansion']
        letters=''.join(word[0].upper() for word in re.findall(r'[A-Za-z]+',expansion))
        if re.fullmatch('[A-Z]{2,10}',raw) and letters==raw:
            return 'CR-L019','lexical_initialism',{'letters':list(raw),'authored_expansion':expansion},()
    if kind=='soft_wrap':
        before=block.source[:span['source_start']].rstrip()
        after=block.source[span['source_end']:].lstrip()
        if before and not re.search(r'[.!?]["”’\')\]]*$',before) and re.match(r'^[a-z]',after):
            return 'CR-L025','same_sentence_continuation',{'do_not_add_sentence_stop':True},()
    if kind in BOUND_INLINE:
        event,candidate=BOUND_INLINE[kind]
        fields={
            'emphasis':{'marked_text':raw,'retain_negation':raw.casefold() in ('no','not','never')},
            'title_style':{'title':raw,'do_not_insert_word_italic_by_default':True},
            'line_break':{'retain_authored_line_boundary':True},
            'math_vector':{'symbol':raw,'retain_vector_role':True},
            'object_label':{'source_label':span.get('reading'),'decorative':span.get('decorative',False)},
            'generated_label':{'source_label':span.get('reading')},
        }[kind]
        return event,candidate,fields,()
    return None


def observations(block, cells, *, source_digest, language):
    """Use exact spans or named cell locators. Existing source readings still render speech."""
    source=block.source
    spans=block.locator.get('inline_semantics',[])
    hidden=[(span['source_start'],span['source_end']) for span in spans if span['kind']=='hidden']
    records=[]

    def add(provider, kind, location, fields, selected=None):
        row={'provider':provider,'kind':kind,**location,'fields':fields}
        if selected:
            event,candidate,semantics,related=selected
            row.update(event_id=event,occurrence_id=key([source_digest,block.id,provider,event,location]),
                       selected_reading={'kind':candidate,'fields':semantics})
            if related:row['related_event_ids']=list(related)
        records.append(row)

    for span in spans:
        a,b=span['source_start'],span['source_end']
        kind=span['kind']
        if kind in ('anchor','hidden') or any(left<=a<=b<=right for left,right in hidden):
            continue
        raw=source[a:b]
        if not 0<=a<=b<=len(source) or raw!=span.get('source',raw):
            continue
        fields={k:v for k,v in span.items() if k not in ('source_start','source_end','source','kind')}
        add('inline_source',kind,{'source_span':[a,b],'source':raw},fields,inline_reading(block,span,raw))
    for link in block.links:
        if link['kind']=='hyperlink' and not any(span['kind']=='hyperlink' for span in spans):
            add('source_link','hyperlink',{}, {'target':link['href'],'label':link.get('label')})

    if block.locator.get('format') in ('epub','markdown','text'):
        directions={unicodedata.bidirectional(c) for c in source} if not source.isascii() else set()
        if 'L' in directions and directions & {'R','AL'}:
            fields={'logical_order_retained':True,'do_not_reverse_entire_line':True}
            add('source_order','bidirectional',{'source_span':[0,len(source)],'source':source},fields,
                ('CR-L047','source_logical_order',fields,()))
        literal=[(span['source_start'],span['source_end']) for span in spans if span['kind']=='literal']
        for match in re.finditer(r"(?<!\w)([^\W\d_][\w’']*)[ \t]+(\1)(?!\w)",source):
            if block.role!='body' or any(match.start()<b and a<match.end() for a,b in hidden+literal):
                continue
            fields={'two_authored_occurrences':True,'occurrences':[list(match.span(1)),list(match.span(2))]}
            add('source_order','authored_repetition',{'source_span':list(match.span()),'source':match[0]},fields,
                ('CR-L033','retain_authored_repetition',fields,()))
    lang=block.locator.get('language',language)
    if lang and lang.split('-')[0].casefold()=='ar' and re.search(r'[\u0620-\u064a]',source):
        fields={'span_language':lang,'preserve_unvocalized_source':True,'qualified_speech':False}
        add('source_language','unqualified_script',{'source_span':[0,len(source)],'source':source},fields,
            ('CR-L013','native_fallback',fields,()))

    if block.role=='body' and re.search(r'[^\W\d_]\.($|["”])',source[-3:]):
        stop=source.rfind('.')
        if not any(span['source_start']<=stop<span['source_end'] for span in spans if span['kind'] in ('hidden','literal')):
            if not any(match.start()<=stop<match.end() for match in PROTECTED.finditer(source)):
                fields={'do_not_speak_word_dot':True}
                add('source_boundary','sentence_stop',{'source_span':[stop,stop+1],'source':'.'},fields,
                    ('CR-L020','sentence_boundary',fields,()))

    joins={join['original']:join for join in block.locator.get('line_wrap_joins',[])}
    if block.locator.get('format')=='pdf':
        for match in re.finditer(r'[A-Za-z]+-\n[a-z]+',source):
            join=joins.get(match[0])
            fields={'derived_word':join['spoken'],'preserve_join_trace':True,'evidence':join['evidence']} if join else {'retained_hyphen':True}
            candidate='source_evidenced_wrap_join' if join else 'retain_lexical_hyphen'
            add('print_layout','hyphen_wrap',{'source_span':list(match.span()),'source':match[0]},fields,
                ('CR-L023',candidate,fields,()))

    rows=block.locator.get('rows',[])
    row_headers=block.locator.get('row_header_columns',{})
    row_counts={}
    for _,_,row,column,header,value in cells:
        original=rows[row][column]
        row_counts[row]=row_counts.get(row,0)+1
        if original.strip() in ('〃','"') and original!=value:
            fields={'header':header,'source_marker':original,'value':value,'inherited_value':value,
                    'antecedent':{'table':block.id,'row':row-1,'column':column}}
            add('table_relation','inherited_cell',{'row':row,'column':column,'source':original},fields,
                ('CR-L032','inherited_cell_value',fields,()))
        columns=row_headers.get(str(row),row_headers.get(row,[]))
        preceding=[n for n in columns if n<column]
        if preceding:
            headers=[rows[0][n] for n in preceding]+[header]
            fields={'header_path':headers,'row_header':' / '.join(rows[row][n] for n in preceding),'value':value}
            add('table_relation','header_path',{'row':row,'column':column,'source':original},fields,
                ('CR-L045','table_relational_narration',fields,()))
    if row_counts:
        fields={'cell_boundaries_preserved':True,'cross_cell_fraction_not_manufactured':True}
        add('table_relation','table',{}, {'row_count':len(row_counts),'column_count':max(row_counts.values())},
            ('CR-L045','table_relational_narration',fields,()))
    return records
