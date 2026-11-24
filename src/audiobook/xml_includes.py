"""Resolve local XML inclusions without mutating original trees or accessing a network."""

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from .core import Error
from .document_intake import MAX_STRUCTURE, xml
from .xml_projection import identifier

XI = '{http://www.w3.org/2001/XInclude}'
INCLUDE = XI+'include'
FALLBACK = XI+'fallback'
XML_BASE = '{http://www.w3.org/XML/1998/namespace}base'


def local_target(part,node,href):
    from .opf_intake import target

    for parent in (*reversed(list(node.iterancestors())),node):
        if base:=parent.get(XML_BASE):
            if base in ('.','..'):
                base += '/'
            part = target(part,base+'hamr.xml' if base.endswith('/') else base)
            if not part:
                return None
    return target(part,href)


def selected_node(root,selector):
    if not selector:
        return root
    match = re.fullmatch(r'element\(([-.\w:]+)\)',selector)
    anchor = match[1] if match else selector
    if not re.fullmatch(r'[-.\w:]+',anchor):
        return None
    found = [node for node in root.iter() if isinstance(node.tag,str) and identifier(node)==anchor]
    return found[0] if len(found)==1 else None


@dataclass(frozen=True)
class Inclusion:
    node: object | None
    problem: str | None
    status: str


class XMLIncludes:
    def __init__(self,root,part,load=None):
        self.roots = {part:root}
        self.owners = {root:part}
        self.bindings = {}
        self.references = []
        self.active = {}
        self.load = load
        self.missing = set()
        self.bytes_read = 0
        self.walk(root,part,())

    def owner(self,node):
        return self.owners[node.getroottree().getroot()]

    def document(self,part):
        if part in self.roots:
            return self.roots[part]
        if part in self.missing:
            return None
        if self.bytes_read>=MAX_STRUCTURE:
            raise Error('source_structure_size','Included XML exceeds the structured reading budget.')
        raw = self.load(part) if self.load else None
        if raw is None:
            self.missing.add(part)
            return None
        self.bytes_read += len(raw)
        if self.bytes_read>MAX_STRUCTURE:
            raise Error('source_structure_size','Included XML exceeds the structured reading budget.')
        root = xml(raw)
        self.roots[part] = root
        self.owners[root] = part
        return root

    def resolve(self,node,part,ancestors):
        href = node.get('href','')
        selector = node.get('xpointer')
        field = node.get('parse','xml')
        target = None
        try:
            uri = urlsplit(href)
        except ValueError:
            uri = None
        if uri and not uri.fragment and (href or selector):
            target = local_target(part,node,href) if href else part
        message = None
        chosen = None
        can_fallback = False
        if field!='xml':
            message = f'XInclude {href!r} uses parse={field!r}. Supply the verified text for this occurrence.'
        elif uri is None or uri.fragment or not (href or selector):
            message = f'XInclude {href!r} needs a valid XML reference.'
        elif target is None:
            can_fallback = True
            message = f'XInclude {href!r} needs a local publication member.'
        else:
            try:
                root = self.document(target)
            except Error as exc:
                if exc.code=='source_changed':
                    raise
                message = f'XInclude {href!r} could not be read. {exc}'
            else:
                chosen = selected_node(root,selector) if root is not None else None
                if chosen is None:
                    can_fallback = True
                    message = f'XInclude {href!r} does not identify one readable local XML occurrence.'
                elif len(ancestors)>=32 or any(chosen is scope or scope in chosen.iterdescendants() for scope in ancestors+(node,)):
                    chosen = None
                    message = f'XInclude {href!r} forms a cycle or exceeds the include nesting budget.'
        status = 'expanded' if chosen is not None else 'unavailable'
        if can_fallback:
            fallback = node.findall(FALLBACK)
            if len(fallback)==1:
                chosen, = fallback
                message = None
                status = 'fallback'
        reference = {'kind':'xml_include','member':part,'href':href,'selector':selector,
                     'xml_path':node.getroottree().getpath(node),'line':node.sourceline,'status':status}
        self.references.append((reference,target))
        return Inclusion(chosen,message,status)

    def walk(self,scope,part,ancestors):
        pending = [scope]
        while pending:
            node = pending.pop()
            if not isinstance(node.tag,str) or node in self.active:
                continue
            self.active[node] = None
            if node.tag==INCLUDE:
                binding = self.resolve(node,part,ancestors+(scope,))
                self.bindings[node] = binding
                if binding.node is not None:
                    self.walk(binding.node,self.owner(binding.node),ancestors+(scope,))
            elif node.tag!=FALLBACK or node is scope:
                pending.extend(reversed(list(node)))
