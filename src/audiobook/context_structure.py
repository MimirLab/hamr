"""Map source-adapter relationships into contextual evidence without rewriting them."""
from bisect import bisect_right
import re
import unicodedata

from .core import key
from .abbreviations import PROTECTED

EVENT_IDS = {'CR-N004','CR-N017','CR-N018','CR-N034','CR-N035','CR-N036','CR-N051','CR-L011','CR-L014','CR-L019','CR-L020','CR-L023',
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


def source_leaves(block, start, stop):
    leaves=block.locator.get('text_leaves',())
    index=bisect_right(leaves,start,key=lambda leaf:leaf['block_span'][1])
    spans=[]
    cursor=start
    while cursor<stop and index<len(leaves):
        leaf=leaves[index]
        first,last=leaf['block_span']
        if first>cursor:
            return None
        end=min(last,stop)
        node_start=leaf['node_start']+cursor-first
        spans.append({'node_rank':leaf['node_rank'],'node_span':[node_start,node_start+end-cursor],
                      'block_span':[cursor,end]})
        cursor=end
        index+=1
    if cursor==stop and len(spans)>1:
        return {'path':block.locator['path'],'spine_occurrence':block.locator['occurrence'],
                'node_basis':'zero-based parsed body descendant index',
                'offset_basis':'half-open Unicode code points','segments':spans}


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
        pronunciation=span.get('pronunciation')
        return 'CR-L014','ruby_reading' if pronunciation else 'base_reading',{
            'base':span.get('base',span['reading']),'annotation':span['annotation'],
            'reading':pronunciation or span['reading'],'speak_once':True},()
    if kind=='lexicon_hint' and span.get('reading') and not (span.get('shadowed_by_inline') or span.get('protected_overlap')):
        readings={s.get('reading') for s in block.locator.get('inline_semantics',[]) if s['kind']=='lexicon_hint'
                  and s['source_start']==span['source_start'] and s['source_end']==span['source_end'] and not s.get('shadowed_by_inline')}
        if len(readings)==1:
            return 'CR-L011','authored_alias',{'use_occurrence_alias':span['reading'],'retain_source_name':raw},()
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


def observations(block, cells, table_fragments, *, source_digest, reading):
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
    if (equation:=block.locator.get('pdf_equation')) and equation['fields']['notation']==source:
        fields={**equation['fields'],'native_ranges':block.locator['native_ranges'],
                'panel_row':block.locator['panel_row'],'panel_column':block.locator['panel_column']}
        add('pdf_equation_cell','source_equation',{'source_span':[0,len(source)],'source':source},fields,
            ('CR-N035','equation',fields,('CR-N051',) if fields['value'].get('exponent') else ()))
    if (inline:=block.locator.get('pdf_inline_formula')) and inline['source']==source:
        for item in inline['formulas']:
            start,end=item['span']
            if source[start:end]==item['source']:
                fields={'script':item['script'],'reading':item['reading']}
                add('pdf_inline','superscript',{'source_span':[start,end],'source':item['source']},fields,
                    ('CR-L030','mathematical_superscript',fields,()))

    if reading.logical_order:
        directions={unicodedata.bidirectional(c) for c in source} if not source.isascii() else set()
        if 'L' in directions and directions & {'R','AL'}:
            fields={'logical_order_retained':True,'do_not_reverse_entire_line':True}
            add('source_order','bidirectional',{'source_span':[0,len(source)],'source':source},fields,
                ('CR-L047','source_logical_order',fields,()))
    if reading.reliable_repetition:
        literal=[(span['source_start'],span['source_end']) for span in spans if span['kind']=='literal']
        for match in re.finditer(r"(?<!\w)([^\W\d_][\w’']*)[ \t]+(\1)(?!\w)",source):
            if block.role!='body' or any(match.start()<b and a<match.end() for a,b in hidden+literal):
                continue
            fields={'two_authored_occurrences':True,'occurrences':[list(match.span(1)),list(match.span(2))]}
            add('source_order','authored_repetition',{'source_span':list(match.span()),'source':match[0]},fields,
                ('CR-L033','retain_authored_repetition',fields,()))
    for overlay in block.locator.get('duplicate_paint_observations',[]):
        start,end=overlay['source_span']
        if source[start:end]!=overlay['text']:continue
        fields={'visible_occurrence_count':1,'observations':[overlay['text']]*2,
                'source_objects':overlay['objects'],'rect':overlay['rect'],'evidence':overlay['evidence']}
        add('pdf_paint','verified_duplicate',{'source_span':[start,end],'source':overlay['text']},fields,
            ('CR-L033','collapse_duplicate_observation',fields,()))
    if block.role=='body' and re.search(r'[^\W\d_]\.($|["”])',source[-3:]):
        stop=source.rfind('.')
        if not any(span['source_start']<=stop<span['source_end'] for span in spans if span['kind'] in ('hidden','literal')):
            if not any(match.start()<=stop<match.end() for match in PROTECTED.finditer(source)):
                fields={'do_not_speak_word_dot':True}
                add('source_boundary','sentence_stop',{'source_span':[stop,stop+1],'source':'.'},fields,
                    ('CR-L020','sentence_boundary',fields,()))

    joins={tuple(join['source_span']):join for join in block.locator.get('line_wrap_joins',[]) if 'source_span' in join}
    if reading.printed_layout:
        for match in re.finditer(r'[A-Za-z]+-\n[ \t]*[a-z]+',source):
            join=joins.get(match.span())
            if join:
                retained=join['kind']=='lexical'
                fields={'derived_word':join['spoken'],'preserve_join_trace':True,'evidence':join['evidence'],
                        'retained_hyphen':retained,'native_region_span':join['native_region_span']}
                candidate='retain_lexical_hyphen' if retained else 'source_evidenced_wrap_join'
            else:
                fields={'retained_hyphen':True}
                candidate='literal_line_break'
            add('print_layout','hyphen_wrap',{'source_span':list(match.span()),'source':match[0]},fields,
                ('CR-L023',candidate,fields,()))

    rows=block.locator.get('rows',[])
    row_headers=block.locator.get('row_header_columns',{})
    row_counts={}
    inherited_cells={}
    hidden_cells={tuple(pair) for pair in block.locator.get('table_hidden_cells',())}
    hidden_rows={index for index,row in enumerate(rows) if row and all((index,column) in hidden_cells for column in range(len(row)))}
    cell_ids=block.locator.get('cell_ids',{})
    pdf_readings={(cell['row'],cell['column']):cell for cell in block.locator.get('pdf_cell_readings',())}
    for _,_,row,column,header,value in cells:
        original=rows[row][column]
        row_counts[row]=row_counts.get(row,0)+1
        if selected:=pdf_readings.get((row,column)):
            fields={**selected['fields'],'header':header,'panel_row':block.locator['panel_row'],
                    'native_ranges':block.locator['pdf_table_cells'][column]['native_ranges']}
            related=('CR-N051',) if selected['event_id']=='CR-N034' and fields.get('exponent') else ()
            add('pdf_table_cell','typed_cell',{'row':row,'column':column,'source':original},fields,
                (selected['event_id'],selected['kind'],fields,related))
        if original.strip() in ('〃','"') and original!=value:
            previous_row=row-1
            while previous_row in hidden_rows:previous_row-=1
            antecedent={'table':block.id,'row':previous_row,'column':column}
            if authored_id:=cell_ids.get(f'{previous_row}:{column}'):
                antecedent['id']=authored_id
            fields={'header':header,'source_marker':original,'value':value,'inherited_value':value,'antecedent':antecedent}
            root=inherited_cells.get((previous_row,column),antecedent)
            inherited_cells[row,column]=root
            if root is not antecedent:fields['value_source']=root
            add('table_relation','inherited_cell',{'row':row,'column':column,'source':original},fields,
                ('CR-L032','inherited_cell_value',fields,()))
        columns=row_headers.get(str(row),row_headers.get(row,[]))
        preceding=[n for n in columns if n<column and (row,n) not in hidden_cells]
        if preceding:
            headers=[rows[0][n] for n in preceding]+[header]
            fields={'header_path':headers,'row_header':' / '.join(rows[row][n] for n in preceding),'value':value}
            add('table_relation','header_path',{'row':row,'column':column,'source':original},fields,
                ('CR-L045','table_relational_narration',fields,()))
    if row_counts:
        fields={'cell_boundaries_preserved':True,'cross_cell_fraction_not_manufactured':True}
        add('table_relation','table',{}, {'row_count':len(row_counts),'column_count':max(row_counts.values())},
            ('CR-L045','table_relational_narration',fields,()))
    if table_fragments:
        fields={'cell_boundaries_preserved':True,'cross_cell_fraction_not_manufactured':True}
        add('table_relation','table_fragments',{}, {'fragments':table_fragments},
            ('CR-L045','companion_object',fields,()))
    return records
