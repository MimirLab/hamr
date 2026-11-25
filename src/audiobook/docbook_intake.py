"""Project DocBook structure into the common document and narration reader."""

from collections import defaultdict
from pathlib import PurePosixPath
from posixpath import dirname, relpath
import re
from urllib.parse import quote

from lxml import etree

from .core import key
from .document_intake import FORMULA_ELEMENTS, MAX_STRUCTURE, XLINK_HREF, XML_LANG, html_read, name, xml_formula
from .xml_includes import FALLBACK, INCLUDE, XMLIncludes, local_target
from .xml_projection import XML_SPACE, XMLProjection, append_text, element_text, identifier

DB = '{http://docbook.org/ns/docbook}'
SUFFIXES = ('.dbk','.docbook')
SECTIONS = frozenset(('set','book','article','chapter','appendix','part','partintro','preface','section',
                      'sect1','sect2','sect3','sect4','sect5','simplesect','reference','refentry','refsection',
                      'refsect1','refsect2','refsect3','refsynopsisdiv','topic','acknowledgements','colophon',
                      'dedication','abstract','glossary','glossdiv','bibliography','bibliodiv','sidebar'))
METADATA = frozenset(('info','bookinfo','articleinfo','chapterinfo','appendixinfo','prefaceinfo',
                      'partinfo','sectioninfo','sect1info','sect2info','sect3info','sect4info','sect5info',
                      'refentryinfo','referenceinfo','refmeta','topicinfo'))
CONTROLS = frozenset(('indexterm','index','toc','tocentry','tocdiv','lot','lotentry','titleabbrev',
                      'revhistory','remark','annotation','area','areaset','areaspec','co','colspec','spanspec','alt'))
CITATIONS = frozenset(('bibliography','bibliodiv','bibliolist'))
EQUATIONS = FORMULA_ELEMENTS | {'inlineequation'}
RESOURCES = frozenset(('imagedata','graphic','inlinegraphic','textdata','audiodata','videodata'))
MEDIA = frozenset(('mediaobject','inlinemediaobject','graphic','inlinegraphic'))
PARAGRAPHS = frozenset(('para','simpara','formalpara','refpurpose'))
LITERAL = frozenset(('programlisting','screen','literallayout','synopsis','cmdsynopsis','funcsynopsis'))
MARKUP = {'emphasis':'em','literal':'code','subscript':'sub','superscript':'sup',
          'blockquote':'blockquote','epigraph':'blockquote','itemizedlist':'ul','orderedlist':'ol',
          'listitem':'li','variablelist':'dl','varlistentry':'div','term':'dt','member':'p',
          'glossterm':'dt','glossdef':'dd','procedure':'ol','step':'li','substeps':'ol','attribution':'p',
          'question':'div','answer':'div','qandaset':'div','qandadiv':'div','qandaentry':'div',
          'note':'div','tip':'div','important':'div','warning':'div','caution':'div','danger':'div',
          'example':'div','informalexample':'div','screenshot':'div',
          'thead':'thead','tbody':'tbody','tfoot':'tfoot','tr':'tr','td':'td','th':'th'}


def is_docbook(root,part):
    if name(root) not in SECTIONS:
        return False
    if root.tag.startswith(DB):
        return True
    if root.tag.startswith('{'):
        return False
    dtd = root.getroottree().docinfo
    return (PurePosixPath(part).suffix.lower() in SUFFIXES or 'DocBook' in (dtd.public_id or '') or
            'docbook' in (dtd.system_url or '').casefold() or root.find('bookinfo') is not None)


def children(node,tags):
    return (child for child in node if name(child) in tags)


def first(node,*tags):
    return next(children(node,tags),None)


def header(node,field):
    result = first(node,field)
    if result is None and (meta:=next(children(node,METADATA),None)) is not None:
        result = first(meta,field)
    if result is None and field=='title' and name(node)=='refentry':
        if (meta:=first(node,'refmeta')) is not None:
            result = first(meta,'refentrytitle')
        if result is None and (named:=first(node,'refnamediv')) is not None:
            result = first(named,'refname')
    return result


def publication_label(root,includes=None):
    node = header(root,'title')
    return element_text(node) if node is not None else None


def authors(root):
    for meta in children(root,METADATA):
        candidates = [*children(meta,('author',)),
                      *(author for group in children(meta,('authorgroup',)) for author in children(group,('author',)))]
        for author in candidates:
            if (person:=first(author,'personname')) is None:
                person = author
            fields = ('honorific','firstname','givenname','othername','surname','lineage','orgname')
            value = ' '.join(filter(None,(element_text(item) for item in children(person,fields))))
            if value:
                yield value


def asset_links(root):
    for node in root.iter():
        if name(node) in RESOURCES and (href:=node.get('fileref')):
            yield node,href


def scope_number(value):
    # Avoid constructing large sparse column arrays from arbitrary metadata.
    return int(value) if value and re.fullmatch(r'[0-9]{1,5}',value) else 0


def cals_rows(group):
    width = scope_number(group.get('cols'))
    if width<1:
        return None
    positions = {}
    previous = 0
    for column in children(group,('colspec',)):
        position = scope_number(column.get('colnum')) if 'colnum' in column.attrib else previous+1
        label = column.get('colname')
        if not previous<position<=width or label in positions:
            return None
        if label:
            positions[label] = position
        previous = position
    rows = []
    for band in ('thead','tbody','tfoot'):
        for section in children(group,(band,)):
            for row in children(section,('row',)):
                placed = []
                column = 0
                for cell in children(row,('entry','entrytbl')):
                    if (name(cell)=='entrytbl' or cell.get('morerows','0')!='0'
                            or any(field in cell.attrib for field in ('namest','nameend','spanname'))):
                        return None
                    label = cell.get('colname')
                    position = positions.get(label,0) if label is not None else column+1
                    if not column<position<=width:
                        return None
                    placed.append((position,cell))
                    column = position
                rows.append((band,row,placed))
    if not rows or width>max((len(cells) for _,_,cells in rows),default=0)+64 or width*len(rows)>100000:
        return None
    return width,rows


def synopsis_line(node):
    kind = name(node)
    if kind=='sbr':
        return '\n'
    if kind not in ('cmdsynopsis','group','arg'):
        return element_text(node)
    if kind=='cmdsynopsis':
        return node.get('sepchar',' ').join(
            synopsis_line(child) for child in children(node,('command','arg','group','sbr')))
    if kind=='group':
        text = ' | '.join(synopsis_line(child) for child in node if name(child) and name(child) not in CONTROLS)
    else:
        parts = [node.text or '']
        for child in node:
            if name(child) and name(child) not in CONTROLS:
                parts.append(synopsis_line(child))
            parts.append(child.tail or '')
        text = ' '.join(''.join(parts).split())
    start,end = {'opt':('[',']'),'req':('{','}')}.get(node.get('choice','opt'),('',''))
    return start+text+end+('...' if node.get('rep')=='repeat' else '')


def function_lines(synopsis):
    for node in children(synopsis,('funcprototype','funcsynopsisinfo')):
        if name(node)=='funcsynopsisinfo':
            yield ''.join(node.itertext()).strip()
        elif (declaration:=first(node,'funcdef')) is not None:
            args = ['...' if name(item)=='varargs' else 'void' if name(item)=='void' else element_text(item)
                    for item in children(node,('paramdef','varargs','void'))]
            yield f'{element_text(declaration)}({", ".join(args)});'


class DocBook(XMLProjection):
    def __init__(self,root,part,includes):
        super().__init__(root,part,includes=includes)
        self.ids = defaultdict(list)
        self.note_ids = {}
        self.entities = {}
        self.entity_bytes = 0
        for node in includes.active:
            if value:=identifier(node):
                self.ids[value].append(node)
            if name(node)=='footnote':
                loc = self.location(node)
                self.note_ids[node] = 'db-note-'+key([loc['source_file'],loc['xml_path']])[:24]

    def target(self,node,anchor):
        choices = self.ids.get(anchor,())
        local = [item for item in choices if self.includes.owner(item)==self.includes.owner(node)]
        candidates = local or choices
        return candidates[0] if len(candidates)==1 else None

    def entity(self,node):
        root = node.getroottree().getroot()
        if root not in self.entities:
            dtd = root.getroottree().docinfo.internalDTD
            self.entities[root] = {entity.name:entity.content for entity in dtd.iterentities()
                                   if not entity.system_url and entity.content and len(entity.content)<=4096
                                   and not re.search(r'[<>&%]',entity.content)} if dtd is not None else {}
        value = self.entities[root].get(node.name)
        size = len(value.encode()) if value else 0
        if size and self.entity_bytes+size<=MAX_STRUCTURE:
            self.entity_bytes += size
            result = self.element(node,'span')
            result.text = value
            return result
        return self.review(node,f'DocBook entity &{node.name}; needs the text from the prepared publisher edition.')

    def section(self,node,depth,literal):
        result = self.element(node,'div')
        if (title:=header(node,'title')) is not None:
            heading = self.element(title,f'h{min(max(depth,1),6)}')
            result.append(self.children(title,heading,depth=depth))
        if (subtitle:=header(node,'subtitle')) is not None:
            result.append(self.children(subtitle,self.element(subtitle,'p'),depth=depth))
        for meta in children(node,METADATA):
            for summary in children(meta,('abstract',)):
                result.append(self.project(summary,depth=depth+1))
        for child in node:
            if name(child) in METADATA | {'title','subtitle'}:
                continue
            if (view:=self.child(child,depth=depth+1,literal=literal)) is not None:
                result.append(view)
        return result

    def note(self,node,target):
        anchor = self.note_ids.get(target)
        if anchor is None:
            return self.review(node,'This DocBook footnote reference has no unique target in the edition.')
        view = self.element(node,'a',attributes={'role':'doc-noteref','href':'#'+anchor},
                            inline_location=self.location(node))
        view.attrib.pop('id',None)
        return view

    def link(self,node,depth,literal):
        anchor = node.get('linkend')
        href = node.get(XLINK_HREF) or node.get('url') or ('#'+quote(anchor,safe='') if anchor else None)
        attrs = {'href':href} if href else {}
        view = self.children(node,self.element(node,'a' if href else 'span',attributes=attrs),
                             depth=depth,literal=literal)
        if element_text(view):
            return view
        alternate = self.target(node,node.get('endterm')) if node.get('endterm') else None
        target = self.target(node,anchor) if anchor else None
        if target in self.note_ids:
            return self.note(node,target)
        if alternate is not None:
            return self.children(alternate,view,depth=depth,literal=literal)
        if target is not None:
            if label:=target.get('xreflabel'):
                view.text = label
                return view
            if (title:=header(target,'title')) is not None:
                return self.children(title,view,depth=depth,literal=literal)
        if name(node)=='xref' or anchor:
            return self.review(node,f'DocBook cross-reference {anchor or href!r} needs its authored link text.')
        view.text = href
        return view

    def phrase(self,node,tag,depth,origin):
        label = element_text(node)
        reading = {'source':label,'kind':'layout_reading','reading':label+'.',
                   'origin':origin,'source_locator':self.location(node)}
        options = {'reading_span':reading} if label and label[-1] not in '.!?' else {}
        return self.children(node,self.element(node,tag,**options),depth=depth)

    def simple_list(self,node,depth,literal):
        inline = node.get('type')=='inline'
        view = self.element(node,'span' if inline else 'ul')
        for item in children(node,('member',)):
            if inline:
                if len(view):
                    append_text(view,', ')
                view.append(self.children(item,self.element(item,'span'),depth=depth,literal=literal))
            else:
                view.append(self.phrase(item,'li',depth,'DocBook list item'))
        return view

    def named_reference(self,node,depth):
        view = self.element(node,'div')
        purpose = first(node,'refpurpose')
        head = node.getparent()
        title = header(head,'title') if head is not None else None
        primary = element_text(title) if title is not None else ''
        aliases = [element_text(item) for item in children(node,('refname',)) if element_text(item)!=primary]
        if aliases:
            line = self.element(node,'p')
            line.text = 'Also called '+', '.join(aliases)+'.'
            view.append(line)
        if purpose is not None:
            view.append(self.children(purpose,self.element(purpose,'p'),depth=depth))
        return view

    def synopsis(self,node,depth):
        view = self.element(node,'pre')
        if any(isinstance(item,etree._Entity) for item in node.iter()):
            return self.children(node,view,depth=depth,literal=True)
        if name(node)=='cmdsynopsis' and first(node,'synopfragment') is None and not any(
                name(item)=='synopfragmentref' for item in node.iter()):
            view.text = synopsis_line(node)
        elif name(node)=='funcsynopsis' and node.get('language','c').casefold()=='c' and not node.xpath(
                'processing-instruction("db")'):
            view.text = '\n'.join(function_lines(node))
        else:
            self.children(node,view,depth=depth,literal=True)
        return view

    def quotation(self,node,depth,literal):
        result = self.children(node,self.element(node,'span'),depth=depth,literal=literal)
        quotes = sum(name(parent)=='quote' for parent in node.iterancestors())
        left,right = ('‘','’') if quotes%2 else ('“','”')
        value = element_text(node)
        if value and not value.startswith(('“','‘','"','\'')):
            result.text = left+(result.text or '')
            append_text(result,right)
        return result

    def bibliography(self,node,depth,literal):
        result = self.element(node,'div')
        if (title:=header(node,'title')) is not None:
            result.append(self.children(title,self.element(title,'p',source_role='reference'),depth=depth))
        for child in children(node,('biblioentry','bibliomixed',*CITATIONS)):
            view = self.project(child,depth=depth,literal=literal)
            if view is not None:
                result.append(view)
        return result

    def media(self,node,depth):
        alternatives = list(children(node,('textobject',)))
        labels = [element_text(item) for item in children(node,('alt',))]
        if not any(labels):
            preferred = [item for item in alternatives if item.get('role')=='alt'] or alternatives
            labels = [element_text(item) for item in preferred if first(item,'textdata') is None]
        descriptions = list(dict.fromkeys(filter(None,labels)))
        if len(descriptions)>1:
            return self.review(node,'This DocBook visual has several distinct descriptions. Supply the intended reading.')
        image = next((item for item in node.iter() if name(item) in ('graphic','inlinegraphic','imagedata')),node)
        label_text = descriptions[0] if descriptions else None
        attrs = {'alt':label_text} if label_text else {}
        if href:=image.get('fileref'):
            owner = self.includes.owner(image)
            path = local_target(owner,image,href)
            attrs['src'] = quote(relpath(path,dirname(owner) or '.'),safe='/') if path else href
        result = self.element(image,'img',attributes=attrs)
        parent = node.getparent()
        if parent is not None and name(parent) in ('figure','informalfigure'):
            label = header(parent,'title')
            if label is not None and label_text==element_text(label):
                result.set('aria-hidden','true')
        if (caption:=first(node,'caption')) is None or name(node)=='inlinemediaobject':
            return result
        figure = self.element(node,'figure')
        figure.append(self.children(caption,self.element(caption,'figcaption'),depth=depth))
        if label_text==element_text(caption):
            result.set('aria-hidden','true')
        figure.append(result)
        return figure

    def table(self,node,depth):
        groups = list(children(node,('tgroup',)))
        caption = header(node,'title')
        if not groups:
            return self.children(node,self.element(node,'table'),depth=depth)
        result = self.element(node,'div')
        if caption is not None:
            result.append(self.phrase(caption,'figcaption',depth,'DocBook caption title'))
        for group in groups:
            geometry = cals_rows(group)
            if geometry is None:
                result.append(self.review(group,'This CALS table has merged, nested, or unresolved cells. Supply a verified table reading.'))
                continue
            width,rows = geometry
            table = self.element(group,'table')
            for band,row,cells in rows:
                view = self.element(row,'tr')
                placed = dict(cells)
                tag = 'th' if band=='thead' else 'td'
                for column in range(1,width+1):
                    cell = placed.get(column)
                    view.append(self.children(cell,self.element(cell,tag),depth=depth) if cell is not None else etree.Element(tag))
                table.append(view)
            result.append(table)
        return result

    def project(self,node,*,depth=1,literal=False):
        kind = name(node)
        if node.tag==INCLUDE:
            return self.includes.project(self,node,depth=depth,literal=literal)
        if node.tag==FALLBACK:
            return self.children(node,self.element(node,'div'),depth=depth,literal=literal)
        if node.tag.startswith('{') and not node.tag.startswith(DB) and kind not in EQUATIONS:
            return None
        if kind in METADATA or kind in CONTROLS:
            return None
        if kind in CITATIONS:
            return self.bibliography(node,depth,literal)
        if kind in ('biblioentry','bibliomixed'):
            return self.children(node,self.element(node,'p',source_role='reference'),depth=depth,literal=literal)
        if kind in SECTIONS:
            return self.section(node,depth,literal)
        if kind=='footnote':
            return self.note(node,node)
        if kind=='footnoteref':
            return self.note(node,self.target(node,node.get('linkend')))
        if kind in ('link','xref','ulink','olink'):
            return self.link(node,depth,literal)
        if kind=='quote':
            return self.quotation(node,depth,literal)
        if kind in EQUATIONS:
            display = kind not in ('math','inlineequation','inline-formula')
            span = xml_formula(node,self.location(node)['source_file'])
            if display:
                return self.element(node,'p',reading_span=span,source_role='equation')
            return self.element(node,'span',reading_span=span)
        if kind in MEDIA:
            return self.media(node,depth)
        if kind in ('table','informaltable'):
            return self.table(node,depth)
        if kind=='simplelist':
            return self.simple_list(node,depth,literal)
        if kind=='td':
            row = node.getparent()
            band = row.getparent() if row is not None else None
            if band is not None and name(band)=='thead':
                return self.children(node,self.element(node,'th'),depth=depth)
        if kind in ('figure','informalfigure'):
            return self.children(node,self.element(node,'figure'),depth=depth)
        if kind=='title' and name(node.getparent()) in ('figure','table','informaltable','example'):
            return self.phrase(node,'figcaption',depth,'DocBook caption title')
        if kind=='refnamediv':
            return self.named_reference(node,depth)
        if kind=='caption':
            tag = 'caption' if name(node.getparent()) in ('table','informaltable') else 'figcaption'
            return self.children(node,self.element(node,tag),depth=depth)
        if kind in ('cmdsynopsis','funcsynopsis'):
            return self.synopsis(node,depth)
        tag = ('p' if kind in PARAGRAPHS else 'pre' if kind in LITERAL else MARKUP.get(kind,'span'))
        literal = kind in LITERAL or node.get(XML_SPACE)=='preserve' or literal and node.get(XML_SPACE)!='default'
        return self.children(node,self.element(node,tag),depth=depth,literal=literal)

    def html(self,root):
        result = etree.Element('body')
        result.append(self.project(root))
        for node,ident in self.note_ids.items():
            note = self.element(node,'aside',attributes={'role':'doc-footnote'})
            note.set('id',ident)
            result.append(self.children(node,note,depth=1))
        return etree.tostring(result,encoding='unicode',method='html')


def read_docbook(root,builder,part,*,includes=None,collect_resources=False):
    includes = includes or XMLIncludes(root,part)
    if title:=publication_label(root):
        builder.doc.title = title
    if lang:=root.get(XML_LANG) or root.get('lang'):
        builder.doc.language = lang
    if people:=list(dict.fromkeys(authors(root))):
        builder.doc.metadata['authors'] = people
    edition = DocBook(root,part,includes)
    html_read(edition.html(root),builder,metadata=False,where=part,collect_resources=collect_resources,
              source_annotations=edition.annotations)
