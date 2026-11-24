"""Read BITS book and part hierarchies using the JATS content projection."""

from collections import defaultdict
from posixpath import dirname, join, relpath, splitext
import re
from urllib.parse import quote

from lxml import etree

from .core import Error, key
from .document_intake import XML_LANG, html_read, name
from .jats_intake import Article, asset_links, author_name
from .xml_includes import FALLBACK, INCLUDE, Inclusion, XMLIncludes, local_target
from .xml_projection import append_text, element_text, identifier

BOOK_STRUCTURE = frozenset(('front-matter','book-body','book-back'))
PARTS = frozenset(('book-part','front-matter-part','book-app'))
NAMED_PARTS = frozenset(('dedication','foreword','preface'))
PART_META = frozenset(('book-part-meta','front-matter-part-meta','book-app-meta'))
NAVIGATION = frozenset(('toc','toc-entry','nav-pointer'))


def is_bits(root):
    return root.tag=='book-part-wrapper' or root.tag=='book' and (
        any(child.tag in BOOK_STRUCTURE | {'book-meta'} for child in root) or
        'Book Interchange' in (root.getroottree().docinfo.public_id or ''))


def part_node(root):
    return next((child for child in root if child.tag in PARTS | NAMED_PARTS),None)


def part_metadata(part):
    return next((child for child in part if child.tag in PART_META),None)


def bookshelf_id(root):
    identities = {element_text(node) for node in root.findall('book-meta/book-id[@book-id-type="pmcid"]')}
    return next(iter(identities)) if len(identities)==1 else None


def bookshelf_toc(root):
    if root.tag=='book-part-wrapper' and root.get('content-type')=='toc' and part_node(root) is None:
        if (identity:=bookshelf_id(root)) and (toc:=root.find('toc')) is not None:
            if any(link.get('link-type')=='booklink' and link.get('source-id')==identity
                   for link in toc.findall('.//toc-entry/nav-pointer/related-object')):
                return toc,identity
    return None


class BookIncludes(XMLIncludes):
    def __init__(self,root,part,load=None):
        super().__init__(root,part,load)
        self.contents = {}
        self.alternates = []
        if layout:=bookshelf_toc(root):
            toc,identity = layout
            represented = self.bind_contents(toc,identity,part)
            if represented:
                folder = dirname(part)
                self.alternates = [join(folder,'license.txt'),splitext(part)[0]+'.pdf',
                                   *(splitext(path)[0]+'.pdf' for path in represented)]

    def bind_contents(self,toc,identity,part):
        represented = []
        visited = set()
        for entry in toc.iter('toc-entry'):
            links = [node for node in entry.findall('nav-pointer/related-object')
                     if node.get('link-type')=='booklink' and node.get('source-id')==identity
                     and node.get('document-id')]
            if not links:
                continue
            targets = {node.get('document-id') for node in links}
            document_id = links[0].get('document-id')
            if len(targets)==1 and document_id in visited:
                continue  # An in-chapter TOC anchor points to the same physical chapter.
            path = None
            selected = None
            if len(targets)==1 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',document_id):
                visited.add(document_id)
                path = join(dirname(part),document_id+'.nxml')
                try:
                    wrapper = self.document(path)
                except Error as exc:
                    if exc.code=='source_changed':
                        raise
                    wrapper = None
                if (wrapper is not None and wrapper.tag=='book-part-wrapper'
                        and wrapper.get('id')==document_id and bookshelf_id(wrapper)==identity):
                    body = part_node(wrapper)
                    meta = part_metadata(body) if body is not None else None
                    if meta is not None and any(element_text(node)==document_id for node in
                                                meta.findall('book-part-id[@book-part-id-type="pmcid"]')):
                        selected = body
            if selected is not None:
                status,problem = 'expanded',None
                represented.append(path)
                self.walk(selected,path,())
            else:
                status = 'unavailable'
                title = entry.find('title')
                label = element_text(title) if title is not None else document_id
                problem = f'The table of contents points to {label!r}, but its matching chapter XML is unavailable.'
            reference = {'kind':'xml_chapter','member':part,'href':path,'selector':document_id,
                         'xml_path':entry.getroottree().getpath(entry),'line':entry.sourceline,'status':status}
            self.references.append((reference,path if selected is not None else None))
            self.contents[entry] = Inclusion(selected,problem,status)
        return represented


def metadata(root,includes=None):
    book = root.find('book-meta')
    if root.tag=='book' or bookshelf_toc(root):
        return book,book
    part = part_node(root)
    if part is None and includes is not None:
        for node in root.findall(INCLUDE):
            selected = includes.bindings[node].node
            if selected is not None and selected.tag in PARTS | NAMED_PARTS:
                part = selected
                break
    return (part_metadata(part) if part is not None else None),book


def title_node(meta):
    if meta is not None:
        node = meta.find('book-title-group/book-title')
        return node if node is not None else meta.find('title-group/title')
    return None


def publication_label(root,includes=None):
    main,book = metadata(root,includes)
    title = title_node(main)
    if title is None:
        title = title_node(book)
    return element_text(title) if title is not None else None


class Book(Article):
    def __init__(self,root,part,includes):
        self.includes = includes
        self.note_owners = defaultdict(set)
        if len(includes.roots)>1:
            for node in self.in_scope('fn',includes.active):
                if target:=identifier(node):
                    self.note_owners[target].add(includes.owner(node))
        super().__init__(root,part,active=includes.active)

    def note_anchor(self,node,target):
        owners = self.note_owners.get(target,())
        if len(owners)<=1:
            return target
        return key([self.includes.owner(node),target])[:24]

    def location(self,node):
        return {'source_file':self.includes.owner(node),
                'xml_path':node.getroottree().getpath(node),'lines':[node.sourceline,node.sourceline]}

    def headings(self,meta,depth):
        if meta is None:
            return
        title = title_node(meta)
        if title is not None:
            group = title.getparent()
            label = group.find('label')
            heading = self.element(title,f'h{min(max(depth,1),6)}')
            if label is not None:
                heading.append(self.children(label,self.element(label,'span'),depth=depth))
                append_text(heading,' ')
                heading.append(self.children(title,self.element(title,'span'),depth=depth))
            else:
                self.children(title,heading,depth=depth)
            yield heading
            if (subtitle:=group.find('subtitle')) is not None:
                yield self.children(subtitle,self.element(subtitle,'p'),depth=depth)
        yield from self.abstracts(meta)

    def image(self,node,description=''):
        result = super().image(node,description)
        owner = self.includes.owner(node)
        if href:=result.get('src'):
            if path:=local_target(owner,node,href):
                result.set('src',quote(relpath(path,dirname(owner) or '.'),safe='/'))
        return result

    def project(self,node,*,depth=0,literal=False):
        kind = name(node)
        if node.tag==INCLUDE:
            binding = self.includes.bindings[node]
            if binding.problem:
                return self.review(node,binding.problem)
            return self.project(binding.node,depth=depth,literal=literal)
        if node.tag==FALLBACK or kind in BOOK_STRUCTURE or kind=='named-book-part-body':
            return self.children(node,self.element(node,'div'),depth=depth,literal=literal)
        if kind in PARTS | NAMED_PARTS:
            result = self.element(node,'div')
            meta = part_metadata(node)
            result.extend(self.headings(meta,depth+1))
            for child in node:
                if not name(child) or name(child) in PART_META:
                    continue
                item = self.project(child,depth=depth+1,literal=literal)
                if item is not None:
                    result.append(item)
            return result
        if kind in NAVIGATION:
            return self.alternate(node)
        if kind in ('book-meta','collection-meta') or kind in PART_META:
            return None
        return super().project(node,depth=depth,literal=literal)

    def html(self):
        body = etree.Element('body')
        if self.article.tag=='book' or self.includes.contents:
            body.extend(self.headings(self.article.find('book-meta'),1))
        if self.includes.contents:
            for node,binding in self.includes.contents.items():
                body.append(self.review(node,binding.problem) if binding.problem else self.project(binding.node,depth=1))
        for child in self.article:
            if name(child) in BOOK_STRUCTURE | PARTS | NAMED_PARTS or child.tag==INCLUDE:
                body.append(self.project(child))
        self.append_notes(body)
        return etree.tostring(body,encoding='unicode',method='html')


def read_bits(root,builder,part,*,includes=None,collect_resources=False):
    includes = includes or BookIncludes(root,part)
    main,book = metadata(root,includes)
    doc = builder.doc
    if title:=publication_label(root,includes):
        doc.title = title
    if lang:=root.get(XML_LANG):
        doc.language = lang
    if main is not None:
        authors = [author_name(node) for node in main.xpath('./contrib-group/contrib[not(@contrib-type) or @contrib-type="author"]')]
        if authors:=list(filter(None,authors)):
            doc.metadata['authors'] = authors
        identifiers = [element_text(node) for node in main if name(node) in ('book-id','book-part-id') and element_text(node)]
        if identifiers:
            doc.metadata['source_identifiers'] = identifiers
    if book is not None and book is not main:
        title = title_node(book)
        if title is not None and (title:=element_text(title)):
            doc.metadata['parent_book_title'] = title
    edition = Book(root,part,includes)
    html_read(edition.html(),builder,metadata=False,where=part,collect_resources=collect_resources,
              source_annotations=edition.annotations)
