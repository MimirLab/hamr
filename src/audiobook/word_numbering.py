"""Resolve saved Word list counters before the shared numbering stage."""

from collections import defaultdict
from dataclasses import dataclass
import re

from .archive_intake import Member
from .core import Error
from .document_intake import attr, name, xml
from .opc_source import Parts
from .source_numbering import marker_component
from .word_stories import STRICT_WORD, WORD, enabled, properties, story_scope


RELATIONSHIPS = ('http://schemas.openxmlformats.org/officeDocument/2006/relationships/',
                 'http://purl.oclc.org/ooxml/officeDocument/relationships/')
NUMBER = re.compile(r'[+-]?[0-9]{1,9}')
FIELD = re.compile(r'%([1-9])(?!\d)')
SIMPLE_LABEL = re.compile(r'\(?%[1-9](?:\.%[1-9])*[.)]?')
ISSUE = 'This Word list has an automatic marker that needs a verified reading.'


def integer(value):
    return int(value) if value and NUMBER.fullmatch(value) else None


def child(node, kind):
    return next((element for element in node if name(element)==kind),())


def value(node, kind):
    item = child(node,kind)
    return attr(item,'val') if hasattr(item,'attrib') else None


def indexed(node, kind, attribute):
    items = defaultdict(list)
    for element in node:
        if name(element)==kind and (ident:=attr(element,attribute)) is not None:
            items[ident].append(element)
    return {ident:matches[0] for ident,matches in items.items() if len(matches)==1}


def declaration(props):
    number = child(props,'numPr')
    ident = value(number,'numId')
    level = value(number,'ilvl')
    return ident,level


@dataclass(frozen=True)
class Level:
    start: int
    restart: int
    format: str
    template: str | None
    style: str | None
    legal: bool
    location: dict


class Numbering:
    def __init__(self,read,members):
        parts = Parts('',[Member(index,path,None) for index,path in enumerate(members,1)])
        try:
            _,links = parts.relationships(parts.find('word/document.xml'),lambda member:read(member.name))
        except Error:
            links = ()
        self.paths = {}
        self.styles,self.abstract,self.instances = {},{},{}
        for kind,ids in (('numbering',('abstractNum','abstractNumId','num','numId')),
                         ('styles',('style','styleId'))):
            matching = [link for link in links if link.kind in {prefix+kind for prefix in RELATIONSHIPS}]
            if len(matching)!=1 or matching[0].member is None:
                continue
            path = matching[0].member.name
            try:
                root = xml(read(path))
            except Error:
                continue
            if root.tag not in {f'{{{ns}}}{kind}' for ns in (WORD,STRICT_WORD)}:
                continue
            self.paths[kind] = path
            if kind=='styles':
                self.styles = indexed(root,*ids)
            else:
                self.abstract = indexed(root,*ids[:2])
                self.instances = indexed(root,*ids[2:])
        self.definitions = {}
        self.counters = defaultdict(dict)

    def styled_instance(self, style):
        visited = set()
        while style in self.styles and style not in visited:
            visited.add(style)
            node = self.styles[style]
            ident,_ = declaration(properties(node,'pPr'))
            if ident is not None:
                return ident,style
            style = value(node,'basedOn')
        return None,None

    def levels(self, ident):
        if ident in self.definitions:
            return self.definitions[ident]
        levels = {}
        self.definitions[ident] = levels
        node = self.instances.get(ident)
        if node is None or (abstract:=self.abstract.get(value(node,'abstractNumId'))) is None:
            return levels
        overrides = indexed(node,'lvlOverride','ilvl')
        levels_by_index = indexed(abstract,'lvl','ilvl')
        for position in range(9):
            index = str(position)
            override = overrides.get(index,())
            custom = child(override,'lvl')
            selected = custom if hasattr(custom,'attrib') else levels_by_index.get(index)
            if selected is None:
                continue
            start = integer(value(override,'startOverride') or value(selected,'start'))
            restart = integer(value(selected,'lvlRestart'))
            legal = child(selected,'isLgl')
            levels[position] = Level(
                0 if start is None else start,
                restart if restart is not None and 0<=restart<=position else position,
                value(selected,'numFmt') or 'decimal',
                value(selected,'lvlText'),
                value(selected,'pStyle'),
                hasattr(legal,'attrib') and enabled(attr(legal,'val')),
                {'source_file':self.paths['numbering'],
                 'xml_path':selected.getroottree().getpath(selected),
                 'instance':ident,'level':position,
                 **({'override_path':override.getroottree().getpath(override)}
                    if hasattr(override,'attrib') else {})})
        return levels

    def next(self, node):
        props = properties(node,'pPr')
        ident,direct_level = declaration(props)
        inherited_style = None
        if ident is None:
            ident,inherited_style = self.styled_instance(value(props,'pStyle'))
        if ident in (None,'0'):
            return None
        levels = self.levels(ident)
        if direct_level is None:
            styled = [index for index,entry in levels.items() if inherited_style and entry.style==inherited_style]
            position = styled[0] if len(styled)==1 else 0
            if len(styled)>1:
                return None,{'issue':ISSUE,'instance':ident}
        else:
            position = integer(direct_level)
        if position not in levels:
            return None,{'issue':ISSUE,'instance':ident,'level':position}

        level = levels[position]
        counters = self.counters[story_scope(node),ident]
        for index, entry in levels.items():
            if index>position and entry.restart==position+1:
                counters.pop(index,None)
        current = counters[position] = counters.get(position,level.start-1)+1
        if level.format in ('bullet','none'):
            return None

        def unresolved():
            return current,{'issue':ISSUE,'location':level.location,'template':level.template,'form':level.format}

        template = level.template or ''
        if not SIMPLE_LABEL.fullmatch(template):
            return unresolved()
        printed,spoken,values = {},[],[]
        for field in FIELD.findall(template):
            index = int(field)-1
            if index>position or index not in levels:
                return unresolved()
            entry = levels[index]
            count = counters.get(index,entry.start)
            form = 'decimal' if level.legal else entry.format
            component = marker_component(count,form,word_letters=True)
            if component is None:
                return unresolved()
            notation,reading = component
            printed[field] = notation
            spoken.append(reading)
            values.append(count)
        label = 'Letter' if len(values)==1 and level.format.endswith('Letter') and not level.legal else 'Number'
        return current,{'notation':FIELD.sub(lambda hit:printed[hit[1]],template),
                        'reading':label+' '+' dot '.join(spoken)+', ',
                        'values':values,'location':level.location}
