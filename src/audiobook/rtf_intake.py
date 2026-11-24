"""Read RTF groups and project their visible text into shared source blocks."""

import codecs
from dataclasses import dataclass, field, replace
import re

from .core import Error
from .rtf_numbering import ISSUE as RTF_LIST_ISSUE, cached_marker, list_formats
from .source_numbering import embed_ordered_label
from .source_notes import Definition, NoteGraph, note_text

WORD = re.compile(rb'([a-zA-Z]+)(-?[0-9]+)? ?')
RAW = re.compile(rb'[^{}\\\r\n]+')
DESTINATIONS = frozenset((
    'colortbl','info','generator','header','headerl','headerr','headerf',
    'footer','footerl','footerr','footerf','fldinst','filetbl','listtable','listoverridetable',
    'themedata','datastore','xmlnstbl','listtext','listname','leveltext','levelnumbers','pntext',
    'annotation','object','pict','nonshppict','shppict','shprslt','nonesttables',
    'ftnsep','ftnsepc','ftncn','aftnsep','aftnsepc','aftncn',
))
OBJECTS = frozenset(('object','pict'))
MERGES = frozenset(('clmgf','clmrg','clvmgf','clvmrg'))
CHARSETS = {77:'mac_roman',128:'cp932',129:'cp949',130:'cp1361',134:'cp936',136:'cp950',
            161:'cp1253',162:'cp1254',163:'cp1258',177:'cp1255',178:'cp1256',
            186:'cp1257',204:'cp1251',222:'cp874',238:'cp1250',254:'cp437'}
CHILD_MODE = {'stylesheet':'style','style':'skip','fonts':'font','font':'skip'}
ENCODING_CONTROLS = frozenset(('ansicpg','mac','pc','pca','f','deff','cpg','plain'))
ESCAPED = {'~':'\u00a0','_':'\u2011','-':'\u00ad','{':'{','}':'}','\\':'\\'}
WORDS = {'line':'\n','tab':'\t','emdash':'—','endash':'–','bullet':'•',
         'lquote':'‘','rquote':'’','ldblquote':'“','rdblquote':'”'}


def tokens(data):
    """Read the wire form once. Binary bytes never participate in group syntax."""
    position = 0
    while position<len(data):
        start = position
        char = data[position]
        position += 1
        if char in (123,125):
            yield ('open' if char==123 else 'close'),None,start,position
        elif char in (10,13):
            continue
        elif char!=92:
            match = RAW.match(data,start)
            position = match.end()
            yield 'text',match[0],start,position
        elif position==len(data):
            raise Error('unreadable_rtf','RTF ends inside a control sequence.')
        elif (symbol:=chr(data[position]))=="'":
            raw = data[position+1:position+3]
            if not re.fullmatch(rb'[0-9A-Fa-f]{2}',raw):
                raise Error('unreadable_rtf',f'RTF contains an invalid hex escape at byte {start}.')
            position += 3
            yield 'text',bytes((int(raw,16),)),start,position
        elif symbol in ESCAPED:
            position += 1
            yield 'text',ESCAPED[symbol],start,position
        elif symbol in ('\r','\n'):
            position += 2 if data[position:position+2]==b'\r\n' else 1
            yield 'control',('par',None),start,position
        elif match:=WORD.match(data,position):
            name = match[1].decode('ascii')
            parameter = match[2]
            if parameter is not None and len(parameter.lstrip(b'-'))>10:
                raise Error('unreadable_rtf',f'An RTF control parameter is too large at byte {start}.')
            number = int(parameter) if parameter is not None else None
            position = match.end()
            if name=='bin':
                if number is None or number<0 or position+number>len(data):
                    raise Error('unreadable_rtf','An RTF binary object is truncated.')
                position += number
            yield 'control',(name,number),start,position
        else:
            position += 1
            yield ('optional' if symbol=='*' else 'symbol'),None,start,position


@dataclass
class Formatting:
    hidden: bool = False
    fallback: int = 1
    encoding: str = 'cp1252'
    style: int = 0
    outline: int | None = None
    font: int | None = None
    codepage: str | None = None
    list_instance: int | None = None
    list_level: int = 0


@dataclass
class Reference:
    at: int
    target: str = ''


@dataclass
class Passage:
    text: str
    spans: list
    outline: int | None
    list_context: tuple | None = None


@dataclass
class Flow:
    fragments: list = field(default_factory=list)
    byte_run: bytearray = field(default_factory=bytearray)
    outline: int | None = None
    started: bool = False
    list_instance: int | None = None
    list_level: int = 0
    marker: tuple | None = None

    def decode(self,encoding,position):
        if self.byte_run:
            try:
                self.fragments.append(self.byte_run.decode(encoding))
            except UnicodeError as exc:
                raise Error('rtf_encoding',f'Could not decode RTF bytes at offset {position}.') from exc
            self.byte_run.clear()

    def reference(self,position,target):
        for part in reversed(self.fragments):
            if isinstance(part,Reference):
                if not part.target:
                    part.target = target
                    return
                break
            if part.strip():
                break
        self.fragments.append(Reference(position,target))

    def take(self,position):
        parts,spans,pending = [],[],[]
        offset = 0
        for item in [*self.fragments,None]:
            if isinstance(item,str):
                pending.append(item)
                continue
            if pending:
                try:
                    text = ''.join(pending).encode('utf-16-le','surrogatepass').decode('utf-16-le')
                except UnicodeError as exc:
                    raise Error('rtf_encoding',f'Invalid Unicode in the RTF passage at byte {position}.') from exc
                parts.append(text)
                offset += len(text)
                pending.clear()
            if item is not None:
                spans.append({'kind':'note_reference','source':'','source_start':offset,'source_end':offset,
                              'href':item.target or f'#rtf-missing:{item.at}','source_locator':{'byte':item.at}})
        raw = ''.join(parts)
        text = raw.strip()
        left = len(raw)-len(raw.lstrip())
        for span in spans:
            index = min(len(text),max(0,span['source_start']-left))
            span.update(source_start=index,source_end=index)
        result = Passage(text,spans,self.outline,(self.marker,self.list_instance,self.list_level))
        self.fragments.clear()
        self.outline = None
        self.started = False
        self.list_instance,self.list_level,self.marker = None,0,None
        return result


@dataclass
class Note:
    start: int
    target: str
    end: int = 0
    kind: str = 'footnote'
    passages: list = field(default_factory=list)
    issue: str | None = None


@dataclass
class Shape:
    start: int
    has_text: bool = False


@dataclass
class Group:
    formatting: Formatting
    flow: Flow
    note: Note | None = None
    mode: str = 'body'
    optional: bool = False
    opened_note: bool = False
    style_id: int | None = None
    base_style: int | None = None
    style_outline: int | None = None
    font: int | None = None
    charset: int | None = None
    codepage: str | None = None
    shape: Shape | None = None
    owns_shape: bool = False
    opens_shape_text: bool = False
    inside_shape_text: bool = False
    marker: tuple | None = None


class Reader:
    def __init__(self,builder):
        self.builder = builder
        self.first_block = len(builder.doc.blocks)
        self.group = Group(Formatting(),Flow())
        self.stack = []
        self.styles = {}
        self.outlines = {}
        self.fonts = {}
        self.default_font = None
        self.notes = []
        self.skip_fallback = 0
        self.rows = []
        self.cells = []
        self.in_row = False
        self.table_issue = None
        self.position = 0

    def decode(self):
        fmt = self.group.formatting
        font = fmt.font if fmt.font is not None else self.default_font
        encoding = fmt.codepage or self.fonts.get(font) or fmt.encoding
        self.group.flow.decode(encoding,self.position)

    def outline(self):
        fmt = self.group.formatting
        if fmt.outline is not None:
            return fmt.outline
        if fmt.style not in self.outlines:
            current = fmt.style
            checked = set()
            while current in self.styles and current not in checked:
                checked.add(current)
                current,level = self.styles[current]
                if level is not None:
                    self.outlines[fmt.style] = level
                    break
            else:
                self.outlines[fmt.style] = None
        return self.outlines[fmt.style]

    def glyph(self,value):
        group = self.group
        if group.formatting.hidden:
            return
        if not group.flow.started and value.strip():
            group.flow.started = True
            group.flow.outline = self.outline()
            group.flow.list_instance = group.formatting.list_instance
            group.flow.list_level = group.formatting.list_level
        if isinstance(value,bytes):
            group.flow.byte_run.extend(value)
        else:
            self.decode()
            group.flow.fragments.append(value)

    def flush_table(self):
        if not self.rows:
            return
        from .document_intake import cell_semantics

        values = [[cell.text for cell in row] for row in self.rows]
        spans = [cell_semantics(i,j,cell.text,cell.spans) for i,row in enumerate(self.rows)
                 for j,cell in enumerate(row) if cell.spans]
        issue = self.table_issue
        if len({len(row) for row in self.rows})>1:
            issue = issue or 'The RTF rows have different cell counts and need a verified reading.'
        self.builder.table(values,f'rtf:{self.position}',headers=[],issue=issue,semantics=spans)
        self.rows.clear()
        self.table_issue = None

    def paragraph(self):
        self.decode()
        group = self.group
        passage = group.flow.take(self.position)
        if not (passage.text or passage.spans):
            return
        label = cached_marker(*passage.list_context,self.list_formats)
        issue = label[1].get('issue') if label else None
        if group.note is not None:
            if issue:
                group.note.issue = group.note.issue or issue
            elif label:
                embed_ordered_label(passage.text,passage.spans,label)
            group.note.passages.append(passage)
            return
        self.flush_table()
        role = 'heading' if passage.outline in range(9) else 'body'
        loc = {'inline_semantics':passage.spans} if passage.spans else {}
        if label:
            value,marker = label
            loc.update(ordered_item=value,ordered_marker=marker)
        item = self.builder.add(passage.text,role,f'rtf:{self.position}',loc=loc,issue=issue)
        if '\n' in passage.text:
            item.locator['authored_line_breaks'] = True

    def open_note(self,start):
        self.decode()
        target = f'#rtf-note:{start}'
        group = self.group
        group.flow.reference(start,target)
        group.note = Note(start,target)
        self.notes.append(group.note)
        group.opened_note = True
        group.flow = Flow()
        group.formatting = replace(group.formatting,hidden=False,style=0,outline=None)

    def close_note(self):
        self.paragraph()
        self.group.note.end = self.position

    def definition(self,note):
        parts,spans = [],[]
        offset = 0
        for passage in note.passages:
            if parts:
                spans.append({'kind':'note_boundary','source':'','source_start':offset,'source_end':offset})
            parts.append(passage.text)
            spans.extend({**span,'source_start':span['source_start']+offset,'source_end':span['source_end']+offset}
                         for span in passage.spans)
            offset += len(passage.text)+1
        text = ' '.join(parts)
        loc = {'inline_semantics':spans,'note_kind':note.kind,'byte_range':[note.start,note.end]}
        if not text.strip():
            note.issue = note.issue or 'The RTF note contains no visible text.'
        block = self.builder.add(text,'note',f'rtf:{note.start}',loc=loc,issue=note.issue)
        if note.issue:
            block.issue = None
            block.locator['note_issue'] = note.issue
        return Definition(block,note_text(block,terminal=True),note.issue)

    def style_control(self,name,number):
        group = self.group
        if group.mode!='style' or number is None:
            return
        if name=='s':
            group.style_id = number
        elif name=='sbasedon':
            group.base_style = number
        elif name=='outlinelevel':
            group.style_outline = number

    def save_font(self):
        group = self.group
        if group.font is not None:
            self.fonts[group.font] = group.codepage or CHARSETS.get(group.charset)
            group.font = group.charset = group.codepage = None

    def font_control(self,name,number):
        if number is None:
            return
        group = self.group
        if name=='f':
            self.save_font()
            group.font = number
        elif name=='fcharset':
            group.charset = number
        elif name=='cpg' and number:
            group.codepage = self.codepage(number)

    @staticmethod
    def codepage(number):
        try:
            return codecs.lookup(f'cp{number}').name
        except LookupError as exc:
            raise Error('rtf_encoding',f'RTF codepage {number} has no local codec.') from exc

    def embedded_issue(self,issue,where):
        if self.group.note is not None:
            self.group.note.issue = issue
        elif self.in_row:
            self.table_issue = self.table_issue or issue
        else:
            self.paragraph()
            self.builder.issue(issue,where)

    def table_control(self,name,number):
        if self.group.note is not None:
            self.group.note.issue = 'An RTF note contains a table that needs a verified reading.'
            if name in ('cell','nestcell','row','nestrow'):
                self.glyph('\n')
            return
        if self.group.inside_shape_text and self.in_row:
            self.table_issue = 'An RTF table contains a text frame with its own table layout.'
            if name.endswith(('cell','row')):
                self.glyph('\n')
            return
        if name in ('trowd','intbl'):
            if not self.in_row:
                self.paragraph()
                self.in_row = True
        elif name in MERGES or name in ('nestcell','nestrow','nesttableprops') or name=='itap' and number is not None and number>1:
            self.table_issue = 'An RTF table has merged or nested cells that need a verified reading.'
        if name in ('cell','nestcell','row','nestrow'):
            self.decode()
            flow = self.group.flow
            if name.endswith('cell') or flow.fragments:
                passage = flow.take(self.position)
                if label:=cached_marker(*passage.list_context,self.list_formats):
                    if issue:=label[1].get('issue'):
                        self.table_issue = self.table_issue or issue
                    else:
                        embed_ordered_label(passage.text,passage.spans,label)
                self.cells.append(passage)
            if name.endswith('row') and self.cells:
                self.rows.append(self.cells.copy())
                self.cells.clear()
                self.in_row = False

    def control(self,name,number,start):
        group = self.group
        if group.optional:
            group.optional = False
            if name not in ('footnote','stylesheet','fonttbl','nesttableprops','shpinst','shptxt','listtext','pntext') and not (
                    group.mode=='style' and name=='s'):
                group.mode = 'skip'
        if group.mode=='skip':
            return
        if group.mode=='list_marker':
            if name=='uc' and number is not None:
                group.formatting.fallback = max(0,number)
            elif name=='u' and number is not None:
                self.glyph(chr(number&0xffff))
                self.skip_fallback = group.formatting.fallback
            elif name in WORDS:
                self.glyph(WORDS[name])
            return
        if group.mode in ('fonts','font'):
            self.font_control(name,number)
            return
        if group.mode=='shape':
            if name=='shptxt':
                group.shape.has_text = True
                group.mode = 'body'
                group.opens_shape_text = group.inside_shape_text = True
                if not self.in_row:
                    self.paragraph()
            return
        if group.mode!='body':
            self.style_control(name,number)
            return
        fmt = group.formatting
        if name=='stylesheet':
            group.mode = 'stylesheet'
        elif name=='fonttbl':
            group.mode = 'fonts'
        elif name=='shpinst':
            if fmt.hidden:
                group.mode = 'skip'
            else:
                group.mode = 'shape'
                group.shape = Shape(start)
                group.owns_shape = True
        elif name=='footnote' and not fmt.hidden:
            self.open_note(start)
        elif name=='ftnalt' and group.note is not None:
            group.note.kind = 'endnote'
        elif name=='nesttableprops':
            self.table_control(name,number)
        elif name in ('listtext','pntext') and not fmt.hidden:
            group.mode = 'list_marker'
            group.marker = name,start
            group.flow = Flow()
        elif name in DESTINATIONS or name=='footnote':
            if name in OBJECTS and not fmt.hidden:
                self.embedded_issue(f'RTF contains an embedded {name} requiring source review.',f'rtf:{start}')
            group.mode = 'skip'
        elif name=='uc' and number is not None:
            fmt.fallback = max(0,number)
        elif name=='u' and number is not None:
            self.glyph(chr(number & 0xffff))
            self.skip_fallback = fmt.fallback
        elif name=='chftn' and not fmt.hidden and (group.note is None or group.flow.started):
            self.decode()
            group.flow.fragments.append(Reference(start))
        elif name=='ansicpg' and number:
            fmt.encoding = self.codepage(number)
        elif name=='cpg' and number:
            fmt.codepage = self.codepage(number)
        elif name=='deff' and number is not None:
            self.default_font = number
        elif name=='f' and number is not None:
            fmt.font = number
            fmt.codepage = None
        elif name in ('mac','pc','pca'):
            fmt.encoding = {'mac':'mac_roman','pc':'cp437','pca':'cp850'}[name]
        elif name=='v':
            fmt.hidden = number!=0
        elif name=='plain':
            fmt.hidden = False
            fmt.font = fmt.codepage = None
        elif name=='pard':
            fmt.style,fmt.outline = 0,None
            fmt.list_instance,fmt.list_level = None,0
        elif name=='ls':
            fmt.list_instance = number
        elif name=='ilvl' and number is not None:
            fmt.list_level = number
        elif name=='s' and number is not None:
            fmt.style,fmt.outline = number,None
        elif name=='outlinelevel' and number is not None:
            fmt.outline = number
        elif name in ('par','page','sect'):
            # Character visibility can hide a paragraph mark, but it still
            # separates the surrounding visible source text.
            if self.in_row and group.note is None:
                self.glyph('\n')
            else:
                self.paragraph()
        elif not fmt.hidden:
            if name in ('trowd','cell','nestcell','row','nestrow','itap') or name in MERGES:
                self.table_control(name,number)
            elif name=='intbl' and number!=0:
                self.table_control(name,number)
            elif name in WORDS:
                self.glyph(WORDS[name])

    def read(self,data):
        self.list_formats = list_formats(tokens(data)) if b'\\listtable' in data else {}
        for kind,value,start,end in tokens(data):
            self.position = end
            group = self.group
            if kind in ('open','close'):
                self.decode()
                self.skip_fallback = 0
                if kind=='open':
                    self.stack.append(group)
                    mode = CHILD_MODE.get(group.mode,group.mode)
                    if group.optional:
                        mode = 'skip'
                    self.group = Group(replace(group.formatting),group.flow,group.note,mode,
                                       shape=group.shape,inside_shape_text=group.inside_shape_text)
                elif not self.stack:
                    raise Error('unreadable_rtf','RTF has unbalanced closing groups.')
                else:
                    if group.opened_note:
                        self.close_note()
                    elif group.marker:
                        name,offset = group.marker
                        parent = self.stack[-1].flow
                        text = group.flow.take(self.position).text
                        if not parent.started:
                            if parent.marker and parent.marker[0]!=text:
                                parent.marker = '',parent.marker[1],end,'conflicting cached markers'
                            elif parent.marker is None:
                                parent.marker = text,offset,end,name
                        elif self.in_row:
                            self.table_issue = self.table_issue or RTF_LIST_ISSUE
                    elif group.mode=='style' and group.style_id is not None:
                        self.styles[group.style_id] = group.base_style,group.style_outline
                        self.outlines.clear()
                    elif group.mode in ('fonts','font'):
                        self.save_font()
                    if group.opens_shape_text and not self.in_row:
                        self.paragraph()
                    if group.owns_shape and not group.shape.has_text:
                        self.embedded_issue('An RTF shape has no readable text box.',f'rtf:{group.shape.start}')
                    if len(self.stack)==1:
                        self.flush_table()
                        self.paragraph()
                    self.group = self.stack.pop()
                continue
            if self.skip_fallback:
                count = min(len(value),self.skip_fallback) if kind=='text' else 1
                self.skip_fallback -= count
                if kind!='text' or len(value)==count:
                    continue
                value = value[count:]
            if kind=='control':
                if value[0] in ENCODING_CONTROLS:
                    self.decode()
                self.control(*value,start)
            elif kind=='optional':
                group.optional = True
            elif kind=='text' and group.mode in ('body','list_marker') and not group.optional:
                self.glyph(value)
        if self.stack:
            raise Error('unreadable_rtf','RTF ended before its last group closed.')
        if self.cells or self.in_row:
            raise Error('unreadable_rtf','RTF ended before its last table row closed.')
        self.decode()
        self.flush_table()
        self.paragraph()
        definitions = {note.target:[self.definition(note)] for note in self.notes}
        graph = NoteGraph(definitions.get)
        for block in self.builder.doc.blocks[self.first_block:]:
            block.links.extend(graph.links_for(block.locator.get('inline_semantics',()),follow=block.role!='note'))
            if cells:=block.locator.get('table_cell_semantics'):
                graph.bind_cells(cells)
                block.links.extend(link for cell in cells for link in cell['notes'])


def read_rtf(data,builder):
    if not data.lstrip().startswith(b'{\\rtf'):
        raise Error('unreadable_rtf','The file does not contain an RTF header.')
    Reader(builder).read(data)
