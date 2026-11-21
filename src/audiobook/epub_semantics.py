"""Preserve explicit XHTML roles before plain-text concatenation loses them."""
import re
from bs4 import NavigableString

EMBEDDED = ('audio', 'canvas', 'embed', 'iframe', 'object', 'video')

def noteref(node):
    return getattr(node,'attrs',None) is not None and ('noteref' in str(node.get('epub:type','')) or node.get('role')=='doc-noteref')

def note_container(node):
    return hasattr(node,'get') and (any(value in str(node.get('epub:type','')).split() for value in ('footnote','endnote')) or node.get('role') in ('doc-footnote','doc-endnote'))


def language(node):
    for n in [node,*node.parents]:
        if hasattr(n,'get') and (value:=n.get('xml:lang') or n.get('lang')):return value
    return None


def math_reading(node,depth=0):
    if depth>16:raise ValueError('Math nesting exceeds the supported subset')
    children=[c for c in node.children if getattr(c,'name',None)]
    name=node.name
    if name in ('mn','mi'):return node.get_text()
    if name=='mo':
        value=node.get_text().strip()
        return {'+':'plus','−':'minus','-':'minus','=':'equals','×':'times','·':'times','/':'divided by',
                '≥':'greater than or equal to','≤':'less than or equal to','>':'greater than','<':'less than','(':'open parenthesis',')':'close parenthesis'}.get(value,value)
    if name in ('math','mrow','semantics','mstyle'):
        meaningful=[c for c in children if c.name not in ('annotation','annotation-xml')]
        return ' '.join(math_reading(c,depth+1) for c in meaningful)
    if name in ('msup','msub','mfrac') and len(children)==2:
        a,b=(math_reading(c,depth+1) for c in children)
        if name=='mfrac':return 'the fraction with numerator '+a+' and denominator '+b
        if children[0].name=='mrow':a='open parenthesis '+a+' close parenthesis'
        return a+(' to the power of ' if name=='msup' else ' subscript ')+b
    raise ValueError('Unsupported mathematical structure: '+name)


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


def describe(node,resolve_asset=None,cover_assets=()):
    pieces=[];spans=[];position=0
    def visit(n):
        nonlocal position
        if isinstance(n,NavigableString):pieces.append(str(n));position+=len(str(n));return
        if n.name in ('script','style'):return
        start=position
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
            try:reading=math_reading(n);issue=None
            except ValueError as exc:reading=None;issue=str(exc)
            spans.append({**data,'kind':'mathml','reading':reading,'issue':issue})
        elif n.name in ('sup','sub') and not any(noteref(a) or a.name=='math' for a in n.parents):
            spans.append({**data,'kind':'superscript' if n.name=='sup' else 'subscript'})
        elif n.name in ('code','kbd','samp'):
            spans.append({**data,'kind':'literal'})
        elif n.name=='ruby':
            base=''.join(str(c) for c in n.descendants if isinstance(c,NavigableString) and not any(a.name in ('rt','rp') for a in c.parents))
            annotation=' '.join(t.get_text(' ',strip=True) for t in n.find_all('rt'))
            glosses=' '.join(t.get_text(' ',strip=True) for t in n.find_all('rt',role='definition'))
            spans.append({**data,'kind':'ruby','reading':base+(', meaning '+glosses if glosses else ''),'annotation':annotation,'origin':'Authored ruby base and annotation'})
        else:
            from .epub_styles import properties
            alignment=properties(n).get('vertical-align',{}).get('value')
            if alignment in ('super','sub'):spans.append({**data,'kind':'superscript' if alignment=='super' else 'subscript','origin':'Authored CSS vertical alignment'})
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
                      'issue':None if label or cover or decorative else 'Image or SVG has no authored accessible description; verify its essential content.'})
    visit(node);raw=''.join(pieces);left=len(raw)-len(raw.lstrip());source=raw.strip()
    for span in spans:
        span['source_start']=max(0,span['source_start']-left)
        span['source_end']=min(len(source),span['source_end']-left)
        span['source']=source[span['source_start']:span['source_end']]
    return source,spans


def reading(block,note_policy='after_paragraph', *, with_spans=False):
    """Use explicit roles; an unclassified superscript is not a guessed power."""
    source=block.source;edits=[];spans=block.locator.get('inline_semantics',[])
    references=[s for s in spans if s['kind']=='note_reference' and not any(h['kind']=='hidden' and h['source_start']<=s['source_start'] and h['source_end']>=s['source_end'] for h in spans)]
    for span in spans:
        a,b=span['source_start'],span['source_end'];kind=span['kind']
        if source[a:b]!=span['source']:continue
        if kind in ('note_reference','hidden'):
            note=next((link for link in block.links if link.get('kind')=='note' and link.get('source_start')==a),None)
            value=' Note. '+note.get('reading',note['text'])+' End note. ' if note and kind=='note_reference' and note_policy=='inline' else ''
            edits.append((a,b,value))
        elif kind=='pagebreak':edits.append((a,b,' '))
        elif kind=='object_label':
            if any(s['source_start']<=a and s['source_end']>=b for s in references):continue
            edits.append((a,b,'' if span.get('cover') or span.get('decorative') else ' '+span.get('reading','')+' '))
        elif kind=='ruby':edits.append((a,b,span['reading']))
        elif kind=='generated_label':
            if not any(s['source_start']<=a and s['source_end']>=b for s in references):edits.append((a,a,' '+span['reading']+' '))
        elif kind=='task_status' and span.get('checked') is not None:edits.append((a,a,'Checked. ' if span['checked'] else 'Unchecked. '))
        elif kind=='lexicon_hint' and span.get('reading') and not span.get('shadowed_by_inline') and not span.get('protected_overlap'):
            edits.append((a,b,span['reading']))
        elif kind=='mathml' and span.get('reading'):edits.append((a,b,span['reading']))
        elif kind=='subscript':edits.append((a,b,' subscript '+span['source']+' '))
    # Outermost explicit math/note roles win over their nested presentation.
    selected=[]
    for a,b,value in sorted(edits,key=lambda x:(x[0],-x[1])):
        if selected and a<selected[-1][1]:continue
        selected.append((a,b,value))
    protected=[]
    if with_spans:
        shift=0
        for a,b,value in selected:
            if value:protected.append((a+shift,a+shift+len(value)))
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
        if span['kind']=='mathml' and span.get('issue'):
            return 'Explicit MathML needs a verified reading: '+span['issue']
    return None
