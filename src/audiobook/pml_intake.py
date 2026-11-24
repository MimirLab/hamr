"""Prepare Palm Markup Language as ordinary chapters, paragraphs and source notes."""
from array import array
from bisect import bisect_right
import codecs
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from urllib.parse import quote, unquote

from lxml import etree

from .core import Error, key
from .document_intake import html_read
from .xml_projection import append_text


STYLES = {'i':'em','B':'strong','b':'strong','o':'s','u':'u','Sp':'sup','Sb':'sub'}
REFERENCES = {'Fn':'footnote','Sd':'sidebar'}
QUOTED = frozenset(('q','Q','Fn','Sd','m','T','w'))
LEXEME = re.compile(
    r'(?P<line>\r\n?|\n)|(?P<command>\\(?:a[0-9]{3}|U[0-9A-Fa-f]{4}|X[0-4]?|C[0-4]?|'
    r'Fn|Sd|Sp|Sb|[pxcrivtonsblBkmqQTwuI\\-]))|'
    r'(?P<definition></?(?:footnote|sidebar)\b[^>\r\n]*>)|'
    r'(?P<text>[^\\<\r\n]+|<)|(?P<unknown>\\[^\r\n]?)')
ARGUMENT = re.compile(r'="([^"\r\n]*)"')
DEFINITION = re.compile(r'<(?P<close>/)?(?P<kind>footnote|sidebar)(?:\s+id="(?P<id>[^"]*)")?\s*>')
NOTE_MARKER = re.compile(r'\s*(?:[0-9]+|[\[\(][0-9]+[\]\)]|[*†‡§¶]+)\s*')
BAD_CHARACTERS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')
ENCODINGS = ((codecs.BOM_UTF8,'utf-8-sig'),(codecs.BOM_UTF16_LE,'utf-16'),(codecs.BOM_UTF16_BE,'utf-16'))


def encoding_for(data,encoding):
    return encoding or next((name for bom,name in ENCODINGS if data.startswith(bom)),'cp1252')


def decode(data,encoding):
    requested = encoding_for(data,encoding)
    try:
        source = data.decode(requested)
    except (UnicodeError,LookupError) as error:
        raise Error('text_encoding','The PML source could not be decoded using '+requested+'.',
                    'Supply the original PML text encoding with --encoding.') from error
    if BAD_CHARACTERS.search(source):
        raise Error('text_encoding','The PML source contains characters outside the supported text range.')
    return source


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    argument: str | None
    start: int
    end: int


def scan(source):
    position = 0
    while position<len(source):
        match = LEXEME.match(source,position)
        kind,value = match.lastgroup,match[0]
        start,position = match.span()
        argument = None
        if kind=='command':
            value = value[1:]
            if value in QUOTED or value.startswith('C'):
                if result:=ARGUMENT.match(source,position):
                    argument = result[1]
                    position = result.end()
        yield Token(kind,value,argument,start,position)


def note_id(kind,value):
    return 'pml-'+kind+'-'+key(value)[:24]


class Reading:
    def __init__(self,source,part,builder):
        self.source = source
        self.part = part
        self.builder = builder
        self.lines = array('I',[0])
        self.lines.extend(hit.end() for hit in re.finditer(r'\r\n?|\n',source))
        self.body = etree.Element('body')
        self.annotations = {}
        self.block = None
        self.heading = None
        self.definition = None
        self.note = None
        self.in_appendix = False
        self.styles = []
        self.hidden = None
        self.reference = None
        self.anchor = None
        self.has_body = False
        self.heading_start = None

    def location(self,start,end):
        return {'source_file':self.part,'character_range':[start,end],
                'lines':[bisect_right(self.lines,start),bisect_right(self.lines,max(start,end-1))]}

    def element(self,tag,token,*,attributes=None,**extra):
        element = etree.Element(tag,attrib=attributes or {})
        if len(self.annotations)>=100000:
            raise Error('source_structure_size','The PML source exceeds the structured reading block budget.')
        label = str(len(self.annotations)+1)
        element.set('data-hamr-node',label)
        self.annotations[label] = {**self.location(token.start,token.end),
                                   'source_element':f'{self.part}:{token.start}',**extra}
        return element

    def extend(self,element,end):
        annotation = self.annotations[element.get('data-hamr-node')]
        annotation.update(self.location(annotation['character_range'][0],end))

    def paragraph(self,token):
        if self.block is None:
            tag = 'p'
            if self.heading:
                level = int(self.heading[1:]) if len(self.heading)>1 else 0
                tag = f'h{level+1}'
            self.block = self.element(tag,self.heading_start or token)
            self.has_body = self.has_body or self.note is None
            parent = self.note if self.note is not None else self.body
            parent.append(self.block)
        self.extend(self.block,token.end)
        return self.block

    def close_paragraph(self):
        self.block = self.anchor = None

    def emit(self,text,token):
        if self.block is None and not text.strip():
            return
        parent = self.paragraph(token)
        if self.reference:
            start,href,kind = self.reference
            if self.anchor is None:
                attributes = {'href':href}
                if kind in REFERENCES:
                    attributes['role'] = 'doc-noteref'
                self.anchor = self.element('a',start,attributes=attributes)
                self.extend(self.anchor,token.end)
                self.annotations[self.anchor.get('data-hamr-node')]['inline_location'] = self.location(start.start,token.end)
                parent.append(self.anchor)
            parent = self.anchor
        for style in self.styles:
            parent = etree.SubElement(parent,STYLES[style])
        append_text(parent,text)

    def review(self,token,reason):
        location = self.location(token.start,token.end)
        node = self.element('span',token,reading_span={'kind':'source_review','source':'','issue':reason,
                                                      'source_locator':location})
        self.paragraph(token).append(node)

    def invisible(self,token):
        if self.hidden is None:
            self.hidden = token
            return
        start = self.hidden
        content = self.source[start.end:token.start]
        if not self.has_body:
            for label,field in (('TITLE','title'),('AUTHOR','author'),('PUBLISHER','publisher')):
                if (match:=re.search(r'\b'+label+r'="([^"\r\n]+)"',content)) and match[1].strip():
                    if field=='title':
                        self.builder.doc.title = match[1].strip()
                    else:
                        self.builder.doc.metadata[field] = match[1].strip()
        self.builder.doc.inventory.append({'kind':'pml_invisible','status':'companion',
                                           **self.location(start.start,token.end)})
        self.hidden = None

    def note_definition(self,token):
        match = DEFINITION.fullmatch(token.value)
        if not match or match['close'] and match['kind']!=self.definition or not match['close'] and (
                not match['id'] or self.note is not None):
            self.review(token,'A Palm note definition has an invalid boundary or identifier.')
            return
        self.close_paragraph()
        if match['close']:
            self.extend(self.note,token.end)
            self.note = self.definition = None
        else:
            self.in_appendix = True
            self.definition = match['kind']
            attributes = {'id':note_id(self.definition,match['id']),'role':'doc-footnote'}
            self.note = self.element('aside',token,attributes=attributes)
            self.body.append(self.note)

    def link(self,token):
        value,argument = token.value,token.argument
        if argument is not None:
            if self.reference or not argument:
                self.review(token,'A Palm link has an empty target or overlaps another link.')
                return
            if value in REFERENCES:
                href = '#'+note_id(REFERENCES[value],argument)
            else:
                href = ('#'+note_id('anchor',unquote(argument[1:])) if argument.startswith('#') else argument)
            self.reference = (token,href,value)
        elif self.reference and self.reference[2]==value:
            if self.anchor is not None:
                self.extend(self.anchor,token.end)
                evidence = self.annotations[self.anchor.get('data-hamr-node')]
                location = self.location(self.reference[0].start,token.end)
                if value in REFERENCES and not NOTE_MARKER.fullmatch(''.join(self.anchor.itertext())):
                    self.anchor.attrib.pop('role')
                    marker = self.element('a',self.reference[0],
                                          attributes={'href':self.anchor.get('href'),'role':'doc-noteref'},
                                          inline_location=location)
                    self.block.append(marker)
                else:
                    evidence['inline_location'] = location
                self.extend(self.block,token.end)
            self.reference = self.anchor = None
        else:
            self.review(token,'A Palm link has no matching opening command.')

    def command(self,token):
        value,argument = token.value,token.argument
        if self.note is not None and (value in REFERENCES or value in ('x','q','Q') or value[0] in 'XC'):
            self.review(token,'Palm note bodies cannot contain chapter controls or linked references.')
            return
        if value=='v':
            self.invisible(token)
        elif value in STYLES or value=='n':
            if value=='n':
                self.styles = [style for style in self.styles if style!='b']
            elif value in self.styles:
                self.styles.remove(value)
            else:
                self.styles.append(value)
        elif value=='x' or value.startswith('X'):
            if self.heading and value in (self.heading,'X'):
                if self.block is not None:
                    self.extend(self.block,token.end)
                self.close_paragraph()
                self.heading = self.heading_start = None
            else:
                if self.heading:
                    self.review(token,'Palm chapter heading commands overlap.')
                self.close_paragraph()
                self.heading = value
                self.heading_start = token
        elif value in ('p','w'):
            self.close_paragraph()
            if value=='p':
                self.builder.doc.inventory.append({'kind':'pml_page_break',**self.location(token.start,token.end)})
        elif value=='q' or value in REFERENCES:
            self.link(token)
        elif value=='Q' and argument:
            self.paragraph(token).append(etree.Element('span',id=note_id('anchor',argument)))
        elif value.startswith('C') and argument:
            self.close_paragraph()
            label = self.element('span',token,source_control=True,chapter_marker=True,
                                 pml_heading_level=int(value[1:] or 0))
            label.text = argument
            self.body.append(label)
        elif value=='m' and argument:
            name = image_name(argument)
            if name:
                props = {'src':quote(name,safe='')}
                if name.casefold()=='cover.png':
                    props.update(role='presentation',alt='')
                self.paragraph(token).append(etree.Element('img',attrib=props))
            else:
                self.review(token,'A Palm image command has an invalid resource name.')
        elif value[0] in ('a','U'):
            try:
                char = (bytes([int(value[1:])]).decode('cp1252') if value.startswith('a')
                        else chr(int(value[1:],16)))
                if BAD_CHARACTERS.search(char):
                    raise ValueError
            except (ValueError,UnicodeError):
                self.review(token,'A Palm character escape has no usable text value.')
            else:
                self.emit(char,token)
        elif value=='\\':
            self.emit('\\',token)
        elif value in ('Q','m','T') or value.startswith('C'):
            self.review(token,'A Palm command has no quoted argument.')
        # Font, indentation, dictionary index and soft hyphen commands are layout controls.

    def read(self):
        for token in scan(self.source):
            if self.hidden is not None and not (token.kind=='command' and token.value=='v'):
                continue
            if token.kind=='definition':
                self.note_definition(token)
            elif self.in_appendix and self.note is None:
                continue
            elif token.kind=='line':
                if self.heading:
                    self.emit(' ',token)
                else:
                    self.close_paragraph()
            elif token.kind=='command':
                self.command(token)
            elif token.kind=='text':
                self.emit(token.value,token)
            else:
                self.review(token,'An unrecognized Palm markup command needs a verified reading.')
        end = len(self.source)
        if self.hidden:
            self.review(self.hidden,'A Palm invisible section has no closing command.')
        if self.reference:
            self.review(self.reference[0],'A Palm link has no closing command.')
        if self.note is not None or self.heading:
            self.review(Token('end','',None,end,end),'A Palm note or chapter heading has no closing command.')
        begin = len(self.builder.doc.blocks)
        html_read(etree.tostring(self.body,encoding='unicode'),self.builder,where=self.part,metadata=False,
                  collect_resources=True,collect_links=True,source_annotations=self.annotations)
        for block in self.builder.doc.blocks[begin:]:
            if limits:=block.locator.get('character_range'):
                block.locator['exact'] = self.source[limits[0]:limits[1]]


def image_name(value):
    if value and value not in ('.','..') and not any(char in value for char in ('/', '\\', ':', '#', '?')):
        return value


def images(source):
    return {name for token in scan(source) if token.kind=='command' and token.value=='m'
            and (name:=image_name(token.argument))}
