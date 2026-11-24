"""Read declared XPS pagination and preserve its original OPC parts."""

from collections import Counter, defaultdict
from itertools import count
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from .archive_intake import containers
from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, xml
from .opc_source import Parts, TYPES, TYPES_NAME, manifest_xml
from .publication_package import bind_member_block, member_bytes, member_location


NAMESPACES = ('http://schemas.microsoft.com/xps/2005/06', 'http://schemas.openxps.org/oxps/v1.0')
SEQUENCES = {ns+'/fixedrepresentation':ns for ns in NAMESPACES}
SEQUENCE_TYPES = frozenset(('application/vnd.ms-package.xps-fixeddocumentsequence+xml',
                            'application/vnd.openxps-fixeddocumentsequence+xml'))
CORE_PROPERTIES = 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties'
MANIFEST_LIMIT = 1024*1024
MAX_PAGES = 10_000


def identifies(node):
    return node is not None and any(child.tag in (TYPES+'Override',TYPES+'Default')
                                    and child.get('ContentType') in SEQUENCE_TYPES for child in node)


def folder_marker(root):
    marker = root/TYPES_NAME
    if marker.is_symlink() or not marker.is_file():
        return False
    with marker.open('rb') as stream:
        raw = stream.read(MANIFEST_LIMIT+1)
    return len(raw)<=MANIFEST_LIMIT and identifies(manifest_xml(raw))


def archive_books(archive,members):
    editions = []
    claimed = set()
    manifests = [item for item in members if PurePosixPath(item.name).name.casefold()==TYPES_NAME.casefold()]
    for marker in sorted(manifests,key=lambda item:item.name):
        if marker.index in claimed or marker.size is not None and marker.size>MANIFEST_LIMIT:
            continue
        if not identifies(manifest_xml(member_bytes(archive,marker,MANIFEST_LIMIT))):
            continue
        parent = PurePosixPath(marker.name).parent
        prefix = '' if parent==PurePosixPath('.') else str(parent)+'/'
        editions.append((marker,f'XPS document ({parent.name})' if prefix else 'XPS document'))
        claimed.update(item.index for item in members if item.name.startswith(prefix))
    return editions,claimed


class Package:
    def __init__(self, archive, manifest, members):
        root = PurePosixPath(manifest.name).parent
        self.parts = Parts('' if str(root)=='.' else str(root)+'/',members)
        self.archive = archive
        self.manifest = manifest
        relationships = self.related(None)
        roots = [link for link in relationships if link.kind in SEQUENCES]
        if len(roots)!=1 or roots[0].member is None:
            raise Error('source_xps_root','The XPS package has no unique local fixed representation.')
        self.sequence = roots[0].member
        self.namespace = SEQUENCES[roots[0].kind]
        self.fixed = '{'+self.namespace+'}'
        self.structure = '{'+self.namespace+'/documentstructure}'
        self.title = None
        metadata = [link.member for link in relationships if link.kind==CORE_PROPERTIES and link.member]
        if len(metadata)==1:
            try:
                title = xml(member_bytes(self.archive,metadata[0],MANIFEST_LIMIT)).findtext(
                    '{http://purl.org/dc/elements/1.1/}title','').strip()
            except Error:
                title = ''
            self.title = title or None

    def read(self,member):
        return member_bytes(self.archive,member,MAX_STRUCTURE)

    def related(self, member):
        return self.parts.relationships(member,lambda part:member_bytes(self.archive,part,MANIFEST_LIMIT))[1]

    def parse(self, member, kind, namespace=None):
        node = xml(self.read(member))
        if node.tag!=(namespace or self.fixed)+kind:
            raise Error('source_xps_part',f'Expected an XPS {kind} in {member.name}.')
        return node

    def resolve(self, owner, href):
        return self.parts.resolve(self.parts.local(owner),href)

    def structure_part(self, member, suffix):
        choices = [link for link in self.related(member) if link.kind==self.namespace+'/'+suffix]
        if choices and (len(choices)!=1 or choices[0].member is None):
            raise Error('source_xps_structure',f'{member.name} has no unique local {suffix} part.')
        return choices[0].member if choices else None

    def emit(self, builder, visual):
        from .pdf_headers import mark_headers
        from .pdf_layout import mark_folios
        from .xps_page import FontMetrics, page_reading

        metrics = FontMetrics(self)
        doc = builder.doc
        outline = []
        targets = defaultdict(list)
        members_at = defaultdict(list)
        pages = count(1)
        references = self.parse(self.sequence,'FixedDocumentSequence').findall(self.fixed+'DocumentReference')
        for document_index,reference in enumerate(references,1):
            address = reference.get('Source','')
            document = self.resolve(self.sequence,address)
            if document is None:
                builder.add('','unreadable',address,loc={'member':self.sequence.name,'archive_entry':self.sequence.index},
                            issue='An XPS document reference has no unique local original.')
                continue
            try:
                page_records = self.parse(document,'FixedDocument').findall(self.fixed+'PageContent')
                structure = self.structure_part(document,'documentstructure')
                if structure:
                    tree = self.parse(structure,'DocumentStructure',self.structure)
                    for link in tree.iter(self.structure+'OutlineEntry'):
                        if link.get('Description') and link.get('OutlineTarget'):
                            outline.append((structure,link.get('Description'),link.get('OutlineTarget'),link.get('OutlineLevel')))
            except Error as error:
                builder.add('','unreadable',document.name,loc={'member':document.name,'archive_entry':document.index},
                            issue=error.message)
                continue
            for document_page,row in enumerate(page_records,1):
                page = next(pages)
                if page>MAX_PAGES:
                    raise Error('source_structure_size','The XPS sequence exceeds its page budget.')
                address = row.get('Source','')
                member = self.resolve(document,address)
                evidence = {'page':page,'reference':address,'member':member.name if member else document.name,
                            'document':document_index,'document_page':document_page,'document_pages':len(page_records)}
                doc.inventory.append(evidence)
                if member is None:
                    evidence['status'] = 'unavailable'
                    builder.add('','unreadable',address,loc={'member':document.name,'archive_entry':document.index,'page':page},
                                issue='The declared XPS page has no unique local original.')
                    continue
                members_at[member].append(page)
                for link in row.findall('.//'+self.fixed+'LinkTarget'):
                    if link.get('Name'):
                        targets[link.get('Name')].append(page)
                before = len(doc.blocks)
                try:
                    root = self.parse(member,'FixedPage')
                    anchors = page_reading(self,member,root,metrics,builder,evidence,visual)
                    for anchor in anchors:
                        if page not in targets[anchor]:
                            targets[anchor].append(page)
                except Error as error:
                    evidence['status'] = 'unavailable'
                    builder.add('','unreadable',member.name,loc={'page':page},issue=error.message)
                for block in doc.blocks[before:]:
                    bind_member_block(block,member,page,doc.source_sha256,{},self.parts.resolve)
        if not doc.inventory:
            raise Error('source_xps_root','The XPS package contains no declared pages.')
        observed_pages = [item for item in doc.inventory if item.get('size')]
        mark_folios(doc.blocks,observed_pages)
        mark_headers(doc.blocks,observed_pages)
        self.navigation(doc,outline,targets,members_at)

    def navigation(self, doc, outline, targets, members_at):
        from .pdf_navigation import apply
        import re

        entries = []
        blocks_at = defaultdict(list)
        for block in doc.blocks:
            if block.role=='body':
                blocks_at[block.locator.get('page')].append(block)
        for index,(owner,title,href,level) in enumerate(outline):
            try:
                uri = urlsplit(href)
            except ValueError:
                continue
            if uri.scheme or uri.netloc or uri.query:
                continue
            anchor = unquote(uri.fragment)
            member = self.resolve(owner,href) if uri.path else None
            anchored = targets.get(anchor,())
            # An ambiguous anchor remains unresolved unless the href also
            # identifies the same physical page.
            if member in members_at:
                candidates = [n for n in members_at[member] if not anchored or n in anchored]
            elif anchor:
                candidates = list(anchored)
            else:
                candidates = []
            page = candidates[0] if len(candidates)==1 else None
            printed = [block.source for block in blocks_at.get(page,())
                       if re.sub(r'^\s*\d+(?:\.\d+)*\.?\s+','',block.source).casefold()==title.casefold()]
            entries.append({'entry':index,'page':page,'title':printed[0] if len(printed)==1 else title,
                            'authored_title':title,'source_member':owner.name,'source_target':href,'level':level})
        if entries:
            chapters = [entry for entry in entries if entry['level'] in (None,'1')]
            apply(doc.blocks,doc.inventory,chapters,source='XPS',identity='xps-outline')
            doc.metadata['source_outlines'] = entries


def read_xps(path,source,sha,title,cache=None):
    from .xps_visual import VisualPages

    with containers(path,source.kind) as archive:
        members = list(archive.members())
        selected = next((item for item in members if item.index==source.entry),None)
        if selected is None or not identifies(manifest_xml(member_bytes(archive,selected,MANIFEST_LIMIT))):
            raise Error('source_xps_root','The selected XPS publication has no valid OPC declaration.')
        package = Package(archive,selected,members)
        builder = DocumentBuilder('xps',package.title or title,sha)
        with VisualPages(path,source,archive,package.parts,members,cache) as visual:
            package.emit(builder,visual)
        seen = Counter(package.parts.local(member) for member in members if member.name.startswith(package.parts.prefix))
        capabilities = ['paginated','image_regions']
        if any(entry.get('route')=='visual_ocr' for entry in builder.doc.inventory):
            capabilities.append('raster_ocr')
        builder.doc.metadata.update(
            source_capabilities=capabilities,
            source_resources=[member_location(sha,item,'package') for item in members
                              if item.name.startswith(package.parts.prefix) and seen[package.parts.local(item)]==1],
            xps_dialect='OpenXPS' if package.namespace==NAMESPACES[1] else 'Microsoft XPS')
        return builder.doc
