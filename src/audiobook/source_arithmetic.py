"""Bind explicit printed arithmetic to the same expression model as authored math."""

import ast
import re


CUE = re.compile(r'\b(?:equation|formula)(?: for [A-Za-z][A-Za-z0-9 \t-]{0,76}?)?'
                 r'(?: is[ \t]+|:[ \t]*)(?P<start>[A-Za-z][ \t]*=[ \t]*)',re.I)
TOKEN = re.compile(r'[ \t]*([0-9]+(?:\.[0-9]+)?|[()+*/^−-]|[A-Za-z](?!\w))')
POWER = re.compile(r'\^[ \t]*([0-9]+)(?![0-9.])')


def printed_expression(text,start):
    end, depth = start, 0
    for _ in range(64):
        token = TOKEN.match(text,end)
        if not token or token[1]==')' and depth==0:
            break
        depth += (token[1]=='(')-(token[1]==')')
        end = token.end()
    else:
        return None
    rhs = text[start:end].strip()
    if (depth or not (0<rhs.count('^')==len(POWER.findall(rhs))) or '//' in rhs or '**' in rhs
            or end<len(text) and text[end] not in ' \t).,;!?'):
        return None
    implicit_product = re.sub(r'(?<=[a-zA-Z0-9)])(?=\()', '*',rhs)
    try:
        ast.parse(implicit_product.replace('^','**').replace('−','-'),mode='eval')
    except (SyntaxError,ValueError):
        return None
    return end


def observe(doc):
    from .pdf_layout import adjacent_native_lines
    from .source_math import MATH_SPANS, speak
    from .tex_math import tex_math

    previous = None
    for block in doc.blocks:
        prefix = ''
        if previous is not None and adjacent_native_lines(previous,block):
            prefix = previous.source[-96:]+' '
        previous = block
        if block.issue or block.role not in ('body','caption') or '^' not in block.source:
            continue
        loc = block.locator
        spans = loc.get('inline_semantics',[])
        window = prefix+block.source
        for cue in CUE.finditer(window):
            start = cue.start('start')-len(prefix)
            if start<0 or (end:=printed_expression(block.source,cue.end()-len(prefix))) is None:
                continue
            if any(span['source_start']<end and start<span['source_end']
                   and span['kind'] in MATH_SPANS | {'hidden','note_reference'} for span in spans):
                continue
            raw = block.source[start:end]
            notation = POWER.sub(r'^{\1}',raw).replace('*','×')
            try:
                reading = speak(tex_math(notation))
            except ValueError:
                continue
            spans.append({'kind':'formula','source_start':start,'source_end':end,'source':raw,'reading':reading,
                          'origin':'Explicit equation introduced in the source prose'})
            loc['inline_semantics'] = spans
