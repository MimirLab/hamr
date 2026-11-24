"""Keep Markdown note definitions and reference occurrences from one AST."""

import re

from .source_scripts import prepare as prepare_scripts


SUPERSCRIPT = re.compile(r'<sup>([+−-]?\d+|st|nd|rd|th)</sup>',re.I)


def styled_script(state,silent):
    """Recognize a static script span without enabling arbitrary inline HTML."""
    match = SUPERSCRIPT.match(state.src,state.pos,state.posMax)
    if not match:
        return False
    if not silent:
        state.push('styled_script','',0).content = match[1]
    state.pos = match.end()
    return True


def html_inline(child):
    from bs4 import BeautifulSoup

    tag = BeautifulSoup(child.content,'html.parser').find(True)
    if tag and tag.name=='br':
        child.type = 'hardbreak'
        return child
    if tag and tag.name=='img':
        child.type = 'image'
        child.content = tag.get('alt','')
        child.attrs = {'src':tag.get('src','')}
        return child
    return None


def visible_inline_html(state):
    hidden_tags = {'script','style','template','noscript'}
    for token in state.tokens:
        if not token.children:
            continue
        result = []
        hidden = None
        for child in token.children:
            if child.type=='html_inline':
                markup = child.content.lower().strip()
                if hidden and markup.startswith('</'+hidden):
                    hidden = None
                    continue
                if hidden:
                    continue
                opened = markup.removeprefix('<').split(maxsplit=1)[0].rstrip('>/')
                if opened in hidden_tags:
                    hidden = opened
                    continue
                child = html_inline(child)
            if child is not None and not hidden:
                result.append(child)
        token.children = result


def inline(token, inline_sources):
    """Return visible inline text and exact note and literal spans."""
    pieces, spans, marks = [], [], []
    position = 0
    for child in token.children or ():
        if child.type in ('em_open','strong_open','link_open'):
            kind='hyperlink' if child.type=='link_open' else 'emphasis'
            marks.append((position,len(pieces),kind,child.attrGet('href') if kind=='hyperlink' else None))
            continue
        if child.type in ('em_close','strong_close','link_close'):
            start,first_piece,kind,href=marks.pop()
            record={'kind':kind,'source_start':start,'source_end':position,'source':''.join(pieces[first_piece:])}
            if href is not None:record['href']=href
            spans.append(record)
            continue
        if child.type == 'footnote_ref':
            label = child.meta.get('label')
            value = f'[^{label}]' if label is not None else '^[' + inline_sources.get(child.meta['id'], '') + ']'
            spans.append({'kind': 'note_reference', 'source': value,
                          'source_start': position, 'source_end': position + len(value),
                          'href': target(child)})
        elif child.type in ('text', 'code_inline', 'html_inline'):
            value = child.content
            if child.type == 'code_inline':
                spans.append({'kind': 'literal', 'source': value,
                              'source_start': position, 'source_end': position + len(value)})
        elif child.type=='styled_script':
            value = child.content
            spans.append({'kind':'superscript','source':value,'source_start':position,
                          'source_end':position+len(value),'origin':'Markdown inline sup element'})
        elif child.type == 'math_inline':
            from .document_intake import formula_span
            from .tex_math import tex_math

            value = child.markup+child.content+child.markup
            spans.append(formula_span(value,lambda:tex_math(child.content),position,'Authored Markdown math',{}))
        elif child.type == 'image':
            value = 'Figure. ' + child.content
        elif child.type in ('softbreak', 'hardbreak'):
            value = '\n' if child.type == 'hardbreak' else ' '
            spans.append({'kind':'line_break' if child.type=='hardbreak' else 'soft_wrap',
                          'source_start':position,'source_end':position+1,'source':value,'authored_surface':'\n'})
        else:
            continue
        pieces.append(value)
        position += len(value)
    source = ''.join(pieces)
    return source,prepare_scripts(source,spans)


def target(token):
    if 'label' in token.meta:
        return 'markdown-note:' + token.meta['label']
    return 'markdown-inline-note:' + str(token.meta['id'])


def definition(tokens, inline_sources):
    """Keep note paragraphs and their dependencies in authored order."""
    from .core import Block
    from .epub_semantics import reading
    from .source_notes import finish_sentence

    parts, spoken, spans, issues = [], [], [], []
    position = 0
    for token in tokens:
        if token.type == 'inline':
            text, local = inline(token, inline_sources)
            if text.strip():
                from .epub_semantics import uncertainty

                if issue := uncertainty(local):
                    issues.append(issue)
                parts.append(text)
                value = reading(Block('',text,{'inline_semantics':local},'note','')).strip()
                if value:
                    spoken.append(finish_sentence(value))
                spans.extend({**span,'source_start':span['source_start']+position,
                              'source_end':span['source_end']+position} for span in local)
                position += len(text)+2
            if any(c.type == 'image' and not c.content.strip() for c in token.children or ()):
                issues.append('An image in this Markdown note has no description.')
        elif token.type in ('fence', 'code_block'):
            parts.append(token.content)
            position += len(token.content)+2
            issues.append('A literal block in this Markdown note needs a verified reading.')
        elif token.type in ('table_open', 'hr', 'footnote_reference_open'):
            issues.append('This Markdown note contains structure that needs a verified reading.')
    if not (spoken or any(span['kind']=='note_reference' for span in spans)):
        issues.append('This Markdown note has no readable text.')
    return '\n\n'.join(parts), ' '.join(spoken), spans, ' '.join(dict.fromkeys(issues)) or None


def split_definitions(tokens):
    body, notes = [], []
    start = None
    depth = 0
    for i, token in enumerate(tokens):
        if token.type == 'footnote_reference_open':
            if depth == 0:
                start = i
            depth += 1
        if depth:
            if token.type == 'footnote_reference_close':
                depth -= 1
                if depth == 0:
                    notes.append((tokens[start], tokens[start+1:i]))
        else:
            body.append(token)
    return body, notes
