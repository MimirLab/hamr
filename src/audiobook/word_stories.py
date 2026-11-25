"""Editable Word stories and the authored compatibility branch that contains them."""

from collections import defaultdict

from .document_intake import attr, name

WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
STRICT_WORD = 'http://purl.oclc.org/ooxml/wordprocessingml/main'
WORD_EXT = 'http://schemas.microsoft.com/office/word/2006/wordml'
WORD_NAMESPACES = (WORD,STRICT_WORD,WORD_EXT)
MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
SHAPE = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'
GROUP = 'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup'
VML = 'urn:schemas-microsoft-com:vml'
DRAWING = frozenset(('http://schemas.openxmlformats.org/drawingml/2006/main',
                     'http://purl.oclc.org/ooxml/drawingml/main'))
GRAPHIC_DATA = {f'{{{ns}}}graphicData' for ns in DRAWING}
SUPPORTED = DRAWING | {WORD,STRICT_WORD,WORD_EXT,SHAPE,GROUP,VML,
                       'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
                       'http://purl.oclc.org/ooxml/drawingml/wordprocessingDrawing'}
TEXTBOX = {f'{{{ns}}}txbxContent' for ns in WORD_NAMESPACES}
VML_BOX = f'{{{VML}}}textbox'
OWNER = f'{{{SHAPE}}}txbx'
CONTINUATION = f'{{{SHAPE}}}linkedTxbx'
ALTERNATE = MC+'AlternateContent'
MISSING_BRANCH = 'This Word content has no readable compatibility branch.'
REMOVED_MARKS = {f'{{{ns}}}{tag}' for ns in WORD_NAMESPACES for tag in ('del','moveFrom')}
DELETED_ROWS = {f'{{{ns}}}del' for ns in WORD_NAMESPACES}
DELETED_CELLS = {f'{{{ns}}}cellDel' for ns in WORD_NAMESPACES}


def branch(node):
    """Select the first choice for which every required namespace is understood."""
    for child in node:
        if child.tag==MC+'Choice':
            prefixes = child.get('Requires','').split()
            if prefixes and all(child.nsmap.get(prefix) in SUPPORTED for prefix in prefixes):
                return child
    return node.find(MC+'Fallback')


def enabled(value):
    return (value or '').lower() not in ('0','false','off')


def properties(node,tag):
    return next((child for child in node if name(child)==tag),())


def removed_paragraph_mark(node):
    return any(mark.tag in REMOVED_MARKS for prop in properties(node,'pPr')
               if name(prop)=='rPr' for mark in prop)


def excluded_kind(node):
    tag = name(node)
    if tag in ('del','moveFrom'):
        return tag
    if tag=='tr' and any(prop.tag in DELETED_ROWS for prop in properties(node,'trPr')):
        return 'deleted_row'
    if tag=='tc' and any(prop.tag in DELETED_CELLS for prop in properties(node,'tcPr')):
        return 'deleted_cell'
    if tag=='r' and any(name(prop) in ('vanish','webHidden') and enabled(attr(prop,'val'))
                        for prop in properties(node,'rPr')):
        return 'hidden'
    return None


def visible(node, *, stop_at_textbox=False):
    if name(node) in ('rPr','sdtPr','sdtEndPr') or excluded_kind(node):
        return
    yield node
    if stop_at_textbox and node.tag in TEXTBOX:
        return
    if node.tag==ALTERNATE:
        selected = branch(node)
        children = selected if selected is not None else ()
    else:
        children = node
    for child in children:
        yield from visible(child,stop_at_textbox=stop_at_textbox)


def story_scope(node):
    """Linked frames belong to the Word story containing their anchors."""
    return next((parent for parent in node.iterancestors()
                 if parent.tag in TEXTBOX or name(parent) in ('body','footnote','endnote')),None)


def next_vml_frame(node):
    for declaration in node.get('style','').split(';'):
        key,sep,value = declaration.partition(':')
        if sep and key.strip().lower()=='mso-next-textbox':
            return value.strip().strip('"\'')
    return None


class Stories:
    def __init__(self,root):
        self.owners = defaultdict(list)
        vml_by_id = defaultdict(list)
        vml_links = {}
        for node in visible(root):
            if node.tag==OWNER and (ident:=node.get('id')) is not None and any(n.tag in TEXTBOX for n in node):
                self.owners[story_scope(node),ident].append(node)
            if node.tag!=VML_BOX:
                continue
            if ident:=node.getparent().get('id'):
                vml_by_id[story_scope(node),ident].append(node)
            if (target:=next_vml_frame(node)) is not None:
                vml_links[node] = target
        self.vml_flow, self.vml_problems = self.link_vml(vml_by_id,vml_links)

    @staticmethod
    def link_vml(by_id,links):
        successors, predecessors, broken = {}, defaultdict(list), set()
        for source,target in links.items():
            matches = by_id.get((story_scope(source),target[1:]),()) if target.startswith('#') else ()
            if len(matches)!=1:
                broken.add(source)
                continue
            successors[source] = matches[0]
            predecessors[matches[0]].append(source)

        flow = {}
        remaining = set(successors)
        for head in successors:
            if head in predecessors:
                continue
            chain = [head]
            while chain[-1] in successors:
                following = successors[chain[-1]]
                if len(predecessors[following])!=1:
                    broken.update(chain)
                    break
                chain.append(following)
            if chain[-1] in broken:
                broken.update(chain)
            if not any(frame in broken for frame in chain):
                flow[head] = tuple(content for frame in chain for content in frame if content.tag in TEXTBOX)
                for follower in chain[1:]:
                    flow[follower] = ()
            remaining.difference_update(chain)
        broken.update(remaining)
        return flow,broken

    def drawing(self,node):
        """Read only the text stories in this drawing, stopping at nested story boundaries."""
        stories,problems = [],[]
        editable = False
        seen_frames = set()
        for child in visible(node,stop_at_textbox=True):
            if child.tag in TEXTBOX:
                editable = True
                parent = child.getparent()
                if parent in self.vml_problems:
                    problems.append('The order of linked Word text boxes could not be verified.')
                if parent in self.vml_flow:
                    if parent not in seen_frames:
                        stories.extend(self.vml_flow[parent])
                        seen_frames.add(parent)
                else:
                    stories.append(child)
            elif child.tag==CONTINUATION:
                editable = True
                if len(self.owners.get((story_scope(child),child.get('id')),()))!=1:
                    problems.append('A linked Word text box has no unique story owner.')
            elif child.tag==ALTERNATE and branch(child) is None:
                problems.append(MISSING_BRANCH)
            elif child.tag in (f'{{{VML}}}imagedata','{urn:schemas-microsoft-com:office:office}OLEObject'):
                problems.append('The Word drawing contains a separate image or embedded object.')
            elif child.tag in GRAPHIC_DATA and child.get('uri') not in (SHAPE,GROUP):
                problems.append('The Word drawing contains a separate graphic.')
        return stories, editable, problems
