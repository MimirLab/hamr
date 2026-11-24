"""Preserve explicit XHTML roles before plain-text concatenation loses them."""
import re
import unicodedata
from bs4 import Comment, Declaration, Doctype, NavigableString, ProcessingInstruction
from .source_math import MATH_SPANS, mathml, speak as speak_math

EMBEDDED = ('audio', 'canvas', 'embed', 'iframe', 'object', 'video')
NON_TEXT = (Comment, Declaration, Doctype, ProcessingInstruction)
AUDIT_SPANS={'hyperlink','title_style','emphasis','display_case','font_style','math_script','math_vector','abbreviation_label'}
HAN_NAMES=('CJK UNIFIED IDEOGRAPH','CJK COMPATIBILITY IDEOGRAPH')
KANA_NAMES=('HIRAGANA LETTER','KATAKANA LETTER')
KANA_MARKS={'KATAKANA-HIRAGANA PROLONGED SOUND MARK','HIRAGANA ITERATION MARK','HIRAGANA VOICED ITERATION MARK',
            'KATAKANA ITERATION MARK','KATAKANA VOICED ITERATION MARK'}

def noteref(node):
    return getattr(node,'attrs',None) is not None and ('noteref' in str(node.get('epub:type','')) or node.get('role')=='doc-noteref')

def note_container(node):
    return hasattr(node,'get') and (any(value in str(node.get('epub:type','')).split() for value in ('footnote','endnote')) or node.get('role') in ('doc-footnote','doc-endnote'))


def language(node):
    for n in [node,*node.parents]:
        if hasattr(n,'get') and (value:=n.get('xml:lang') or n.get('lang')):return value
    return None


def hidden(node):
    if not hasattr(node,'get'):return False
    style=re.sub(r'\s+','',node.get('style','').lower())
    from .epub_styles import properties
    css=properties(node)
    display=css.get('display',{}).get('value');visibility=css.get('visibility',{}).get('value')
    return node.has_attr('hidden') or node.get('aria-hidden')=='true' or css.get('speak',{}).get('value')=='none' or (display=='none' if display is not None else 'display:none' in style) or (visibility=='hidden' if visibility is not None else 'visibility:hidden' in style)


def accessible_label(node):
    if node.get('aria-label'):return node['aria-label'].strip()
    root=node
    while root.parent is not None:root=root.parent
    if node.get('aria-labelledby'):
        labels=[root.find(id=identifier) for identifier in node['aria-labelledby'].split()]
        if all(n is not None for n in labels):return ' '.join(n.get_text(' ',strip=True) for n in labels)
    if node.get('alt'):return node['alt'].strip()
    if node.name=='svg':
        title=node.find('title',recursive=False)
        if title:return title.get_text(' ',strip=True)
    return ''


def table_headers(rows, identifiers):
    """Bind simple column labels and explicit same-table header references."""
    cell_ids={f'{row}:{column}':element['id'] for row,cells in enumerate(rows)
              for column,element in enumerate(cells) if element.get('id') and identifiers[element['id']]==1}
    headers=[cell_ids.get(f'0:{column}') if cell.name=='th' and cell.get('scope') in (None,'col') else None
             for column,cell in enumerate(rows[0])]
    columns={identifier:column for column,identifier in enumerate(headers) if identifier}
    row_headers={}
    overrides={}
    issue=None
    for row,cells in enumerate(rows[1:],1):
        scoped=[column for column,cell in enumerate(cells) if cell.name=='th' and cell.get('scope')=='row']
        row_headers[row]=scoped
        local={cell_ids.get(f'{row}:{column}'):column for column in scoped}
        for column,cell in enumerate(cells):
            if not cell.has_attr('headers'):
                continue
            value=cell['headers']
            refs=set(value.split() if isinstance(value,str) else value)
            matched=refs & columns.keys()
            if len(matched)!=1 or any(ident not in columns and not 0<=local.get(ident,-1)<column for ident in refs):
                issue='Authored table header references need a verified column and row association.'
                continue
            header_column=columns[next(iter(matched))]
            overrides[f'{row}:{column}']=header_column
    locator={'header_ids':headers,'cell_ids':cell_ids,'row_header_columns':row_headers}
    if overrides:locator['header_columns']=overrides
    return locator,issue


def ruby_pronunciation(node):
    """Recognize an unambiguous authored kana reading for each base group."""
    if node.find(['ruby','rtc','br']) or any(parent.name in ('ruby','code','kbd','samp') for parent in node.parents):
        return None
    if any(child.name in ('code','kbd','samp') or hidden(child) or noteref(child) for child in node.find_all(True)):
        return None
    base=[]; readings=[]
    for child in node.children:
        if isinstance(child,NON_TEXT):continue
        if child.name=='rp':continue
        if child.name!='rt':
            base.append(str(child) if isinstance(child,NavigableString) else child.get_text())
            continue
        if child.get('role')=='definition':
            return None
        names=[unicodedata.name(c,'') for c in unicodedata.normalize('NFC',''.join(base).strip())]
        if not any(name.startswith(HAN_NAMES) for name in names) or not all(
                name.startswith(HAN_NAMES+KANA_NAMES) or name in KANA_MARKS or name=='IDEOGRAPHIC ITERATION MARK' for name in names):
            return None
        reading=child.get_text().strip()
        names=[unicodedata.name(c,'') for c in unicodedata.normalize('NFC',reading)]
        if not any(name.startswith(KANA_NAMES) for name in names) or not all(
                name.startswith(KANA_NAMES) or name in KANA_MARKS for name in names):
            return None
        readings.append(reading)
        base.clear()
    return ''.join(readings) if readings and not ''.join(base).strip() else None


def ruby_base(node):
    if node.name in ('rt','rp','rtc','script','style') or hidden(node):
        return ''
    return ''.join(str(child) if isinstance(child,NavigableString) else ruby_base(child)
                   for child in node.children if not isinstance(child,NON_TEXT))


def describe(node,resolve_asset=None,cover_assets=()):
    pieces=[];spans=[];position=0
    def visit(n):
        nonlocal position
        if isinstance(n,NavigableString):
            if not isinstance(n,NON_TEXT):
                pieces.append(str(n));position+=len(str(n))
            return
        if n.name in ('script','style'):return
        start=position;first_piece=len(pieces)
        if n.name=='br':
            pieces.append('\n');position+=1
            spans.append({'source_start':start,'source_end':position,'kind':'line_break','origin':'Authored XHTML br element'})
        else:
            for child in n.children:visit(child)
        end=position
        if end<=start:return
        data={'source_start':start,'source_end':end}
        if n.get('id'):spans.append({**data,'kind':'anchor','id':n['id']})
        if hidden(n):spans.append({**data,'kind':'hidden','disposition':'companion','evidence':'Explicit hidden attribute or inline visibility declaration'})
        if noteref(n):spans.append({**data,'kind':'note_reference','href':n.get('href')})
        elif n.get('role')=='doc-pagebreak' or 'pagebreak' in str(n.get('epub:type','')).split():
            spans.append({**data,'kind':'pagebreak','label':n.get('aria-label') or n.get('title') or n.get_text(),'disposition':'source page lookup'})
        elif n.name=='math' and not n.find_parent('math'):
            try:reading=speak_math(mathml(n));issue=None
            except ValueError as exc:reading=None;issue=str(exc)
            spans.append({**data,'kind':'mathml','reading':reading,'issue':issue})
        elif n.name in ('sup','sub') and not any(noteref(a) or a.name=='math' for a in n.parents):
            spans.append({**data,'kind':'superscript' if n.name=='sup' else 'subscript'})
        elif n.name in ('code','kbd','samp'):
            spans.append({**data,'kind':'literal'})
        elif n.name=='ruby':
            base=ruby_base(n)
            annotation=' '.join(t.get_text(' ',strip=True) for t in n.find_all('rt'))
            glosses=' '.join(t.get_text(' ',strip=True) for t in n.find_all('rt',role='definition') if not hidden(t))
            spans.append({**data,'kind':'ruby','base':base,'reading':base+(', meaning '+glosses if glosses else ''),
                          'annotation':annotation,'origin':'Authored ruby base and annotation'})
            if pronunciation:=ruby_pronunciation(n):spans[-1]['pronunciation']=pronunciation
        else:
            from .epub_styles import properties
            alignment=properties(n).get('vertical-align',{}).get('value')
            if alignment in ('super','sub'):spans.append({**data,'kind':'superscript' if alignment=='super' else 'subscript','origin':'Authored CSS vertical alignment'})
        if n.name=='a' and n.get('href') and not noteref(n):
            spans.append({**data,'kind':'hyperlink','href':n['href']})
        if n.name=='abbr' and n.get('title'):
            spans.append({**data,'kind':'abbreviation_label','expansion':n['title']})
        if n.name in ('em','strong','cite'):
            spans.append({**data,'kind':'title_style' if n.name=='cite' else 'emphasis','element':n.name})
        from .epub_styles import properties
        transform=properties(n).get('text-transform',{})
        if transform.get('value') in ('uppercase','lowercase','capitalize'):
            spans.append({**data,'kind':'display_case','transform':transform['value'],'origin':transform['origin']})
        font=properties(n).get('font-style')
        if font or n.name=='i':
            style=font['value'].casefold() if font else 'italic'
            if style.split()[:1] in (['italic'],['oblique'],['normal']):
                spans.append({**data,'kind':'font_style','style':style,
                              'origin':font['origin'] if font else 'Authored XHTML i element'})
        if n.name in ('msup','msub') and n.find_parent('math'):
            children=[child for child in n.children if getattr(child,'name',None)]
            if len(children)==2 and children[1].name in ('mn','mi'):
                symbol=children[1].get_text()
                if symbol and symbol==symbol.strip() and not children[1].find(True) and ''.join(pieces[first_piece:]).endswith(symbol):
                    spans.append({'source_start':end-len(symbol),'source_end':end,'kind':'math_script',
                                  'relation':'power' if n.name=='msup' else 'index','base':children[0].get_text(),
                                  'value':speak_math(mathml(children[1]))})
        if n.name=='mi' and n.find_parent('math') and n.get('intent') in ('vector',':vector'):
            spans.append({**data,'kind':'math_vector','intent':n['intent']})
        from .pronunciation import attribute
        if attribute(n,'ph'):
            spans.append({**data,'kind':'pronunciation_hint','phoneme':attribute(n,'ph'),'alphabet':attribute(n,'alphabet',inherit=True),'language':language(n),'origin':'inline ssml:ph','applied':False})
        if n.get('lang') or n.get('xml:lang'):
            spans.append({**data,'kind':'language','language':language(n)})
    # Zero-width objects are meaningful too; retain their actual insertion point.
    # Handle them in a wrapper so visit's empty-text early return cannot erase it.
    original_visit=visit
    def visit(n):
        start=position
        original_visit(n)
        if not isinstance(n,NavigableString):
            from .epub_styles import properties
            for side,at in [('before',start),('after',position)]:
                declaration=properties(n).get(side)
                if declaration and declaration['value']:spans.append({'source_start':at,'source_end':at,'kind':'generated_label','reading':declaration['value'],'origin':declaration['origin'],'selector':declaration['selector']})
        if not isinstance(n,NavigableString) and position==start and noteref(n):
            spans.append({'source_start':start,'source_end':start,'kind':'note_reference','href':n.get('href')})
        if not isinstance(n,NavigableString) and (n.get('role')=='checkbox' or n.name=='input' and n.get('type')=='checkbox'):
            status=n.get('aria-checked')
            if status is None and n.name=='input':status='true' if n.has_attr('checked') else 'false'
            spans.append({'source_start':start,'source_end':start,'kind':'task_status','checked':True if status=='true' else False if status=='false' else None,'origin':'Explicit XHTML checkbox state'})
        if not isinstance(n,NavigableString) and position==start and n.get('id'):
            spans.append({'source_start':start,'source_end':start,'kind':'anchor','id':n['id']})
        if isinstance(n,NavigableString) or n.name not in ('img','image','svg'):return
        if any(hidden(a) for a in [n,*n.parents]):return
        if any(a.name=='svg' and accessible_label(a) for a in n.parents):return
        label=accessible_label(n);href=n.get('src') or n.get('xlink:href') or n.get('href')
        asset=resolve_asset(href) if href and resolve_asset else None
        cover=asset in cover_assets if asset else False
        decorative=n.get('role') in ('presentation','none') and n.has_attr('alt') and not n['alt'].strip()
        # A wrapper around an image delegates to that image if no label exists.
        if n.name=='svg' and not label and n.find(['img','image']):return
        spans.append({'source_start':start,'source_end':position,'kind':'object_label','source':'',
                      'reading':label,'image_resource':asset,'cover':cover,'decorative':decorative,
                      'inside_note':any(noteref(a) for a in n.parents),
                      'issue':None if label or cover or decorative else 'Image or SVG has no authored accessible description; verify its essential content.'})
    visit(node);raw=''.join(pieces);left=len(raw)-len(raw.lstrip());source=raw.strip()
    for span in spans:
        span['source_start']=max(0,span['source_start']-left)
        span['source_end']=min(len(source),span['source_end']-left)
        span['source']=source[span['source_start']:span['source_end']]
    return source,spans


def reading(block,note_policy='after_paragraph', *, with_spans=False, extra_spans=()):
    """Use explicit roles; an unclassified superscript is not a guessed power."""
    source=block.source;edits=[];spans=[*block.locator.get('inline_semantics',()),*extra_spans]
    layout_edits=set()
    references=[s for s in spans if s['kind']=='note_reference' and not any(h['kind']=='hidden' and h['source_start']<=s['source_start'] and h['source_end']>=s['source_end'] for h in spans)]
    hidden_ranges=[(s['source_start'],s['source_end']) for s in spans if s['kind']=='hidden']
    for span in spans:
        a,b=span['source_start'],span['source_end'];kind=span['kind']
        if source[a:b]!=span['source']:continue
        if kind!='hidden' and any(start<=a and b<=end for start,end in hidden_ranges):continue
        if kind in ('note_reference','hidden'):
            note=next((link for link in block.links if link.get('kind')=='note' and link.get('source_start')==a and
                       link.get('reference_href',span.get('href'))==span.get('href')),None)
            value=' Note. '+note.get('reading',note['text'])+' End note. ' if note and kind=='note_reference' and note_policy=='inline' else ''
            edits.append((a,b,value))
        elif kind=='pagebreak':edits.append((a,b,' '))
        elif kind=='object_label':
            if span.get('inside_note',any(s['source_start']<=a and s['source_end']>=b for s in references)):continue
            edits.append((a,b,'' if span.get('cover') or span.get('decorative') else ' '+span.get('reading','')+' '))
        elif kind=='ruby':edits.append((a,b,span.get('pronunciation',span['reading'])))
        elif kind=='generated_label':
            if not any(s['source_start']<=a and s['source_end']>=b for s in references):edits.append((a,a,' '+span['reading']+' '))
        elif kind=='task_status' and span.get('checked') is not None:edits.append((a,a,'Checked. ' if span['checked'] else 'Unchecked. '))
        elif kind=='lexicon_hint' and span.get('reading') and not span.get('shadowed_by_inline') and not span.get('protected_overlap'):
            edits.append((a,b,span['reading']))
        elif kind in MATH_SPANS or kind in ('typed_reading','layout_reading'):
            if span.get('reading'):
                edit = (a,b,span['reading'])
                edits.append(edit)
                if kind=='layout_reading':layout_edits.add(edit)
        elif kind=='subscript':edits.append((a,b,' subscript '+span['source']+' '))
    # Outermost explicit math/note roles win over their nested presentation.
    selected=[]
    for a,b,value in sorted(edits,key=lambda x:(x[0],x[1]!=x[0],-x[1])):
        if selected and a<selected[-1][1]:continue
        selected.append((a,b,value))
    protected=[]
    if with_spans:
        shift=0
        for a,b,value in selected:
            if value and (a,b,value) not in layout_edits:protected.append((a+shift,a+shift+len(value)))
            shift+=len(value)-(b-a)
        for span in spans:
            if span['kind']!='literal':continue
            a,b=span['source_start'],span['source_end']
            if any(x<b and a<y for x,y,_ in selected):continue
            before=sum(len(value)-(y-x) for x,y,value in selected if y<=a)
            after=sum(len(value)-(y-x) for x,y,value in selected if y<=b)
            protected.append((a+before,b+after))
    for a,b,value in reversed(selected):source=source[:a]+value+source[b:]
    notes={link['href']:link.get('reading',link['text']) for link in block.links if link.get('kind')=='note' and any(s['source_start']==link.get('source_start') for s in references)}
    if note_policy=='after_paragraph':
        for text in notes.values():source+=' Note. '+text+' End note.'
    elif note_policy=='companion' and notes:source+=' A note is available in the companion.'
    return (source,protected) if with_spans else source


def note_issues(block,note_policy):
    if note_policy=='companion':return []
    spans=block.locator.get('inline_semantics',[])
    references=[s for s in spans if s['kind']=='note_reference' and not any(h['kind']=='hidden' and h['source_start']<=s['source_start'] and h['source_end']>=s['source_end'] for h in spans)]
    return list(dict.fromkeys(link['issue'] for link in block.links if link.get('kind')=='note' and link.get('issue') and any(s['source_start']==link.get('source_start') for s in references)))


def uncertainty(spans):
    hidden_ranges=[s for s in spans if s['kind']=='hidden']
    for span in spans:
        if span['kind']!='hidden' and any(s['source_start']<=span['source_start'] and s['source_end']>=span['source_end'] for s in hidden_ranges):continue
        if span['kind']=='object_label' and span.get('issue'):return span['issue']
        if span['kind']=='task_status' and span.get('checked') is None:return 'Checkbox has an unresolved or mixed authored state; verify the intended reading.'
        if span['kind']=='superscript' and re.fullmatch(r'[+−-]?\d+',span['source']):
            return 'Styled superscript has no explicit mathematical or linked-note role; verify its meaning before speech.'
        if span['kind'] in MATH_SPANS and span.get('issue'):
            label = 'Explicit MathML' if span['kind']=='mathml' else span.get('origin','Authored equation')
            return label+' needs a verified reading: '+span['issue']
    return None
