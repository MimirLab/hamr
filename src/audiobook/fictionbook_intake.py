"""Prepare FictionBook sections, verse, notes and visuals as shared markup."""

import re
from urllib.parse import quote, unquote

from lxml import etree

from .document_intake import FORMULA_ELEMENTS, XLINK_HREF, XML_LANG, html_read, name, xml_formula
from .xml_projection import XMLProjection, append_text, element_text, identifier

FB2 = '{http://www.gribuser.ru/xml/fictionbook/2.0}'
ROLE = '{http://www.w3.org/1999/xlink}role'
NOTE_BODIES = frozenset(('notes','comments','footnotes','endnotes'))
DIVS = frozenset(('body','section','notes','notebody','div','poem','annotation','subscription','figure'))
INLINE = {'emphasis':'em','strikethrough':'s','text-author':'p','subtitle':'p','v':'p',
          'empty-line':'br','p':'p','ul':'ul','ol':'ol','li':'li','strong':'strong','em':'em','sub':'sub',
          'sup':'sup','s':'s','br':'br','table':'table','thead':'thead','tbody':'tbody','tfoot':'tfoot',
          'tr':'tr','th':'th','td':'td','caption':'caption'}
NOTE_NUMBER = re.compile(r'\[?\d+\]?\.?|[*†‡]+')


def value(root,path):
    item = root.find(path) if root is not None else None
    return element_text(item) if item is not None else ''


def names(metadata,fb2):
    if metadata is None:
        return
    if fb2:
        for person in metadata.findall('{*}author'):
            fields = [value(person,'{*}'+tag) for tag in ('first-name','middle-name','last-name')]
            yield ' '.join(filter(None,fields)) or value(person,'{*}nickname')
    else:
        for person in metadata.findall('{*}fb3-relations/{*}subject'):
            if person.get('link')=='author' or person.get('type')=='author':
                yield value(person,'{*}title') or value(person,'{*}name')


class FictionBook(XMLProjection):
    def __init__(self,root,part):
        super().__init__(root,part)
        self.fb2 = root.tag in ('FictionBook',FB2+'FictionBook')
        self.note_groups = set()
        self.note_sections = {}
        bodies = root.findall('{*}body') if self.fb2 else root.findall('{*}notes')
        references = {self.note_target(node) for node in root.iter() if self.is_note(node)}
        for i,body in enumerate(bodies):
            named_notes = body.get('name','').casefold() in NOTE_BODIES
            if not self.fb2 or named_notes:
                self.note_groups.add(body)
            for node in body:
                target = identifier(node)
                if not target:
                    continue
                if not self.fb2 and name(node)=='notebody' or self.fb2 and name(node)=='section' and (
                        named_notes or i>0 and target in references):
                    self.note_sections[node] = target

    @staticmethod
    def is_note(node):
        return name(node)=='note' or name(node)=='a' and (
            node.get('type','').casefold()=='note' or node.get(ROLE,'').casefold() in ('note','footnote','endnote'))

    @staticmethod
    def note_target(node):
        address = node.get('href') or node.get(XLINK_HREF) or ''
        return unquote(address.removeprefix('#')) if re.fullmatch(r'#?[^/?#:\s]+',address) else ''

    def note_reference(self,node,depth,literal):
        value = self.note_target(node)
        if not value:
            return self.review(node,'The FictionBook note has no local target.')
        attrs = {'href':'#'+quote(value,safe=''),'role':'doc-noteref'}
        anchor = self.element(node,'a',attributes=attrs,inline_location=self.location(node))
        anchor.attrib.pop('id',None)
        return self.children(node,anchor,depth=depth,literal=literal)

    def note(self,node,ident):
        result = self.element(node,'aside',attributes={'role':'doc-footnote','id':ident})
        for child in node:
            if name(child)=='title' and NOTE_NUMBER.fullmatch(element_text(child)):
                continue
            rendered = self.child(child,depth=1,literal=False)
            if rendered is not None:
                result.append(rendered)
            append_text(result,child.tail)
        return result

    def title(self,node,depth):
        parent = node.getparent()
        heading = parent is not None and name(parent) in ('FictionBook','fb3-body','body','section')
        tag = f'h{min(max(depth,1),6)}' if heading else 'p'
        lines = [child for child in node if name(child) in ('p','v') and (element_text(child) or len(child))]
        if not lines:
            return self.children(node,self.element(node,tag),depth=depth)
        result = self.element(node,tag)
        for line in lines:
            if len(result):
                result.append(etree.Element('br'))
            result.append(self.children(line,self.element(line,'span'),depth=depth))
        return result

    def stanza(self,node,depth):
        lines = [line for line in node if name(line) in ('v','p','br','empty-line')]
        result = self.element(node,'p',authored_line_breaks=True)
        for index,line in enumerate(lines):
            if index:
                result.append(etree.Element('br'))
            result.append(self.children(line,self.element(line,'span'),depth=depth))
        return result

    def project(self,node,*,depth=1,literal=False):
        kind = name(node)
        if node in self.note_sections or node in self.note_groups or kind in ('binary','description'):
            return None
        if self.is_note(node):
            return self.note_reference(node,depth,literal)
        if kind=='title':
            return self.title(node,depth)
        if kind=='stanza':
            return self.stanza(node,depth)
        if kind in ('img','image'):
            href = node.get('src') or node.get(XLINK_HREF)
            attrs = {'alt':node.attrib['alt']} if 'alt' in node.attrib else {}
            if href:
                attrs['src'] = href
            return self.element(node,'img',attributes=attrs)
        if kind in ('pagebreak','paper-page-break','paperpagebreak'):
            return self.element(node,'span',attributes={'role':'doc-pagebreak','aria-hidden':'true'})
        if kind in FORMULA_ELEMENTS:
            span = xml_formula(node,self.part)
            if kind=='math' or kind=='inline-formula':
                return self.element(node,'span',reading_span=span)
            return self.element(node,'p',reading_span=span,source_role='equation')
        if kind=='a':
            href = node.get(XLINK_HREF) or node.get('href')
            attrs = {'href':href} if href else {}
            return self.children(node,self.element(node,'a',attributes=attrs),depth=depth,literal=literal)
        if kind=='code':
            block = name(node.getparent()) in ('section','body','div')
            return self.children(node,self.element(node,'pre' if block else 'code'),depth=depth,literal=True)
        if kind in ('cite','epigraph','blockquote'):
            return self.children(node,self.element(node,'blockquote'),depth=depth,literal=literal)
        if kind in DIVS:
            return self.children(node,self.element(node,'div'),depth=depth+(kind=='section'),literal=literal)
        return self.children(node,self.element(node,INLINE.get(kind,'span')),depth=depth,literal=literal)

    def html(self,root):
        view = etree.Element('body')
        if self.fb2:
            for node in root.findall('{*}body'):
                if (body:=self.project(node)) is not None:
                    view.append(body)
        else:
            self.children(root,view,depth=1)
        for node,ident in self.note_sections.items():
            view.append(self.note(node,ident))
        return etree.tostring(view,encoding='unicode',method='html')


def read_fictionbook(root,builder,part,*,metadata=None,collect_resources=False):
    edition = FictionBook(root,part)
    if edition.fb2:
        metadata = root.find('{*}description/{*}title-info')
        title = value(metadata,'{*}book-title')
    else:
        title = value(metadata,'{*}title/{*}main')
    doc = builder.doc
    if title:
        doc.title = title
    if language:=value(metadata,'{*}lang') or root.get(XML_LANG):
        doc.language = language
    if authors:=list(dict.fromkeys(filter(None,names(metadata,edition.fb2)))):
        doc.metadata['authors'] = authors
    html_read(edition.html(root),builder,metadata=False,where=part,
              collect_resources=collect_resources,source_annotations=edition.annotations)
