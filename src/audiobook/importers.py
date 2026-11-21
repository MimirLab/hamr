from __future__ import annotations
import io,os,posixpath,re,subprocess,zipfile
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit,unquote
from bs4 import BeautifulSoup
from lxml import etree
from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from .core import Block,Document,Error,digest,run
from .text_source import physical_lines, plain

MAX_INPUT=200*1024*1024

def block(text,loc,role='body',chapter='Opening',evidence=None,issue=None,links=None):
    return Block('b'+digest(str(loc).encode())[:16],text,loc,role,chapter,evidence or [],issue,links or [])

def markdown(data:bytes)->Document:
    from itertools import accumulate
    from markdown_it.token import Token
    from . import markdown_notes

    try: text=data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise Error('text_encoding', f'Markdown cannot be decoded as UTF-8 at byte {exc.start}.',
                    'Save the original as UTF-8 Markdown and try again.') from exc
    # Markdown-it line maps count only CR, LF and CRLF, not Unicode separators.
    lines=[match[0] for match in physical_lines(text)]
    offsets=list(accumulate(map(len, lines),initial=0))
    md=MarkdownIt('commonmark',{'html':False}).enable('table').use(footnote_plugin,move_to_end=False,always_match_refs=True)
    env={}
    tokens, definitions=markdown_notes.split_definitions(md.parse(text,env))
    inline_notes={i:n for i,n in env.get('footnotes',{}).get('list',{}).items() if 'tokens' in n}
    inline_sources={i:n['content'] for i,n in inline_notes.items()}
    out=[]; chapter='Opening'; title=None; notes={}; note_blocks=[]

    def location(first,last):
        start,end=offsets[first],offsets[last]
        return {'format':'markdown','lines':[first+1,last],'start':start,'end':end,'exact':text[start:end]}

    def add_note(href, label, children, provenance):
        source,reading,issue=markdown_notes.definition(children,inline_sources)
        candidates=notes.setdefault(href,[])
        loc={'format':'markdown','note':label}
        if candidates:loc['definition_occurrence']=len(candidates)+1
        note=block(source,loc,'note',evidence=['Markdown AST note definition'])
        note.locator.update(provenance)
        if issue:note.locator['note_issue']=issue
        candidates.append((note,reading));note_blocks.append(note)

    for definition,children in definitions:
        label=definition.meta['label']
        add_note('markdown-note:'+label,label,children,location(*definition.map))
    for index,note in inline_notes.items():
        add_note(f'markdown-inline-note:{index}',f'inline:{index}',
                 [Token('inline','',0,children=note['tokens'])],{'exact':'^['+note['content']+']'})
    i=0;chapter_id=None;list_depth=0
    while i<len(tokens):
        t=tokens[i]
        if t.type in ('bullet_list_open','ordered_list_open'):list_depth+=1
        elif t.type in ('bullet_list_close','ordered_list_close'):list_depth-=1
        if t.type=='hr':
            loc={'format':'markdown','lines':[t.map[0]+1,t.map[1]],'exact':''.join(lines[t.map[0]:t.map[1]])}
            if chapter_id is not None:loc['chapter_id']=chapter_id
            out.append(block(loc['exact'].strip(),loc,'scene_break',chapter,['Markdown AST thematic break']))
        if t.type in ('heading_open','paragraph_open') and i+1<len(tokens):
            v=tokens[i+1]; loc=location(*t.map)
            source, spans=markdown_notes.inline(v,inline_sources)
            if any(c.type=='hardbreak' for c in (v.children or [])):loc['authored_line_breaks']=True
            links=markdown_notes.links_for(spans,notes)
            issue='Image has no authored description; verify whether it carries essential content.' if any(c.type=='image' and not c.content.strip() for c in (v.children or [])) else None
            role='heading' if t.type=='heading_open' else 'body'
            task=re.match(r'^\[([xX ])\]\s+',source) if list_depth and role=='body' else None
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
            if spans:b.locator['inline_semantics']=spans
            out.append(b)
            i+=3; continue
        if t.type in ('fence','code_block'):
            loc={'format':'markdown','lines':[t.map[0]+1,t.map[1]],'exact':''.join(lines[t.map[0]:t.map[1]])}
            if chapter_id is not None:loc['chapter_id']=chapter_id
            out.append(block(t.content,loc,'code',chapter,['AST literal block']))
        if t.type=='table_open':
            start=t.map[0]; end=t.map[1]; rows=[]; row=[]; references=[]; j=i+1
            while j<len(tokens) and tokens[j].type!='table_close':
                q=tokens[j]
                if q.type=='tr_open': row=[]
                if q.type=='inline':
                    source, refs=markdown_notes.inline(q,inline_sources)
                    row.append(source); references.extend(ref for ref in refs if ref['kind']=='note_reference')
                if q.type=='tr_close': rows.append(row)
                j+=1
            loc={'format':'markdown','lines':[start+1,end],'exact':''.join(lines[start:end]),'rows':rows}
            if chapter_id is not None:loc['chapter_id']=chapter_id
            issue='Markdown table note references need a verified reading.' if references else None
            out.append(block('\n'.join(' | '.join(r) for r in rows),loc,'table',chapter,['AST table with explicit header row'],issue,markdown_notes.links_for(references,notes)))
            i=j
        i+=1
    for note in note_blocks:note.chapter=chapter
    out.extend(note_blocks)
    return Document(title or 'Document','markdown',digest(data),out)

def html_text(node):
    from bs4 import NavigableString
    parts=[]
    for child in node.descendants:
        if isinstance(child,NavigableString):
            if not any(a.name in ('script','style') for a in child.parents): parts.append(str(child))
        elif child.name=='br': parts.append('\n')
    return ''.join(parts)

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

def epub(data:bytes)->Document:
    from .epub_obfuscation import font_references, verify_font_manifest

    try: z=zipfile.ZipFile(io.BytesIO(data))
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
    from .epub_semantics import AUDIT_SPANS,EMBEDDED,describe,language,uncertainty,hidden,accessible_label,note_container
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
        chapter=chapter_labels.get((path,''),chapter)
        found=0; start=len(out); positions={}; covered=set(); initial_chapter=chapter
        ranks={id(node):rank for rank,node in enumerate(body.descendants)}
        for idx,node in enumerate(body.find_all(['h1','h2','h3','h4','p','pre','table','figcaption','math','img','image','svg'])):
            if node.find_parent(['table','pre']): continue
            if any(id(a) in covered for a in node.parents):continue
            if node.name=='math' and node.find_parent(['p','h1','h2','h3','h4','figcaption','math']):continue
            if any(note_container(a) for a in node.parents):continue
            chapter=chapter_labels.get((path,node.get('id')),chapter)
            text=html_text(node).strip()
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
            if vars(s).get('_audiobook_lexicon_problems'):issue=' '.join(vars(s)['_audiobook_lexicon_problems'])
            if node.name in ('img','image'):
                href=node.get('src') if node.name=='img' else (node.get('xlink:href') or node.get('href'))
                if href: image_resource,_=archive_path(posixpath.dirname(path),href)
                text=accessible_label(node); issue=None if text else 'Image has no authored description; verify whether it carries essential content.'
                if image_resource in cover_assets:
                    role='cover'; text=text or 'Cover image'; issue=None
                elif node.get('role') in ('presentation','none') and node.has_attr('alt') and not node['alt'].strip():
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
            links=[]
            note_references=iter(sorted((s for s in semantics if s['kind']=='note_reference'),key=lambda s:s['source_start']))
            for a in node.find_all('a',href=True):
                if 'noteref' in str(a.get('epub:type','')) or a.get('role')=='doc-noteref':
                    location=next(note_references,None)
                    try:np,anchor=document_reference(path,a['href']); n=soup(np).find(id=anchor)
                    except Error:issue='Linked note resource is missing or unsupported.';continue
                    if not n: issue='Linked note target is missing.'; continue
                    note,note_spans=describe(n,lambda href:archive_path(posixpath.dirname(np),href)[0],cover_assets)
                    note_spans+=annotate(note,note_spans,vars(soup(np)).get('_audiobook_lexicons',[]),language(n) or (lang[0] if lang else 'en'))
                    note_loc={'format':'epub','path':np,'anchor':anchor,'inline_semantics':note_spans,'language':language(n) or (lang[0] if lang else 'en')}
                    note_block=block(note,note_loc,'note',chapter)
                    from .epub_semantics import reading as note_reading
                    from .pronunciation import unresolved
                    note_problems=[uncertainty(note_spans),*style_issues(n),*vars(soup(np)).get('_audiobook_lexicon_problems',[]),*unresolved(note_block)]
                    if any(not any(hidden(a) for a in [obj,*obj.parents]) for obj in n.find_all(EMBEDDED)):
                        note_problems.append('This EPUB note contains embedded content that needs a verified reading.')
                    if n.find('a',attrs={'role':'doc-noteref'}) or any('noteref' in str(a.get('epub:type','')).split() for a in n.find_all('a')):note_problems.append('Nested linked notes require a verified reading; recursive note expansion is not inferred.')
                    note_problem=' '.join(dict.fromkeys(p for p in note_problems if p)) or None
                    target=(np,anchor);linked_targets[target]=n
                    links.append({'kind':'note','href':np+'#'+anchor,'text':note,'reading':note_reading(note_block),'inline_semantics':note_spans,'language':note_loc['language'],'issue':note_problem,**({'source_start':location['source_start']} if location else {})})
                elif role!='navigation':
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
            loc={'format':'epub','occurrence':occurrence,'path':path,'anchor':node.get('id'),'element':idx,'dom_rank':ranks[id(node)],'base_text_length':len(base_text)}
            identity_spans=[{k:v for k,v in s.items() if s['kind']!='ruby' or k not in ('base','pronunciation')}
                            for s in semantics if s['kind'] not in AUDIT_SPANS]
            if identity_spans:
                loc['inline_semantics']=identity_spans;loc['exact']=str(node)
                if any(s['kind']=='line_break' for s in identity_spans):loc['authored_line_breaks']=True
                issue=issue or uncertainty(identity_spans)
            if language(node):loc['language']=language(node)
            if role=='table':
                table_cells=[r.find_all(['th','td']) for r in node.find_all('tr')]
                rows=[[html_text(c).strip() for c in row] for row in table_cells]
                loc['rows']=rows
                if node.find(attrs={'rowspan':True}) or node.find(attrs={'colspan':True}): issue='Merged table header associations require review.'
            out.append(block(text,loc,role,chapter,['EPUB spine occurrence and DOM element'],issue,links)); found+=1
            if semantics:
                loc['inline_semantics']=semantics
                if 'exact' not in loc:loc['exact']=str(node)
            if role=='table' and table_cells:
                loc['header_ids']=[cell.get('id') for cell in table_cells[0]]
                loc['row_header_columns']={i:[j for j,cell in enumerate(row)
                                             if cell.name=='th' and cell.get('scope')=='row']
                                           for i,row in enumerate(table_cells[1:],1)}
                from .epub_semantics import reading
                ruby_cells=[]
                for i,row in enumerate(table_cells):
                    for j,cell in enumerate(row):
                        if not cell.find('ruby'):continue
                        source,spans=describe(cell)
                        if source!=rows[i][j]:continue
                        inline=[span for span in spans if span['kind'] in ('ruby','hidden')]
                        spoken=reading(block(source,{'inline_semantics':inline}))
                        if spoken!=source:ruby_cells.append({'row':i,'column':j,'source':source,'reading':spoken})
                if ruby_cells:loc['ruby_cells']=ruby_cells
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
            raw=''.join(pending)
            if raw.strip():
                ancestors=[container,*container.parents]; role='body'
                if any(hidden(a) for a in ancestors):role='hidden'
                elif any(a.name=='nav' for a in ancestors):role='navigation'
                elif any(a.get('id') in ('pg-header','pg-footer','project-gutenberg-license') for a in ancestors if hasattr(a,'get')): role='reference'
                elif any(note_container(a) for a in ancestors):role='note'
                elif raw.strip()=='[Illustration]': role='placeholder'
                anchor=next((a.get('id') for a in ancestors if hasattr(a,'get') and a.get('id')),None)
                loc={'format':'epub','occurrence':occurrence,'path':path,'anchor':anchor,'text_node_rank':first_rank,'exact':raw}
                b=block(raw.strip(),loc,role,initial_chapter,['Independent DOM text-occurrence coverage audit','Recovered inline leaves with original whitespace'])
                out.append(b); positions[b.id]=first_rank; fallback_ids.add(b.id); found+=1
            pending=[]; container=None; first_rank=None
        for node in body.descendants:
            if id(node) in covered: flush_orphans()
            if not isinstance(node,NavigableString): continue
            ancestors=list(node.parents)
            if any(id(a) in covered or a.name in ('script','style') for a in ancestors): flush_orphans(); continue
            parent=next((a for a in ancestors if hidden(a) or a.name in block_tags),body)
            if parent is not container: flush_orphans(); container=parent; first_rank=ranks[id(node)]
            pending.append(str(node))
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
    for (path,anchor),node in linked_targets.items():
        body=soup(path).find('body') or soup(path);ranks={id(n):i for i,n in enumerate(body.descendants)}
        subtree={ranks[id(n)] for n in [node,*node.descendants] if id(n) in ranks}
        existing=[b for b in out if b.locator.get('path')==path and (not anchor or b.locator.get('anchor')==anchor or b.locator.get('dom_rank',b.locator.get('text_node_rank')) in subtree or any(s.get('kind')=='anchor' and s.get('id')==anchor for s in b.locator.get('inline_semantics',[])))]
        if existing:
            # A document link with no fragment lands at the first block in
            # each legitimate repeated spine occurrence.
            existing=list({b.locator.get('occurrence'):b for b in reversed(existing)}.values())
            target_ids[path+'#'+anchor]=existing;continue
        source=html_text(node).strip();auxiliary_chars+=len(source)
        loc={'format':'epub','path':path,'anchor':anchor,'auxiliary':True,'exact':str(node)}
        issue=None
        if auxiliary_chars>4*1024*1024:
            source='';issue='Linked auxiliary source exceeds the bounded companion import; inspect the original EPUB.'
        target=block(source,loc,'note',out[-1].chapter if out else 'Opening',['Authored internal link target retained for offline lookup'],issue)
        target_ids[path+'#'+anchor]=[target];auxiliary.append(target)
    for b in out:
        for link in b.links:
            if link.get('href') in target_ids:
                candidates=target_ids[link['href']]
                local=[target for target in candidates if target.locator.get('path')==b.locator.get('path') and target.locator.get('occurrence')==b.locator.get('occurrence')]
                destinations=['source.html#'+target.id for target in local or candidates]
                link['destinations']=destinations
                if len(destinations)==1:link['destination']=destinations[0]
    out.extend(auxiliary)
    for path,parsed in soups.items():
        warnings.extend(path+': '+warning for warning in vars(parsed).get('_audiobook_css_warnings',[]))
    if nav_records:inventory.append({'kind':'navigation','entries':nav_records,'purpose':'Chapter view only; primary source order remains the spine occurrence order.'})
    if fonts:inventory.append({'kind':'font_obfuscation','resources':sorted(fonts),'disposition':'Font bytes retained in the original EPUB; narration uses the text resources.'})
    if any(b.role=='placeholder' for b in out):warnings.append('This source edition contains illustration placeholders; no visual relationship is inferred from them.')
    return Document(title[0] if title else 'Book','epub',digest(data),out,lang[0] if lang else 'en',inventory=inventory,warnings=warnings,metadata=metadata)

def pdf(data:bytes,ocr_cache:Path|None=None,ocr_config=None)->Document:
    import pypdfium2 as pdfium
    from PIL import Image,ImageChops,ImageStat
    from .pdf_geometry import crop_glyphs,glyph_baseline
    from .pdf_textlayer import nonpainting_regions
    from .text_evidence import suspect_characters
    try: doc=pdfium.PdfDocument(data)
    except Exception: raise Error('unreadable_pdf','PDF is protected, damaged or unsupported.')
    out=[]; inventory=[];outlines=[]
    for index,item in enumerate(doc.get_toc()):
        destination=item.get_dest();page_index=destination.get_index() if destination else None
        mode,position=destination.get_view() if destination else (0,[])
        top=position[1] if mode==1 and len(position)>1 else position[0] if mode in (3,6) and position else None
        outlines.append({'entry':index,'title':item.get_title(),'depth':item.level,'page':page_index+1 if page_index is not None else None,'view_mode':mode,'view_position':position,'top':top})
    for n,page in enumerate(doc):
        if n>=2000: raise Error('page_limit','PDF exceeds 2000-page import limit.')
        tp=page.get_textpage(); native=tp.get_text_range(); w,h=page.get_size()
        # Independently inventory image objects, including raster beside hidden/selectable text.
        image_objects=[(obj,list(obj.get_bounds())) for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE])]
        images=[rect for _,rect in image_objects]
        entry={'page':n+1,'size':[w,h],'glyphs':tp.count_chars(),'rotation':page.get_rotation(),'raster_regions':images,'route':'native','native_observation':native}
        if damaged:=suspect_characters(native):entry['native_character_diagnostics']=damaged
        box=page.get_bbox();media=page.get_mediabox()
        nonpainting=nonpainting_regions(page,box)
        if nonpainting:entry['nonpainting_text_regions']=nonpainting
        entry['native_geometry_qualified']=page.get_rotation()==0 and all(abs(a-b)<.01 for a,b in zip(box,(0,0,w,h))) and all(abs(a-b)<.01 for a,b in zip(box,media))
        entry['visible_cropbox']=list(box)
        inventory.append(entry)
        chapter=f'Page {n+1}'
        content_images=images
        panels,marks=[],[]
        cached_glyphs=None
        if len(images)>=4 and not nonpainting and native.strip() and entry['native_geometry_qualified']:
            from .pdf_rules import classify
            cached_glyphs=crop_glyphs(tp,box)
            panels,rules,marks,remaining=classify(image_objects,cached_glyphs[0])
            if panels:
                entry.update(raster_table_rules=rules,raster_text_marks=marks,raster_content_regions=remaining,ruled_panels=panels)
                content_images=remaining
        if content_images or nonpainting or not native.strip():
            entry['ocr_admission']=[reason for condition,reason in ((content_images,'raster content regions'),(nonpainting,'nonpainting text layer'),(not native.strip(),'no native text')) if condition]
            bitmap=page.render(scale=2); im=bitmap.to_pil().convert('RGB')
            extrema=ImageStat.Stat(im).stddev
            if max(extrema)<0.8 and not native.strip():
                entry['route']='verified_blank'; bitmap.close(); tp.close(); page.close(); continue
            try:
                from .pdf_ocr import configuration,observe
                if ocr_config is None:ocr_config=configuration()
                b=io.BytesIO(); im.save(b,format='PNG')
                observed,reused=observe(b.getvalue(),ocr_config,ocr_cache)
                if ocr_cache is not None:
                    import sys
                    print(f'PDF visual OCR: {n+1}/{len(doc)} pages; '+('saved observation reused.' if reused else 'observation checkpointed.'),file=sys.stderr,flush=True)
                rows=[x.split('\t',11) for x in observed.splitlines()[1:]]
                words=[x for x in rows if len(x)==12 and x[11].strip() and float(x[10])>=0]
                groups={}
                for x in words: groups.setdefault(tuple(x[1:5]),[]).append(x)
                text='\n'.join(' '.join(x[11] for x in group) for group in groups.values())
                conf=sum(float(x[10]) for x in words)/max(1,len(words)); issue=None
                if not text.strip() or conf<65: issue='Visible page text could not be read reliably.'
                from .pdf_fidelity import damaged_regnal_glyph,disputes
                critical_disputes=disputes(native,text);entry['critical_disputes']=critical_disputes
                if critical_disputes:issue='Native and visual OCR disagree on critical values or mathematical notation. Verify the visible source before rendering this page.'
                if not issue and any(float(x[10])<70 and re.search(r'\d',x[11]) for x in words): issue='Low-confidence numeric OCR requires verification.'
                entry.update(route='visual_ocr',ocr_confidence=conf,native_observation=native,ocr_observation=text,raw_OCR_reused=reused,ocr_words=[{'text':x[11],'confidence':float(x[10]),'rect_pixels':[int(v) for v in x[6:10]],'group':[int(v) for v in x[1:5]]} for x in words])
                from .pdf_layout import paragraphs
                parts=paragraphs(words,n+1,h,None if critical_disputes else issue)
                from .pdf_regions import localize,glyph_reader
                box=page.get_bbox();media=page.get_mediabox()
                geometry=page.get_rotation()==0 and all(abs(a-b)<.01 for a,b in zip(box,(0,0,w,h))) and all(abs(a-b)<.01 for a,b in zip(box,media))
                native_region=glyph_reader(tp) if geometry else lambda rect:''
                entry['critical_localization']=localize(parts,native,text,native_region,geometry)
                if not parts:
                    parts=[{'text':text,'locator':{'format':'pdf','page':n+1,'rect':[0,0,w,h]},'issue':issue}]
                from .pdf_words import localize_words
                entry['lexical_localization']=localize_words(parts,native,text,native_region,geometry)
                for part in parts:
                    if damaged_regnal_glyph(part['text']):
                        reason='Visual OCR shows an unmatched bracket in a possible regnal numeral. Verify the printed reading before synthesis.'
                        part['issue']=' '.join(filter(None,(part['issue'],reason)))
                    locator=part['locator']
                    joins=locator.pop('line_wrap_joins',None)
                    out.append(block(part['text'],locator,'body',chapter,['Independent raster inventory','Tesseract paragraph geometry','Native text retained as competing observation'],part['issue']))
                    if joins:locator['line_wrap_joins']=joins
            except Error as e:
                out.append(block(native,{'format':'pdf','page':n+1,'rect':[0,0,w,h]},'unreadable',chapter,['Raster inventory'],e.message))
            except (subprocess.SubprocessError,OSError):
                out.append(block(native,{'format':'pdf','page':n+1,'rect':[0,0,w,h]},'unreadable',chapter,issue='OCR failed; verify this page.'))
            finally: bitmap.close()
        else:
            # Build glyph lines, then use a large central gutter only with bilateral evidence.
            from .pdf_overlays import verified_overlays
            overlays=verified_overlays(page,tp) if entry['native_geometry_qualified'] else []
            if overlays:entry['duplicate_paint_observations']=overlays
            chars,outside,clipped=cached_glyphs if cached_glyphs is not None else crop_glyphs(tp,box)
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
            lines=[]
            for ch,rect,index in (() if panels else chars):
                l,b,r,t=rect
                baseline=glyph_baseline(tp,index,box[1],b)
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
                    text=''; last=None;previous=None
                    for l,r,ch,rect,index in chunk:
                        if ch.isspace():text+=' '
                        else:
                            # PDFium already represents native spaces. Do not
                            # split adjacent digits/initials on side bearings.
                            if previous is not None and index!=previous+1 and last is not None and l-last>2 and not text.endswith(' '):text+=' '
                            text+=ch
                        last=r;previous=index
                    if text.strip():segments.append({'text':text.strip(),'rect':[chunk[0][0],min(x[3][1] for x in chunk),chunk[-1][1],max(x[3][3] for x in chunk)],'y':line['y']})
            left=[s for s in segments if s['rect'][2]<w*.52]; right=[s for s in segments if s['rect'][0]>w*.48]
            two=len(left)>=3 and len(right)>=3 and max(s['rect'][2] for s in left)+12<min(s['rect'][0] for s in right)
            if two and not panels:
                # Spanning elements split the page into bands; each band's left column precedes right.
                full=[s for s in segments if s not in left and s not in right]; ordered=[]; high=float('inf')
                for spanning in sorted(full,key=lambda s:-s['y'])+[{'y':-1,'text':''}]:
                    band=[s for s in left+right if spanning['y']<s['y']<high]
                    ordered+=sorted(band,key=lambda s:(s in right,-s['y']))
                    if spanning['text']: ordered.append(spanning)
                    high=spanning['y']
                segments=ordered; entry['route']='geometric_two_columns'
            from collections import Counter
            coverage=lambda value:Counter(c for c in value if not c.isspace())
            issue=None
            if coverage(visible_native)!=coverage(''.join(s['text'] for s in segments)):
                issue='Native glyph reconstruction does not preserve all observed characters; verify the original page.'
            if clipped:issue='A meaningful glyph crosses the visible CropBox edge; verify the original source before narration.'
            if page.get_rotation()!=0:issue='Rotated native PDF text needs a verified visible reading and order; this geometry path is not qualified.'
            entry['glyph_character_coverage']='exact' if not issue else 'disputed'
            if table_segments:
                segments=sorted(segments+table_segments,key=lambda s:(-s['y'],s.get('ordered_group',s['rect'][0])))
            for idx,s in enumerate(segments):
                locator={'format':'pdf','page':n+1,'rect':s['rect'],'line':idx,**s.get('locator',{})}
                local_issue=issue or s.get('issue')
                if not local_issue and marks and s['rect'] and not s.get('locator'):
                    l,b,r,t=s['rect']
                    if any(x1>l and x0<r and y1>b and y0<t for x0,y0,x1,y1 in (m['rect'] for m in marks)):
                        local_issue='A raster mark overlaps this native line. Verify the original before speech.'
                out.append(block(s['text'],locator,s.get('role','body'),chapter,['PDF glyph geometry',entry['route'],'Native whitespace and punctuation retained'],local_issue))
                if local_issue:continue
                l,b,r,t=s['rect']
                observations=[]
                for overlay in overlays:
                    x,y,x1,y1=overlay['rect']
                    if l<=x+.02 and b<=y+.02 and x1<=r+.02 and y1<=t+.02 and s['text'].count(overlay['text'])==1:
                        start=s['text'].index(overlay['text'])
                        observations.append({**overlay,'source_span':[start,start+len(overlay['text'])]})
                if observations:locator['duplicate_paint_observations']=observations
            if not segments and native.strip():out.append(block('',{'format':'pdf','page':n+1,'rect':[0,0,w,h]},'unreadable',chapter,issue='Native glyphs fall outside the visible crop; verify whether the visible page is blank.'))
        tp.close(); page.close()
    doc.close()
    from .pdf_layout import mark_folios
    mark_folios(out,inventory)
    from .pdf_references import mark_indexes
    mark_indexes(out,inventory)
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
    from .pdf_pagination import gaps
    for finding in gaps(out,inventory):
        loc={'format':'pdf','page':finding['page'],'prior_PDF_page':finding['prior_PDF_page'],'pagination_review':finding}
        at=next(i for i,b in enumerate(out) if b.locator.get('page',0)>=finding['page'])
        out.insert(at,block('',loc,'unreadable',out[at].chapter,['Competing printed-folio observations and stable neighbouring sequences'],finding['reason']))
    from .pdf_navigation import apply as outline_navigation
    if outlines:
        outline_navigation(out,inventory,outlines)
        if inventory:inventory[0]['source_outlines']=outlines
    warnings=['PDF semantic tags and complex layout are not yet generally qualified.']
    if any(e.get('status')=='unresolved' for e in outlines):warnings.append('Some PDF outline destinations lack a unique matching heading or qualified coordinates. All prose remains in source order; chapter navigation needs review.')
    return Document('PDF document','pdf',digest(data),out,inventory=inventory,warnings=warnings)

ADAPTERS={'.md':markdown,'.markdown':markdown,'.epub':epub,'.pdf':pdf,'.txt':plain}

def input_adapter(path):
    adapter=ADAPTERS.get(path.suffix.lower())
    if not adapter: raise Error('unsupported_format','Supported input formats: EPUB, Markdown, PDF, plain text (.txt).')
    return adapter

def inspect(path:Path)->Document:
    if path.stat().st_size>MAX_INPUT: raise Error('input_limit','Input exceeds 200 MiB limit.')
    with path.open('rb') as f: data=f.read(MAX_INPUT+1)
    if len(data)>MAX_INPUT: raise Error('input_limit','Input exceeds 200 MiB limit.')
    d=input_adapter(path)(data)
    if not d.blocks: raise Error('empty_document','Document contains no narratable material.')
    return d

IMPORT_REVISION='import-23'
def cached_inspect(path:Path,cache:Path)->Document:
    from .core import atomic,key,loadjson,writejson,filehash
    import importlib.metadata
    if path.stat().st_size>MAX_INPUT: raise Error('input_limit','Input exceeds 200 MiB.')
    with path.open('rb') as f: data=f.read(MAX_INPUT+1)
    if len(data)>MAX_INPUT: raise Error('input_limit','Input exceeds 200 MiB.')
    dependencies={name:importlib.metadata.version(name) for name in ['beautifulsoup4','lxml','markdown-it-py','mdit-py-plugins','pypdfium2','Pillow','tinycss2','webencodings']}
    ocr_config=None;reusable_extraction=True
    if path.suffix.lower()=='.pdf':
        import shutil
        ocr_root=Path(os.environ.get('AUDIOBOOK_OCR_ROOT',Path.home()/'.local/share/audiobook-tools/ocr'))
        exe=Path(shutil.which('tesseract') or ocr_root/'usr/bin/tesseract')
        dependencies['ocr_binary']=filehash(exe) if exe.is_file() else 'missing'
        datafile=ocr_root/'usr/share/tessdata/eng.traineddata'
        dependencies['ocr_eng']=filehash(datafile) if datafile.is_file() else 'system'
        from .pdf_ocr import configuration
        try:
            ocr_config=configuration();dependencies['ocr_observer']=ocr_config[2]
            reusable_extraction=ocr_config[3]
        except Error:
            reusable_extraction=False
    implementation={p.name:filehash(p) for p in Path(__file__).parent.glob('*.py') if p.name in ('importers.py','pronunciation.py','markdown_notes.py','text_source.py','text_evidence.py') or p.name.startswith(('epub_','pdf_'))}
    identity=key({'source':digest(data),'extension':path.suffix.lower(),'import_revision':IMPORT_REVISION,'dependencies':dependencies,'implementation':implementation})
    target=cache/(identity+'.json')
    if target.is_symlink(): raise Error('unsafe_path','Extraction cache entry cannot be a symlink.')
    if target.exists() and reusable_extraction:
        envelope=loadjson(target); payload=envelope['document']
        if envelope['document_sha256']==key(payload) and payload['source_sha256']==digest(data):
            return Document(payload['title'],payload['format'],payload['source_sha256'],[Block(**b) for b in payload['blocks']],payload['language'],payload['inventory'],payload['warnings'],payload.get('metadata',{}))
    # Import from the immutable bytes we just identified, never reread the mutable path.
    adapter=input_adapter(path)
    doc=adapter(data,ocr_cache=cache/'pdf-observations',ocr_config=ocr_config) if path.suffix.lower()=='.pdf' else adapter(data)
    if not doc.blocks: raise Error('empty_document','Document contains no narratable material.')
    payload=doc.json()
    if reusable_extraction:writejson(target,{'document_sha256':key(payload),'document':payload,'import_revision':IMPORT_REVISION,'dependencies':dependencies,'implementation':implementation})
    return doc
