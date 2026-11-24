"""Project a Word package into Hamr's common reading blocks."""
from collections import defaultdict
from dataclasses import dataclass, field

from .document_intake import (attr, cell_semantics, formula_span, multiple_cells, name,
                              plain_xml_text, structured_zip, xml)
from .epub_semantics import uncertainty
from .source_math import office_math, office_tag, office_text
from .source_notes import Definition, NoteGraph, note_text
from .word_stories import ALTERNATE, MISSING_BRANCH, TEXTBOX, Stories, branch, enabled, excluded_kind, properties, visible


@dataclass
class Paragraph:
    text: str = ''
    spans: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    issue: str | None = None
    stories: list = field(default_factory=list)


def join_paragraphs(paragraphs, *, note=False):
    pieces, spans = [], []
    offset = 0
    issue = None
    for paragraph in paragraphs:
        if paragraph.text and pieces:
            offset += 1
            if note:
                spans.append({'kind':'note_boundary','source_start':offset,'source_end':offset,'source':''})
        spans.extend({**span,'source_start':span['source_start']+offset,'source_end':span['source_end']+offset}
                     for span in paragraph.spans)
        if paragraph.text:
            pieces.append(paragraph.text)
            offset += len(paragraph.text)
        issue = issue or paragraph.issue
    return Paragraph(' '.join(pieces),spans,issue=issue)


def table_members(node,tag):
    for child in node:
        if excluded_kind(child):
            continue
        if name(child)==tag:
            yield child
        elif name(child) in ('sdt','sdtContent','customXml','ins','moveTo'):
            yield from table_members(child,tag)
        elif child.tag==ALTERNATE and (selected:=branch(child)) is not None:
            yield from table_members(selected,tag)


def read_word(path,builder):
    with structured_zip(path) as (read,members):
        document = xml(read('word/document.xml'))
        drawing_parts = {'document':Stories(document)}
        meta = read('docProps/core.xml',required=False)
        if meta:
            title = next((plain_xml_text(item) for item in xml(meta) if name(item)=='title'),None)
            if title:
                builder.doc.title = title
        pending_notes = defaultdict(list)
        for part in ('footnotes','endnotes'):
            data = read('word/'+part+'.xml',required=False)
            if data:
                root = xml(data)
                drawing_parts[part] = Stories(root)
                for note in root:
                    ident = attr(note,'id')
                    if ident is not None and attr(note,'type') in (None,'normal'):
                        pending_notes['#'+part[:-1]+':'+ident].append((part,note))

        def paragraph(node, *, note_part=None):
            pieces,spans,excluded,problems = [],[],[],[]
            textboxes = []
            drawings = drawing_parts[note_part or 'document']
            offset = 0

            def add(text):
                nonlocal offset
                pieces.append(text)
                offset += len(text)

            def walk(item):
                tag = name(item)
                if hidden:=excluded_kind(item):
                    excluded.append({'kind':hidden,'text':plain_xml_text(item)})
                    return
                if item.tag==ALTERNATE:
                    selected = branch(item)
                    if selected is None:
                        problems.append(MISSING_BRANCH)
                    else:
                        for child in selected:
                            walk(child)
                    return
                if tag=='r':
                    props = properties(item,'rPr')
                    start = offset
                    for child in item:
                        if name(child)!='rPr':
                            walk(child)
                    if any(name(n) in ('i','b') and enabled(attr(n,'val')) for n in props):
                        spans.append({'kind':'emphasis','source_start':start,'source_end':offset})
                    if align:=next((attr(n,'val') for n in props if name(n)=='vertAlign'),None):
                        if align in ('superscript','subscript') and start<offset:
                            spans.append({'kind':align,'source_start':start,'source_end':offset})
                    return
                if office_tag(item) in ('oMath','oMathPara'):
                    raw = office_text(item)
                    member = 'word/'+(note_part or 'document')+'.xml'
                    spans.append(formula_span(raw,lambda:office_math(item),offset,'Office Math',
                                              member+':'+item.getroottree().getpath(item)))
                    add(raw)
                    return
                if tag in ('drawing','pict') or item.tag in TEXTBOX:
                    stories,editable,drawing_problems = drawings.drawing(item)
                    if editable or MISSING_BRANCH in drawing_problems:
                        textboxes.extend(stories)
                        problems.extend(drawing_problems)
                        return
                if tag in ('drawing','pict','object','txbxContent','oMath','oMathPara','altChunk'):
                    label = next((value for n in visible(item,stop_at_textbox=True) if (value:=attr(n,'descr'))),None)
                    if label:
                        spans.append({'kind':'object_label','source_start':offset,'source_end':offset,
                                      'reading':label})
                    else:
                        problems.append(f'Word {tag} needs a verified reading.')
                    return
                if tag in ('footnoteReference','endnoteReference'):
                    kind = tag.removesuffix('Reference')
                    ident = kind+':'+str(attr(item,'id'))
                    href = '#'+ident
                    spans.append({'kind':'note_reference','source_start':offset,'source_end':offset,'href':href})
                    return
                if tag=='t':
                    add(item.text or '')
                    return
                if tag in ('tab','br','cr'):
                    start = offset
                    add('\t' if tag=='tab' else '\n')
                    if tag!='tab':
                        spans.append({'kind':'line_break','source_start':start,'source_end':offset})
                    return
                if tag in ('pPr','instrText','delText','fldChar','bookmarkStart','bookmarkEnd'):
                    return
                for child in item:
                    walk(child)
            walk(node)
            untrimmed = ''.join(pieces)
            left = len(untrimmed)-len(untrimmed.lstrip())
            text = untrimmed.strip()
            for span in spans:
                span['source_start'] = min(len(text),max(0,span['source_start']-left))
                span['source_end'] = min(len(text),max(0,span['source_end']-left))
                span['source'] = text[span['source_start']:span['source_end']]
            issue = ' '.join(dict.fromkeys(problems)) or uncertainty(spans)
            return Paragraph(text,spans,excluded,issue,textboxes)

        def embedded_paragraphs(node,*,note_part=None):
            for child in node:
                if excluded_kind(child):
                    continue
                if child.tag==ALTERNATE:
                    if (selected:=branch(child)) is not None:
                        yield from embedded_paragraphs(selected,note_part=note_part)
                    else:
                        yield Paragraph(issue=MISSING_BRANCH)
                    continue
                if name(child)=='p' or office_tag(child) in ('oMath','oMathPara'):
                    result = paragraph(child,note_part=note_part)
                    yield result
                    for story in result.stories:
                        yield from embedded_paragraphs(story,note_part=note_part)
                elif name(child) in ('sdt','sdtContent','customXml','ins','moveTo'):
                    yield from embedded_paragraphs(child,note_part=note_part)
                elif name(child)=='tbl':
                    yield Paragraph(issue='A nested Word table needs a verified reading.')
                elif name(child) in ('altChunk','object'):
                    yield Paragraph(issue='The Word story refers to embedded content that must be reviewed.')

        def visit(node,textbox=None):
            tag = name(node)
            xml_path = node.getroottree().getpath(node)
            loc = 'word/document.xml:'+xml_path
            provenance = {}
            if textbox is not None:
                anchor = next((parent for parent in textbox.iterancestors() if name(parent)=='p'), textbox)
                tree = node.getroottree()
                provenance = {'source_file':'word/document.xml','xml_path':xml_path,
                              'text_box':tree.getpath(textbox),'text_box_anchor':tree.getpath(anchor)}
            if node.tag==ALTERNATE:
                if (selected:=branch(node)) is not None:
                    visit(selected,textbox)
                else:
                    builder.issue(MISSING_BRANCH,loc)
                return
            if tag=='p' or office_tag(node) in ('oMath','oMathPara'):
                content = paragraph(node)
                props = properties(node,'pPr')
                style = next((attr(n,'val') for n in props if name(n)=='pStyle'),None) or ''
                role = 'heading' if style.lower().startswith(('heading','title')) or any(
                    name(n)=='outlineLvl' for n in props) else 'body'
                if content.excluded:
                    builder.doc.inventory.append({'location':loc,'selected_edition':'visible final',
                                                  'excluded_runs':content.excluded})
                spans = [span for span in content.spans if span['kind']!='line_break']
                item = builder.add(content.text,role,loc,loc={**provenance,'inline_semantics':spans},
                                   issue=content.issue)
                if item and any(span['kind']=='line_break' and span['source']=='\n' for span in content.spans):
                    item.locator['authored_line_breaks'] = True
                for story in content.stories:
                    visit(story,story)
            elif tag=='tbl':
                rows = []
                heads = []
                issue = None
                for child in visible(node):
                    child_tag = name(child)
                    if child.tag==ALTERNATE and branch(child) is None:
                        issue = MISSING_BRANCH
                        break
                    if child is not node and (child_tag in ('tbl','vMerge','hMerge') or
                            child_tag=='gridSpan' and multiple_cells(attr(child,'val') or '0')):
                        issue = 'Merged cells or nested Word tables need a verified table reading.'
                        break
                semantics = []
                for row in table_members(node,'tr'):
                    if any(name(n)=='tblHeader' and enabled(attr(n,'val')) for n in properties(row,'trPr')):
                        heads.append(len(rows))
                    cells = []
                    for cell in table_members(row,'tc'):
                        content = join_paragraphs(embedded_paragraphs(cell))
                        issue = issue or content.issue
                        if content.spans:
                            semantics.append(cell_semantics(len(rows),len(cells),content.text,content.spans))
                        cells.append(content.text)
                    rows.append(cells)
                item = builder.table(rows,loc,headers=heads if rows else None,issue=issue,semantics=semantics)
                if item:
                    item.locator.update(provenance)
            elif tag in ('altChunk','object'):
                builder.issue('The Word body refers to embedded content that must be reviewed.',loc)
            elif tag not in ('del','moveFrom','sectPr','sdtPr','sdtEndPr'):
                for child in node:
                    visit(child,textbox)
        visit(document)
        definitions = defaultdict(list)
        for href,notes in pending_notes.items():
            for part,node in notes:
                content = join_paragraphs(embedded_paragraphs(node,note_part=part),note=True)
                xml_path = node.getroottree().getpath(node)
                member = 'word/'+part+'.xml'
                location = {'source_file':member,'xml_path':xml_path,'note_kind':part[:-1],
                            'inline_semantics':content.spans}
                if item:=builder.add(content.text,'note',member+':'+xml_path,loc=location):
                    definitions[href].append(Definition(item,note_text(item,terminal=True),content.issue))
        graph = NoteGraph(definitions.get)
        for item in builder.doc.blocks:
            item.links.extend(graph.links_for(item.locator.get('inline_semantics',()),follow=item.role!='note'))
            if cells:=item.locator.get('table_cell_semantics'):
                graph.bind_cells(cells)
                item.links.extend(link for cell in cells for link in cell['notes'])
        if any(n.startswith(('word/header','word/footer')) for n in members):
            builder.doc.warnings.append('Page header and footer parts remain in the portable Word document.')
