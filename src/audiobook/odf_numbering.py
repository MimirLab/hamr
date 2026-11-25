"""Read saved OpenDocument list labels and bind them to their first paragraph."""

import re

from .odf_content import OFFICE, STYLE, TEXT, XML_ID
from .source_numbering import HTML_STYLE, SIGNED_INTEGER, marker_component


GROUP = TEXT+'list'
ITEM = TEXT+'list-item'
NUMBERED = TEXT+'numbered-paragraph'
NUMBER = TEXT+'list-level-style-number'
PARAGRAPHS = (TEXT+'p',TEXT+'h')
OMIT = frozenset(OFFICE+tag for tag in ('annotation','scripts','forms')) | {
    TEXT+'tracked-changes',TEXT+'deletion'}
AFFIX = re.compile(r'[()\[\].: \t]*')
ISSUE = 'This OpenDocument list has an automatic marker that needs a verified reading.'


def integer(value, default):
    return int(value) if value and SIGNED_INTEGER.fullmatch(value) else default


def authored_marks(body, parts, *, omitted=None):
    definitions = {}
    for member,root in parts:
        for list_style in root.iter(TEXT+'list-style'):
            key = list_style.get(STYLE+'name')
            if key:
                definitions[key] = (member,{integer(n.get(TEXT+'level'),0):n for n in list_style})
    marks = {}
    previous, ids, paragraphs = {},{},{}

    def record(owner, style, depth, value, parents, broken):
        target = next((node for node in owner if node.tag in PARAGRAPHS and ''.join(node.itertext()).strip()),None)
        if target is None:
            return
        member,levels = definitions.get(style,(None,{}))
        rule = levels.get(depth)
        expected = rule is not None and rule.tag==NUMBER
        cached = next((node for node in owner if node.tag==TEXT+'number'),None)
        printed = ''.join(cached.itertext()).strip() if cached is not None else ''
        if not expected and not (style and rule is None or broken or printed):
            return
        location = {'source_file':member,'list_style':style,'level':depth,
                    'item_path':owner.getroottree().getpath(owner)}
        if rule is not None:
            location['xml_path'] = rule.getroottree().getpath(rule)
        error = {'issue':ISSUE,'location':location}
        if printed:
            error['notation'] = printed
            error['location']['cached_marker_path'] = cached.getroottree().getpath(cached)
        if not expected or broken:
            marks[target] = (value,error)
            return
        form = rule.get(STYLE+'num-format','1')
        if not form:
            return
        count = integer(rule.get(TEXT+'display-levels'),1)
        counters = (*parents,(style,depth,value))[-count:] if 1<=count<=depth else ()
        components = []
        for other_style,level,number in counters:
            other = definitions.get(other_style,(None,{}))[1].get(level)
            if other is None or other.tag!=NUMBER or number is None:
                break
            symbol = other.get(STYLE+'num-format','1')
            letters = other.get(STYLE+'num-letter-sync')=='true'
            component = marker_component(number,HTML_STYLE.get(symbol),word_letters=letters)
            if component is None:
                break
            components.append(component)
        prefix,suffix = (rule.get(STYLE+'num-'+side,'') for side in ('prefix','suffix'))
        if not components or len(components)!=count or not AFFIX.fullmatch(prefix) or not AFFIX.fullmatch(suffix):
            marks[target] = (value,error)
            return
        notation = prefix+'.'.join(symbol for symbol,_ in components)+suffix
        label = 'Letter' if count==1 and form in ('A','a') else 'Number'
        if cached is not None and printed!=notation.strip():
            marks[target] = (value,error)
            return
        marks[target] = (value,{'notation':notation,'reading':label+' '+' dot '.join(word for _,word in components)+', ',
                               'values':[item[2] for item in counters],'location':location})

    def start(style,level):
        _,levels = definitions.get(style,(None,{}))
        rule = levels.get(level)
        return integer(rule.get(TEXT+'start-value'),1) if rule is not None else 1

    def visit(node, scope, style=None, parents=()):
        if node.tag in OMIT or omitted is not None and omitted(node):
            return
        if node.tag in (TEXT+'note-body', OFFICE+'text'):
            scope = node
        if node.tag==GROUP:
            style = node.get(TEXT+'style-name') or style
            depth = len(parents)+1
            key = (scope,depth)
            continuation = node.get(TEXT+'continue-list')
            continued = ids.get(continuation) if continuation else previous.get(key) if (
                node.get(TEXT+'continue-numbering')=='true') else None
            broken = bool(continuation and (not continued or continued[:2]!=(style,depth)))
            value = continued[2]+1 if continued and continued[:2]==(style,depth) else start(style,depth)
            for child in node:
                if child.tag==ITEM:
                    value = integer(child.get(TEXT+'start-value'),value)
                    record(child,style,depth,value,parents,broken)
                    for entry in child:
                        visit(entry,scope,style,(*parents,(style,depth,value)))
                    value += 1
                elif child.tag==TEXT+'list-header':
                    for entry in child:
                        visit(entry,scope,style,parents)
            previous[key] = style,depth,value-1
            if ident:=node.get(XML_ID):
                ids[ident] = None if ident in ids else previous[key]
            return
        if node.tag==NUMBERED:
            style = node.get(TEXT+'style-name') or style
            depth = integer(node.get(TEXT+'level'),1)
            ident = node.get(TEXT+'list-id')
            key = (scope,ident,style,depth)
            value = integer(node.get(TEXT+'start-value'),paragraphs.get(key,start(style,depth)-1)+1)
            parents = tuple((style,index,paragraphs.get((scope,ident,style,index)))
                            for index in range(1,min(depth,11)))
            record(node,style,depth,value,parents,not 1<=depth<=10)
            paragraphs[key] = value
        for child in node:
            visit(child,scope,style,parents)

    visit(body,body)
    return marks
