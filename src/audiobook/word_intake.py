"""Project a Word package into Hamr's common reading blocks."""
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache

from .document_intake import (attr, formula_span, name,
                              plain_xml_text, structured_zip, xml)
from .epub_semantics import uncertainty
from .source_scripts import prepare as prepare_scripts
from .source_math import office_math, office_tag, office_text
from .source_notes import Definition, NoteGraph, note_text
from .source_numbering import embed_ordered_label
from .source_commands import literal_paragraph_location, literal_spans, literal_style
from .table_grid import Grid, count, passage, passages, simple_matrix
from .word_stories import ALTERNATE, MISSING_BRANCH, TEXTBOX, Stories, branch, enabled, excluded_kind, properties, visible
from .word_numbering import Numbering, value as style_value


@dataclass
class Paragraph:
    text: str = ''
    spans: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    issue: str | None = None
    stories: list = field(default_factory=list)
    location: dict | None = None
    numbering: tuple | None = None


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


def setting(node,group,key,default=None):
    return next((attr(child,'val') or default for child in properties(node,group) if name(child)==key),None)


def word_location(node,part='document'):
    return {'source_file':f'word/{part}.xml','xml_path':node.getroottree().getpath(node)}


def read_word(path,builder):
    with structured_zip(path) as (read,members):
        document = xml(read('word/document.xml'))
        numbering = Numbering(read,members)
        drawing_parts = {'document':Stories(document)}

        @cache
        def character_marks(style):
            marks = defaultdict(list)
            for ident,definition in numbering.style_chain(style):
                if attr(definition,'type')!='character':
                    break
                for field in properties(definition,'rPr'):
                    if name(field) in ('i','b'):
                        marks[name(field)].append((ident,definition,enabled(attr(field,'val'))))
            # Repeated toggle settings need the complete visual style context.
            return {key:items[0][:2] for key,items in marks.items() if len(items)==1 and items[0][2]}

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

        def paragraph(node, *, note_part=None, embed=False):
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
                    direct = {name(n):enabled(attr(n,'val')) for n in props if name(n) in ('i','b')}
                    if any(direct.values()):
                        spans.append({'kind':'emphasis','source_start':start,'source_end':offset})
                    elif offset>start:
                        for key,(ident,definition) in character_marks(style_value(props,'rStyle')).items():
                            if key in direct:
                                continue
                            spans.append({'kind':'emphasis','source_start':start,'source_end':offset,
                                          'style_definition':{'id':ident,'source_file':numbering.paths['styles'],
                                                              'xml_path':definition.getroottree().getpath(definition)}})
                            break
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
            spans = prepare_scripts(text,spans)
            issue = ' '.join(dict.fromkeys(problems)) or uncertainty(spans)
            order = numbering.next(node)
            if order:
                value,marker = order
                issue = issue or marker.get('issue')
                if embed and not issue:
                    embed_ordered_label(text,spans,order)
            return Paragraph(text,spans,excluded,issue,textboxes,word_location(node,note_part or 'document'),order)

        def embedded_content(node,*,note_part=None):
            for child in node:
                if excluded_kind(child):
                    continue
                if child.tag==ALTERNATE:
                    if (selected:=branch(child)) is not None:
                        yield from embedded_content(selected,note_part=note_part)
                    else:
                        yield Paragraph(issue=MISSING_BRANCH)
                    continue
                if name(child)=='p' or office_tag(child) in ('oMath','oMathPara'):
                    result = paragraph(child,note_part=note_part,embed=True)
                    yield result
                    for story in result.stories:
                        yield from embedded_content(story,note_part=note_part)
                elif name(child) in ('sdt','sdtContent','customXml','ins','moveTo'):
                    yield from embedded_content(child,note_part=note_part)
                elif name(child)=='tbl':
                    yield child
                elif name(child) in ('altChunk','object'):
                    yield Paragraph(issue='The Word story refers to embedded content that must be reviewed.')

        def table(node):
            grid = Grid()
            if any(child.tag==ALTERNATE and branch(child) is None for child in visible(node)):
                grid.issue(MISSING_BRANCH)
            vertical = {}
            for row in table_members(node,'tr'):
                grid.begin_row(count(setting(row,'trPr','gridBefore'),minimum=0,default=0))
                head = setting(row,'trPr','tblHeader','true')
                role = 'column' if head is not None and enabled(head) else 'data'
                next_vertical = {}
                left = None
                for cell in table_members(row,'tc'):
                    columns = count(setting(cell,'tcPr','gridSpan','0'))
                    v_merge = setting(cell,'tcPr','vMerge','continue')
                    h_merge = setting(cell,'tcPr','hMerge','continue')
                    loc = word_location(cell)
                    pieces, parts = [],[]

                    def flush():
                        content = join_paragraphs(pieces)
                        if content.issue:
                            grid.issue(content.issue)
                        if content.text or content.spans:
                            part = passage(content.text,content.spans)
                            locations = [p.location for p in pieces if p.location and (p.text or p.spans)]
                            part['location'] = locations[0] if len(locations)==1 else loc
                            if len(locations)>1:
                                part['source_members'] = locations
                            parts.append(part)
                        pieces.clear()

                    for content in embedded_content(cell):
                        if isinstance(content,Paragraph):
                            pieces.append(content)
                        else:
                            flush()
                            parts.append({'table':table(content)})
                    flush()
                    start = grid.column
                    if h_merge=='continue' and left is not None and not parts and v_merge is None:
                        left['columns'] += columns
                        left.setdefault('continuations',[]).append(loc)
                        grid.claim(left,start,start+columns)
                        continue
                    if v_merge=='continue' and start in vertical and not parts and h_merge is None:
                        owner = vertical[start]
                        grid.cover(loc,columns=columns,continuation=owner)
                        next_vertical[start] = owner
                        left = None
                        continue
                    if h_merge not in (None,'restart') or v_merge not in (None,'restart'):
                        grid.issue('A merged Word cell has an unresolved continuation. Check the recorded origin.')
                    origin = grid.place(parts,loc,columns=columns,role=role)
                    if v_merge=='restart':
                        next_vertical[start] = origin
                    left = origin if h_merge=='restart' else None
                vertical = next_vertical
                grid.end_row(count(setting(row,'trPr','gridAfter'),minimum=0,default=0))
            return grid.finish()

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
                if role=='body' and not (content.issue or content.numbering or content.stories) and literal_spans(content.spans):
                    for ident,definition in numbering.style_chain(style):
                        if attr(definition,'type')!='paragraph':
                            break
                        if literal_style(style_value(definition,'name')):
                            role = 'code'
                            provenance.update(literal_paragraph_location(
                                node,'word/document.xml',definition,numbering.paths['styles'],ident))
                            break
                if content.excluded:
                    builder.doc.inventory.append({'location':loc,'selected_edition':'visible final',
                                                  'excluded_runs':content.excluded})
                spans = [span for span in content.spans if span['kind']!='line_break']
                identity_spans = [span for span in spans if 'style_definition' not in span]
                item = builder.add(content.text,role,loc,loc={**provenance,'inline_semantics':identity_spans},
                                   issue=content.issue)
                if item:
                    item.locator['inline_semantics'] = spans
                if item and content.numbering:
                    value,marker = content.numbering
                    item.locator.update(ordered_item=value,ordered_marker=marker)
                if item and any(span['kind']=='line_break' and span['source']=='\n' for span in content.spans):
                    item.locator['authored_line_breaks'] = True
                for story in content.stories:
                    visit(story,story)
            elif tag=='tbl':
                grid = table(node)
                if matrix:=simple_matrix(grid):
                    rows,headers,entries = matrix
                    if item:=builder.table(rows,loc,headers=headers,semantics=entries):
                        item.locator.update(provenance)
                else:
                    builder.grid(grid,loc,loc=provenance)
            elif tag in ('altChunk','object'):
                builder.issue('The Word body refers to embedded content that must be reviewed.',loc)
            elif tag not in ('del','moveFrom','sectPr','sdtPr','sdtEndPr'):
                for child in node:
                    visit(child,textbox)
        visit(document)
        definitions = defaultdict(list)
        for href,notes in pending_notes.items():
            for part,node in notes:
                content = join_paragraphs((token if isinstance(token,Paragraph) else
                                           Paragraph(issue='A table in a Word note needs a verified reading.')
                                           for token in embedded_content(node,note_part=part)),note=True)
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
            if grid:=item.locator.get('table_grid'):
                cells = list(passages(grid))
                graph.bind_cells(cells)
                item.links.extend(link for cell in cells for link in cell['notes'])
        if any(n.startswith(('word/header','word/footer')) for n in members):
            builder.doc.warnings.append('Page header and footer parts remain in the portable Word document.')
