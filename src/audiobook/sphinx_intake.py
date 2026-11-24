"""Read declarative Sphinx projects in the order of their authored toctrees."""

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath
import posixpath
import re
from urllib.parse import urlsplit

from .archive_intake import containers
from .core import Document, Error, key
from .importers import block
from .publication_package import capture_folder, folder_files, member_bytes, member_location
from .source_bundle import MAX_MANIFEST, relative_file, safe_name

CONFIG = 'conf.py'
RST = ('.rst','.rest')
BUILD_DIRS = ('.git','.hg','.svn','_build','__pycache__')
VIRTUAL = {'genindex','modindex','py-modindex','search'}
SETTINGS = {'root_doc','master_doc','source_suffix','include_patterns','exclude_patterns',
            'project','language','source_encoding','rst_prolog','rst_epilog'}


@dataclass(frozen=True)
class Layout:
    root: str
    config: str
    start: str
    suffixes: tuple
    title: str = ''
    language: str = 'en'
    include: tuple = ('**',)
    exclude: tuple = ()
    encoding: str = 'utf-8-sig'
    preamble: str = ''
    postamble: str = ''
    generated: tuple = ()


def glob_match(name,pattern):
    tokens = re.split(r'(\*\*/|\*\*|\*|\?|\[[^\]]+\])',pattern)
    pieces = {'**/':'(?:.*/)?','**':'.*','*':'[^/]*','?':'[^/]'}
    for i,token in enumerate(tokens):
        if token in pieces:
            tokens[i] = pieces[token]
        elif token.startswith('[') and token.endswith(']'):
            values = token[1:-1]
            sign = '^' if values.startswith('!') else ''
            values = values[1:] if sign else values
            tokens[i] = '(?!/)['+sign+values.replace('\\','\\\\')+']' if values else re.escape(token)
        else:
            tokens[i] = re.escape(token)
    try:
        return re.fullmatch(''.join(tokens),name) is not None
    except re.error:
        return name==pattern


def settings(raw,name):
    try:
        parsed = ast.parse(raw,filename=name)
    except (SyntaxError,UnicodeError,ValueError,RecursionError) as exc:
        raise Error('source_sphinx_config',f'Cannot parse the Sphinx settings in {name}.') from exc
    data,dynamic = {},set()
    for node in parsed.body:
        if isinstance(node,ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target,ast.Name)]
        elif isinstance(node,(ast.AnnAssign,ast.AugAssign)) and isinstance(node.target,ast.Name):
            targets = [node.target.id]
        else:
            continue
        for field in SETTINGS.intersection(targets):
            try:
                if isinstance(node,ast.AugAssign):
                    raise ValueError('requires evaluation')
                data[field] = ast.literal_eval(node.value)
                dynamic.discard(field)
            except (ValueError,TypeError,RecursionError):
                dynamic.add(field)
    return data,dynamic


def patterns(value):
    if not isinstance(value,(tuple,list)) or not all(isinstance(item,str) for item in value):
        raise Error('source_sphinx_config','Sphinx include and exclude patterns must be lists of text.')
    return tuple(value)


def resolve(reference,current,root):
    if reference.startswith('/'):
        raw = posixpath.join(root,reference[1:])
    else:
        raw = posixpath.join(posixpath.dirname(current),reference)
    found = safe_name(posixpath.normpath(raw))
    if root and not found.startswith(root+'/'):
        raise Error('source_sphinx_member',f'{reference!r} leaves the source project.')
    return found


def layout(root,raw):
    config = posixpath.join(root,CONFIG)
    data,dynamic = settings(raw,config)
    start = data.get('root_doc',data.get('master_doc','index'))
    if not isinstance(start,str) or not start:
        raise Error('source_sphinx_config','The Sphinx root document must be a local name.')
    suffixes = data.get('source_suffix','.rst')
    if isinstance(suffixes,str):
        suffixes = (suffixes,)
    if isinstance(suffixes,dict):
        suffixes = [suffix for suffix,name in suffixes.items() if name in ('restructuredtext','rst')]
    suffixes = patterns(suffixes)
    if not all(re.fullmatch(r'\.[A-Za-z0-9_-]{1,16}',suffix) for suffix in suffixes):
        raise Error('source_sphinx_config','A Sphinx source suffix is not a file extension.')
    preamble,postamble = data.get('rst_prolog') or '',data.get('rst_epilog') or ''
    if not isinstance(preamble,str) or not isinstance(postamble,str):
        raise Error('source_sphinx_config','Sphinx reStructuredText prolog and epilog settings must be text.')
    if 'master_doc' in dynamic and 'root_doc' in data and 'root_doc' not in dynamic:
        dynamic.remove('master_doc')
    title,lang,encoding = (data.get(field) for field in ('project','language','source_encoding'))
    return Layout(root,config,resolve('/'+start,config,root),suffixes,title if isinstance(title,str) else '',
                  lang if isinstance(lang,str) else 'en',patterns(data.get('include_patterns',['**'])),
                  patterns(data.get('exclude_patterns',[])),encoding if isinstance(encoding,str) else 'utf-8-sig',
                  preamble,postamble,tuple(sorted(dynamic)))


def root_candidates(book):
    return tuple(book.start+suffix for suffix in book.suffixes)


def folder_book(path):
    if not (path/CONFIG).is_file():
        return None
    # Ordinary Python source folders also use conf.py. Require a local source page.
    if not any(candidate.is_file() for candidate in path.rglob('*.rst')) and not any(
            candidate.is_file() for candidate in path.rglob('*.rest')):
        return None
    with relative_file(path,CONFIG).open('rb') as stream:
        raw = stream.read(MAX_MANIFEST+1)
    if len(raw)>MAX_MANIFEST:
        raise Error('source_structure_size','Sphinx configuration exceeds the reading limit.')
    book = layout('',raw)
    return book if any((path/name).is_file() for name in root_candidates(book)) else None


def capture_sphinx(root,target):
    capture_folder(root,target,lambda:folder_files(root,exclude=BUILD_DIRS))


def archive_books(archive,members):
    configs = [item for item in members if PurePosixPath(item.name).name==CONFIG]
    if not configs:
        return [],set()
    names = {item.name for item in members}
    roots = []
    for config in configs:
        root = posixpath.dirname(config.name)
        prefix = root+'/' if root else ''
        if not any(name.startswith(prefix) and name.endswith(RST) for name in names):
            continue
        book = layout(root,member_bytes(archive,config,MAX_MANIFEST))
        if any(candidate in names for candidate in root_candidates(book)):
            roots.append((book,config))
    if len({book.config for book,_ in roots})!=len(roots):
        raise Error('ambiguous_package_member','The source archive repeats a Sphinx configuration.')
    owned = {item.index for item in members if any(not book.root or item.name.startswith(book.root+'/') for book,_ in roots)}
    return roots,owned


def key_for(logical,occurrence,item):
    return 'b'+key([logical,occurrence,item])[:16]


def read_sphinx(path,source,sha,title):
    with containers(path,source.kind) as archive:
        members = [item for item in archive.members() if not item.name.startswith('__MACOSX/')]
        roots,_ = archive_books(archive,members)
        selected = next(((book,config) for book,config in roots if config.index==source.entry),None)
        if not selected:
            raise Error('source_sphinx','The selected Sphinx project no longer has a readable root document.')
        book,config = selected
        prefix = book.root+'/' if book.root else ''
        exclusions = tuple(other.root+'/' for other,_ in roots if other.root!=book.root and other.root.startswith(prefix))
        local = [item for item in members if item.name.startswith(prefix) and not item.name.startswith(exclusions)]
        return SphinxPublication(archive,local,book,config,sha,title).read()


class SphinxPublication:
    def __init__(self,archive,members,book,config,sha,title):
        self.archive,self.book = archive,book
        self.doc = Document(book.title or PurePosixPath(book.root).name or title,'rst',sha,[],book.language)
        self.members = {}
        self.resources = {}
        self.pages = {}
        self.seen = set()
        for item in members:
            self.members.setdefault(item.name,[]).append(item)
            relative = item.name.removeprefix(book.root+'/') if book.root else item.name
            if any(folder in BUILD_DIRS for folder in PurePosixPath(relative).parts[:-1]):
                continue
            for suffix in book.suffixes:
                if item.name.endswith(suffix) and any(glob_match(relative,pat) for pat in book.include) and not any(
                        glob_match(relative,pat) or relative.startswith(pat.rstrip('/')+'/') for pat in book.exclude):
                    self.pages.setdefault(item.name.removesuffix(suffix),[]).append(item)
        for name,items in self.members.items():
            if len(items)==1:
                self.resources[name] = member_location(sha,items[0],'package_member')|{'status':'unlisted'}
        self.resources[config.name].update(role='descriptor',status='preserved')
        self.config = config

    def member(self,name):
        options = self.members.get(name,())
        if len(options)!=1:
            raise Error('source_sphinx_member',f'The Sphinx member is {"missing" if not options else "repeated"}: {name}.')
        return options[0]

    def include(self,current,name):
        target = resolve(name,current,self.book.root)
        member = self.member(target)
        self.resources[target]['status'] = 'included'
        return target,member_bytes(self.archive,member,32*1024*1024)

    def issue(self,label,occurrence,location,reason):
        self.doc.blocks.append(block('',location|{'chapter_id':occurrence},'unreadable',label,issue=reason))

    def read(self):
        if self.book.generated:
            reason = 'Sphinx needs applied project settings for '+', '.join(self.book.generated)+'. Open the built single-file HTML to use those readings.'
            self.issue(self.doc.title,'sphinx:config',self.resources[self.config.name],reason)
        self.chapter(self.book.start,{},(),0)
        self.doc.metadata['sphinx'] = {'root':self.book.root,'start':self.book.start,'builder':'singlehtml'}
        self.doc.metadata['source_resources'] = list(self.resources.values())
        return self.doc

    def chapter(self,name,row,ancestors,depth):
        from .rst_intake import rst_blocks

        self.doc.inventory.append(row)
        row.update(path=name,depth=depth)
        if name in self.seen:
            row['status'] = 'duplicate navigation'
            return
        if len(ancestors)>=64:
            self.chapter_failure(row,'The Sphinx table of contents exceeds the maximum project depth.')
            return
        members = self.pages.get(name,())
        if len(members)!=1:
            if name in VIRTUAL:
                row['status'] = 'generated navigation'
            else:
                self.chapter_failure(row,f'The Sphinx chapter is {"missing or excluded" if not members else "ambiguous"}: {name}.')
            return
        member, = members
        try:
            text = member_bytes(self.archive,member,32*1024*1024).decode(self.book.encoding)
            prepared = rst_blocks(text,member.name,self.doc.source_sha256,include=self.include,sphinx=True,
                                  language=self.book.language,preamble=self.book.preamble,
                                  postamble=self.book.postamble,config=self.book.config)
        except (Error,UnicodeError,LookupError) as exc:
            self.chapter_failure(row,f'Cannot read {member.name}: {exc}')
            return
        self.seen.add(name)
        occurrence = f'sphinx:{len(self.doc.inventory)}'
        fallback = row.get('navigation_label') or ('Opening' if depth==0 else f'Chapter {len(self.seen)}')
        label = next((item.source for item in prepared.blocks if item.role=='heading'),fallback)
        root = self.resources[member.name]
        root.update(role='text',status='represented')
        row.update(status='represented',member=member.name,label=label,occurrence=occurrence,ancestors=list(ancestors))
        ids = {item.id:key_for(member.name,occurrence,item.id) for item in prepared.blocks}
        destinations = {'source.html#'+original:'source.html#'+target for original,target in ids.items()}
        for item in prepared.blocks:
            item.id = ids[item.id]
            item.chapter = label
            origin = self.resources.get(item.locator.get('source_file'),root)
            item.locator.update(member=origin['member'],archive_entry=origin['archive_entry'],chapter_id=occurrence,
                                location_id=key([self.doc.source_sha256,origin['archive_entry'],item.id])[:20])
            if control:=item.locator.get('source_control'):
                self.children(member,control,item.locator,(*ancestors,name),depth+1)
                continue
            for link in item.links:
                if 'destinations' in link:
                    link['destinations'] = [destinations.get(addr,addr) for addr in link['destinations']]
                if 'destination' in link:
                    link['destination'] = destinations.get(link['destination'],link['destination'])
            for ref in item.locator.pop('resource_references',[]):
                try:
                    href = urlsplit(ref['href'])
                    asset = (self.resources.get(resolve(href.path,origin['member'],self.book.root))
                             if href.path and not (href.scheme or href.netloc or href.query) else None)
                except (Error,ValueError):
                    asset = None
                if asset:
                    if asset['status']=='unlisted':
                        asset['status']='referenced'
                    item.links.append({'kind':'source_resource','href':ref['href'],'label':ref.get('label') or href.path,
                                       'destinations':['source.html#resource-'+asset['location_id']]})
            if item.issue:
                row['status'] = 'needs_review'
            self.doc.blocks.append(item)

    def chapter_failure(self,row,reason):
        row.update(status='failed',reason=reason)
        occurrence = f'sphinx:{len(self.doc.inventory)}'
        origin = row.get('location') or self.resources[self.config.name]
        location = {**origin,'path':row['path'],
                    'location_id':key([self.doc.source_sha256,occurrence,row['path']])[:20]}
        self.issue(row.get('navigation_label') or row['path'],occurrence,location,reason)

    def children(self,member,control,location,ancestors,depth):
        from .rst_intake import EXPLICIT

        options = control['options']
        remaining = self.pages.keys()-{ancestors[-1]}
        chapters = []
        for entry in control['entries']:
            target = entry['entry']
            explicit = EXPLICIT.fullmatch(target)
            label,target = explicit.groups() if explicit else ('',target)
            line = entry['line']
            entry_location = {**location,'lines':[line,line]}
            row = {'target':target,'navigation_label':label,'caption':options.get('caption'),
                   'hidden':'hidden' in options,'location':entry_location}
            if re.match(r'(?:\w[\w+.-]*:|//)',target) or target=='self':
                self.doc.inventory.append(row|{'status':'self' if target=='self' else 'external'})
                continue
            try:
                # Document references resolve beside the containing Sphinx page.
                name = resolve(target,member.name,self.book.root)
            except Error as exc:
                self.doc.inventory.append(row|{'path':target})
                self.chapter_failure(self.doc.inventory[-1],str(exc))
                continue
            for suffix in self.book.suffixes:
                if name.endswith(suffix):
                    name = name.removesuffix(suffix)
                    break
            if not explicit and 'glob' in options and any(char in target for char in '*?['):
                matches = sorted(item for item in remaining if glob_match(item,name))
                if not matches:
                    self.doc.inventory.append(row|{'path':name})
                    self.chapter_failure(self.doc.inventory[-1],f'No Sphinx chapter matches {target!r}.')
                remaining.difference_update(matches)
            else:
                matches = [name]
                remaining.discard(name)
            chapters.extend((item,row.copy()) for item in matches)
        if 'reversed' in options:
            chapters.reverse()
        for name,row in chapters:
            self.chapter(name,row,ancestors,depth)
