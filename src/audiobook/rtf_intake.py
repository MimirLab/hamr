"""Read ordered RTF paragraphs and tables without loading a desktop suite."""
from dataclasses import dataclass, replace
import codecs
import re

from .core import Error

WORD = re.compile(rb'([a-zA-Z]+)(-?[0-9]+)? ?')
DESTINATIONS = frozenset((
    'fonttbl','colortbl','stylesheet','info','generator','header','headerl','headerr',
    'footer','footerl','footerr','fldinst','filetbl','listtable','listoverridetable',
    'themedata','datastore','xmlnstbl','listtext','pntext','annotation','object',
    'pict','nonshppict','shppict','shpinst','footnote',
))
OBJECTS = frozenset(('object','pict','footnote','shpinst'))
ESCAPED = {'~':'\u00a0','_':'\u2011','-':'\u00ad','{':'{','}':'}','\\':'\\'}
WORDS = {'line':'\n','tab':'\t','emdash':'—','endash':'–','bullet':'•',
         'lquote':'‘','rquote':'’','ldblquote':'“','rdblquote':'”'}


@dataclass
class State:
    skip: bool = False
    hidden: bool = False
    fallback: int = 1
    encoding: str = 'cp1252'


def read_rtf(data,builder):
    if not data.lstrip().startswith(b'{\\rtf'):
        raise Error('unreadable_rtf','The file does not contain an RTF header.')
    groups = []
    state = State()
    skip_next = 0
    buffer,bytes_run,table,cells = [],bytearray(),[],[]
    in_row = False
    position = 0

    def flush_bytes():
        if bytes_run:
            try:
                buffer.append(bytes_run.decode(state.encoding))
            except UnicodeError as exc:
                raise Error('rtf_encoding', f'Could not decode RTF bytes at offset {position}.') from exc
            bytes_run.clear()

    def glyph(value):
        nonlocal skip_next
        if skip_next:
            skip_next -= 1
        elif not (state.skip or state.hidden):
            if isinstance(value,int):
                bytes_run.append(value)
            else:
                flush_bytes()
                buffer.append(value)

    def text():
        flush_bytes()
        value = ''.join(buffer).encode('utf-16-le','surrogatepass').decode('utf-16-le','strict').strip()
        buffer.clear()
        return value

    def flush_table():
        if table:
            builder.table(table.copy(),f'rtf:{position}',headers=[])
            table.clear()

    def paragraph():
        value = text()
        if value:
            flush_table()
            item = builder.add(value,where=f'rtf:{position}')
            if '\n' in value:
                item.locator['authored_line_breaks'] = True

    while position<len(data):
        char = data[position]
        position += 1
        if char==123:
            flush_bytes()
            groups.append(state)
            state = replace(state)
        elif char==125:
            flush_bytes()
            if not groups:
                raise Error('unreadable_rtf','RTF has unbalanced closing groups.')
            state = groups.pop()
        elif char in (10,13):
            continue
        elif char!=92:
            glyph(char)
        elif position>=len(data):
            raise Error('unreadable_rtf','RTF ends inside a control sequence.')
        else:
            symbol = chr(data[position])
            if symbol==chr(39):
                try:
                    value = int(data[position+1:position+3],16)
                except ValueError as exc:
                    raise Error('unreadable_rtf','RTF contains an invalid hex escape.') from exc
                glyph(value)
                position += 3
            elif symbol=='*':
                flush_bytes()
                state.skip = True
                position += 1
            elif symbol in ESCAPED:
                glyph(ESCAPED[symbol])
                position += 1
            elif match:=WORD.match(data,position):
                control = match[1].decode('ascii')
                number = int(match[2]) if match[2] is not None else None
                position = match.end()
                if control=='bin':
                    if number is None or number<0 or position+number>len(data):
                        raise Error('unreadable_rtf','An RTF binary object is truncated.')
                    position += number
                if state.skip:
                    continue
                if control in DESTINATIONS:
                    flush_bytes()
                    if control in OBJECTS:
                        builder.issue(f'RTF contains an embedded {control} requiring source review.',f'rtf:{position}')
                    state.skip = True
                elif control=='uc' and number is not None:
                    state.fallback = max(0,number)
                elif control=='u' and number is not None:
                    if not state.hidden:
                        flush_bytes()
                        buffer.append(chr(number & 0xffff))
                    skip_next = state.fallback
                elif control=='ansicpg' and number is not None:
                    flush_bytes()
                    try:
                        state.encoding = codecs.lookup(f'cp{number}').name
                    except LookupError as exc:
                        raise Error('rtf_encoding', f'RTF codepage {number} has no local codec.') from exc
                elif control in ('mac','pc','pca'):
                    flush_bytes()
                    state.encoding = {'mac':'mac_roman','pc':'cp437','pca':'cp850'}[control]
                elif control=='v':
                    flush_bytes()
                    state.hidden = number!=0
                elif control=='plain':
                    flush_bytes()
                    state.hidden = False
                elif not state.hidden and control=='trowd':
                    if not in_row:
                        if buffer or bytes_run:
                            paragraph()
                        in_row = True
                elif not state.hidden and control in ('cell','nestcell'):
                    cells.append(text())
                elif not state.hidden and control in ('row','nestrow'):
                    if buffer or bytes_run:
                        cells.append(text())
                    if cells:
                        table.append(cells.copy())
                        cells.clear()
                    in_row = False
                elif not state.hidden and control in ('par','page','sect'):
                    if in_row:
                        glyph('\n')
                    else:
                        paragraph()
                elif control in WORDS:
                    glyph(WORDS[control])
            else:
                position += 1
    if groups:
        raise Error('unreadable_rtf','RTF ended before its last group closed.')
    if cells or in_row:
        raise Error('unreadable_rtf','RTF ended before its last table row closed.')
    flush_table()
    paragraph()
