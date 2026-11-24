"""Follow GitBook space manifests and preserve their ordered Markdown sources."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import posixpath
import re
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

from .archive_intake import containers
from .core import Error
from .markdown_publication import MarkdownPublication, chapter_issue, joined, local_path, utf8
from .publication_package import capture_folder, folder_files, member_bytes
from .source_bundle import MAX_MANIFEST, natural, relative_file, relative_path, safe_name, stamp

CONFIG = '.gitbook.yaml'
SITE = 'gitbook-docs.yaml'
SUMMARY = 'SUMMARY.md'
MARKDOWN = ('.md','.markdown')
GENERATED = frozenset(('.git','.hg','.svn'))


@dataclass(frozen=True)
class Layout:
    root: str
    source: str
    home: str
    summary: str
    config: str
    site: str = ''
    key: str = ''
    title: str = ''
    explicit_home: bool = False
    language: str = ''


def path_under(value,base,name):
    if not isinstance(value,str) or not value:
        raise Error('source_gitbook_config',f'GitBook {name} must be a local path.')
    path = posixpath.normpath(joined(base,value))
    if path=='.' and not base:
        return ''
    if base and path!=base and not path.startswith(base+'/'):
        raise Error('source_gitbook_config',f'GitBook {name} leaves its declared root: {value!r}.')
    return safe_name(path)


def settings(raw, name):
    import yaml

    class Loader(yaml.SafeLoader):
        def construct_mapping(self,node,deep=False):
            keys = set()
            for key,_ in node.value:
                if not isinstance(key,yaml.ScalarNode):
                    raise ValueError('expected scalar manifest keys')
                if key.value in keys:
                    raise ValueError(f'key {key.value!r} is repeated')
                keys.add(key.value)
            return super().construct_mapping(node,deep=deep)

    try:
        data = yaml.load(raw,Loader=Loader)
        if data is None:
            return {}
        if isinstance(data,dict):
            return data
        raise ValueError('expected a YAML mapping')
    except (yaml.YAMLError,UnicodeError,RecursionError,ValueError,TypeError) as exc:
        raise Error('source_gitbook_config',f'Cannot read GitBook configuration {name}: {exc}.') from exc


def layout(root,raw,*,site='',key='',title='',language=''):
    config = joined(root,CONFIG)
    data = settings(raw,config) if raw is not None else {}
    src = path_under(data.get('root','.'),root,'content root')
    structure = data.get('structure',{})
    if not isinstance(structure,dict):
        raise Error('source_gitbook_config','GitBook structure must map the home and summary paths.')
    home = path_under(structure.get('readme','README.md'),src,'home page')
    summary = path_under(structure.get('summary',SUMMARY),src,'summary')
    return Layout(root,src,home,summary,config if raw is not None else '',site,key,title,'readme' in structure,language)


def mapped_spaces(raw,name,repository):
    data = settings(raw,name)
    site = data.get('site')
    if not isinstance(site,dict) or not isinstance(site.get('structure'),list):
        raise Error('source_gitbook_config',f'{name} must declare a site.structure list.')
    parent = posixpath.dirname(name)
    pending = list(reversed(site['structure']))
    groups, keys, roots = set(),set(),set()
    while pending:
        node = pending.pop()
        if not isinstance(node,dict):
            raise Error('source_gitbook_config','GitBook site entries must be mappings.')
        if node.get('type') in ('section','section-group'):
            children = node.get('children',[])
            if not isinstance(children,list) or id(node) in groups:
                raise Error('source_gitbook_config','GitBook site section groups must have an acyclic list of children.')
            groups.add(id(node))
            pending.extend(reversed(children))
            continue
        content = node.get('content')
        if not isinstance(content,dict) or content.get('directory') is None:
            continue
        directory = content['directory']
        if not isinstance(directory,str):
            raise Error('source_gitbook_config','GitBook content.directory must be a local path.')
        absolute = directory.startswith('/')
        root = path_under((directory[1:] or '.') if absolute else directory,
                          repository if absolute else parent,'mapped space')
        key = node.get('key')
        if not isinstance(key,str) or not key or key in keys or root in roots:
            raise Error('source_gitbook_config','Mapped GitBook spaces need distinct keys and directories.')
        keys.add(key)
        roots.add(root)
        language = content.get('language')
        yield root,key,str(node.get('title') or key),language if isinstance(language,str) else ''


def folder_marker(path):
    summary = path/SUMMARY
    if not summary.is_file():
        return False
    with relative_file(path,SUMMARY).open('rb') as stream:
        raw = stream.read(MAX_MANIFEST+1)
    if len(raw)>MAX_MANIFEST:
        raise Error('source_structure_size','GitBook navigation exceeds its reading budget.')
    text = utf8(raw,SUMMARY,'GitBook')
    return has_local_chapter(text,SUMMARY,'')


def folder_layouts(root):
    def read(name):
        path = root/name
        if not path.is_file():
            return None
        with relative_file(root,name).open('rb') as stream:
            data = stream.read(MAX_MANIFEST+1)
        if len(data)>MAX_MANIFEST:
            raise Error('source_structure_size',f'GitBook configuration exceeds its reading budget: {name}.')
        return data
    if (data:=read(SITE)) is None:
        return [layout('',read(CONFIG))]
    return [layout(path,read(joined(path,CONFIG)),site=SITE,key=key,title=title,language=language)
            for path,key,title,language in mapped_spaces(data,SITE,'') if (root/path).is_dir()]


def folder_choices(root):
    return [(book.key or 'book',book.title or root.name) for book in folder_layouts(root)]


def archive_spaces(archive,members,*,declared=True):
    names = {}
    markers = {CONFIG,SITE} if declared else {SUMMARY}
    if not any(posixpath.basename(item.name) in markers for item in members):
        return [],set()
    for item in members:
        names.setdefault(item.name,[]).append(item)

    def unique(name):
        matches = names.get(name,())
        if len(matches)>1:
            raise Error('ambiguous_package_member',f'The GitBook archive repeats {name}.')
        return matches[0] if matches else None

    def read(name):
        item = unique(name)
        return member_bytes(archive,item,MAX_MANIFEST) if item else None

    books,owned = [],set()
    scopes = set()
    candidates = []
    if declared:
        repository = posixpath.commonpath([posixpath.dirname(item.name) for item in members])
        for site in sorted(name for name in names if posixpath.basename(name)==SITE):
            for root,key,title,language in mapped_spaces(read(site),site,repository):
                candidates.append(layout(root,read(joined(root,CONFIG)),site=site,key=key,title=title,language=language))
                scopes.add(root)
        candidates.extend(layout(posixpath.dirname(name),read(name)) for name in sorted(names)
                          if posixpath.basename(name)==CONFIG and posixpath.dirname(name) not in scopes)
    else:
        for name in sorted(names):
            if posixpath.basename(name)==SUMMARY:
                root = posixpath.dirname(name)
                if has_local_chapter(utf8(read(name),name,'GitBook'),name,root):
                    candidates.append(layout(root,None))

    for candidate in candidates:
        exclusions = nested_spaces(candidate,candidates)
        local = [item for item in members if within(item.name,candidate.source)
                 and not any(within(item.name,root) for root in exclusions)]
        chapters = [item for item in local if markdown_page(item.name,candidate) and item.name!=candidate.summary]
        summary = unique(candidate.summary)
        home = unique(candidate.home)
        config = unique(candidate.config) if candidate.config else None
        site = unique(candidate.site) if candidate.site else None
        # A selected snapshot retains the complete site manifest. Other
        # mappings do not own a publication unless their source is present.
        if not (summary or chapters) and not config:
            continue
        descriptor = summary or config or home or chapters[0]
        books.append((candidate,descriptor))
        owned.update(item.index for item in local)
        owned.update(item.index for item in (site,config) if item)
    return books,owned


def within(name,root):
    return name.startswith(root+'/') if root else True


def nested_spaces(book,spaces):
    return {space.root for space in spaces if space.root!=book.root and within(space.root,book.source)}


def markdown_page(name,book):
    if not within(name,book.source):
        return False
    relative = name.removeprefix(book.source+'/') if book.source else name
    path = PurePosixPath(relative)
    return path.suffix.casefold() in MARKDOWN and not any(part.startswith('.') for part in path.parts)


def summary_rows(text):
    part = None
    depth = 0
    parents = []
    tokens = MarkdownIt('commonmark').parse(text)
    for index,token in enumerate(tokens):
        if token.type in ('bullet_list_open','ordered_list_open'):
            depth += 1
        elif token.type in ('bullet_list_close','ordered_list_close'):
            depth -= 1
        elif token.type=='heading_open' and token.tag=='h2' and depth==0:
            part = tokens[index+1].content
        elif token.type=='fence' and depth and (component:=re.search(r'(?m)^\s*type:\s*(builtin:[\w-]+)\s*$',token.content)):
            yield {'label':parents[-1] if parents else 'Generated navigation','target':None,
                   'component':component[1],'depth':depth,'parents':parents[1:],
                   'part':part,'summary_line':token.map[0]+1}
        elif token.type=='inline' and depth:
            title,href = [],None
            for child in token.children or ():
                if child.type=='link_open':
                    title,href = [],child.attrGet('href')
                    link_title = child.attrGet('title')
                elif child.type=='link_close' and href is not None:
                    label = ''.join(title).strip()
                    parents[depth:] = []
                    yield {'label':label,'target':href,'depth':depth,'parents':parents[1:],
                           'part':part,'summary_line':token.map[0]+1,
                           **({'navigation_label':link_title} if link_title else {})}
                    while len(parents)<depth:
                        parents.append('')
                    parents.append(label)
                    href = None
                elif href is not None and child.type in ('text','code_inline','image'):
                    title.append(child.content)


def has_local_chapter(text,summary,root):
    for row in summary_rows(text):
        name = local_path(row['target'],summary,root) if row['target'] else None
        if name and name!=summary and PurePosixPath(name).suffix.casefold() in MARKDOWN:
            return True
    return False


def chapter_rows(publication,book):
    if book.summary in publication.members:
        return list(summary_rows(publication.read(book.summary,MAX_MANIFEST))),'summary'
    names = [name for name in publication.members if markdown_page(name,book)]
    def order(name):
        path = PurePosixPath(name)
        return (*((1,natural(part)) for part in path.parts[:-1]),
                (0 if path.stem.casefold()=='readme' else 1,natural(path.name)))
    rows = [{'label':PurePosixPath(name).stem,'target':name,'depth':len(PurePosixPath(
                 name.removeprefix(book.source+'/') if book.source else name).parts),
             'parents':list(PurePosixPath(posixpath.relpath(name,book.source or '.')).parts[:-1]),'part':None,
             'member':name} for name in sorted(names,key=order)]
    return rows,'inferred'


def capture_gitbook(root,target,*,entry=1):
    books = folder_layouts(root)
    if not entry or entry>len(books):
        raise Error('source_changed','The selected GitBook space disappeared before capture.')
    selected = books[entry-1]
    source = relative_path(root,selected.source) if selected.source else root
    excluded = GENERATED | {posixpath.relpath(path,selected.source or '.') for path in nested_spaces(selected,books)}
    def files():
        if not source.is_dir():
            raise Error('source_gitbook_config','The declared GitBook content directory does not exist.')
        data = {joined(selected.source,name):value for name,value in folder_files(source,exclude=excluded).items()}
        for name in (selected.config,selected.site):
            if name:
                data[name] = stamp(relative_file(root,name).stat())
        return data
    capture_folder(root,target,files)


def read_gitbook(path,source,sha,title):
    with containers(path,source.kind) as archive:
        members = [item for item in archive.members() if not item.name.startswith('__MACOSX/')]
        choices,owned = archive_spaces(archive,members)
        if not any(member.index==source.entry for _,member in choices):
            choices,_ = archive_spaces(archive,[item for item in members if item.index not in owned],declared=False)
        book,descriptor = next(((book,member) for book,member in choices if member.index==source.entry),(None,None))
        if book is None:
            raise Error('source_gitbook','The saved GitBook space descriptor is no longer available.')
        excluded = nested_spaces(book,(space for space,_ in choices))
        return read_publication(archive,[item for item in members if not any(within(item.name,root) for root in excluded)],
                                book,descriptor,sha,title)


def read_publication(archive,members,book,descriptor,sha,title):
    from .gitbook_markup import markup

    descriptors = [item for item in members if item.name in (book.summary,book.config,book.site)]
    publication = MarkdownPublication(archive,members,book.source,sha,
                                      book.title or PurePosixPath(book.root).name or title,'GitBook',descriptors)
    document = publication.document
    if book.language:
        document.language = book.language
    rows,basis = chapter_rows(publication,book)
    seen = set()
    for row in rows:
        target = row['target']
        row['member'] = row.get('member') or (local_path(target,book.summary,book.source) if target else None)
    home = next((row for row in rows if row['member']==book.home),None)
    if home:
        rows.remove(home)
        rows.insert(0,home)
    elif book.home in publication.members or book.explicit_home:
        rows.insert(0,{'label':'Introduction','target':book.home,'member':book.home,'depth':1,'parents':[],'part':None})
    if not rows:
        raise Error('source_gitbook','The GitBook space contains no local Markdown pages.')
    document.metadata['gitbook'] = {'navigation':basis,'content_root':book.source,'home':book.home,
                                   **({'space':book.key} if book.key else {})}
    for number,row in enumerate(rows,1):
        row.update(occurrence=f'gitbook:{number}',status='draft')
        document.inventory.append(row)
        if component:=row.get('component'):
            reason=f'GitBook navigation declares {component} content generated outside these Markdown pages.'
            row.update(status='generated source unavailable',reason=reason)
            chapter_issue(document,row,descriptor,reason)
            continue
        if not row['target']:
            continue
        name = row['member']
        if name in seen:
            row['status'] = 'duplicate navigation'
            continue
        try:
            try:
                target = urlsplit(row['target'])
            except ValueError as exc:
                raise Error('source_gitbook_member',f'Invalid navigation address: {row["target"]!r}.') from exc
            if target.scheme in ('http','https') or target.netloc:
                row['status'] = 'external'
                continue
            if not name or name==book.summary or not markdown_page(name,book):
                raise Error('source_gitbook_member','The GitBook chapter must address a local Markdown page inside the content root.')
            member = publication.entry(name)
            seen.add(name)
            row['archive_entry'] = member.index
            publication.add(row,member,publication.read(name),configure=markup)
        except Error as error:
            row.update(status='failed',reason=str(error))
            chapter_issue(document,row,descriptor,str(error))
    return publication.finish()
