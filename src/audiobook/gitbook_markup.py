"""Expose GitBook's structural Markdown extensions using ordinary source blocks."""

import re

from mdit_py_plugins.front_matter import front_matter_plugin

TAG = re.compile(r'{%\s*([\w-]+)\b((?:(?!%}).)*?)%}')
ATTRIBUTE = re.compile(r'''([\w-]+)\s*=\s*("([^"]*)"|'([^']*)')''')
WRAPPERS = frozenset(('hint','code','content-ref','tabs','tab','stepper','step','prompt','file','embed'))


def markup(md):
    md.options['html'] = True
    md.use(front_matter_plugin)
    md.block.ruler.before('paragraph','gitbook_directive',directive,
                          {'alt':['paragraph','reference','blockquote','list']})
    md.core.ruler.after('inline','gitbook_inline',inline_markup)


def properties(value):
    return {match[1]:match[3] if match[3] is not None else match[4] for match in ATTRIBUTE.finditer(value)}


def directive(state,start,end,silent):
    if state.is_code_block(start):
        return False
    raw = state.src[state.bMarks[start]+state.tShift[start]:state.eMarks[start]].strip()
    match = TAG.fullmatch(raw)
    if not match:
        return False
    if silent:
        return True
    tag,args = match.groups()
    token = state.push('source_container','',0)
    token.map = [start,start+1]
    base = tag.removeprefix('end')
    fields = properties(args)
    if tag=='tab' and fields.get('title'):
        token.content = fields['title']
        token.meta['role'] = 'heading'
    elif tag in ('file','embed') and (href:=fields.get('src') or fields.get('url')):
        token.content = fields.get('title') or 'Attached source'
        token.meta.update(role='navigation',reference={'kind':'link','href':href,'label':token.content})
    elif base not in WRAPPERS:
        token.meta.update(role='unreadable',issue=f'GitBook component {tag!r} needs its rendered content.')
    state.line = start+1
    return True


def inline_markup(state):
    from bs4 import BeautifulSoup

    for token in state.tokens:
        if not token.children:
            continue
        result = []
        for child in token.children:
            if child.type=='text':
                child.content = TAG.sub(lambda match:'' if match[1].removeprefix('end') in WRAPPERS else match[0],
                                        child.content)
                if match:=TAG.search(child.content):
                    token.meta['source_issue'] = f'GitBook component {match[1]!r} needs its rendered content.'
            elif child.type=='html_inline':
                tag = BeautifulSoup(child.content,'html.parser').find(True)
                if tag and tag.name=='br':
                    child.type = 'hardbreak'
                elif tag and tag.name=='img':
                    child.type = 'image'
                    child.content = tag.get('alt','')
                    child.attrs = {'src':tag.get('src','')}
                else:
                    continue
            result.append(child)
        token.children = result
