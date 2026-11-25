"""Read the statements, string literals and opaque facets of FrameMaker MIF."""

from bisect import bisect_right
from dataclasses import dataclass, field
import re

from .core import Error
from .document_intake import MAX_STRUCTURE

HEADER = re.compile(rb"\A(?:\xef\xbb\xbf)?(?:[ \t\r\n]|#[^\r\n]*(?:\r?\n|\r))*<MIFFile\s+([0-9]{1,8})(?:\.[0-9]+)?\s*>")
TOKEN = re.compile(
    r"(?P<space>[ \t\n\f]+)|(?P<comment>\#[^\n]*)|"
    r"(?P<facet>^=[A-Za-z][^\n]*)|(?P<open><[A-Za-z][\w]*)|(?P<close>>)|"
    r"(?P<quoted>`(?:[^'\\]|\\.)*')|(?P<word>[^\s<>`#]+)", re.M|re.S,
)
END_INSET = re.compile(r'^=EndInset[ \t]*(?:\n|$)', re.M)
ESCAPES = {'q':"'",'Q':'`','>':'>','\\':'\\','t':'\t'}
STRING_ESCAPE = re.compile(r'\\([qQ>\\t])')
MAX_STATEMENTS = 550_000
MAX_DEPTH = 96


def matches_header(raw):
    return bool(HEADER.match(raw))


def argument(token):
    return STRING_ESCAPE.sub(lambda match:ESCAPES[match[1]],token[1:-1]) if token.startswith('`') else token


@dataclass(slots=True)
class Statement:
    name: str
    line: int
    end: int = 0
    args: list = field(default_factory=list)
    children: list = field(default_factory=list)

    @property
    def value(self):
        return self.args[0] if self.args else ''

    def all(self,name):
        return (child for child in self.children if child.name==name)

    def first(self,name):
        return next(self.all(name),None)

    def get(self,name,default=''):
        child = self.first(name)
        return child.value if child is not None and child.args else default

    def walk(self,name):
        for child in self.children:
            if child.name==name:
                yield child
            yield from child.walk(name)


def parse_mif(raw):
    if len(raw)>MAX_STRUCTURE:
        raise Error('source_structure_size','The FrameMaker manuscript exceeds the structured reader memory budget.',
                    'Save a smaller MIF chapter or open a tagged PDF export.')
    header = HEADER.match(raw)
    if not header:
        raise Error('source_mif_signature','The input does not identify an Adobe FrameMaker MIF document.',
                    'Open a FrameMaker MIF export. Geographic MapInfo MIF uses a different format.')
    if int(header[1])<8:
        raise Error('source_mif_version','This MIF uses the older FrameMaker character encoding.',
                    'Save a UTF-8 MIF export (version 8 or later), or open a readable PDF export.')
    try:
        data = raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
    except UnicodeDecodeError as exc:
        raise Error('source_mif_encoding',f'The FrameMaker MIF text is not UTF-8 at byte {exc.start+1:,}.',
                    'Save the MIF again from FrameMaker or open its tagged PDF export.') from exc
    newlines = [match.start() for match in re.finditer('\n',data)]
    line = lambda position:bisect_right(newlines,position)+1

    def invalid(position,reason):
        raise Error('source_mif_syntax',f'{reason} at MIF line {line(position):,}.',
                    'Open the complete original or save the document as MIF again.')

    root = Statement('',1)
    stack = [root]
    offset = count = 0
    while offset<len(data):
        token = TOKEN.match(data,offset)
        if not token:
            invalid(offset,'Unrecognized FrameMaker syntax or unfinished quoted text')
        kind = token.lastgroup
        if kind=='open':
            count += 1
            if count>MAX_STATEMENTS or len(stack)>MAX_DEPTH:
                raise Error('source_structure_size','The FrameMaker manuscript exceeds the statement budget.',
                            'Save a smaller MIF chapter or open a tagged PDF export.')
            node = Statement(token[0][1:],line(offset))
            stack[-1].children.append(node)
            stack.append(node)
        elif kind=='close':
            if len(stack)==1:
                invalid(offset,'An unmatched closing bracket occurs')
            stack.pop().end = line(offset)
        elif kind=='facet':
            if not (len(stack)>1 and stack[-1].name=='ImportObject'):
                invalid(offset,'An embedded graphic facet is outside its import object')
            end = END_INSET.search(data,offset)
            if not end:
                invalid(offset,'An embedded graphic has no closing facet marker')
            offset = end.end()
            continue
        elif kind in ('quoted','word'):
            if len(stack)==1:
                invalid(offset,'Unexpected text outside a FrameMaker statement')
            stack[-1].args.append(argument(token[0]))
        offset = token.end()
    if len(stack)!=1:
        invalid(len(data),'A FrameMaker statement was not closed')
    root.end = line(len(data))
    return root


def loose_mif(path):
    with path.open('rb') as file:
        return parse_mif(file.read(MAX_STRUCTURE+1))
