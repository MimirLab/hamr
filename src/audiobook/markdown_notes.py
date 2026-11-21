"""Keep Markdown note definitions and reference occurrences from one AST."""


def inline(token, inline_sources):
    """Return visible inline text and exact note and literal spans."""
    pieces, spans = [], []
    position = 0
    for child in token.children or ():
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
        elif child.type == 'image':
            value = 'Figure. ' + child.content
        elif child.type in ('softbreak', 'hardbreak'):
            value = '\n' if child.type == 'hardbreak' else ' '
        else:
            continue
        pieces.append(value)
        position += len(value)
    return ''.join(pieces), spans


def target(token):
    if 'label' in token.meta:
        return 'markdown-note:' + token.meta['label']
    return 'markdown-inline-note:' + str(token.meta['id'])


def definition(tokens, inline_sources):
    """Describe supported note children in document order, retaining uncertainty."""
    parts = []
    issues = []
    for token in tokens:
        if token.type == 'inline':
            text, spans = inline(token, inline_sources)
            if text.strip():
                parts.append(text)
            if any(span['kind'] == 'note_reference' for span in spans):
                issues.append('Nested Markdown note references need a verified reading.')
            if any(c.type == 'image' and not c.content.strip() for c in token.children or ()):
                issues.append('An image in this Markdown note has no description.')
        elif token.type in ('fence', 'code_block'):
            parts.append(token.content)
            issues.append('A literal block in this Markdown note needs a verified reading.')
        elif token.type in ('table_open', 'hr', 'footnote_reference_open'):
            issues.append('This Markdown note contains structure that needs a verified reading.')
    # Paragraphs and list items remain separate. Supply a spoken boundary when
    # the source item has none, while keeping the definition verbatim in exact.
    reading = ' '.join(p.strip() if p.rstrip().endswith(('.', '!', '?')) else p.strip() + '.' for p in parts)
    if not reading:
        issues.append('This Markdown note has no readable text.')
    return '\n\n'.join(parts), reading, ' '.join(dict.fromkeys(issues)) or None


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


def links_for(references, notes):
    links = []
    for reference in references:
        if reference['kind'] != 'note_reference':
            continue
        href = reference['href']
        candidates = notes.get(href, [])
        link = {'kind': 'note', 'href': href, 'source_start': reference['source_start']}
        if not candidates:
            link.update(text='', issue='Unresolved Markdown footnote reference requires verification.')
        else:
            destinations = ['source.html#' + note.id for note, _ in candidates]
            link['destinations'] = destinations
            if len(candidates) == 1:
                note, reading = candidates[0]
                link.update(text=note.source, reading=reading, issue=note.locator.get('note_issue'), destination=destinations[0])
            else:
                link.update(text='', issue='Duplicate Markdown note definitions need a verified choice.')
        links.append(link)
    return links
