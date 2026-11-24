"""Bind mdBook's authored structure and local inclusions to ordinary Markdown."""

from bisect import bisect_right
from dataclasses import dataclass
from pathlib import PurePosixPath
import posixpath
import re
import tomllib
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from .archive_intake import Member, containers
from .core import Document, Error, key
from .importers import block, markdown
from .publication_package import capture_folder, folder_files, member_bytes, member_location
from .source_bundle import MAX_MANIFEST, relative_file, relative_path, safe_name, stamp
from .text_source import physical_lines

SUMMARY = 'SUMMARY.md'
CONFIG = 'book.toml'
CHAPTER_LIMIT = 32*1024*1024
DIRECTIVE = re.compile(r'(\\)?\{\{\s*#(include|rustdoc_include|playground|playpen|title)\s+([^{}]+?)\s*\}\}')
ANCHOR = re.compile(r'ANCHOR(_END)?:\s*([\w-]+)')


@dataclass(frozen=True)
class Book:
    root: str
    source: str
    summary: Member
    config: Member | None
    metadata: dict


def joined(base,name):
    return posixpath.join(base,name) if base else name


def configuration(raw):
    try:
        config = tomllib.loads(raw.decode('utf-8-sig'))
        book = config.get('book',{})
        src = book.get('src','src')
        if not isinstance(src,str):
            raise TypeError('mdBook src must be text')
        src = posixpath.normpath(src)
        return (safe_name(src) if src!='.' else ''),config
    except (AttributeError,TypeError,ValueError,UnicodeError) as exc:
        raise Error('source_mdbook_config','Cannot read the mdBook authorship settings in book.toml.') from exc


def folder_book(root):
    config = root/CONFIG
    if config.is_file():
        config = relative_file(root,CONFIG)
        with config.open('rb') as stream:
            raw = stream.read(MAX_MANIFEST+1)
        if len(raw)>MAX_MANIFEST:
            raise Error('source_structure_size','mdBook configuration exceeds its reading budget.')
        src,_ = configuration(raw)
    else:
        src = 'src'
    if (root/joined(src,SUMMARY)).is_file():
        return src
    return None


def archive_books(archive,members):
    roots = {posixpath.dirname(item.name) for item in members if posixpath.basename(item.name)==CONFIG}
    roots.update(posixpath.dirname(posixpath.dirname(item.name)) for item in members
                 if item.name=='src/SUMMARY.md' or item.name.endswith('/src/SUMMARY.md'))
    if not roots:
        return [],set()
    names = {}
    for item in members:
        names.setdefault(item.name,[]).append(item)
    books = []
    for root in sorted(roots):
        configs = names.get(joined(root,CONFIG),[])
        if len(configs)>1:
            raise Error('ambiguous_package_member','An mdBook archive repeats its book.toml path.')
        config = configs[0] if configs else None
        try:
            src,metadata = configuration(member_bytes(archive,config,MAX_MANIFEST)) if config else ('src',{})
        except Error:
            if joined(root,'src/SUMMARY.md') in names:
                raise
            continue
        directory = joined(root,src).rstrip('/')
        summaries = names.get(joined(directory,SUMMARY),[])
        if len(summaries)>1:
            raise Error('ambiguous_package_member','An mdBook archive repeats its SUMMARY.md path.')
        if summaries:
            books.append(Book(root,directory,summaries[0],config,metadata))
    source_roots = {PurePosixPath(book.source) for book in books}
    configuration_entries = {book.config.index for book in books if book.config}
    owned = {item.index for item in members if item.index in configuration_entries
             or any(parent in source_roots for parent in PurePosixPath(item.name).parents)}
    return books,owned


def local_path(reference,current,root):
    try:
        url = urlsplit(reference)
        if url.scheme or url.netloc or url.query:
            return None
        file = unquote(url.path,errors='strict')
        path = safe_name(posixpath.normpath(joined(posixpath.dirname(current),file))) if file else current
        if root and not path.startswith(root+'/'):
            return None
        return path
    except (Error,UnicodeError,ValueError):
        return None


def summary_rows(raw):
    tokens = MarkdownIt('commonmark').parse(raw)
    depth,part,initial = 0,None,True
    parents = []
    for index, token in enumerate(tokens):
        if token.type in ('bullet_list_open','ordered_list_open'):
            depth += 1
        elif token.type in ('bullet_list_close','ordered_list_close'):
            depth -= 1
        elif token.type=='heading_open' and token.tag=='h1':
            if not initial:
                part = tokens[index+1].content
            initial = False
        elif token.type=='inline':
            label, target = [],None
            for child in token.children or ():
                if child.type=='link_open':
                    label,target = [],child.attrGet('href')
                elif child.type=='link_close' and target is not None:
                    title = ''.join(label).strip()
                    parents[depth:] = []
                    yield {'label':title,'target':target,'depth':depth,'parents':parents[1:],
                           'part':part,'summary_line':token.map[0]+1}
                    if depth:
                        while len(parents)<depth:
                            parents.append('')
                        parents.append(title)
                    target = None
                    initial = False
                elif target is not None and child.type in ('text','code_inline','image'):
                    label.append(child.content)


def utf8(raw, name):
    try:
        return raw.decode('utf-8-sig')
    except UnicodeError as exc:
        raise Error('text_encoding',f'mdBook member is not valid UTF-8: {name}.') from exc


def include_ranges(text, spec):
    selector = spec.split(':')
    spans = []

    def retain(line):
        if spans and spans[-1][1]==line.start():
            spans[-1] = (spans[-1][0],line.end())
        else:
            spans.append((line.start(),line.end()))

    if len(selector)<=2 and all(not number or number.isascii() and number.isdecimal() for number in selector):
        def number(digits):
            digits = digits.lstrip('0')
            return int(digits or '0') if len(digits)<10 else len(text)+1
        start = max(number(selector[0])-1,0) if selector[0] else 0
        stop = number(selector[1]) if len(selector)>1 and selector[1] else start+1 if spec and len(selector)==1 else len(text)+1
        if not spec:
            spans = [(0,len(text))] if text else []
        else:
            for index,line in enumerate(physical_lines(text)):
                if index>=stop:
                    break
                if index>=start:
                    retain(line)
    else:
        found = False
        for line in physical_lines(text):
            anchor = ANCHOR.search(line[0])
            if not anchor and found:
                retain(line)
            elif anchor and anchor[2]==spec:
                if anchor[1] and found:
                    break
                if not anchor[1]:
                    found = True
        if not found:
            raise Error('source_mdbook_include',f'The include anchor {spec!r} is missing.')
    if spans:
        start,end = spans[-1]
        ending = 2 if text[max(start,end-2):end]=='\r\n' else int(text[end-1:end] in ('\r','\n'))
        spans[-1] = start,end-ending
    return spans


class Expansion:
    def __init__(self,read,root):
        self.read = read
        self.root = root
        self.parts = []
        self.spans = []
        self.size = 0
        self.dependencies = set()
        self.changed = False

    def add(self,name,source,start,end,value=None):
        text = source[start:end] if value is None else value
        if not text:
            return
        stop = self.size+len(text)
        if stop>CHAPTER_LIMIT:
            raise Error('source_structure_size','Expanded mdBook chapter exceeds its reading budget.')
        self.parts.append(text)
        self.spans.append((self.size,stop,name,start,end))
        self.size = stop

    def expand(self,name,text,ranges,stack):
        if name in stack or len(stack)>=32:
            raise Error('source_mdbook_include',f'Circular or too deeply nested mdBook include at {name}.')
        for start,end in ranges:
            self.expand_range(name,text,start,end,(*stack,name))

    def expand_range(self,name,text,start,end,stack):
        cursor = start
        for command in DIRECTIVE.finditer(text,start,end):
            self.changed = True
            self.add(name,text,cursor,command.start())
            cursor = command.end()
            escaped,kind,args = command.groups()
            if escaped:
                self.add(name,text,command.start()+1,cursor)
                continue
            if kind=='title':
                continue
            filename,_,spec = args.split()[0].partition(':')
            target = local_path(filename,name,self.root)
            if not target or target==name:
                raise Error('source_mdbook_include',f'Cannot resolve local mdBook include {filename!r} in {name}.')
            self.dependencies.add(target)
            included = self.read(target)
            ranges = include_ranges(included,spec)
            if kind in ('playground','playpen'):
                self.add(name,text,command.start(),cursor,'```rust\n')
            self.expand(target,included,ranges,stack)
            if kind in ('playground','playpen'):
                self.add(name,text,command.start(),cursor,'\n```')
        self.add(name,text,cursor,end)


def chapter_text(read,root,name,*,includes):
    text = read(name)
    expansion = Expansion(read,root)
    if includes:
        expansion.expand(name,text,[(0,len(text))],())
    else:
        expansion.add(name,text,0,len(text))
    return expansion


def capture_mdbook(root,target,*,container):
    prefix = root.relative_to(container).as_posix() if root!=container else ''
    src = folder_book(root)
    if src is None:
        raise Error('source_mdbook','The mdBook source directory has no SUMMARY.md.')
    directory = joined(prefix,src)
    tree = relative_path(container,directory) if directory else container
    settings = joined(prefix,CONFIG)
    generated = {'.git','.hg','.svn'}
    if (root/CONFIG).is_file():
        _,data = configuration(relative_file(root,CONFIG).read_bytes())
        build = data.get('build',{})
        output = build.get('build-dir','book') if isinstance(build,dict) else 'book'
        if isinstance(output,str):
            if where:=local_path(output,settings,directory):
                generated.add(where.removeprefix(directory+'/') if directory else where)

    def inventory():
        listed = {joined(directory,name):proof for name,proof in folder_files(tree,exclude=generated).items()}
        if (root/CONFIG).is_file():
            listed[settings] = stamp(relative_file(container,settings).stat())
        return listed

    def files():
        listed = inventory()
        def read(name):
            path = relative_file(container,name)
            listed[name] = stamp(path.stat())
            if listed[name][2]>CHAPTER_LIMIT:
                raise Error('source_structure_size',f'mdBook member exceeds its reading budget: {name}.')
            return utf8(path.read_bytes(),name)
        summary = joined(directory,SUMMARY)
        for row in summary_rows(read(summary)):
            if not row['target'] or not (name:=local_path(row['target'],summary,directory)):
                continue
            try:
                chapter_text(read,'',name,includes=True)
            except Error:
                # The immutable import reports this failure while retaining healthy chapters.
                continue
        return listed

    def verify(listed):
        current = inventory()
        for name in listed.keys()-current.keys():
            current[name] = stamp(relative_file(container,name).stat())
        return current

    capture_folder(container,target,files,verify=verify)


def add_chapter(document, label, occurrence, member, content, resources):
    chapter = markdown(content.encode('utf-8'),collect_resources=True,first_note_definition=True)
    ids = {item.id:'b'+key([member.name,occurrence,item.id])[:16] for item in chapter.blocks}
    destinations = {'source.html#'+source:'source.html#'+target for source,target in ids.items()}
    for item in chapter.blocks:
        item.id = ids[item.id]
        item.chapter = label
        for link in item.links:
            if 'destinations' in link:
                link['destinations'] = [destinations.get(destination,destination) for destination in link['destinations']]
            if 'destination' in link:
                link['destination'] = destinations.get(link['destination'],link['destination'])
        for ref in item.locator.pop('resource_references',[]):
            target = local_path(ref['href'],member.name,'')
            resource = resources.get(target)
            if resource and target!=member.name:
                resource['status'] = 'referenced'
                item.links.append({'kind':'source_resource','href':ref['href'],'label':ref.get('label') or target,
                                   'destinations':['source.html#resource-'+resource['location_id']]})
        item.locator.update(member=member.name,archive_entry=member.index,chapter_id=occurrence,
                            location_id=key([document.source_sha256,member.index,item.id])[:20])
    document.blocks.extend(chapter.blocks)
    return chapter.blocks


def bind_expansion(blocks, expansion, members, sha):
    if not expansion.changed:
        return
    ends = [span[1] for span in expansion.spans]
    for item in blocks:
        locator = item.locator
        start,end = locator.get('start'),locator.get('end')
        if start is None or end is None:
            continue
        origins = []
        for index in range(bisect_right(ends,start),len(ends)):
            lower,upper,path,begin,stop = expansion.spans[index]
            if lower>=end:
                break
            member = members[path][0]
            if upper-lower==stop-begin:
                begin,stop = begin+max(start-lower,0),stop-max(upper-end,0)
            origins.append({'member':path,'archive_entry':member.index,'start':begin,'end':stop,
                            'location_id':member_location(sha,member,'include')['location_id']})
        locator['source_spans'] = origins
        locator['expanded_range'] = [locator.pop('start'),locator.pop('end')]
        locator.pop('lines',None)


def chapter_issue(document,row,summary,reason):
    location = member_location(document.source_sha256,summary,'summary')
    location.update(format='markdown',lines=[row['summary_line']]*2,chapter_id=row['occurrence'],
                    path=row.get('member'),location_id=key([document.source_sha256,row['occurrence']])[:20])
    document.blocks.append(block('',location,'unreadable',row['label'],issue=reason))


def read_mdbook(path,source,sha,title):
    with containers(path,source.kind) as archive:
        members = [entry for entry in archive.members() if not entry.name.startswith('__MACOSX/')]
        books,_ = archive_books(archive,members)
        selected = next((book for book in books if book.summary.index==source.entry),None)
        if selected is None:
            raise Error('source_mdbook','The selected mdBook summary could not be located in the saved source.')
        return read_book(archive,members,selected,sha,title)


def read_book(archive,entries,book,sha,title):
    config = book.metadata.get('book',{})
    named = config.get('title') or PurePosixPath(book.root).name or title
    if not isinstance(named,str):
        raise Error('source_mdbook_config','The mdBook title must be text.')
    document = Document(named,'markdown',sha,[])
    authors = config.get('authors')
    if isinstance(authors,list) and all(isinstance(author,str) for author in authors) and authors:
        document.metadata['author'] = ', '.join(authors)
    language = config.get('language')
    if isinstance(language,str):
        document.metadata['language'] = language
    text = utf8(member_bytes(archive,book.summary,MAX_MANIFEST),book.summary.name)
    by_name = {}
    for member in entries:
        by_name.setdefault(member.name,[]).append(member)
    def entry_for(name):
        items = by_name.get(name,[])
        if len(items)!=1:
            detail = 'repeated' if items else 'missing'
            raise Error('source_mdbook_member',f'The mdBook member is {detail}: {name}.')
        return items[0]
    def read(name):
        return utf8(member_bytes(archive,entry_for(name),CHAPTER_LIMIT),name)
    resources = {name:member_location(sha,matches[0],'package_member')|{'status':'unlisted'}
                 for name,matches in by_name.items() if len(matches)==1 and
                 ((not book.source or name.startswith(book.source+'/')) or matches[0]==book.config)}
    resources[book.summary.name].update(role='descriptor',status='preserved')
    if book.config:
        resources[book.config.name].update(role='descriptor',status='preserved')
    build = book.metadata.get('build',{})
    preprocess = book.metadata.get('preprocessor',{})
    defaults = build.get('use-default-preprocessors',True) if isinstance(build,dict) else True
    if isinstance(preprocess,dict):
        custom = [name for name,rule in preprocess.items() if not isinstance(rule,dict) or 'command' in rule
                  or name not in ('links','index')]
        if custom:
            document.warnings.append('mdBook declares custom preprocessors. Hamr reads local source content without running them: '+', '.join(custom)+'.')
        defaults = defaults or 'links' in preprocess
    summary = list(summary_rows(text))
    if not summary:
        raise Error('source_mdbook','SUMMARY.md does not declare any chapter links.')
    for number,row in enumerate(summary,1):
        row.update(occurrence=f'mdbook:{number}',status='draft')
        document.inventory.append(row)
        if not row['target']:
            continue
        name = local_path(row['target'],book.summary.name,book.source)
        row['member'] = name or row['target']
        try:
            if not name or name==book.summary.name:
                raise Error('source_mdbook_member','The chapter reference must select a local source file beside the summary.')
            member = entry_for(name)
            row['archive_entry'] = member.index
            expansion = chapter_text(read,'',name,includes=defaults)
            for dependency in sorted(expansion.dependencies):
                resource = resources.setdefault(dependency,member_location(sha,entry_for(dependency),'include'))
                resource.update(status='referenced')
            blocks = add_chapter(document,row['label'],row['occurrence'],member,''.join(expansion.parts),resources)
            bind_expansion(blocks,expansion,by_name,sha)
            row['blocks'] = [item.id for item in blocks]
            row['status'] = 'needs_review' if any(item.issue for item in blocks) else 'represented' if blocks else 'empty'
            resource = resources.setdefault(name,member_location(sha,member,'text'))
            resource.update(role='text',status='represented')
        except Error as error:
            row.update(status='failed',reason=str(error))
            chapter_issue(document,row,book.summary,str(error))
    document.metadata['source_resources'] = list(resources.values())
    return document
