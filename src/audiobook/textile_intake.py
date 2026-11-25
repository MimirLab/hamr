"""Project resident Textile output into the common structured reading pipeline."""

from collections import defaultdict
from html import escape, unescape
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .core import Error, digest
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read, html_read
from .opf_intake import target
from .publication_package import bind_member_block, capture_linked_members, member_bytes, member_location
from .source_bundle import stamp


IMAGE_SYNTAX = re.compile(rb'!|<img\b',re.I)
RAILS_TITLE = re.compile(r'\Ah2\.[ \t]+\S')
RAILS_PROLOGUE = re.compile(r'^endprologue\.[ \t]*$',re.M)
RAILS_SECTION = re.compile(r'^h[3-6]\.[ \t]+',re.M)
RAILS_EXAMPLE = re.compile(r'^[ \t]*<(/?)(yaml|shell|ruby|erb|html|sql|plain|code)(?:[ \t]+[^<>\n]+)?>[ \t]*$',re.M)
RUBY_ANNOTATION = re.compile(r'</?(?:rb|rt|rp|rtc)(?:[\s/>])',re.I)
RAILS_MONOSPACE = re.compile(r'<tt>(.*?)</tt>',re.S)
RAILS_INLINE = re.compile(r'(?<!\w)\+([^\s+](?:[^+\n]*?[^\s+])?)\+')
RAILS_ADMONITION = re.compile(r'^(TIP|IMPORTANT|CAUTION|WARNING|NOTE|INFO)[.:][ \t]*',re.M)
RAILS_TABLE_ROW = re.compile(r'^[ \t]*\|[^\n]+\|[ \t]*$',re.M)
BADGE_PATHS = {
    'img.shields.io':'/',
    'sonarcloud.io':'/api/project_badges/',
    'codecov.io':'/github/',
}


def is_textile(name):
    return PurePosixPath(name).suffix.casefold()=='.textile'


def rails_guide(text):
    """Prepare the historical Rails Guides extensions before standard Textile."""
    text = text.replace('\r\n','\n').replace('\r','\n')
    if not RAILS_TITLE.match(text.lstrip()) or not (prologue:=RAILS_PROLOGUE.search(text)):
        return None
    if RAILS_SECTION.search(text,0,prologue.start()):
        return None
    # The producer displays the prologue above the body. Keep those words
    # and remove the reserved section delimiter.
    text = text[:prologue.start()]+text[prologue.end():]
    parts = []
    start = 0
    opened = None
    for marker in RAILS_EXAMPLE.finditer(text):
        ending,language = marker.groups()
        if opened is None:
            if not ending:
                opened = marker
            continue
        if not ending or language!=opened[2]:
            continue
        literal = text[opened.end():marker.start()].strip('\n')
        if language=='ruby' and RUBY_ANNOTATION.search(literal):
            opened = None
            continue
        parts.append(text[start:opened.start()])
        literal = literal.replace('<plus>','+')
        content = escape(literal,quote=False).replace('\n','&#10;')
        parts.append(f'\n\nnotextile. <pre><code class="language-{language}">{content}</code></pre>\n\n')
        start = marker.end()
        opened = None
    if opened is not None:
        line = text.count('\n',0,opened.start())+1
        raise Error('source_textile',f'The <{opened[2]}> example at line {line} is missing its closing tag.',
                    'Close the example in the Textile source or open a rendered HTML edition.')
    parts.append(text[start:])
    return RAILS_ADMONITION.sub(lambda m: f'p(rails-{m[1].lower()}). ',''.join(parts))


def render(raw,encoding=None):
    from textile.core import Textile

    class Reader(Textile):
        def fImage(self,match):
            image = super().fImage(match)
            # Textile emits alt="" when no description was written. Keep
            # that distinct from a null alternative declared in source HTML.
            return image.replace(' alt=""','',1) if match.group(4) is None else image

    class RailsReader(Reader):
        unrestricted_url_schemes = Reader.unrestricted_url_schemes+('irc','ircs')

        def code(self,text):
            text = super().code(text)

            def literal(match):
                value = unescape(match[1].replace('<plus>','+'))
                return self.shelve(f'<code>{escape(value,quote=False)}</code>')

            text = RAILS_MONOSPACE.sub(literal,text)
            text = RAILS_INLINE.sub(literal,text)
            return text.replace('<plus>',self.shelve('+')) if '<plus>' in text else text

        def table(self,text):
            text = RAILS_TABLE_ROW.sub(lambda row: re.sub(r'\|_\.(?=\S)','|_. ',row[0]),text)
            return super().table(text)

    try:
        text = raw.decode(encoding or 'utf-8-sig')
    except (UnicodeError,LookupError) as exc:
        raise Error('text_encoding','The Textile document does not decode with the selected encoding.') from exc
    # Generated footnote anchors belong to the stable source. Internal parser
    # placeholders can remain random because they are removed before return.
    prefix = 'hamr-'+digest(raw)[:16]+'-'
    while prefix in text:
        prefix += 'ref-'
    guide = rails_guide(text)
    parser = (RailsReader if guide is not None else Reader)(get_sizes=False)
    parser.linkPrefix = prefix
    try:
        output = parser.parse(text if guide is None else guide)
    except (RecursionError,ValueError) as exc:
        raise Error('source_textile','The Textile structure could not be read.',
                    'Open a smaller authored section or a rendered HTML edition.') from exc
    soup = BeautifulSoup(output,'html.parser')
    if guide is not None and (heading:=soup.find('h2',recursive=False)):
        title = soup.new_tag('title')
        title.string = heading.get_text(' ',strip=True)
        soup.insert(0,title)
    return soup,prefix


def project_notes(soup,prefix):
    references = ('#fn'+prefix,'#note'+prefix)
    definitions = defaultdict(list)
    anchors = set()
    for element in soup.find_all(id=True):
        definitions[element['id']].append(element)
        if (element.name=='p' and element['id'].startswith('fn'+prefix) or
                element.name=='span' and element['id'].startswith('note'+prefix)
                and element.find_parent('li') is not None):
            anchors.add(element['id'])
    for reference in soup.select('sup > a[href]'):
        href = reference['href']
        if not href.startswith(references):
            continue
        reference['role'] = 'doc-noteref'
        anchors.add(href[1:])
    for marker in soup.find_all('sup'):
        if marker.find('a') or not marker.get_text(strip=True).isdecimal():
            continue
        if 'footnote' in marker.get('class',()) or marker.find(id=lambda value: value and value.startswith('noteref'+prefix)):
            marker['aria-hidden'] = 'true'
    for anchor in sorted(anchors):
        for target_element in definitions.get(anchor,()):
            note = target_element if target_element.name in ('p','li') else target_element.find_parent(['p','li'])
            if note is None:
                continue
            if target_element is not note:
                # Move the generated anchor to the note container while
                # retaining any separate ID written by the author.
                del target_element['id']
                if previous:=note.get('id'):
                    authored_anchor = soup.new_tag('span',id=previous)
                    note.insert(0,authored_anchor)
                note['id'] = anchor
            note['role'] = 'doc-footnote'
            if marker:=note.find('sup',recursive=False):
                links = marker.find_all('a',href=True)
                if ((not links and marker.get_text(strip=True).isdecimal()) or
                        links and all(link['href'].startswith(('#noteref'+prefix,'#fnrev'+prefix)) for link in links)):
                    marker.decompose()


def is_badge(path):
    try:
        parsed = urlsplit(path)
    except ValueError:
        return False
    if parsed.scheme not in ('https','http'):
        return False
    host = parsed.hostname
    return (host in BADGE_PATHS and parsed.path.startswith(BADGE_PATHS[host]) or
            host=='github.com' and re.fullmatch(r'/[^/]+/[^/]+/actions/workflows/[^/]+/badge\.svg',parsed.path))


def prepare(soup,prefix,builder,part):
    project_notes(soup,prefix)
    for paragraph in soup.find_all('p'):
        images = paragraph.find_all('img',src=True)
        if images and not paragraph.get_text(strip=True) and all(is_badge(image['src']) for image in images):
            paragraph['aria-hidden'] = 'true'
            builder.doc.inventory.append({'kind':'project_badges','status':'companion','source_file':part,
                                          'images':[image['src'] for image in images]})
    html_read(str(soup),builder,where=part,collect_resources=True,collect_links=True)
    for block in builder.doc.blocks:
        block.locator['source_file'] = part


def image_targets(soup,part):
    return {resolved for image in soup.find_all('img',src=True) if (resolved:=target(part,image['src']))}


def asset_names(raw,part,encoding=None):
    # A plain manuscript need not undergo a second parse to discover assets.
    if encoding or IMAGE_SYNTAX.search(raw) or raw.startswith((b'\xff\xfe',b'\xfe\xff')):
        soup,_ = render(raw,encoding)
        return image_targets(soup,part)
    return set()


def discover_loose_assets(path,scope):
    try:
        return asset_names(bounded_read(path),path.relative_to(scope).as_posix())
    except Error as error:
        if error.code not in ('text_encoding','source_structure_size','source_textile'):
            raise
        return set()


def archive_claims(archive,members):
    available = defaultdict(list)
    for member in members:
        available[member.name].append(member)
    claimed = set()
    for member in members:
        if is_textile(member.name):
            try:
                names = asset_names(member_bytes(archive,member,MAX_STRUCTURE),member.name)
            except Error as error:
                if error.code not in ('text_encoding','source_structure_size','source_textile'):
                    raise
                continue
            for name in names:
                if not is_textile(name):
                    claimed.update(asset.index for asset in available.get(name,()))
    return claimed


def capture_textile(path,area,source,*,scope=None):
    scope = scope if scope is not None else path.parent
    part = path.relative_to(scope).as_posix()
    original = stamp(path.stat())
    requested = {name for name in asset_names(bounded_read(path),part,source.encoding) if not is_textile(name)}
    return capture_linked_members(path,area,source,scope,original,requested)


def read_textile(path,source,sha,title):
    from .archive_intake import containers

    builder = DocumentBuilder('textile',title,sha)
    if source.kind=='file':
        part = source.member_name or path.name
        soup,prefix = render(bounded_read(path),source.encoding)
        prepare(soup,prefix,builder,part)
        return builder.doc
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        selected = next((item for item in members if item.index==source.entry),None)
        if selected is None or not is_textile(selected.name) and source.route_override!='textile':
            raise Error('source_member_missing','The saved Textile manuscript is missing.')
        soup,prefix = render(member_bytes(archive,selected,MAX_STRUCTURE),source.encoding)
        names = image_targets(soup,selected.name)-{selected.name}
        prepare(soup,prefix,builder,selected.name)
        records = {selected.name:member_location(sha,selected,'text')|{'status':'represented'}}
        matching = defaultdict(list)
        for item in members:
            if item.name in names:
                matching[item.name].append(item)
        for name in sorted(names):
            assets = matching[name]
            if len(assets)==1 and not is_textile(name):
                records[name] = member_location(sha,assets[0],'asset')|{'status':'preserved'}
            else:
                builder.doc.inventory.append({'kind':'textile_image','href':name,'source_file':selected.name,
                                              'status':'ambiguous' if len(assets)>1 else 'unavailable'})
        for ordinal,block in enumerate(builder.doc.blocks,1):
            bind_member_block(block,selected,ordinal,sha,records,target)
        builder.doc.metadata['source_resources'] = list(records.values())
        builder.doc.inventory.extend(records.values())
        return builder.doc
