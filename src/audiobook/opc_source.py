"""Resolve package part names while keeping each physical archive member."""

from collections import defaultdict
from dataclasses import dataclass
from posixpath import dirname, join, normpath, split
import re
from urllib.parse import quote, unquote, urlsplit

from .archive_intake import Member
from .core import Error
from .document_intake import xml
from .source_bundle import safe_name

TYPES_NAME = '[Content_Types].xml'
TYPES = '{http://schemas.openxmlformats.org/package/2006/content-types}'
RELS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
ASCII_LOWER = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')
ESCAPE_ERROR = re.compile(r'%(?![0-9A-Fa-f]{2})|%(?:2f|5c)',re.I)


def part_key(path):
    if ESCAPE_ERROR.search(path):
        return None
    try:
        decoded = safe_name(unquote(path,encoding='utf-8',errors='strict'))
    except (Error,UnicodeError):
        return None
    if any(ord(letter)<32 or ord(letter)==127 for letter in decoded):
        return None
    # OPC equivalence is ASCII case insensitive on the encoded name.
    return quote(decoded,safe='/[]!$&\'()*+,;=@-._~').translate(ASCII_LOWER)


def reference(owner,href):
    if not href or any(ord(letter)<32 for letter in href):
        return None
    try:
        uri = urlsplit(href)
    except ValueError:
        return None
    if uri.scheme or uri.netloc or uri.query or not uri.path:
        return None
    path = uri.path.removeprefix('/') if uri.path.startswith('/') else join(dirname(owner),uri.path)
    return part_key(normpath(path))


def relationship_name(owner):
    folder,leaf = split(owner)
    return join(folder,'_rels',leaf+'.rels')


def manifest_xml(raw):
    try:
        node = xml(raw)
    except Error:
        return None
    return node if node.tag==TYPES+'Types' else None


@dataclass(frozen=True)
class Relationship:
    id: str
    kind: str
    target: str
    member: Member | None
    external: bool


class Parts:
    def __init__(self,prefix,members):
        self.prefix = prefix
        self.index = defaultdict(list)
        for member in members:
            if member.name.startswith(prefix) and (key:=part_key(self.local(member))) is not None:
                self.index[key].append(member)

    def local(self,member):
        return member.name[len(self.prefix):]

    def find(self,path):
        return self.one(part_key(path))

    def one(self,key):
        options = self.index.get(key,())
        return options[0] if len(options)==1 else None

    def resolve(self,owner,href):
        return self.one(reference(owner,href))

    def relationships(self,owner,read):
        part = self.local(owner) if owner is not None else ''
        path = relationship_name(part)
        candidates = self.index.get(part_key(path),())
        if len(candidates)>1:
            raise Error('source_opc_relationships',f'The package contains equivalent relationship parts named {path}.')
        if not candidates:
            return None,()
        rels, = candidates
        root = xml(read(rels))
        if root.tag!=RELS+'Relationships':
            raise Error('source_opc_relationships',f'Invalid package relationships in {rels.name}.',
                        'Open an intact edition from the publisher.')
        links = []
        for node in root.findall(RELS+'Relationship'):
            address = node.get('Target','')
            external = node.get('TargetMode','Internal')=='External'
            links.append(Relationship(node.get('Id',''),node.get('Type',''),address,
                                      None if external else self.resolve(part,address),external))
        return rels,tuple(links)
