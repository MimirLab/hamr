"""Attach physical XML positions while preparing shared structured markup."""

import re

from lxml import etree

from .core import Error
from .document_intake import XML_LANG, name, plain_xml_text

XML_ID = '{http://www.w3.org/XML/1998/namespace}id'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
LAYOUT_SPACE = re.compile(r'[ \t\r\n]+')
LOCATOR_ATTRIBUTES = ('id','headers','scope','rowspan','colspan')


def identifier(node):
    return node.get('id') or node.get(XML_ID)


def element_text(node):
    return ' '.join(plain_xml_text(node).split())


def normal_text(text,literal=False):
    return text if literal or text is None else LAYOUT_SPACE.sub(' ',text)


def append_text(parent,text):
    if text:
        if len(parent):
            parent[-1].tail = (parent[-1].tail or '')+text
        else:
            parent.text = (parent.text or '')+text


class XMLProjection:
    def __init__(self,root,part,*,includes=None):
        self.part = part
        self.tree = root.getroottree()
        self.annotations = {}
        self.includes = includes

    def location(self,node):
        parent = node.getparent() if isinstance(node,etree._Entity) else node
        if self.includes is not None:
            part = self.includes.owner(node)
        else:
            part = self.part
        result = {'source_file':part,'xml_path':parent.getroottree().getpath(parent),
                  'lines':[node.sourceline,node.sourceline]}
        if parent is not node:
            result['xml_entity'] = node.name
            result['entity_child'] = parent.index(node)+1
        return result

    def review(self,node,message):
        origin = self.location(node)
        return self.element(node,'span',source_issue=message,
                            reading_span={'kind':'source_review','source':'','issue':message,
                                          'source_locator':origin})

    def element(self,node,tag,*,attributes=None,**evidence):
        result = etree.Element(tag,attrib=attributes or {})
        label = str(len(self.annotations)+1)
        result.set('data-hamr-node',label)
        for field in LOCATOR_ATTRIBUTES:
            if value:=node.get(field):
                result.set(field,value)
        if anchor:=identifier(node):
            result.set('id',anchor)
        if language:=node.get(XML_LANG):
            result.set('lang',language)
        location = self.location(node)
        self.annotations[label] = {**location,'source_element':location['source_file']+':'+location['xml_path'],**evidence}
        return result

    def children(self,node,result,*,depth,literal=False):
        result.text = normal_text(node.text,literal)
        for child in node:
            projected = self.child(child,depth=depth,literal=literal)
            if projected is not None:
                result.append(projected)
            append_text(result,normal_text(child.tail,literal))
        return result

    def child(self,node,*,depth,literal):
        if isinstance(node,etree._Entity):
            return self.entity(node)
        return self.project(node,depth=depth,literal=literal) if name(node) else None

    def entity(self,node):
        origin = self.location(node)
        raise Error('source_xml',f'An unresolved XML entity occurs in {origin["source_file"]} at line {node.sourceline}.',
                    'Open an edition with the entities expanded in its source.')
