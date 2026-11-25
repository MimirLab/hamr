from __future__ import annotations
import io,os,posixpath,re,subprocess,zipfile
from collections import Counter
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit,unquote
from bs4 import BeautifulSoup
from lxml import etree
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath.index import math_inline_dollar
from mdit_py_plugins.footnote import footnote_plugin
from .core import Block,Document,Error,digest,filehash,run
from .text_source import physical_lines, plain

MAX_INPUT=200*1024*1024
MARKDOWN_DOLLAR = math_inline_dollar(allow_space=False,allow_digits=False)
TEX_COMMAND = re.compile(r'\\[A-Za-z]+')
MARKDOWN_MATH_BODY = re.compile(r'[\w\s.,=+*/^_{}()<>\[\]−×÷±∞|\x27-]+')

def block(text,loc,role='body',chapter='Opening',evidence=None,issue=None,links=None):
    return Block('b'+digest(str(loc).encode())[:16],text,loc,role,chapter,evidence or [],issue,links or [])

def qualified_markdown_math(state,silent):
    """Recognize inline math with expression evidence beyond balanced dollars."""
    start = state.pos
    if state.src[start]!='$' or not MARKDOWN_DOLLAR(state,True):
        return False
    end, state.pos = state.pos,start
    if end>state.posMax or end-start>4096:
        return False
    body = state.src[start+1:end-1]
    visible = TEX_COMMAND.sub('',body)
    # A price followed by a later dollar sign can look like a delimiter pair.
    # Ordinary words inside that pair are not TeX expression evidence.
    if re.search(r'[^\W\d_]{2,}',visible) or visible and not MARKDOWN_MATH_BODY.fullmatch(visible):
        return False
    if not (TEX_COMMAND.search(body) or re.fullmatch(r'[^\W\d_]',body)
            or re.search(r'[=+*/^_{}()<>\[\]−×÷±∞|\x27-]',body)):
        return False
    return MARKDOWN_DOLLAR(state,silent)

def markdown(data:bytes, *, collect_resources=False, first_note_definition=False, configure=None)->Document:
    from itertools import accumulate
    from markdown_it.token import Token
    from . import markdown_notes
    from .epub_semantics import uncertainty
    from .source_math import MATH_SPANS
    from .source_notes import Definition, NoteGraph

    try: text=data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise Error('text_encoding', f'Markdown cannot be decoded as UTF-8 at byte {exc.start}.',
                    'Save the original as UTF-8 Markdown and try again.') from exc
    # Markdown-it line maps count only CR, LF and CRLF, not Unicode separators.
    lines=[match[0] for match in physical_lines(text)]
    offsets=list(accumulate(map(len, lines),initial=0))
    md=MarkdownIt('commonmark',{'html':False}).enable('table').use(footnote_plugin,move_to_end=False,always_match_refs=True)
    md.inline.ruler.before('escape','qualified_math',qualified_markdown_math)
    md.inline.ruler.before('html_inline','styled_script',markdown_notes.styled_script)
    if configure:
        configure(md)
    env={}
    tokens, definitions=markdown_notes.split_definitions(md.parse(text,env))
    inline_notes={i:n for i,n in env.get('footnotes',{}).get('list',{}).items() if 'tokens' in n}
    inline_sources={i:n['content'] for i,n in inline_notes.items()}
    out=[]; chapter='Opening'; title=None; notes={}; note_blocks=[]

    def preserve_resources(target,contents):
        if not collect_resources:
            return
        references = []
        for token in contents:
            for child in token.children or ():
                if child.type=='image' and (href:=child.attrGet('src')):
                    references.append({'kind':'image','href':href,'label':child.content})
                elif child.type=='link_open' and (href:=child.attrGet('href')):
                    references.append({'kind':'link','href':href})
        if references:
            target['resource_references'] = references

    def location(first,last):
        start,end=offsets[first],offsets[last]
        return {'format':'markdown','lines':[first+1,last],'start':start,'end':end,'exact':text[start:end]}

    def add_note(href, label, children, provenance):
        source,reading,spans,issue=markdown_notes.definition(children,inline_sources)
        candidates=notes.setdefault(href,[])
        loc={'format':'markdown','note':label}
        if candidates:loc['definition_occurrence']=len(candidates)+1
        note=block(source,loc,'note',evidence=['Markdown AST note definition'])
        note.locator.update(provenance)
        if spans:note.locator['inline_semantics']=spans
        preserve_resources(note.locator,children)
        if issue:note.locator['note_issue']=issue
        candidates.append(Definition(note,reading,issue));note_blocks.append(note)

    for definition,children in definitions:
        label=definition.meta['label']
        add_note('markdown-note:'+label,label,children,location(*definition.map))
    for index,note in inline_notes.items():
        add_note(f'markdown-inline-note:{index}',f'inline:{index}',
                 [Token('inline','',0,children=note['tokens'])],{'exact':'^['+note['content']+']'})
    graph = NoteGraph(notes.get,first_definition=first_note_definition)
    for note in note_blocks:
        note.links = graph.links_for(note.locator.get('inline_semantics',()),follow=False)
    i=0;chapter_id=None;lists=[];quote_depth=0
    while i<len(tokens):
        t=tokens[i]
        if t.type in ('bullet_list_open','ordered_list_open'):
            next_number = int(t.attrGet('start') or 1) if t.type=='ordered_list_open' else None
            lists.append({'next':next_number,'item':None})
        elif t.type in ('bullet_list_close','ordered_list_close'):
            lists.pop()
        elif t.type=='list_item_open' and lists[-1]['next'] is not None:
            lists[-1]['item'] = lists[-1]['next']
            lists[-1]['next'] += 1
        elif t.type=='list_item_close':
            lists[-1]['item'] = None
        elif t.type=='blockquote_open':quote_depth+=1
        elif t.type=='blockquote_close':quote_depth-=1
        if t.type=='source_container' and (role:=t.meta.get('role')):
            loc=location(*t.map)
            if chapter_id is not None:loc['chapter_id']=chapter_id
            item=block(t.content,loc,role,chapter,issue=t.meta.get('issue'))
            if collect_resources and 'reference' in t.meta:
                item.locator['resource_references']=[t.meta['reference']]
            out.append(item)
        if t.type=='html_block':
            from .document_intake import DocumentBuilder, html_read

            start,end=t.map
            fragment=DocumentBuilder('markdown',title,digest(data))
            html_read(t.content.encode('utf-8'),fragment,where=f'Markdown HTML at line {start+1}',
                      metadata=False,collect_resources=collect_resources)
            for item in fragment.doc.blocks:
                if item.role=='heading':
                    chapter = item.chapter
                    title = title or chapter
                    chapter_id = f'markdown-html:{start+1}:{item.locator["ordinal"]}'
                item.chapter=chapter
                item.locator.update({k:v for k,v in location(start,end).items() if k!='exact'})
                if chapter_id is not None:item.locator['chapter_id']=chapter_id
                out.append(item)
        if t.type=='hr':
            loc={'format':'markdown','lines':[t.map[0]+1,t.map[1]],'exact':''.join(lines[t.map[0]:t.map[1]])}
            if chapter_id is not None:loc['chapter_id']=chapter_id
            out.append(block(loc['exact'].strip(),loc,'scene_break',chapter,['Markdown AST thematic break']))
        if t.type in ('heading_open','paragraph_open') and i+1<len(tokens):
            v=tokens[i+1]; loc=location(*t.map)
            source, spans=markdown_notes.inline(v,inline_sources)
            if any(c.type=='hardbreak' for c in (v.children or [])):loc['authored_line_breaks']=True
            links=graph.links_for(spans)
            issue='Image has no authored description; verify whether it carries essential content.' if any(c.type=='image' and not c.content.strip() for c in (v.children or [])) else None
            issue = issue or v.meta.get('source_issue') or uncertainty(spans)
            role='heading' if t.type=='heading_open' else 'body'
            task=re.match(r'^\[([xX ])\]\s+',source) if lists and role=='body' else None
            if task:loc['checklist']={'checked':task[1].lower()=='x','marker':task[0],'origin':'Explicit Markdown task marker in a list item'}
            if role=='heading':
                chapter=source
                for ref in reversed(spans):
                    if ref['kind']=='note_reference':chapter=chapter[:ref['source_start']]+chapter[ref['source_end']:]
                chapter=chapter.strip() or 'Opening'; title=title or chapter
                chapter_id='markdown:'+str(t.map[0]+1)
            if chapter_id is not None:loc['chapter_id']=chapter_id
            # Exact/raw text and extra interpretations should not alter stable block IDs.
            exact=loc.pop('exact')
            b=block(source,loc,role,chapter,['Markdown AST source range'],issue=issue,links=links)
            b.locator['exact']=exact
            if lists and role=='body' and source and lists[-1]['item'] is not None:
                b.locator['ordered_item'] = lists[-1]['item']
                lists[-1]['item'] = None
            if quote_depth:b.locator['block_quote']=True
            if spans:b.locator['inline_semantics']=spans
            preserve_resources(b.locator,[v])
            out.append(b)
            i+=3; continue
        if t.type in ('fence','code_block'):
            loc={'format':'markdown','lines':[t.map[0]+1,t.map[1]],'exact':''.join(lines[t.map[0]:t.map[1]])}
            if chapter_id is not None:loc['chapter_id']=chapter_id
            out.append(block(t.content,loc,'code',chapter,['AST literal block']))
        if t.type == 'math_block':
            from .document_intake import formula_span
            from .tex_math import tex_math

            loc = location(*t.map)
            if chapter_id is not None:loc['chapter_id']=chapter_id
            source = loc['exact'].strip()
            span = formula_span(source,lambda:tex_math(t.content),0,'Authored Markdown math',{})
            loc['inline_semantics'] = [span]
            out.append(block(source,loc,'equation',chapter,issue=span['issue']))
        if t.type=='table_open':
            start=t.map[0]; end=t.map[1]; rows=[]; row=[]; cells=[]; j=i+1
            while j<len(tokens) and tokens[j].type!='table_close':
                q=tokens[j]
                if q.type=='tr_open': row=[]
                if q.type=='inline':
                    source, spans=markdown_notes.inline(q,inline_sources)
                    if any(span['kind']=='note_reference' or span['kind'] in MATH_SPANS for span in spans):
                        cells.append({'row':len(rows),'column':len(row),'source':source,'spans':spans})
                    row.append(source)
                if q.type=='tr_close': rows.append(row)
                j+=1
            loc={'format':'markdown','lines':[start+1,end],'exact':''.join(lines[start:end]),'rows':rows}
            if chapter_id is not None:loc['chapter_id']=chapter_id
            b=block('\n'.join(' | '.join(r) for r in rows),loc,'table',chapter,['AST table with explicit header row'])
            if cells:
                graph.bind_cells(cells)
                b.issue = next((issue for cell in cells if (issue:=uncertainty(cell['spans']))),None)
                b.locator['table_cell_semantics']=cells
                b.locator['table_header_cells']=list(range(len(rows[0])))
                b.links.extend(link for cell in cells for link in cell['notes'])
            preserve_resources(b.locator,tokens[i+1:j])
            out.append(b)
            i=j
        i+=1
    for note in note_blocks:note.chapter=chapter
    out.extend(note_blocks)
    return Document(title or 'Document','markdown',digest(data),out)

def html_runs(node):
    from bs4 import NavigableString
    from .epub_semantics import NON_TEXT
    for child in node.descendants:
        if isinstance(child,NavigableString):
            if not isinstance(child,NON_TEXT) and not any(a.name in ('script','style') for a in child.parents):
                yield child,str(child)
        elif child.name=='br':
            yield None,'\n'


def html_text(node):
    return ''.join(value for _,value in html_runs(node))


def epub_text(runs, ranks):
    """Return trimmed source and its coordinates in parsed XHTML text leaves."""
    runs=list(runs)
    raw=''.join(value for _,value in runs)
    left=len(raw)-len(raw.lstrip())
    text=raw.strip()
    right=left+len(text)
    leaves=[]
    position=0
    for node,value in runs:
        end=position+len(value)
        a,b=max(position,left),min(end,right)
        if node is not None and a<b:
            leaves.append({'node_rank':ranks[id(node)],'block_span':[a-left,b-left],'node_start':a-position})
        position=end
    return text,leaves if len(leaves)>1 else []

def archive_path(base,href):
    u=urlsplit(href)
    if u.scheme or u.netloc: raise Error('unsafe_epub','EPUB package contains an external resource reference.')
    p=posixpath.normpath(posixpath.join(base,unquote(u.path)))
    if p.startswith('../') or p.startswith('/') or '\\' in p: raise Error('unsafe_epub','EPUB resource escapes archive root.')
    return p,unquote(u.fragment)

def document_reference(document_path,href):
    u=urlsplit(href)
    if not u.path and not u.scheme and not u.netloc:return document_path,unquote(u.fragment)
    return archive_path(posixpath.dirname(document_path),href)

def epub(data:bytes|Path)->Document:
    from .html_table import read_grid, requires_grid
    from .table_grid import passages, source_rows, source_text
    from .epub_obfuscation import font_references, verify_font_manifest

    try: z=zipfile.ZipFile(io.BytesIO(data) if isinstance(data,bytes) else data)
    except zipfile.BadZipFile: raise Error('unreadable_epub','Invalid EPUB ZIP container.')
    info=z.infolist()
    if len(info)>10000 or sum(x.file_size for x in info)>MAX_INPUT: raise Error('archive_limit','EPUB exceeds decoded size or entry limit.')
    names=set()
    for x in info:
        if x.flag_bits&1: raise Error('protected_epub','EPUB contains ZIP-encrypted resources.')
        if x.filename in names: raise Error('unsafe_epub','Duplicate EPUB archive entry.')
        names.add(x.filename); archive_path('',x.filename)
        if x.file_size>max(1024*1024,x.compress_size*500): raise Error('archive_limit','EPUB compression ratio exceeds safety bound.')
    def xml(name):
        try: return etree.fromstring(z.read(name),etree.XMLParser(resolve_entities=False,no_network=True))
        except (KeyError,etree.XMLSyntaxError): raise Error('unreadable_epub','Missing or malformed EPUB package XML.')
    fonts=font_references(xml('META-INF/encryption.xml'),names) if 'META-INF/encryption.xml' in names else set()
    container=xml('META-INF/container.xml'); roots=container.xpath('//*[local-name()="rootfile"]/@full-path')
    if not roots: raise Error('unreadable_epub','EPUB container has no rootfile.')
    opf_path,_=archive_path('',roots[0])
    if opf_path in fonts:raise Error('protected_epub','EPUB protection metadata names the package document as a font.')
    opf=xml(opf_path); base=posixpath.dirname(opf_path)
    title=opf.xpath('//*[local-name()="title"]/text()'); lang=opf.xpath('//*[local-name()="language"]/text()')
    if opf.xpath('//*[local-name()="meta" and @property="rendition:layout" and text()="pre-paginated"]'): raise Error('fixed_layout','Fixed-layout EPUB requires visual review; reflowable EPUB is supported.')
    manifest_nodes={x.get('id'):x for x in opf.xpath('//*[local-name()="manifest"]/*')}
    manifest={identifier:archive_path(base,x.get('href'))[0] for identifier,x in manifest_nodes.items() if x.get('href')}
    verify_font_manifest(fonts,manifest,manifest_nodes)
    # A declared cover asset is decorative furniture, not a missing story diagram.
    cover_ids=set(opf.xpath('//*[local-name()="meta" and @name="cover"]/@content'))
    cover_assets={manifest[i] for i in cover_ids if i in manifest}
    cover_assets.update(manifest[i.get('id')] for i in opf.xpath('//*[local-name()="manifest"]/*') if i.get('id') in manifest and 'cover-image' in i.get('properties','').split())
    metadata={}
    creators=[];contributors=[]
    for creator in opf.xpath('//*[local-name()="metadata"]/*[local-name()="creator"]'):
        name=''.join(creator.itertext()).strip();role=creator.get('{http://www.idpf.org/2007/opf}role') or creator.get('role')
        if creator.get('id'):
            refined=opf.xpath('//*[local-name()="meta" and @refines=$ref and @property="role"]/text()',ref='#'+creator.get('id'))
            if refined:role=str(refined[0]).strip()
        if name and role in (None,'aut','author'):creators.append(name)
        elif name:contributors.append({'name':name,'role':role})
    if creators:metadata['authors']=creators
    if contributors:metadata['contributors']=contributors
    identifiers=[str(x).strip() for x in opf.xpath('//*[local-name()="metadata"]/*[local-name()="identifier"]/text()') if str(x).strip()]
    if identifiers:metadata['source_identifiers']=identifiers
    series=opf.xpath('//*[local-name()="meta" and @name="calibre:series"]/@content')
    part=opf.xpath('//*[local-name()="meta" and @name="calibre:series_index"]/@content')
    if not series:
        # EPUB collections need an explicit series refinement; other collection
        # types and positions must not silently become audiobook series tags.
        for collection in opf.xpath('//*[local-name()="meta" and @property="belongs-to-collection" and @id]'):
            reference='#'+collection.get('id')
            kind=opf.xpath('//*[local-name()="meta" and @refines=$ref and @property="collection-type"]/text()',ref=reference)
            if [str(value).strip() for value in kind]!=['series']:continue
            series=[''.join(collection.itertext()).strip()]
            part=opf.xpath('//*[local-name()="meta" and @refines=$ref and @property="group-position"]/text()',ref=reference)
            break
    if series:metadata['series']=str(series[0]).strip()
    if part:metadata['series_part']=str(part[0]).strip()
    if cover_assets:
        asset=sorted(cover_assets)[0]
        if asset in names:metadata['cover']={'path':asset,'sha256':digest(z.read(asset))}
    soups={}
    def soup(path):
        if path in fonts:raise Error('protected_epub','EPUB references an obfuscated font as a text resource.')
        if path not in soups:
            try: soups[path]=BeautifulSoup(z.read(path),'xml')
            except KeyError: raise Error('unreadable_epub','Missing EPUB spine/note resource.')
            from .epub_styles import apply
            def css(href):
                target,_=document_reference(path,href)
                if target in fonts:raise Error('protected_epub','EPUB references an obfuscated font as a stylesheet or lexicon.')
                return z.read(target),target
            soups[path].__dict__['_audiobook_css_warnings']=apply(soups[path],css)
            from .pronunciation import linked
            entries,problems=linked(soups[path],css)
            soups[path].__dict__['_audiobook_lexicons']=entries
            soups[path].__dict__['_audiobook_lexicon_problems']=problems
        return soups[path]
    chapter_labels={};navigation=[]
    for item in opf.xpath('//*[local-name()="manifest"]/*'):
        path=manifest.get(item.get('id'))
        if not path:continue
        if 'nav' in item.get('properties','').split():
            nav=soup(path)
            navs=nav.find_all('nav');tocs=[n for n in navs if 'toc' in str(n.get('epub:type','')) or n.get('role')=='doc-toc']
            chosen=tocs or navs[:1] or [nav]
            for a in [a for node in chosen for a in node.find_all('a',href=True)]:
                try:
                    target,anchor=document_reference(path,a['href']);label=a.get_text(' ',strip=True)
                    chapter_labels[(target,anchor)]=label;navigation.append({'path':target,'anchor':anchor,'label':label,'origin':path})
                except Error: continue
        elif item.get('media-type')=='application/x-dtbncx+xml':
            ncx=xml(path)
            for point in ncx.xpath('//*[local-name()="navPoint"]'):
                src=point.xpath('./*[local-name()="content"]/@src'); label=point.xpath('./*[local-name()="navLabel"]/*[local-name()="text"]/text()')
                if src and label:
                    target,anchor=archive_path(posixpath.dirname(path),src[0]); chapter_labels[(target,anchor)]=label[0]
                    navigation.append({'path':target,'anchor':anchor,'label':label[0],'origin':path})
    from .epub_semantics import AUDIT_SPANS,EMBEDDED,NON_TEXT,describe,language,uncertainty,hidden,accessible_label,decorative_image,note_container,table_headers
    from .epub_notes import EpubNotes
    from .epub_semantics import noteref
    from .source_numbering import bind_ordered, html_ordered_items
    notes=EpubNotes(soup,document_reference,lambda path,href:archive_path(posixpath.dirname(path),href)[0],
                    cover_assets,lang[0] if lang else 'en')
    out=[]; inventory=[]; chapter='Opening';linked_targets={}
    for occurrence,item in enumerate(opf.xpath('//*[local-name()="spine"]/*'),1):
        if item.get('linear')=='no': continue
        from .epub_resources import resolve_spine
        path,chain,fallback_issue=resolve_spine(item.get('idref'),manifest_nodes,manifest,names)
        if fallback_issue:
            loc={'format':'epub','occurrence':occurrence,'path':manifest.get(item.get('idref'),''),'fallback_chain':chain}
            out.append(block('',loc,'unreadable',chapter,['Linear source occurrence retained for review'],fallback_issue))
            inventory.append({'occurrence':occurrence,'path':loc['path'],'fallback_chain':chain,'disposition':'source review','issue':fallback_issue})
            continue
        s=soup(path); body=s.find('body') or s
        ordered_items = html_ordered_items(body)
        chapter=chapter_labels.get((path,''),chapter)
        found=0; start=len(out); positions={}; covered=set(); initial_chapter=chapter
        ranks={id(node):rank for rank,node in enumerate(body.descendants)}
        identifiers=Counter(tag.get('id') for tag in s.find_all(id=True))
        for idx,node in enumerate(body.find_all(['h1','h2','h3','h4','p','pre','table','figcaption','math','img','image','svg'])):
            if node.find_parent(['table','pre']): continue
            if any(id(a) in covered for a in node.parents):continue
            if node.name=='math' and node.find_parent(['p','h1','h2','h3','h4','figcaption','math']):continue
            if any(note_container(a) for a in node.parents):continue
            chapter=chapter_labels.get((path,node.get('id')),chapter)
            text,leaves=epub_text(html_runs(node),ranks) if node.name!='table' else (html_text(node).strip(),[])
            role='heading' if re.match('h[1-4]',node.name) else {'pre':'code','table':'table','figcaption':'caption','math':'equation','img':'figure','image':'figure','svg':'figure'}.get(node.name,'body')
            issue=None
            image_resource=None
            base_text,semantics=describe(node,lambda href:archive_path(posixpath.dirname(path),href)[0],cover_assets)
            from .pronunciation import annotate
            semantics+=annotate(base_text,semantics,vars(s).get('_audiobook_lexicons',[]),language(node) or (lang[0] if lang else 'en'))
            issue=uncertainty(semantics)
            from .epub_styles import issues as style_issues
            css_issues=style_issues(node)
            if css_issues:issue=' '.join(dict.fromkeys(css_issues))
            lexicon_problems = vars(s).get('_audiobook_lexicon_problems')
            if lexicon_problems:issue=' '.join(lexicon_problems)
            if node.name in ('img','image'):
                href=node.get('src') if node.name=='img' else (node.get('xlink:href') or node.get('href'))
                if href: image_resource,_=archive_path(posixpath.dirname(path),href)
                text=accessible_label(node); issue=None if text else 'Image has no authored description; verify whether it carries essential content.'
                if image_resource in cover_assets:
                    role='cover'; text=text or 'Cover image'; issue=None
                elif decorative_image(node) and not text:
                    role='hidden';text='Decorative image';issue=None
                # The direct image source is its literal authored label, not
                # a zero-width position inside its containing paragraph.
                semantics=[s for s in semantics if s['kind']!='object_label']
            elif node.name=='svg':
                if accessible_label(node):text=html_text(node).strip()
                objects=[s for s in semantics if s['kind']=='object_label']
                if objects and all(s.get('cover') for s in objects) and not text.strip():
                    role='cover';text='Cover image';image_resource=objects[0].get('image_resource');issue=None
                elif len(objects)==1:image_resource=objects[0].get('image_resource')
            if not css_issues and any(hidden(a) for a in [node,*node.parents]):role='hidden';issue=None
            elif any(a.name=='nav' for a in [node,*node.parents]):role='navigation'
            elif note_container(node):role='note'

            if not text and not issue and not any(s['kind']=='object_label' and (s.get('reading') or s.get('cover')) for s in semantics): continue
            if role=='heading': chapter=text
            links=notes.links_for(path,semantics)
            for a in node.find_all('a',href=True):
                if role!='navigation' and not noteref(a):
                    try:
                        np,anchor=document_reference(path,a['href'])
                        if not np.lower().endswith(('.xhtml','.html','.htm','.svg')):continue
                        target_node=(soup(np).find(id=anchor) if anchor else soup(np).find('body'))
                        if target_node is not None:
                            linked_targets[(np,anchor)]=target_node
                            links.append({'kind':'hyperlink','href':np+'#'+anchor,'label':a.get_text(' ',strip=True)})
                    except Error:pass  # Original link remains in exact markup.
            for obj in node.find_all(['img','image','svg'])+([node] if node.name in ('img','image','svg') else []):
                for identifier in str(obj.get('aria-details') or obj.get('aria-describedby') or '').split():
                    target_node=s.find(id=identifier)
                    if target_node is None:issue='Authored image description target is missing.';continue
                    linked_targets[(path,identifier)]=target_node
                    links.append({'kind':'description','href':path+'#'+identifier,'label':'Extended description'})
            if any(a.get('id') in ('pg-header','pg-footer','project-gutenberg-license') for a in [node,*node.parents] if hasattr(a,'get')): role='reference'
            elif text.strip()=='[Illustration]': role='placeholder'
            grid = None
            if role=='table' and requires_grid(node):
                def read_cell(owner,children):
                    from .pronunciation import unresolved

                    source,spans=describe(owner,lambda href:archive_path(posixpath.dirname(path),href)[0],
                                          cover_assets,children=children)
                    spans+=annotate(source,spans,vars(s).get('_audiobook_lexicons',[]),language(owner) or (lang[0] if lang else 'en'))
                    problem = uncertainty(spans) or ' '.join(unresolved(block(source,{'inline_semantics':spans})))
                    return source,notes.bind(path,spans),problem
                def locate(owner):
                    return {'path':path,'occurrence':occurrence,'dom_rank':ranks[id(owner)],
                            **({'anchor':owner['id']} if owner.get('id') else {})}
                grid = read_grid(node,read_cell,locate,identifiers=identifiers)
                cells = list(passages(grid))
                notes.graph.bind_cells(cells)
                links = [link for link in links if link['kind']!='note']
                links.extend(link for cell in cells for link in cell['notes'])
                base_text = text = source_text(grid)
                semantics = []
                issue = grid['issue'] or ' '.join(dict.fromkeys([*css_issues,*(lexicon_problems or ())])) or None
                if not text and not issue and not any(cell['spans'] for cell in cells):
                    continue
            loc={'format':'epub','occurrence':occurrence,'path':path,'anchor':node.get('id'),'element':idx,'dom_rank':ranks[id(node)],'base_text_length':len(base_text)}
            identity_spans=[{k:v for k,v in s.items() if s['kind']!='ruby' or k not in ('base','pronunciation')}
                            for s in semantics if s['kind'] not in AUDIT_SPANS]
            if identity_spans:
                loc['inline_semantics']=identity_spans;loc['exact']=str(node)
                if any(s['kind']=='line_break' for s in identity_spans):loc['authored_line_breaks']=True
                issue=issue or uncertainty(identity_spans)
            if language(node):loc['language']=language(node)
            if role=='table':
                if grid is not None:
                    loc['table_grid'] = grid
                    loc['rows'] = source_rows(grid)
                    loc['exact'] = str(node)
                    table_cells = []
                else:
                    table_cells=[r.find_all(['th','td']) for r in node.find_all('tr')]
                    loc['rows']=[[html_text(c).strip() for c in row] for row in table_cells]
            out.append(block(text,loc,role,chapter,['EPUB spine occurrence and DOM element'],issue,links)); found+=1
            bind_ordered(out[-1],node,ordered_items)
            if node.find_parent('blockquote') is not None:out[-1].locator['block_quote']=True
            if leaves and text==base_text and role in ('body','heading','caption'):
                loc['text_leaves']=leaves
            if semantics:
                loc['inline_semantics']=semantics
                if 'exact' not in loc:loc['exact']=str(node)
            if role=='table' and table_cells:
                table_locator,header_issue=table_headers(table_cells,identifiers)
                loc.update(table_locator)
                if header_issue and not out[-1].issue:out[-1].issue=header_issue
                from .epub_semantics import reading
                def table_entry(owner):
                    source,spans=describe(owner,lambda href:archive_path(posixpath.dirname(path),href)[0],cover_assets)
                    spans+=annotate(source,spans,vars(s).get('_audiobook_lexicons',[]),language(owner) or (lang[0] if lang else 'en'))
                    return {'source':source,'spans':notes.bind(path,spans),'notes':[]}
                ruby_cells=[];semantic_cells=[];hidden_cells=[]
                for i,row in enumerate(table_cells):
                    for j,cell in enumerate(row):
                        if any(hidden(tag) for tag in (cell,*cell.parents)):
                            hidden_cells.append([i,j])
                        entry=table_entry(cell)
                        source,spans=entry['source'],entry['spans']
                        if spans:
                            semantic_cells.append({'row':i,'column':j,**entry})
                        if not cell.find('ruby'):continue
                        inline=[span for span in spans if span['kind'] in ('ruby','hidden')]
                        spoken=reading(block(source,{'inline_semantics':inline}))
                        if spoken!=source:ruby_cells.append({'row':i,'column':j,'source':source,'reading':spoken})
                if ruby_cells:loc['ruby_cells']=ruby_cells
                if semantic_cells:loc['table_cell_semantics']=semantic_cells
                if hidden_cells:loc['table_hidden_cells']=hidden_cells
                loc['table_header_cells']=[i for i,c in enumerate(table_cells[0]) if c.name=='th' and c.get('scope') in (None,'col') and [0,i] not in hidden_cells]
                if (caption:=node.find('caption',recursive=False)) and not hidden(caption):
                    loc['table_caption']=table_entry(caption)
                visible_cells=[cell for cell in semantic_cells if [cell['row'],cell['column']] not in hidden_cells]
                notes.graph.bind_cells(([loc['table_caption']] if 'table_caption' in loc else [])+visible_cells)
            # Append asset evidence after identity creation to preserve existing img locators.
            if image_resource:
                out[-1].locator['image_resource']=image_resource
                out[-1].evidence.append('Image resource resolved within EPUB archive')
                if role=='cover': out[-1].evidence.append('OPF declares this asset as the cover')
            positions[out[-1].id]=ranks[id(node)]; covered.add(id(node))
        # Audit every DOM text occurrence independently of the supported tag list.
        # Recover contiguous inline leaves without inserting whitespace into literals.
        from bs4 import NavigableString
        fallback_ids=set(); pending=[]; container=None; first_rank=None
        block_tags={'div','section','article','aside','li','dt','dd','blockquote','body','h5','h6','nav'}
        def flush_orphans():
            nonlocal pending,container,first_rank,found
            raw=''.join(value for _,value in pending)
            text,leaves=epub_text(pending,ranks)
            if text:
                ancestors=[container,*container.parents]; role='body'
                if any(hidden(a) for a in ancestors):role='hidden'
                elif any(a.name=='nav' for a in ancestors):role='navigation'
                elif any(a.get('id') in ('pg-header','pg-footer','project-gutenberg-license') for a in ancestors if hasattr(a,'get')): role='reference'
                elif any(note_container(a) for a in ancestors):role='note'
                elif text=='[Illustration]': role='placeholder'
                anchor=next((a.get('id') for a in ancestors if hasattr(a,'get') and a.get('id')),None)
                loc={'format':'epub','occurrence':occurrence,'path':path,'anchor':anchor,'text_node_rank':first_rank,'exact':raw}
                b=block(text,loc,role,initial_chapter,['Independent DOM text-occurrence coverage audit','Recovered inline leaves with original whitespace'])
                bind_ordered(b,container,ordered_items)
                if leaves and role=='body':loc['text_leaves']=leaves
                out.append(b); positions[b.id]=first_rank; fallback_ids.add(b.id); found+=1
            pending=[]; container=None; first_rank=None
        for node in body.descendants:
            if id(node) in covered: flush_orphans()
            if not isinstance(node,NavigableString) or isinstance(node,NON_TEXT): continue
            ancestors=list(node.parents)
            if any(id(a) in covered or a.name in ('script','style') for a in ancestors): flush_orphans(); continue
            parent=next((a for a in ancestors if hidden(a) or a.name in block_tags),body)
            if parent is not container: flush_orphans(); container=parent; first_rank=ranks[id(node)]
            pending.append((node,str(node)))
        flush_orphans()
        # Container pseudo-elements fall outside paragraph extraction. Preserve
        # their insertion points once, including empty and hidden containers.
        from .epub_styles import properties
        for node in [body,*body.find_all()]:
            if id(node) in covered or any(id(a) in covered for a in node.parents):continue
            for side in ('before','after'):
                declaration=properties(node).get(side)
                if not declaration or not declaration['value']:continue
                at=ranks.get(id(node),-1) if side=='before' else max((ranks[id(n)] for n in node.descendants if id(n) in ranks),default=ranks.get(id(node),-1))+.5
                loc={'format':'epub','occurrence':occurrence,'path':path,'dom_rank':at,'generated_side':side,'inline_semantics':[{'kind':'generated_label','source_start':0,'source_end':0,'source':'','reading':declaration['value'],'origin':declaration['origin'],'selector':declaration['selector']}],'exact':str(node)}
                role='hidden' if any(hidden(a) for a in [node,*node.parents]) else 'note' if any(note_container(a) for a in [node,*node.parents]) else 'body'
                generated=block('',loc,role,initial_chapter,['Authored CSS generated label at a container boundary'])
                out.append(generated);positions[generated.id]=at;fallback_ids.add(generated.id);found+=1
        opaque=[]
        for node in body.find_all(EMBEDDED):
            if any(parent.name in EMBEDDED or note_container(parent) for parent in node.parents):continue
            rank=ranks[id(node)]
            loc={'format':'epub','occurrence':occurrence,'path':path,'dom_rank':rank,'element_type':node.name,'exact':str(node)}
            concealed=any(hidden(parent) for parent in [node,*node.parents])
            issue=None if concealed else f'Embedded EPUB {node.name} needs a verified accessible reading or companion disposition.'
            item=block('',loc,'hidden' if concealed else 'unreadable',initial_chapter,['Independent inventory of embedded EPUB content'],issue)
            out.append(item);positions[item.id]=rank;fallback_ids.add(item.id);found+=1
            opaque.append({'block':item.id,'element':node.name,'disposition':'authored hidden' if concealed else 'source review'})
        ordered=sorted(out[start:],key=lambda b:positions[b.id]); current=initial_chapter
        for b in ordered:
            if b.id in fallback_ids: b.chapter=current
            else: current=b.chapter
        out[start:]=ordered
        inventory.append({'occurrence':occurrence,'path':path,'fallback_chain':chain,'recovered_inline_runs':len(fallback_ids)-len(opaque),'represented_blocks':len(ordered),'text_coverage':'Every non-script/style DOM text occurrence is assigned to a supported block or a recovered inline run.'})
        if opaque:inventory[-1]['embedded_objects']=opaque
        if not found: out.append(block('',{'format':'epub','path':path,'occurrence':occurrence},'unreadable',chapter,issue='Image-only or empty spine resource requires visual review.'))
    from .epub_navigation import route
    out,nav_records,warnings=route(out,navigation,soup)
    # Addressable auxiliary source is separate from the primary spine. Link
    # every calling occurrence to one local companion target with backlinks.
    auxiliary=[];target_ids={};auxiliary_chars=0
    note_addresses={candidate[0].block.id:notes.addresses[href] for href,candidate in notes.definitions.items() if candidate}
    linked_targets.update((address,node) for address,(node,_) in notes.nodes.items())
    blocks_by_path={}
    for item in out:
        blocks_by_path.setdefault(item.locator.get('path'),[]).append(item)
    document_ranks={}
    for address,node in linked_targets.items():
        path,anchor=address
        existing=[]
        if candidates:=blocks_by_path.get(path):
            if path not in document_ranks:
                body=soup(path).find('body') or soup(path)
                document_ranks[path]={id(n):i for i,n in enumerate(body.descendants)}
            ranks=document_ranks[path]
            subtree={ranks[id(n)] for n in [node,*node.descendants] if id(n) in ranks}
            existing=[b for b in candidates if not anchor or b.locator.get('anchor')==anchor or
                      b.locator.get('dom_rank',b.locator.get('text_node_rank')) in subtree or
                      any(s.get('kind')=='anchor' and s.get('id')==anchor for s in b.locator.get('inline_semantics',[]))]
        if existing:
            existing=list({b.locator.get('occurrence'):b for b in reversed(existing)}.values())
            target_ids[address]=existing
            continue
        source=html_text(node).strip();auxiliary_chars+=len(source)
        loc={'format':'epub','path':path,'anchor':anchor,'auxiliary':True,'exact':str(node)}
        issue=None
        if auxiliary_chars>4*1024*1024:
            source='';issue='Linked auxiliary source exceeds the bounded companion import; inspect the original EPUB.'
        if address in notes.nodes and not issue:
            target=notes.nodes[address][1]
            target.chapter=out[-1].chapter if out else 'Opening'
            target.links=notes.graph.links_for(target.locator['inline_semantics'],follow=False)
        else:
            target=block(source,loc,'note',out[-1].chapter if out else 'Opening',['Authored internal link target retained for offline lookup'],issue)
        target_ids[address]=[target];auxiliary.append(target)
    href_addresses={path+'#'+anchor:(path,anchor) for path,anchor in target_ids}
    def destinations(block,addresses):
        result=[]
        for address in addresses:
            candidates=target_ids.get(address,())
            local=[target for target in candidates if target.locator.get('path')==block.locator.get('path') and
                   target.locator.get('occurrence')==block.locator.get('occurrence')]
            result.extend('source.html#'+target.id for target in local or candidates)
        return list(dict.fromkeys(result))
    for b in [*out,*auxiliary]:
        cell_entries=[*b.locator.get('table_cell_semantics',())]
        if caption:=b.locator.get('table_caption'):
            cell_entries.append(caption)
        if grid:=b.locator.get('table_grid'):
            cell_entries.extend(passages(grid))
        for link in [*b.links,*(link for cell in cell_entries for link in cell['notes'])]:
            href=link.get('href')
            address=notes.addresses.get(href) if link['kind']=='note' else href_addresses.get(href)
            chain_addresses=[note_addresses.get(value.removeprefix('source.html#'))
                             for value in link.get('destinations',())] if link['kind']=='note' else []
            if address is not None:
                main=destinations(b,[address])
                if all_destinations:=destinations(b,[address,*chain_addresses]):
                    link['destinations']=all_destinations
                if len(main)==1:link['destination']=main[0]
                else:link.pop('destination',None)
    out.extend(auxiliary)
    for path,parsed in soups.items():
        warnings.extend(path+': '+warning for warning in vars(parsed).get('_audiobook_css_warnings',[]))
    if nav_records:inventory.append({'kind':'navigation','entries':nav_records,'purpose':'Chapter view only; primary source order remains the spine occurrence order.'})
    if fonts:inventory.append({'kind':'font_obfuscation','resources':sorted(fonts),'disposition':'Font bytes retained in the original EPUB; narration uses the text resources.'})
    if any(b.role=='placeholder' for b in out):warnings.append('This source edition contains illustration placeholders; no visual relationship is inferred from them.')
    return Document(title[0] if title else 'Book','epub',digest(data) if isinstance(data,bytes) else filehash(data),out,lang[0] if lang else 'en',inventory=inventory,warnings=warnings,metadata=metadata)

def pdf(data:bytes|Path,ocr_cache:Path|None=None,ocr_config=None)->Document:
    import pypdfium2 as pdfium
    from .pdf_visual import Observer, authored_artifact, bind_captions, merge_regions, regional_images
    from .pdf_geometry import asciidoctor_tips,crop_glyphs,glyph_baseline,redundant_note_icons
    from .epub_semantics import uncertainty
    from .pdf_scripts import raised_digits, spans as script_spans
    from .pdf_tagged_tables import TaggedTables, outer_tables
    from .pdf_tags import line_paragraph, page_tags, paragraph_owners
    from .pdf_textlayer import nonpainting_regions
    from .source_scripts import prepare as prepare_scripts
    from .text_evidence import suspect_characters
    try: doc=pdfium.PdfDocument(data)
    except Exception: raise Error('unreadable_pdf','PDF is protected, damaged or unsupported.')
    out=[]; inventory=[];outlines=[]
    producer=doc.get_metadata_value('Producer')
    visual=Observer(ocr_cache,ocr_config)
    tagged=TaggedTables()
    for index,item in enumerate(doc.get_toc()):
        destination=item.get_dest();page_index=destination.get_index() if destination else None
        mode,position=destination.get_view() if destination else (0,[])
        top=position[1] if mode==1 and len(position)>1 else position[0] if mode in (3,6) and position else None
        outlines.append({'entry':index,'title':item.get_title(),'depth':item.level,'page':page_index+1 if page_index is not None else None,'view_mode':mode,'view_position':position,'top':top})
    for n,page in enumerate(doc):
        tp=page.get_textpage(); native=tp.get_text_range(); w,h=page.get_size()
        tags=page_tags(page,n+1)
        table_tags=list(outer_tables(tags))
        # Independently inventory image objects, including raster beside hidden/selectable text.
        image_objects=[(obj,list(obj.get_bounds())) for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE])]
        images=[rect for _,rect in image_objects]
        entry={'page':n+1,'size':[w,h],'glyphs':tp.count_chars(),'rotation':page.get_rotation(),'raster_regions':images,'route':'native','native_observation':native}
        if not entry['rotation'] and (label:=doc.get_page_label(n)):
            entry['declared_page_label'] = label
        redundant = redundant_note_icons(tp,native)
        tips = asciidoctor_tips(page,tp,native,producer) if not entry['rotation'] else []
        tip_headings = {item['heading_index']:item for item in tips}
        skip = {item['native_index'] for item in (*redundant,*tips)}
        if redundant:
            entry['redundant_note_icons'] = redundant
        if tips:
            entry['source_tip_icons'] = tips
        if damaged:=suspect_characters(''.join(' ' if i in skip else c for i,c in enumerate(native)) if skip else native):
            entry['native_character_diagnostics']=damaged
        box=page.get_bbox();media=page.get_mediabox()
        nonpainting=nonpainting_regions(page,box)
        if nonpainting:entry['nonpainting_text_regions']=nonpainting
        full_geometry=page.get_rotation()==0 and all(abs(a-b)<.01 for a,b in zip(box,(0,0,w,h))) and all(abs(a-b)<.01 for a,b in zip(box,media))
        entry['native_geometry_qualified']=full_geometry
        entry['visible_cropbox']=list(box)
        inventory.append(entry)
        chapter=f'Page {n+1}'
        visual_objects=[]
        artifacts=[]
        for obj,rect in image_objects:
            if authored_artifact(obj): artifacts.append(rect)
            else: visual_objects.append((obj,rect))
        content_images=[rect for _,rect in visual_objects]
        if artifacts:entry['authored_raster_artifacts']=artifacts
        panels,marks=[],[]
        cached_glyphs=None
        if len(visual_objects)>=4 and not nonpainting and native.strip() and entry['native_geometry_qualified']:
            from .pdf_rules import classify
            cached_glyphs=crop_glyphs(tp,box)
            panels,rules,marks,remaining=classify(visual_objects,cached_glyphs[0])
            if panels:
                entry.update(raster_table_rules=rules,raster_text_marks=marks,raster_content_regions=remaining,ruled_panels=panels)
                content_images=remaining
        crops=None
        if (content_images and native.strip() and not nonpainting and not damaged
                and entry['native_geometry_qualified'] and not panels):
            if cached_glyphs is None:
                cached_glyphs=crop_glyphs(tp,box)
            crops=regional_images(content_images,cached_glyphs[0],w,h)
        first_block=len(out)
        if crops is None and (content_images or nonpainting or damaged or not native.strip()):
            if table_tags:
                tagged.observe(page,tp,tags,table_tags,(),entry,admitted=False)
            entry['ocr_admission']=[reason for condition,reason in ((content_images,'raster content regions'),
                (nonpainting,'nonpainting text layer'),(damaged,'native character mapping'),(not native.strip(),'no native text')) if condition]
            parts, observation=visual.read(page,tp,entry,[0,0,w,h],native)
            entry['route']='verified_blank' if observation['status']=='verified_blank' else 'visual_ocr'
            entry.update({key:value for key,value in observation.items() if key not in ('rect','status')})
            entry['visual_observations']=[observation]
            for part in parts:
                locator=part['locator']
                out.append(block(part['text'],locator,part.get('role','body'),chapter,
                                 ['Independent visual OCR','Native observation retained'],part['issue']))
        else:
            # Build glyph lines, then use a large central gutter only with bilateral evidence.
            from .pdf_overlays import verified_overlays
            overlays=verified_overlays(page,tp) if entry['native_geometry_qualified'] else []
            if overlays:entry['duplicate_paint_observations']=overlays
            chars,outside,clipped=cached_glyphs if cached_glyphs is not None else crop_glyphs(tp,box)
            if skip:
                chars = [glyph for glyph in chars if glyph[2] not in skip]
            visible_native=''.join(ch for ch,_,_ in chars)
            entry.update(excluded_crop_glyphs=outside,visible_native_observation=visible_native,clipped_visible_glyphs=clipped)
            if page.get_rotation()==0:entry['native_geometry_qualified']=True
            hyphens={i for ch,_,i in chars if ch=='\ufffe' and pdfium.raw.FPDFText_IsHyphen(tp,i)==1}
            if hyphens:
                entry['native_hyphen_glyphs']=sorted(hyphens)
                chars=[('-' if i in hyphens else ch,rect,i) for ch,rect,i in chars]
                visible_native=''.join(ch for ch,_,_ in chars)
            table_segments=[]
            outside_panels=[]
            if panels:
                from .pdf_tables import framed_source,source_segments,surrounding_segments
                tables,claimed=framed_source(tp,chars,panels,marks)
                table_segments=source_segments(tables,native)
                chars=[glyph for glyph in chars if glyph[2] not in claimed]
                outside_panels=surrounding_segments(tp,chars)
                visible_native=''.join(ch for ch,_,_ in chars)
                entry['route']='native_ruled_panels'
                entry['table_source_glyphs']=len(claimed)
            elif full_geometry and not (table_tags or content_images or clipped or overlays):
                from .visual_table_intake import native_parts

                tables=native_parts(page,tp,chars,n+1,w,h)
                if tables:
                    claimed=set().union(*(indices for indices,_ in tables))
                    chars=[glyph for glyph in chars if glyph[2] not in claimed]
                    visible_native=''.join(ch for ch,_,_ in chars)
                    for _,table in tables:
                        rect=table['locator']['rect']
                        table_segments.append({**table,'rect':rect,'y':rect[3]})
                    entry['table_source_glyphs']=len(claimed)
                    entry['native_visual_tables']=[{'rect':part['rect'],'issue':part['issue']} for part in table_segments]
            lines=[]
            authorship = paragraph_owners(tags) if not panels else {}
            line_glyphs = [] if panels else chars
            baselines = {index:glyph_baseline(tp,index,box[1],rect[1]) for _,rect,index in line_glyphs}
            raised = raised_digits(tp,line_glyphs,baselines) if full_geometry and not overlays else {}
            for ch,rect,index in line_glyphs:
                l,b,r,t=rect
                baseline=baselines[raised.get(index,index)]
                # A descender's box reaches below the baseline of its word.
                target=next((x for x in lines if abs(x['y']-baseline)<3.5),None)
                if target is None: target={'y':baseline,'chars':[]}; lines.append(target)
                target['chars'].append((l,r,ch,rect,index))
            segments=outside_panels
            for line in sorted(lines,key=lambda a:-a['y']):
                seq=sorted(line['chars'],key=lambda x:(x[0],x[4])); chunks=[]; current=[]; last=None
                for x in seq:
                    if last is not None and x[0]-last>max(18,w*.04): chunks.append(current); current=[]
                    current.append(x); last=x[1]
                if current: chunks.append(current)
                for chunk in chunks:
                    text=''; last=None;previous=None; scripts=[]
                    for l,r,ch,rect,index in chunk:
                        if ch.isspace():text+=' '
                        else:
                            # PDFium already represents native spaces. Do not
                            # split adjacent digits/initials on side bearings.
                            if previous is not None and index!=previous+1 and last is not None and l-last>2 and not text.endswith(' '):text+=' '
                            if index in raised:
                                scripts.append((len(text),len(text)+len(ch),index,raised[index]))
                            text+=ch
                        last=r;previous=index
                    if text.strip():
                        segment = {'text':text.strip(),'rect':[chunk[0][0],min(x[3][1] for x in chunk),chunk[-1][1],max(x[3][3] for x in chunk)],'y':line['y']}
                        if full_geometry and not (overlays or table_tags):
                            segment['glyphs'] = chunk
                        spans = []
                        if scripts:
                            padding = len(text)-len(text.lstrip())
                            spans = prepare_scripts(segment['text'],script_spans(segment['text'],(
                                (a-padding,b-padding,index,base) for a,b,index,base in scripts)))
                        first_glyph = next((glyph[-1] for glyph in chunk if glyph[2].strip()),None)
                        if tip:=tip_headings.get(first_glyph):
                            spans.append({'kind':'generated_label','source_start':0,'source_end':0,'source':'',
                                          'reading':tip['label'],'origin':tip['basis'],'native_index':tip['native_index']})
                        if spans:
                            segment['locator'] = {'inline_semantics':spans}
                            segment['issue'] = uncertainty(spans)
                        if table_tags:
                            segment['table_glyphs'] = {x[4] for x in chunk if x[2].strip()}
                        if authorship and (paragraph := line_paragraph(tp, chunk, authorship)) is not None:
                            segment['tagged_paragraph'] = paragraph
                        segments.append(segment)
            if not panels:
                if full_geometry and not (overlays or table_tags):
                    from .pdf_literals import group_terminal_transcripts, observe_command_frames

                    segments = group_terminal_transcripts(page,tp,segments)
                    observe_command_frames(page,tp,segments)
                from .pdf_layout import column_order
                def uncertain_boundary(row):
                    for item in row:
                        item['issue'] = ('Native PDF column order is uncertain where text shares a baseline '
                                         'with a spanning segment. Verify this row before narration.')
                if ordered:=column_order(segments,w,lambda item:item['rect'],baseline=lambda item:item['y'],
                                         coincident_boundary=uncertain_boundary):
                    segments=ordered
                    entry['route']='geometric_two_columns'
            coverage=lambda value:Counter(c for c in value if not c.isspace())
            issue=None
            if coverage(visible_native)!=coverage(''.join(s['text'] for s in segments)):
                issue='Native glyph reconstruction does not preserve all observed characters; verify the original page.'
            if clipped:issue='A meaningful glyph crosses the visible CropBox edge; verify the original source before narration.'
            if page.get_rotation()!=0:issue='Rotated native PDF text needs a verified visible reading and order; this geometry path is not qualified.'
            entry['glyph_character_coverage']='exact' if not issue else 'disputed'
            artifacts = (tagged.observe(page,tp,tags,table_tags,chars,entry,admitted=not panels and not issue)
                         if table_tags else set())
            if table_segments:
                segments=sorted(segments+table_segments,key=lambda s:(-s['y'],s.get('ordered_group',s['rect'][0])))
            for idx,s in enumerate(segments):
                locator={'format':'pdf','page':n+1,'rect':s['rect'],'line':idx,**s.get('locator',{})}
                local_issue=issue or s.get('issue')
                if not local_issue and marks and not s.get('locator',{}).get('ruled_panel'):
                    l,b,r,t=s['rect']
                    if any(x1>l and x0<r and y1>b and y0<t for x0,y0,x1,y1 in (m['rect'] for m in marks)):
                        local_issue='A raster mark overlaps this native line. Verify the original before speech.'
                out.append(block(s['text'],locator,s.get('role','body'),chapter,['PDF glyph geometry',entry['route'],'Native whitespace and punctuation retained'],local_issue))
                if indices:=s.get('table_glyphs'):
                    tagged.line(n+1,out[-1].id,indices,artifacts is not None and indices<=artifacts)
                if local_issue:continue
                if 'tagged_paragraph' in s:
                    locator['tagged_paragraph'] = s['tagged_paragraph']
                l,b,r,t=s['rect']
                observations=[]
                for overlay in overlays:
                    x,y,x1,y1=overlay['rect']
                    if l<=x+.02 and b<=y+.02 and x1<=r+.02 and y1<=t+.02 and s['text'].count(overlay['text'])==1:
                        start=s['text'].index(overlay['text'])
                        observations.append({**overlay,'source_span':[start,start+len(overlay['text'])]})
                if observations:locator['duplicate_paint_observations']=observations
            if not segments and native.strip():out.append(block('',{'format':'pdf','page':n+1,'rect':[0,0,w,h]},'unreadable',chapter,issue='Native glyphs fall outside the visible crop; verify whether the visible page is blank.'))
        if crops:
            entry['route']='regional_visual_ocr'
            entry['ocr_admission']=['disjoint visible raster regions']
            observations=[]
            for rect in crops:
                parts, observation=visual.read(page,tp,entry,rect,regional=True)
                observations.append(observation)
                for part in parts:
                    output=block(part['text'],part['locator'],part.get('role','body'),chapter,
                                 ['Independent regional visual OCR'],part['issue'])
                    observation.setdefault('source_blocks',[]).append(output.id)
                    out.append(output)
            entry['visual_observations']=observations
            blocks=out[first_block:]
            bind_captions(blocks,observations)
            out[first_block:]=merge_regions(blocks,w)
        tp.close(); page.close()
    doc.close()
    out=tagged.project(out,block)
    from .pdf_layout import mark_folios
    mark_folios(out,inventory)
    from .pdf_references import mark_bibliographies,mark_indexes
    mark_indexes(out,inventory)
    mark_bibliographies(out,inventory)
    from .pdf_headers import mark_headers
    mark_headers(out,inventory)
    from .pdf_flow import insertions,overlaps
    by_id={b.id:b for b in out}
    for finding in overlaps(out,inventory):
        for bid in finding['source_blocks']:
            by_id[bid].locator['overlap_review']=finding
            if not by_id[bid].issue:by_id[bid].issue=finding['reason']
    for finding in insertions(out,inventory,expanded=True):
        for bid in finding['source_blocks']:
            by_id[bid].locator['flow_review']=finding
            if not by_id[bid].issue:by_id[bid].issue=finding['reason']
    from .pdf_pagination import gaps,repeats
    for finding in [*gaps(out,inventory),*repeats(out,inventory)]:
        loc={'format':'pdf','page':finding['page'],'prior_PDF_page':finding['prior_PDF_page'],'pagination_review':finding}
        at=next(i for i,b in enumerate(out) if b.locator.get('page',0)>=finding['page'])
        out.insert(at,block('',loc,'unreadable',out[at].chapter,['Corroborated printed folios and neighbouring page evidence'],finding['reason']))
    from .pdf_navigation import apply as outline_navigation
    if outlines:
        outline_navigation(out,inventory,outlines)
        if inventory:inventory[0]['source_outlines']=outlines
    warnings=[]
    if any(e.get('status')=='geometry_fallback' for p in inventory for e in p.get('tagged_table_observations',())):
        warnings.append('Some PDF table tags could not be bound to the visible content. The visible reading and original remain available.')
    if any(e.get('status')=='unresolved' for e in outlines):warnings.append('Some PDF outline destinations lack a unique matching heading or qualified coordinates. All prose remains in source order; chapter navigation needs review.')
    return Document('PDF document','pdf',digest(data) if isinstance(data,bytes) else filehash(data),out,inventory=inventory,warnings=warnings)

ADAPTERS={'.md':markdown,'.markdown':markdown,'.epub':epub,'.pdf':pdf,'.txt':plain}

def input_adapter(path):
    adapter=ADAPTERS.get(path.suffix.lower())
    if not adapter:
        raise Error('unsupported_format','Use a supported format listed by hamr agent capabilities.')
    return adapter


def inspect(path:Path)->Document:
    from .intake import inspect_source

    return inspect_source(path)


IMPORT_REVISION='import-59'
def cached_inspect(path:Path,cache:Path)->Document:
    from .intake import inspect_source

    return inspect_source(path,cache)
