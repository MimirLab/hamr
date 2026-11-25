"""Project InDesign and InCopy story markup into shared document semantics."""

from collections import defaultdict
from html import unescape
import re

from lxml import etree

from .core import Error
from .document_intake import DocumentBuilder, bounded_read, html_read, name, xml
from .xml_projection import XMLProjection, append_text

PACKAGE = '{http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging}'
FRAME = frozenset(('TextFrame','TextPath'))
WRAPPERS = frozenset(('XMLElement','XMLStory','HyperlinkTextSource','CrossReferenceSource',
                      'Text','Story','CharacterStyleRange','ParagraphStyleRange','Change'))
GRAPHICS = frozenset(('Image','PDF','EPS','PICT','ImportedPage','SVG'))
SHAPES = frozenset(('Rectangle','Oval','Polygon','GraphicLine','Group'))
HEADING = re.compile(r'(?:heading|header|h)[ _-]*([1-6])$',re.I)


def story_nodes(root):
    if root is None:
        return ()
    if root.tag=='Story':
        return (root,)
    if root.tag in ('Document',PACKAGE+'Story'):
        return tuple(root.findall('Story'))
    return ()


def style_name(value):
    return unescape(value.split('/')[-1]).strip().casefold() if value else ''


def change_deleted(node):
    return node.get('ChangeType','').casefold() in ('deletedtext','deleted','deletion')


def style_properties(node):
    fields = ('FontStyle','Position')
    properties = node.find('Properties')
    return {field:value for field in fields if
            (value:=node.get(field) or (properties.findtext(field) if properties is not None else None))
            and value not in ('Nothing','n')}


class Styles:
    def __init__(self,root=None):
        self.nodes = {}
        if root is not None:
            for kind in ('ParagraphStyle','CharacterStyle'):
                self.nodes.update((node.get('Self'),node) for node in root.iter(kind) if node.get('Self'))

    def lineage(self,ident):
        visited = set()
        while ident in self.nodes and ident not in visited:
            visited.add(ident)
            node = self.nodes[ident]
            yield node
            ident = node.findtext('Properties/BasedOn') or node.get('BasedOn')

    def paragraph(self,node):
        ident = node.get('AppliedParagraphStyle')
        for style in self.lineage(ident):
            for export in style.findall('.//StyleExportTagMap'):
                tag = export.get('ExportTag','').casefold()
                if tag in {'p','pre','blockquote',*(f'h{i}' for i in range(1,7))}:
                    return tag
        title = style_name(ident)
        if title in ('title','chapter title'):
            return 'h1'
        if heading:=HEADING.fullmatch(title):
            return 'h'+heading[1]
        if title in ('code','code block','preformatted'):
            return 'pre'
        return 'blockquote' if title in ('blockquote','block quote') else 'p'

    def character(self,node):
        ident = node.get('AppliedCharacterStyle')
        tags = {'em','strong','sub','sup','code'}
        inherited = {}
        for style in reversed(list(self.lineage(ident))):
            inherited.update(style_properties(style))
        inherited.update(style_properties(node))
        chosen = style_name(ident)
        face = inherited.get('FontStyle',chosen).casefold()
        result = []
        if 'bold' in face:
            result.append('strong')
        if any(value in face for value in ('italic','oblique')) or chosen in ('emphasis','emph'):
            result.append('em')
        if inherited.get('Position') in ('Superscript','OTSuperscript'):
            result.append('sup')
        elif inherited.get('Position') in ('Subscript','OTSubscript'):
            result.append('sub')
        if chosen in ('code','inline code'):
            result.append('code')
        for style in self.lineage(ident):
            for export in style.findall('.//StyleExportTagMap'):
                if (tag:=export.get('ExportTag','').casefold()) in tags and tag not in result:
                    result.append(tag)
        return tuple(result)

    def list_kind(self,node):
        values = (node,*self.lineage(node.get('AppliedParagraphStyle')))
        for style in values:
            if kind:=style.get('BulletsAndNumberingListType'):
                return {'BulletList':'ul','NumberedList':'ol'}.get(kind)
        return None


class StoryProjection(XMLProjection):
    def __init__(self,node,part,styles,resolve_story=None,resolve_asset=None):
        super().__init__(node,part)
        self.styles = styles
        self.resolve_story = resolve_story
        self.resolve_asset = resolve_asset
        self.parts = {node.getroottree().getroot():part}
        self.definitions = []

    def location(self,node):
        result = super().location(node)
        result['source_file'] = self.parts[node.getroottree().getroot()]
        return result

    def content(self,node):
        result = node.text or ''
        for item in node:
            if isinstance(item,etree._Entity):
                self.entity(item)
            if isinstance(item,etree._ProcessingInstruction) and item.target=='ACE':
                if (item.text or '').strip()=='4' and any(name(parent)=='Footnote' for parent in node.iterancestors()):
                    # The writer's generated footnote label is resolved by NoteGraph.
                    result += item.tail or ''
                    continue
                if result:
                    yield 'text',node,(),result
                    result = ''
                yield 'review',item,(),f'Unrecognized InDesign text control ACE {item.text or ""}.'
            result += item.tail or ''
        if result:
            yield 'text',node,(),result

    def events(self,root,paragraph=None,tags=()):
        for node in root:
            kind = name(node)
            if kind=='ParagraphStyleRange':
                yield from self.events(node,node,tags)
            elif kind=='CharacterStyleRange':
                yield from self.events(node,paragraph,(*tags,*self.styles.character(node)))
            elif kind=='Content':
                for event,part,_,value in self.content(node):
                    yield event,part,paragraph,tags,value
            elif kind=='TextVariableInstance':
                value = node.get('ResultText')
                if value is not None:
                    yield 'text',node,paragraph,tags,value
                else:
                    yield 'review',node,paragraph,tags,'This InDesign text variable has no saved result.'
            elif kind in ('Br','ParagraphBreak','ForcedLineBreak','Table','Footnote','Note','HiddenText') or kind in FRAME|SHAPES|GRAPHICS:
                yield kind,node,paragraph,tags,None
            elif kind in WRAPPERS and not (kind=='Change' and change_deleted(node)):
                yield from self.events(node,paragraph,tags)
            elif isinstance(node,etree._Entity):
                self.entity(node)

    def figure(self,node):
        graphic = node if name(node) in GRAPHICS else next(
            (item for item in node.iterdescendants() if name(item) in GRAPHICS),None)
        if graphic is None:
            return None
        attributes = {}
        for origin in (node,graphic):
            options = origin.find('ObjectExportOption')
            if options is not None and options.get('ApplyTagType')=='Artifact':
                return None
            if options is not None and (label:=options.get('CustomAltText','').strip()) not in ('','$ID/'):
                attributes['alt'] = label
                break
        link = graphic.find('Link')
        if link is not None and self.resolve_asset:
            if src:=self.resolve_asset(link.get('LinkResourceURI','')):
                attributes['src'] = src
        if not attributes.get('alt'):
            return self.review(node,'This placed InDesign graphic has no authored accessible description.')
        return self.element(node,'img',attributes=attributes)

    def table(self,node):
        rows = defaultdict(list)
        count = node.get('HeaderRowCount','0')
        if not count.isascii() or not count.isdecimal() or len(count)>8:
            return self.review(node,'This InDesign table has an invalid header row count.')
        headers = int(count)
        for cell in node.findall('Cell'):
            try:
                column,row = map(int,cell.get('Name','').split(':'))
                if min(column,row)<0:
                    raise ValueError
            except ValueError:
                return self.review(node,'An InDesign table cell does not have a valid row and column.')
            rows[row].append((column,cell))
        if not rows:
            for row,item in enumerate(node.findall('Row')):
                rows[row].extend(enumerate(item.findall('Cell')))
        table = self.element(node,'table')
        for row,cells in sorted(rows.items()):
            tr = etree.SubElement(table,'tr')
            for _,cell in sorted(cells,key=lambda item:item[0]):
                tag = 'th' if row<headers else 'td'
                attrs = {field.lower():value for field in ('RowSpan','ColumnSpan')
                         if (value:=cell.get(field)) and value.isdecimal() and value!='1'}
                if 'columnspan' in attrs:
                    attrs['colspan'] = attrs.pop('columnspan')
                item = self.element(cell,tag,attributes=attrs)
                self.flow(cell,item,inside=True)
                tr.append(item)
        return table if any(len(row) for row in table) else self.review(node,'This InDesign table has no readable cells.')

    def flow(self,root,target,*,inside=False):
        block = None
        listing = None
        starts = set()
        deferred = []

        def flush():
            nonlocal block
            target.extend(deferred)
            deferred.clear()
            block = None

        for kind,node,paragraph,tags,value in self.events(root):
            if kind in ('Br','ParagraphBreak'):
                flush()
                continue
            if kind=='Table':
                flush()
                target.append(self.table(node))
                listing = None
                continue
            if kind in ('Note','HiddenText'):
                flush()
                editorial = self.element(node,'aside',attributes={'hidden':'hidden'})
                self.flow(node,editorial,inside=True)
                if len(editorial):
                    target.append(editorial)
                listing = None
                continue
            if kind in FRAME:
                part = node.get('ParentStory')
                if part and part!='n' and self.resolve_story:
                    resolved = self.resolve_story(part)
                    nearby = etree.Element('div')
                    if resolved is None:
                        nearby.append(self.review(node,f'The anchored story {part} is missing from this IDML package.'))
                    elif resolved:
                        resource,story = resolved
                        self.parts[story.getroottree().getroot()] = resource
                        self.flow(story,nearby,inside=inside)
                    if block is not None or paragraph is not None:
                        deferred.extend(nearby)
                    else:
                        target.extend(nearby)
                continue
            rendered = None
            if kind in SHAPES|GRAPHICS:
                rendered = self.figure(node)
                if rendered is None:
                    continue
                settings = node.find('Properties/AnchoredObjectSetting')
                if settings is not None and settings.get('AnchoredPosition') in ('Anchored','AboveLine'):
                    # Floating artwork belongs beside the paragraph, without
                    # breaking the sentence in which its anchor was stored.
                    if block is not None or paragraph is not None:
                        deferred.append(rendered)
                    else:
                        target.append(rendered)
                    continue
            elif kind=='Footnote':
                ident = 'idml-note-'+str(len(self.definitions)+1)
                self.definitions.append((ident,node))
                rendered = self.element(node,'a',attributes={'role':'doc-noteref','href':'#'+ident},
                                        inline_location=self.location(node))
            elif kind=='review':
                rendered = self.review(node,value)
            if block is None:
                structured = paragraph is not None and not inside
                tag = self.styles.paragraph(paragraph) if structured else 'p'
                list_kind = self.styles.list_kind(paragraph) if structured and tag=='p' else None
                if list_kind:
                    restart = list_kind=='ol' and paragraph not in starts and paragraph.get('NumberingContinue')=='false'
                    if listing is None or listing.tag!=list_kind or restart:
                        first = paragraph.get('NumberingStartAt','')
                        attrs = {'start':first} if list_kind=='ol' and re.fullmatch(r'-?[0-9]{1,9}',first) else {}
                        listing = etree.SubElement(target,list_kind,attrs)
                    starts.add(paragraph)
                    block = self.element(node,'li')
                    listing.append(block)
                else:
                    listing = None
                    block = self.element(node,tag)
                    target.append(block)
            if kind=='text':
                if not tags:
                    append_text(block,value)
                    continue
                rendered = etree.Element(tags[0])
                last = rendered
                for tag in tags[1:]:
                    last = etree.SubElement(last,tag)
                last.text = value
            elif kind=='ForcedLineBreak':
                rendered = etree.Element('br')
            if rendered is not None:
                block.append(rendered)
        flush()

    def write(self,node,builder):
        body = etree.Element('div')
        self.flow(node,body)
        for ident,definition in self.definitions:
            note = self.element(definition,'aside',attributes={'role':'doc-footnote','id':ident})
            self.flow(definition,note,inside=True)
            body.append(note)
        html_read(etree.tostring(body,method='html',encoding='unicode'),builder,where=self.part,metadata=False,
                  collect_resources=True,source_annotations=self.annotations)


def read_icml(path,source,sha,title):
    root = xml(bounded_read(path))
    stories = story_nodes(root)
    if root.tag not in ('Document','Story',PACKAGE+'Story') or not stories:
        raise Error('source_icml','This ICML file does not contain an InCopy story.',
                    'Open the saved InCopy story or export the active story again.')
    builder = DocumentBuilder('icml',root.get('Name') or title,sha)
    part = source.member_name or path.name
    styles = Styles(root)
    for story in stories:
        StoryProjection(story,part,styles).write(story,builder)
    return builder.doc
