"""Prepare DAISY Z39.98 authoring XML for the shared structured-text reader."""

from collections import defaultdict
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit

from lxml import etree

from .core import Error
from .document_intake import XML_LANG, html_read, name, xml_formula
from .source_math import MATHML
from .xml_includes import XMLIncludes, local_target
from .xml_projection import XML_SPACE, XMLProjection, append_text, element_text, identifier, normal_text

NS = '{http://www.daisy.org/ns/z3998/authoring/}'
DIAGRAM = NS[:-1]+'features/description/}'
SELECT = NS[:-1]+'features/select/}'
XLINK = '{http://www.w3.org/1999/xlink}href'
TARGET_SETS = frozenset(f'{scheme}://www.daisy.org/z3998/2012/auth/features/select/functionSets/#{key}'
                       for scheme in ('http','https') for key in ('target','targetFunctionSet'))
FORMAT_TEST = re.compile(r"""([\w.-]+):format\s*\(\s*(['"])([\w]+)\2\s*\)""")
IMAGE_SUFFIXES = frozenset(('.gif','.jpeg','.jpg','.png','.svg','.webp','.tif','.tiff'))
PARAGRAPHS = frozenset(('p','byline','dateline','hd','entry'))
SECTIONS = frozenset(('body','frontmatter','bodymatter','backmatter','section','block','verse','lngroup',
                      'aside','bibliography','glossary','index','cover','frontcover','backcover','spine','flaps'))
HTML = {'item':'li','table':'table','thead':'thead','tbody':'tbody','tfoot':'tfoot','tr':'tr',
        'td':'td','th':'th','sub':'sub','sup':'sup','citation':'cite','abbr':'abbr'}


def is_zedai(root):
    return root is not None and root.tag==NS+'document'


def local(node):
    return name(node) if node is not None and isinstance(node.tag,str) and node.tag.startswith(NS) else ''


def meta_value(node):
    return (node.get('content') or element_text(node)).strip()


def metadata(root,field):
    fields = {'dc:'+field,'dcterms:'+field}
    head = root.find(NS+'head')
    records = list(head.iter(NS+'meta')) if head is not None else []
    body = root.find(NS+'body')
    if body is not None:
        records.extend(node for node in body.iter() if local(node) and
                       (field!='title' or local(node) in ('h','hd','hpart')))
    return list(dict.fromkeys(value for node in records
                              if fields.intersection(node.get('property','').split()) and (value:=meta_value(node))))


def publication_label(root,includes=None):
    titles = metadata(root,'title')
    if titles:
        return titles[0]
    body = root.find(NS+'body')
    if body is not None:
        return next((value for node in body.iter(NS+'h') if (value:=element_text(node))),None)


def asset_links(root):
    for node in root.iter(NS+'object'):
        if href:=node.get('src'):
            yield node,href


class DescriptionResources(XMLIncludes):
    def __init__(self,root,part,load=None):
        self.descriptions = {}
        super().__init__(root,part,load)

    def walk(self,scope,part,ancestors):
        self.active.update((node,None) for node in scope.iter() if isinstance(node.tag,str))
        for node in scope.iter(NS+'description'):
            if href:=node.get(XLINK):
                self.descriptions[node] = self.description(node,part,href)

    def description(self,node,part,href):
        path = local_target(part,node,href)
        chosen = None
        try:
            fragment = unquote(urlsplit(href).fragment)
            source = self.document(path) if path else self.roots[part] if href.startswith('#') else None
            if source is not None:
                self.active.update((item,None) for item in source.iter() if isinstance(item.tag,str))
                candidates = [item for item in source.iter(NS+'description',DIAGRAM+'description')
                              if item is not node and not item.get(XLINK) and
                              (identifier(item)==fragment if fragment else source is not self.roots[part])]
                if len(candidates)==1:
                    chosen, = candidates
        except (ValueError,Error) as error:
            if isinstance(error,Error) and error.code=='source_changed':
                raise
        self.references.append(({'kind':'xml_include','relation':'zedai_description','member':part,'href':href,
                                 'xml_path':node.getroottree().getpath(node),'line':node.sourceline,
                                 'status':'expanded' if chosen is not None else 'unavailable'},path))
        return chosen


def target_format(expr,prefixes):
    components = re.split(r'\s+(and|or)\s+',expr.strip())
    operators = set(components[1::2])
    if len(operators)>1:
        return None
    values = []
    for component in components[::2]:
        negate = re.fullmatch(r'not\s*\((.*)\)',component)
        matched = FORMAT_TEST.fullmatch(negate[1].strip() if negate else component)
        if matched is None or matched[1] not in prefixes:
            return None
        values.append((matched[3]=='TALKING_BOOK') != bool(negate))
    return any(values) if 'or' in operators else all(values)


class Edition(XMLProjection):
    def __init__(self,root,part,includes):
        super().__init__(root,part,includes=includes)
        self.body = root.find(NS+'body')
        if self.body is None:
            raise Error('source_zedai','This ZedAI document has no authored body.')
        self.head = root.find(NS+'head')
        self.inventory = []
        self.choices = {}
        self.ids = defaultdict(list)
        self.definitions = {}
        self.backlinks = defaultdict(list)
        declarations = self.head.iter(NS+'meta') if self.head is not None else ()
        prefixes = {meta_value(node) for node in declarations if node.get('about') in TARGET_SETS
                    and any(field.endswith(':prefix') for field in node.get('property','').split())}
        for node in self.active(root,prefixes):
            if anchor:=self.anchor(node):
                self.ids[anchor].append(node)
        spoken = {target for node in self.body.iter(NS+'noteref',NS+'annoref')
                  if self.selected(node) for target in self.targets(node)}
        for node in self.body.iter(NS+'note',NS+'annotation'):
            if not self.selected(node):
                continue
            anchor = self.anchor(node)
            if node.tag==NS+'note' and anchor not in spoken and node.get('ref'):
                anchor = anchor or f'hamr-zedai-note-{len(self.definitions)+1}'
                while anchor in self.ids and self.ids[anchor]!=[node]:
                    anchor += '-note'
                for target in self.targets(node):
                    candidates = self.ids.get(target,())
                    if len(candidates)==1 and local(candidates[0]) in PARAGRAPHS|{'h','hpart','td','th','span','ln'}:
                        self.backlinks[candidates[0]].append((node,anchor))
            if anchor:
                self.definitions[node] = anchor

    def anchor(self,node):
        return identifier(node) or node.get(SELECT+'selid')

    def targets(self,node):
        return [ref.removeprefix('#') for ref in node.get('ref','').split()]

    def selected(self,node):
        return all(parent.tag not in (SELECT+'when',SELECT+'otherwise') or self.choices.get(parent.getparent()) is parent
                   for parent in node.iterancestors())

    def active(self,node,prefixes):
        if not isinstance(node.tag,str):
            return
        yield node
        if node.tag==SELECT+'select':
            branches = [item for item in node if item.tag in (SELECT+'when',SELECT+'otherwise')]
            unsupported = []
            default = node.findall(SELECT+'otherwise')
            choice = None
            for branch in branches:
                if branch.tag==SELECT+'otherwise':
                    continue
                expr = branch.get('expr','')
                verdict = target_format(expr,prefixes)
                if verdict is None:
                    unsupported.append(expr)
                elif verdict:
                    choice = branch
                    break
            choice = choice if choice is not None else default[0] if len(default)==1 else None
            self.choices[node] = choice
            self.inventory.append({'kind':'text_variant','encoding':'zedai-select',**self.location(node),
                                   'status':'selected' if choice is not None else 'review',
                                   'unsupported_conditions':unsupported,
                                   'readings':[{**self.location(branch),'expression':branch.get('expr'),
                                                'text':element_text(branch),'selected':branch is choice}
                                               for branch in branches]})
            if choice is not None:
                yield from self.active(choice,prefixes)
        else:
            for child in node:
                yield from self.active(child,prefixes)

    def element(self,node,tag,**kwargs):
        result = super().element(node,tag,**kwargs)
        if anchor:=self.anchor(node):
            result.set('id',anchor)
        return result

    def note_marker(self,node,target):
        marker = self.element(node,'a',attributes={'role':'doc-noteref','href':'#'+quote(target,safe='')},
                              inline_location=self.location(node))
        marker.attrib.pop('id',None)
        return marker

    def note_reference(self,node,depth,literal):
        targets = self.targets(node)
        if len(targets)!=1:
            return self.review(node,'The ZedAI note reference does not identify one note.')
        marker = self.note_marker(node,targets[0])
        if (label:=node.get('value')) is not None:
            marker.text = label
            return marker
        # Text inside a reference without a marker value belongs to the prose.
        result = self.children(node,self.element(node,'span'),depth=depth,literal=literal)
        result.append(marker)
        return result

    def diagram_reading(self,node):
        body = node.find(DIAGRAM+'body')
        if body is None:
            return None
        return next((item for tag in ('longdesc','summary') for item in body.findall(DIAGRAM+tag)
                     if element_text(item)),None)

    def object(self,node,depth):
        href = node.get('src','')
        description = node.find(NS+'description')
        if description is None:
            description = next((matches[0] for anchor in node.get('desc','').split()
                                if len(matches:=self.ids.get(anchor.removeprefix('#'),()))==1
                                and matches[0].tag in (NS+'description',DIAGRAM+'description')),None)
        if description is not None and description.get(XLINK):
            description = self.includes.descriptions.get(description)
        if description is not None and description.tag==DIAGRAM+'description':
            description = self.diagram_reading(description)
        label = element_text(description) if description is not None else element_text(node)
        evidence = {}
        if description is not None:
            location = self.location(description)
            evidence['authored_description'] = location
            self.inventory.append({'kind':'zedai_description',**location,
                                   'target':self.location(node),'text':label,'status':'used' if label else 'unavailable'})
        target = local_target(self.part,node,href) if href else None
        # The projected URI resolves from the document member, including an inherited xml:base.
        effective = quote(posixpath.relpath(target,posixpath.dirname(self.part) or '.'),safe='/') if target else href
        media = node.get('srctype','').lower()
        path = href.split('?',1)[0].split('#',1)[0]
        if media.startswith('image/') or not media and (posixpath.splitext(path)[1].lower() in IMAGE_SUFFIXES
                                                        or href.startswith('data:image/')):
            return self.element(node,'img',attributes={'src':effective,'alt':label} if label else {'src':effective},
                                **evidence)
        if label:
            result = self.element(node,'p' if local(node.getparent()) in SECTIONS else 'span',**evidence)
            result.text = label
            return result
        return self.review(node,'This ZedAI media object has no usable authored description.')

    def project(self,node,*,depth=0,literal=False):
        kind = local(node)
        if node.tag==SELECT+'select':
            choice = self.choices[node]
            if choice is None:
                return self.review(node,'ZedAI could not select one reading. Supply the intended alternative.')
            inside = any(local(parent) in PARAGRAPHS|{'h','hpart','ln','th','td'} for parent in node.iterancestors())
            return self.children(choice,self.element(choice,'span' if inside else 'div'),depth=depth,literal=literal)
        if node.tag==DIAGRAM+'description':
            chosen = self.diagram_reading(node)
            if chosen is None:
                return self.review(node,'This ZedAI diagram has no authored text description.')
            self.inventory.append({'kind':'zedai_description',**self.location(chosen),'text':element_text(chosen),
                                   'status':'used'})
            return self.children(chosen,self.element(chosen,'div'),depth=depth)
        if isinstance(node.tag,str) and node.tag==f'{{{MATHML}}}math':
            display = node.get('display')=='block'
            return self.element(node,'p' if display else 'span',reading_span=xml_formula(node,self.part),
                                **({'source_role':'equation'} if display else {}))
        if kind in ('note','annotation','description','meta','head','lnum','transition','separator'):
            if kind=='annotation':
                self.inventory.append({'kind':'zedai_annotation',**self.location(node),'text':element_text(node),
                                       'targets':self.targets(node),'status':'preserved'})
            return None
        if kind in ('noteref','annoref'):
            return self.note_reference(node,depth,literal)
        if kind=='object':
            return self.object(node,depth)
        if not kind:
            return self.review(node,f'This ZedAI feature needs a verified reading for {name(node)}.')
        if kind=='pagebreak':
            result = self.element(node,'span',attributes={'role':'doc-pagebreak','aria-hidden':'true'})
            result.text = node.get('value') or element_text(node)
            return result
        attrs, evidence = {},{}
        if kind=='h':
            tag = f'h{min(max(depth,1),6)}'
        elif kind=='hpart':
            tag = 'span'
        elif kind=='ln':
            tag = 'p' if local(node.getparent()) in ('lngroup','verse') else 'span'
            if tag=='p':
                evidence['authored_line_breaks'] = True
        elif kind=='toc':
            tag = 'nav'
            evidence['source_control'] = True
        elif kind=='list':
            tag = 'ol' if node.get('type')=='ordered' else 'ul'
            if tag=='ol' and (start:=node.get('start')):
                attrs['start'] = start
        elif kind in ('d','quote'):
            tag = 'q' if kind=='d' or local(node.getparent()) in PARAGRAPHS else 'blockquote'
        elif kind=='code':
            tag = 'code' if local(node.getparent()) in PARAGRAPHS else 'pre'
        elif kind=='emph':
            tag = 'strong' if 'strong' in node.get('class','').split() else 'em'
        elif kind=='ref':
            tag = 'a'
            if (targets:=self.targets(node)) and len(targets)==1:
                attrs['href'] = '#'+quote(targets[0],safe='')
            elif href:=node.get(XLINK):
                attrs['href'] = href
        elif kind=='caption':
            tag = 'caption' if local(node.getparent())=='table' else 'figcaption'
        else:
            tag = HTML.get(kind,'p' if kind in PARAGRAPHS else 'div' if kind in SECTIONS else 'span')
        if (kind in PARAGRAPHS|{'h'} and
                any(parent.tag in (NS+'index',NS+'bibliography') for parent in node.iterancestors())):
            evidence['source_role'] = 'reference'
        literal = node.get(XML_SPACE)=='preserve' or tag=='pre' or literal and node.get(XML_SPACE)!='default'
        result = self.children(node,self.element(node,tag,attributes=attrs,**evidence),
                               depth=depth+(kind=='section'),literal=literal)
        if kind=='ln' and tag=='span':
            append_text(result,'\n')
        if kind=='hpart' and not node.tail and local(node.getnext())=='hpart':
            append_text(result,' ')
        for note,target in self.backlinks.get(node,()):
            result.append(self.note_marker(note,target))
        return result

    def html(self):
        out = etree.Element('body')
        out.append(self.project(self.body))
        for note,anchor in self.definitions.items():
            role = 'doc-endnote' if note.get('role')=='endnote' else 'doc-footnote'
            result = self.element(note,'aside',attributes={'role':role})
            result.set('id',anchor)
            out.append(self.children(note,result,depth=0))
        return etree.tostring(out,encoding='unicode',method='html')


def read_zedai(root,builder,part,*,collect_resources=False,includes=None):
    edition = Edition(root,part,includes or DescriptionResources(root,part))
    doc = builder.doc
    if title:=publication_label(root):
        doc.title = title
    if language:=root.get(XML_LANG):
        doc.language = language
    if authors:=metadata(root,'creator'):
        doc.metadata['authors'] = authors
    html_read(edition.html(),builder,where=part,collect_resources=collect_resources,
              source_annotations=edition.annotations)
    doc.inventory.extend(edition.inventory)
    if any(item.get('unsupported_conditions') for item in edition.inventory):
        doc.warnings.append('Some ZedAI selection conditions are unsupported. The plan records the alternatives and the selected text.')
