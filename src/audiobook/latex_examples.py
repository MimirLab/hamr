"""Project the text-only output of a recognized local example package."""

from dataclasses import dataclass, replace
import re

from pylatexenc.latexwalker import (
    LatexCharsNode, LatexCommentNode, LatexGroupNode, LatexMacroNode, LatexSpecialsNode,
    LatexWalker, LatexWalkerParseError, get_default_latex_context_db,
)
from pylatexenc.macrospec import MacroSpec, MacroStandardArgsParser


# The publisher's package at oetiker/lshort 357b1a686cf242f03b80a4720c8fcde03876adf3.
# Its line routing and inline output were checked against the printed edition.
LSHORT_EXAMPLE = 'fcc06ea79b3cd3f941e1a96a0966e2ce067c8299bcac9c6cf42a16adeff6b76c'
UNSUPPORTED_TEXT = re.compile(r'[&_^#]|\r?\n[ \t]*\r?\n')
INLINE_STYLE = {'emph':'em','textit':'i','textsl':'i','textup':'span','textbf':'strong','strong':'strong'}
SHAPE = {'itshape':True,'slshape':True,'upshape':False,'normalfont':False}
LITERALS = frozenset(('%','#','&','_','$','{','}',' ','TeX','LaTeX','ldots','dots','textemdash','textendash'))
COMMANDS = INLINE_STYLE.keys() | SHAPE.keys() | LITERALS | {'RenewCommandCopy','enquote'}


def channels(raw):
    """Route lines to displayed code and generated output independently."""
    printed, output = [], []
    lines = raw.splitlines(keepends=True)
    for marker, destination in (('!hide', printed), ('!show', output)):
        suppressed = False
        for line in lines:
            if marker+'begin' in line:
                suppressed = True
            if marker+'end' in line:
                suppressed = False
            if not suppressed and marker not in line:
                destination.append(line)
    return ''.join(printed), ''.join(output)


def parser_context():
    context = get_default_latex_context_db()
    specs = [MacroSpec(name, MacroStandardArgsParser('*{' if name=='enquote' else '{'))
             for name in (*INLINE_STYLE, 'enquote')]
    specs.append(MacroSpec('RenewCommandCopy', MacroStandardArgsParser('{{')))
    context.add_context_category('hamr-example', macros=specs, prepend=True)
    return context


@dataclass(frozen=True)
class Style:
    italic: bool = False
    bold_emphasis: bool = False
    quotation_depth: int = 0


def render(nodes, style, inline, depth=0):
    if depth>=12:
        raise ValueError('Example nesting is too deep.')
    output = []
    for node in nodes:
        if isinstance(node, LatexCommentNode):
            continue
        if isinstance(node, LatexGroupNode):
            output.append(render(node.nodelist, style, inline, depth+1))
            continue
        if isinstance(node, LatexMacroNode):
            name = node.macroname
            if name in SHAPE:
                style = replace(style, italic=SHAPE[name])
                continue
            args = tuple(a for a in (node.nodeargd.argnlist if node.nodeargd else ()) if a is not None)
            if name=='RenewCommandCopy' and len(args)==2 and all(
                    isinstance(arg,LatexGroupNode) and len(arg.nodelist)==1 and
                    isinstance(arg.nodelist[0],LatexMacroNode) for arg in args):
                if tuple(arg.nodelist[0].macroname for arg in args)==('emph','strong'):
                    style = replace(style, bold_emphasis=True)
                    continue
            if name=='enquote' and args and isinstance(args[-1],LatexGroupNode):
                level = style.quotation_depth if len(args)==1 else style.quotation_depth or 1
                left, right = ('‘','’') if level%2 else ('“','”')
                body = render(args[-1].nodelist, replace(style, quotation_depth=level+1), inline, depth+1)
                output.append(left+body+right)
                continue
            if name in INLINE_STYLE and len(args)==1 and isinstance(args[0],LatexGroupNode):
                tag = INLINE_STYLE.get(name)
                italic = style.italic
                if name=='emph' and style.bold_emphasis:
                    tag = 'strong'
                elif name=='emph':
                    italic = not italic
                elif name in ('textit','textsl','textup'):
                    italic = name!='textup'
                body = render(args[0].nodelist, replace(style, italic=italic), inline, depth+1)
                # Keep logical emphasis when the surrounding font is already italic.
                reset = ' style="font-style:normal"' if style.italic and not italic else ''
                output.append(f'<{tag}{reset}>{body}</{tag}>')
                continue
            if name not in LITERALS:
                raise ValueError('Unsupported command in example output.')
        elif isinstance(node, LatexCharsNode):
            if UNSUPPORTED_TEXT.search(node.chars):
                raise ValueError('Example output needs its paragraph or alignment verified.')
        elif not isinstance(node, LatexSpecialsNode) or node.specials_chars not in ('~','---','--',"``","''"):
            raise ValueError('Unsupported example output structure.')
        text = ('&nbsp;' if isinstance(node,LatexSpecialsNode) and node.specials_chars=='~'
                else inline([node]))
        output.append(f'<i>{text}</i>' if style.italic and text.strip() else text)
    return ''.join(output)


def prose_output(text, context, inline):
    text = text.strip()
    if not text or len(text)>4096:
        return None
    try:
        nodes, _, _ = LatexWalker(text,latex_context=context,tolerant_parsing=False).get_latex_nodes()
        return render(nodes, Style(), inline)
    except (LatexWalkerParseError, RecursionError, ValueError):
        return None
