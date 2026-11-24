"""Project authored XPS page stories into shared paragraphs and table cells."""

from .document_intake import name
from .table_grid import Grid, count, passage
from .xps_page import assemble, locator


class Story:
    def __init__(self, member, owners, evidence, namespace):
        self.member = member
        self.owners = owners
        self.evidence = evidence
        self.namespace = namespace
        self.used = set()

    def at(self, node):
        return {'page':self.evidence['page'],'story_member':self.member.name,
                'story_element':node.getroottree().getpath(node)}

    def children(self, node):
        return (child for child in node if isinstance(child.tag,str) and child.tag.startswith(self.namespace))

    def bind(self, refs):
        observed = []
        problem = None
        for ref in refs:
            runs = self.owners.get(ref)
            if runs is None:
                problem = f'The story references an absent or ambiguous XPS element: {ref or "(unnamed)"}.'
                continue
            fresh = [run for run in runs if run.key not in self.used]
            if len(fresh)!=len(runs):
                problem = f'The XPS story assigns element {ref} to conflicting positions.'
            self.used.update(run.key for run in fresh)
            observed.extend(fresh)
        return observed,problem

    def text(self, node, refs):
        observed,problem = self.bind(refs)
        sections = list(assemble(observed))
        text = ' '.join(part for part,_,_ in sections if part)
        problem = problem or next((issue for _,_,issue in sections if issue),None)
        return {'text':text,'location':{**self.at(node),**(locator(observed,self.evidence) if observed else {})},
                'issue':problem}

    def table(self, node):
        grid = Grid()
        for group in self.children(node):
            for row in self.children(group):
                if name(group)!='TableRowGroupStructure' or name(row)!='TableRowStructure':
                    grid.issue('The XPS story has a table child without an authored row.')
                    continue
                grid.begin_row()
                for cell in self.children(row):
                    if name(cell)!='TableCellStructure':
                        grid.issue('The XPS story has a table row child without an authored cell.')
                        continue
                    parts = []
                    for part in self.elements(cell):
                        if 'grid' in part:
                            parts.append({'table':part['grid']})
                        else:
                            parts.append({**passage(part['text']),'location':part['location']})
                            if part.get('issue'):
                                grid.issue(part['issue'])
                    grid.place(parts,self.at(cell),implicit=True,
                               columns=count(cell.get('ColumnSpan')),rows=count(cell.get('RowSpan'),maximum=10_000))
                grid.end_row()
        return grid.finish()

    def elements(self, node):
        for child in self.children(node):
            role = name(child)
            if role in ('ParagraphStructure','FigureStructure'):
                refs = [ref.get('NameReference','') for ref in self.children(child) if name(ref)=='NamedElement']
                yield self.text(child,refs)
            elif role=='NamedElement':
                yield self.text(child,[child.get('NameReference','')])
            elif role=='TableStructure':
                yield {'grid':self.table(child),'location':self.at(child)}
            elif role in ('SectionStructure','ListStructure','ListItemStructure'):
                if role=='ListItemStructure' and child.get('Marker'):
                    marker = self.text(child,[child.get('Marker')])
                    if marker.get('issue') or marker['text'].strip('•·‣◦ '):
                        yield marker
                yield from self.elements(child)
            elif role!='StoryBreak':
                yield {'text':'','location':self.at(child),'issue':f'The XPS story element {role} has no verified reading.'}


def story_reading(package, page, owners, builder, evidence):
    part = package.structure_part(page,'storyfragments')
    if part is None:
        return set()
    root = package.parse(part,'StoryFragments',package.structure)
    story = Story(part,owners,evidence,package.structure)
    rows = [(fragment.get('FragmentType'),list(story.elements(fragment)))
            for fragment in root.findall(package.structure+'StoryFragment')]
    for kind,paragraphs in rows:
        for row in paragraphs:
            if 'grid' in row:
                block = builder.grid(row['grid'],part.name,loc=row['location'])
                if block is not None and kind in ('Header','Footer'):
                    block.role = 'furniture'
            else:
                role = 'furniture' if kind in ('Header','Footer') else 'body'
                builder.add(row['text'],role,part.name,loc=row['location'],issue=row['issue'])
    evidence['story_part'] = part.name
    return story.used
