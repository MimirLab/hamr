"""Read authored math structure before projecting it into narration."""

from dataclasses import dataclass
import re
import unicodedata

from bs4 import NavigableString
from lxml import etree

MATHML = 'http://www.w3.org/1998/Math/MathML'
OFFICE_MATH = frozenset(('http://schemas.openxmlformats.org/officeDocument/2006/math',
                         'http://purl.oclc.org/ooxml/officeDocument/math'))
WORD = frozenset(('http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                  'http://purl.oclc.org/ooxml/wordprocessingml/main'))
OPERATORS = {
    '+':'plus', '−':'minus', '-':'minus', '±':'plus or minus', '=':'equals',
    '×':'times', '·':'times', '\u2062':'times', '/':'divided by', '÷':'divided by',
    '≥':'greater than or equal to', '≤':'less than or equal to',
    '>':'greater than', '<':'less than', '≠':'does not equal',
    '(':'open parenthesis', ')':'close parenthesis', '[':'open bracket', ']':'close bracket',
    ',':'comma', '∞':'infinity', '∣':'vertical bar', '′':'prime', '″':'double prime',
}
TOKEN = re.compile(r'\d+(?:\.\d+)?|[^\W\d_]+|[^\s]', re.UNICODE)
NUMBER = re.compile(r'\d+(?:\.\d+)?')
ZERO_THICKNESS = re.compile(r'(?:0+\.?0*|\.0+)(?:%|[a-z]+)?', re.I)
SCRIPT_ROLES = {'sSup':('msup',('e','sup')), 'sSub':('msub',('e','sub')),
                'sSubSup':('msubsup',('e','sub','sup')), 'f':('mfrac',('num','den'))}
MATH_SPANS = frozenset(('mathml','formula'))


@dataclass(frozen=True)
class Expression:
    kind: str
    children: tuple = ()
    text: str = ''


def speak(expr):
    kind = expr.kind
    if kind == 'mn':
        return ''.join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in expr.text)
    if kind in ('mi','mtext'):
        return expr.text
    if kind == 'mo':
        return OPERATORS.get(expr.text,expr.text)
    children = tuple(speak(child) for child in expr.children)
    if kind in ('math','mrow'):
        return ' '.join(children)
    if kind == 'mfrac':
        a,b = children
        return 'the fraction with numerator '+a+' and denominator '+b
    if kind == 'msqrt':
        value = ' '.join(children)
        return 'the square root of '+value+(' end root' if len(children)>1 or compound(expr.children[0]) else '')
    if kind == 'mroot':
        a,b = children
        return 'the root with index '+b+' and radicand '+a
    def argument(index):
        value = children[index]
        return 'open parenthesis '+value+' close parenthesis' if compound(expr.children[index]) else value

    base = argument(0)
    if kind == 'msup':
        return base+' to the power of '+argument(1)
    if kind == 'msub':
        return base+' subscript '+argument(1)
    if kind == 'msubsup':
        return base+' subscript '+argument(1)+' to the power of '+argument(2)
    raise ValueError('Unsupported mathematical structure: '+kind)


def compound(expr):
    while expr.kind in ('math','mrow') and len(expr.children)==1:
        expr = expr.children[0]
    return bool(expr.children)


def mathml(node, depth=0):
    """Accept Beautiful Soup nodes from HTML and native XML from containers."""
    if depth>16:
        raise ValueError('Math nesting exceeds the supported subset')
    if isinstance(node,etree._Element):
        qualified = etree.QName(node)
        if qualified.namespace!=MATHML:
            raise ValueError('Unexpected namespace inside MathML')
        tag = qualified.localname
        if any(isinstance(child,etree._Entity) for child in node):
            raise ValueError('MathML contains an unresolved entity')
        elements = [child for child in node if isinstance(child.tag,str)]
        raw = node.text or ''
        has_mixed_text = bool(raw.strip() or any((child.tail or '').strip() for child in node))
    else:
        tag = node.name
        elements = [child for child in node.children if getattr(child,'name',None)]
        raw = node.get_text()
        has_mixed_text = any(type(child) is NavigableString and child.strip() for child in node.children)
    if tag in ('mn','mi','mo','mtext') and not elements and raw.strip():
        return Expression(tag,text=raw.strip())
    if has_mixed_text:
        raise ValueError('MathML contains text outside a declared token')
    if tag=='semantics':
        elements = [child for child in elements if local_name(child) not in ('annotation','annotation-xml')]
        if len(elements)!=1:
            raise ValueError('MathML semantics has no unique displayed expression')
    if tag=='mfrac' and ZERO_THICKNESS.fullmatch(node.get('linethickness','').strip()):
        raise ValueError('MathML stack has no fraction bar')
    argument_count = {'msup':2,'msub':2,'msubsup':3,'mfrac':2,'mroot':2}
    if (tag in ('math','mrow','mstyle','semantics','msqrt') and elements
            or tag in argument_count and len(elements)==argument_count[tag]):
        kind = 'math' if tag in ('mstyle','semantics') else tag
        return Expression(kind,tuple(mathml(child,depth+1) for child in elements))
    raise ValueError('Unsupported mathematical structure: '+tag)


def local_name(node):
    return etree.QName(node).localname if isinstance(node,etree._Element) else node.name


def mathml_text(node):
    parts = []
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item,str):
            parts.append(item)
            continue
        if item.tag in (f'{{{MATHML}}}annotation',f'{{{MATHML}}}annotation-xml'):
            continue
        for child in reversed(item):
            if child.tail:
                stack.append(child.tail)
            if isinstance(child.tag,str):
                stack.append(child)
        if item.text:
            parts.append(item.text)
    return ''.join(parts).strip()


def office_tag(node):
    if not isinstance(node.tag,str):
        return None
    tag = etree.QName(node)
    return tag.localname if tag.namespace in OFFICE_MATH else None


def office_value(node, default):
    return node.get(f'{{{etree.QName(node).namespace}}}val',default)


def office_text(node):
    parts = []
    stack = [node]
    while stack:
        item = stack.pop()
        if not isinstance(item.tag,str):
            continue
        tag = etree.QName(item)
        if tag.namespace in WORD and tag.localname in ('del','moveFrom'):
            continue
        if office_tag(item)=='t':
            parts.append(item.text or '')
        else:
            stack.extend(reversed(item))
    return ''.join(parts)


def office_children(node):
    for child in node:
        if not isinstance(child.tag,str):
            continue
        tag = etree.QName(child)
        if tag.namespace in WORD and tag.localname in ('del','moveFrom'):
            continue
        if tag.namespace in WORD and tag.localname in ('ins','moveTo'):
            yield from office_children(child)
        else:
            yield child


def office_run(node):
    raw = []
    literal = False
    for child in office_children(node):
        kind = office_tag(child)
        if kind=='t':
            raw.append(child.text or '')
        elif kind=='rPr':
            literal = any(office_tag(prop)=='nor' and office_value(prop,'1') not in ('0','false','off')
                          for prop in child)
        elif etree.QName(child).namespace in WORD and etree.QName(child).localname=='rPr':
            if any(etree.QName(prop).localname in ('vanish','webHidden') for prop in child if isinstance(prop.tag,str)):
                raise ValueError('An Office Math run has separate visibility information')
        else:
            raise ValueError('Unsupported Office Math run content: '+etree.QName(child).localname)
    return ''.join(raw),literal


def math_tokens(text):
    for match in TOKEN.finditer(text):
        token = match[0]
        if NUMBER.fullmatch(token):
            yield Expression('mn',text=token)
        elif token.isalpha():
            yield Expression('mi',text=token)
        elif token in OPERATORS:
            yield Expression('mo',text=token)
        else:
            raise ValueError('Unrecognized Office Math token: '+repr(token))


def office_group(elements, depth):
    parts, pending = [], []
    for item in elements:
        if office_tag(item)=='r':
            value,literal = office_run(item)
            if not literal:
                pending.append(value)
                continue
            parts.extend(math_tokens(''.join(pending)))
            pending.clear()
            if value.strip():
                parts.append(Expression('mtext',text=value.strip()))
        else:
            parts.extend(math_tokens(''.join(pending)))
            pending.clear()
            parts.append(office_math(item,depth+1))
    parts.extend(math_tokens(''.join(pending)))
    if not parts:
        raise ValueError('An Office Math argument is empty')
    if len(parts)==1:
        return parts[0]
    return Expression('mrow',tuple(parts))


def office_math(node, depth=0):
    if depth>16:
        raise ValueError('Math nesting exceeds the supported subset')
    tag = office_tag(node)
    elements = list(office_children(node))
    if tag=='oMathPara':
        equations = [element for element in elements if office_tag(element)!='oMathParaPr']
        if len(equations)!=1 or office_tag(equations[0])!='oMath':
            raise ValueError('Office Math display has no unique bound expression')
        return office_math(equations[0],depth+1)
    if tag in ('oMath','e','num','den','sup','sub','deg'):
        return office_group((element for element in elements if office_tag(element)!='argPr'),depth)
    if tag in SCRIPT_ROLES or tag=='rad':
        fields = {}
        properties = None
        property_tag = tag+'Pr'
        for element in elements:
            role = office_tag(element)
            if not role or role in fields or role==property_tag and properties is not None:
                raise ValueError('Office Math has ambiguous or unbound arguments')
            if role==property_tag:
                properties = element
            else:
                fields[role] = element
        if tag in SCRIPT_ROLES:
            kind,roles = SCRIPT_ROLES[tag]
            if fields.keys()!=set(roles):
                raise ValueError('Office Math is missing a required argument')
            if tag=='f' and properties is not None:
                fraction_types = [office_value(prop,'bar') for prop in properties
                                  if office_tag(prop)=='type']
                if len(fraction_types)>1 or any(value not in ('bar','skw','lin') for value in fraction_types):
                    raise ValueError('Office Math fraction has an unqualified presentation type')
            return Expression(kind,tuple(office_math(fields[role],depth+1) for role in roles))
        if fields.keys() not in ({'e'},{'e','deg'}):
            raise ValueError('Office Math radical has missing or unbound arguments')
        hide = False
        if properties is not None:
            settings = [office_value(prop,'1') for prop in properties if office_tag(prop)=='degHide']
            if len(settings)>1 or any(setting not in ('1','0','on','off','true','false') for setting in settings):
                raise ValueError('Office Math radical has an invalid degree setting')
            hide = bool(settings and settings[0] in ('1','on','true'))
        value = office_math(fields['e'],depth+1)
        degree = fields.get('deg')
        if hide or degree is None or not office_text(degree).strip():
            return Expression('msqrt',(value,))
        return Expression('mroot',(value,office_math(degree,depth+1)))
    name = local_name(node)
    raise ValueError('Unsupported Office Math structure: '+name)
