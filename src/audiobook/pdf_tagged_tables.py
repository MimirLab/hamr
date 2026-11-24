"""Project complete PDF table tags only when their visible text is accounted for."""
from collections import Counter, defaultdict
from ctypes import c_void_p, cast
from dataclasses import dataclass, replace

from pypdfium2 import raw

from .core import Error
from .pdf_tags import INLINE_TAGS, Mark, Tag
from .table_grid import Grid, count, passage, source_rows, source_text


class UnboundTable(ValueError):
    pass


def outer_tables(tags):
    for tag in tags:
        if isinstance(tag,Tag):
            if tag.role=='Table':
                yield tag
            else:
                yield from outer_tables(tag.children)


def marks(nodes):
    for node in nodes:
        if isinstance(node,Mark):
            yield node
        elif isinstance(node,Tag):
            yield from marks(node.children)


def stitch(left,right):
    """PDFium retains empty child positions for structure on other pages."""
    if left is None:
        return right
    if right is None or left==right:
        return left
    if (not isinstance(left,Tag) or not isinstance(right,Tag) or left.path!=right.path or
            left.role!=right.role or left.attributes!=right.attributes or len(left.children)!=len(right.children)):
        raise UnboundTable('The same PDF structure position has conflicting owners.')
    return replace(left,children=tuple(stitch(a,b) for a,b in zip(left.children,right.children)))


def rect_for(rectangles):
    return [min(box[0] for box in rectangles),min(box[1] for box in rectangles),
            max(box[2] for box in rectangles),max(box[3] for box in rectangles)]


def ranges(indices):
    pieces = []
    for index in indices:
        if pieces and index==pieces[-1][1]:
            pieces[-1][1] += 1
        else:
            pieces.append([index,index+1])
    return pieces


@dataclass
class BoundText:
    part: dict
    indices: set
    leading_space: bool
    trailing_space: bool


def bind_run(textpage,glyphs,mark):
    chunks = []
    last = None
    for char,_,index in glyphs:
        if last is not None and index>last+1 and char.strip() and not chunks[-1].endswith(' '):
            gap = textpage.get_text_range(last+1,index-last-1)
            if gap and gap.isspace():
                chunks.append(' ')
        chunks.append(' ' if char.isspace() else char)
        last = index
    present = {index for char,_,index in glyphs if char.strip()}
    if not present:
        return None
    loc = {'page':mark.page,'marked_content_id':mark.ident,
           'native_ranges':ranges(index for _,_,index in glyphs),
           'rect':rect_for([rect for char,rect,_ in glyphs if char.strip()])}
    return BoundText({**passage(''.join(chunks).strip()),'location':loc},present,
                     glyphs[0][0].isspace(),glyphs[-1][0].isspace())


def observe_glyphs(page,textpage,chars,wanted):
    from .pdf_visual import authored_artifact

    direct = {cast(obj.raw,c_void_p).value for obj in page.get_objects(
        max_depth=1,filter=[raw.FPDF_PAGEOBJ_TEXT])}
    artifacts = {cast(obj.raw,c_void_p).value for obj in page.get_objects(
        max_depth=1,filter=[raw.FPDF_PAGEOBJ_TEXT]) if authored_artifact(obj)}
    nested_ids = {raw.FPDFPageObj_GetMarkedContentID(obj) for obj in page.get_objects(
        filter=[raw.FPDF_PAGEOBJ_TEXT]) if cast(obj.raw,c_void_p).value not in direct}
    grouped = defaultdict(list)
    artifact_indices = set()
    for char,rect,index in chars:
        obj = raw.FPDFText_GetTextObject(textpage,index)
        if not obj:
            continue
        addr = cast(obj,c_void_p).value
        if addr in artifacts and char.strip():
            artifact_indices.add(index)
        ident = raw.FPDFPageObj_GetMarkedContentID(obj)
        # A form has its own content-ID scope. PDFium's public tag API exposes
        # the ID but omits the structure item's stream reference.
        if ident in wanted and addr in direct and addr not in artifacts and ident not in nested_ids:
            grouped[ident].append((char,rect,index))
    return grouped,artifact_indices


def location(node,parts):
    places = []
    for part in parts:
        if 'table' in part:
            places.extend(cell['location'] for cell in part['table']['cells'])
        elif 'source_members' in part:
            places.extend(part['source_members'])
        else:
            places.append(part['location'])
    pages = sorted({p for place in places for p in place.get('pages',[place.get('page')]) if p is not None})
    return {'tag_path':list(node.path),'pages':pages,**({'page':pages[0]} if pages else {})}


def compile_table(table,bound):
    grid = Grid()

    def read_parts(node,allow_nested=True):
        result, pending = [],[]

        def flush():
            if pending:
                parts = [item.part for item in pending]
                locations = [part['location'] for part in parts]
                tokens = [parts[0]['source']]
                for first,second,a,b in zip(pending,pending[1:],locations,locations[1:]):
                    if (first.trailing_space or second.leading_space or a['page']!=b['page'] or
                            a['native_ranges'][-1][1]!=b['native_ranges'][0][0]):
                        tokens.append(' ')
                    tokens.append(second.part['source'])
                result.append({**passage(''.join(tokens)),'location':{**location(node,parts),**locations[0]},
                               **({'source_members':locations.copy()} if len(locations)>1 else {})})
                pending.clear()

        def visit(child):
            if isinstance(child,Mark):
                if child not in bound:
                    raise UnboundTable('A PDF table content ID does not have a unique visible text owner.')
                pending.append(bound[child])
                return
            if child is None:
                raise UnboundTable('A PDF table has an unresolved structure slot.')
            if child.role=='Table' and allow_nested:
                flush()
                result.append({'table':compile_table(child,bound)})
            elif child.role in ('P','L','LI','Lbl','LBody','Div','Sect') or child.role in INLINE_TAGS:
                boundary = child.role not in INLINE_TAGS
                if boundary:
                    flush()
                for member in child.children:
                    visit(member)
                if boundary:
                    flush()
            else:
                raise UnboundTable('A PDF table contains content outside the qualified text and table roles.')

        for member in node.children:
            visit(member)
        flush()
        return result

    def rows(node,heading=False):
        for item in node.children:
            if isinstance(item,Tag) and item.role=='TR':
                yield item,heading
            elif isinstance(item,Tag) and item.role in ('THead','TBody','TFoot'):
                yield from rows(item,item.role=='THead')
            elif isinstance(item,Tag) and item.role=='Caption':
                parts = read_parts(item,allow_nested=False)
                if not parts or grid.caption:
                    raise UnboundTable('A PDF table caption has no single verified reading.')
                grid.caption = {**passage(' '.join(p['source'] for p in parts)),
                                'location':location(item,parts),'after':grid.row>=0}
            else:
                raise UnboundTable('A PDF table row or row group has no verified structure owner.')

    header_band = True
    widths = []
    for row,heading in rows(table):
        header_band = header_band and bool(row.children) and all(
            isinstance(cell,Tag) and cell.role=='TH' for cell in row.children)
        grid.begin_row()
        occupied = set(grid.active)
        for cell in row.children:
            if not isinstance(cell,Tag) or cell.role not in ('TH','TD'):
                raise UnboundTable('A PDF table cell has no verified structure owner.')
            attrs = dict(cell.attributes)
            if any(value is None for value in attrs.values()):
                grid.issue('A PDF table cell has conflicting or invalid authored attributes.')
            scope = attrs.get('Scope')
            role = 'data'
            if cell.role=='TH':
                if scope in ('Column','Row','Both'):
                    role = scope.lower()
                else:
                    role = 'column' if heading or header_band else 'row'
                    if scope is not None or role=='row' and (occupied or grid.column):
                        grid.issue('A PDF table heading lacks a verified row or column association.')
            parts = read_parts(cell)
            grid.place(parts,location(cell,parts),columns=count(attrs.get('ColSpan')),
                       rows=count(attrs.get('RowSpan'),maximum=10000),role=role,identifier=attrs.get('id'),
                       references=attrs.get('Headers') or (),implicit=True)
        grid.end_row()
        widths.append((len(grid.active),max(grid.active,default=-1)+1))
    if widths and any(cells!=grid.width or edge!=grid.width for cells,edge in widths):
        grid.issue('The PDF table tags leave an unresolved cell or column position.')
    return grid.finish()


class TaggedTables:
    """Collect page observations, then replace text only after complete ownership checks."""
    def __init__(self):
        self.fragments = defaultdict(list)
        self.bound = {}
        self.pages = {}
        self.lines = defaultdict(list)

    def observe(self,page,textpage,tags,tables,chars,entry,*,admitted):
        number = entry['page']
        self.pages[number] = entry
        for table in tables:
            self.fragments[table.path].append((number,table))
        if not admitted:
            return
        ids = Counter(mark.ident for mark in marks(tags))
        wanted = {mark.ident for mark in marks(tables) if ids[mark.ident]==1}
        grouped,artifacts = observe_glyphs(page,textpage,chars,wanted)
        for ident,glyphs in grouped.items():
            mark = Mark(number,ident)
            if observed:=bind_run(textpage,glyphs,mark):
                self.bound[mark] = observed
        entry['native_table_repeat_candidates'] = ranges(sorted(artifacts))
        return artifacts

    def line(self,number,block_id,indices,artifact):
        if indices:
            self.lines[number].append((block_id,indices,artifact))

    def project(self,blocks,make_block):
        replacements = {}
        consumed = set()
        positions = {block.id:i for i,block in enumerate(blocks)} if self.fragments else {}
        for path,fragments in self.fragments.items():
            pages = [number for number,_ in fragments]
            evidence = {'tag_path':list(path),'pages':pages}
            try:
                table = None
                for _,fragment in fragments:
                    table = stitch(table,fragment)
                grid = compile_table(table,self.bound)
                claimed = self.claims(table)
                locations,repeated = self.positions(blocks,positions,pages,grid,claimed)
                if consumed.intersection(locations):
                    raise UnboundTable('Two PDF tables claim the same visible source lines.')
                ranges_by_page = {str(number):ranges(sorted(indices)) for number,indices in claimed.items()}
                first = locations[0]
                rects = [blocks[index].locator['rect'] for index in locations
                         if blocks[index].locator['page']==pages[0]]
                loc = {'format':'pdf','page':pages[0],'rect':rect_for(rects),
                       'tagged_table':{**evidence,'native_ranges':ranges_by_page},
                       'table_grid':grid,'rows':source_rows(grid)}
                replacements[first] = make_block(source_text(grid),loc,'table',blocks[first].chapter,
                    ['PDF authored table tags','Visible glyph ownership verified'],grid['issue'])
                consumed.update(locations)
                for index in repeated:
                    blocks[index].role = 'furniture'
                    blocks[index].locator['authored_table_header_repeat'] = list(path)
                    blocks[index].evidence.append('PDF artifact repeats an authored heading on a continuation page.')
                evidence['status'] = 'needs_review' if grid['issue'] else 'projected'
                if grid['issue']:
                    evidence['reason'] = grid['issue']
                evidence['native_ranges'] = ranges_by_page
            except (UnboundTable,Error) as error:
                evidence.update(status='geometry_fallback',reason=str(error))
            for number in pages:
                self.pages[number].setdefault('tagged_table_observations',[]).append(evidence)
        return [replacements[i] if i in replacements else b for i,b in enumerate(blocks)
                if i not in consumed or i in replacements]

    def claims(self,table):
        claimed = defaultdict(set)
        for mark in marks((table,)):
            claimed[mark.page].update(self.bound[mark].indices)
        return claimed

    def positions(self,blocks,indices_by_block,pages,grid,claimed):
        if not claimed or pages!=sorted(claimed) or pages!=list(range(pages[0],pages[-1]+1)):
            raise UnboundTable('PDF table ownership does not cover a continuous sequence of source pages.')
        located = []
        for page,required in claimed.items():
            seen = set()
            for block_id,indices,_ in self.lines[page]:
                pos = indices_by_block.get(block_id)
                if pos is None:
                    continue
                if not indices.intersection(required):
                    continue
                if not indices<=required or indices.intersection(seen) or blocks[pos].issue:
                    raise UnboundTable('A PDF table shares a native line with unrelated or disputed glyphs.')
                located.append(pos)
                seen.update(indices)
            if seen!=required:
                raise UnboundTable('A PDF table glyph has no verified native reading line.')
        located.sort()
        headings = {' '.join(' '.join(part['source'] for part in cell['parts'] if 'source' in part).split())
                    for cell in grid['cells'] if cell['role'] in ('column','both')}
        repeats = {pos for page in pages[1:] for block_id,_,artifact in self.lines[page]
                   if artifact and (pos:=indices_by_block.get(block_id)) is not None and pos<located[-1]
                   and ' '.join(blocks[pos].source.split()) in headings}
        if set(range(located[0],located[-1]+1))-set(located)-repeats:
            raise UnboundTable('Non-table text intervenes between visible PDF table ranges.')
        return located,repeats
