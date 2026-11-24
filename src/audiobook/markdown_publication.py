"""Assemble ordered Markdown chapters into the same semantic source model."""

import posixpath
from urllib.parse import unquote, urlsplit

from .core import Document, Error, key
from .fragment_locations import rebase_blocks
from .importers import block, markdown
from .publication_package import member_bytes, member_location
from .source_bundle import safe_name

CHAPTER_LIMIT = 32*1024*1024


def joined(base,name):
    return posixpath.join(base,name) if base else name


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


def utf8(raw,name,label):
    try:
        return raw.decode('utf-8-sig')
    except UnicodeError as exc:
        raise Error('text_encoding',f'{label} member is not valid UTF-8: {name}.') from exc


def add_chapter(document, label, occurrence, member, content, resources,*,first_note_definition=False,configure=None):
    chapter = markdown(content.encode('utf-8'),collect_resources=True,
                       first_note_definition=first_note_definition,configure=configure)
    rebase_blocks(chapter.blocks,(member.name,occurrence))
    for item in chapter.blocks:
        item.chapter = label
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


def chapter_issue(document,row,summary,reason):
    location = member_location(document.source_sha256,summary,'summary')
    location['format'] = 'markdown'
    if line:=row.get('summary_line'):
        location['lines'] = [line,line]
    location.update(chapter_id=row['occurrence'],
                    path=row.get('member'),location_id=key([document.source_sha256,row['occurrence']])[:20])
    document.blocks.append(block('',location,'unreadable',row['label'],issue=reason))


class MarkdownPublication:
    def __init__(self,archive,entries,source_root,sha,title,label,descriptors=()):
        self.archive = archive
        self.document = Document(title,'markdown',sha,[])
        self.label = label
        self.members = {}
        for member in entries:
            self.members.setdefault(member.name,[]).append(member)
        self.resources = {
            name:member_location(sha,matches[0],'package_member')|{'status':'unlisted'}
            for name,matches in self.members.items() if len(matches)==1 and
            (not source_root or name.startswith(source_root+'/'))
        }
        for descriptor in descriptors:
            self.resources[descriptor.name] = member_location(sha,descriptor,'descriptor')|{'status':'preserved'}

    def entry(self,name):
        items = self.members.get(name,[])
        if len(items)!=1:
            detail = 'repeated' if items else 'missing'
            raise Error('source_publication_member',f'The {self.label} member is {detail}: {name}.')
        return items[0]

    def read(self,name,limit=CHAPTER_LIMIT):
        return utf8(member_bytes(self.archive,self.entry(name),limit),name,self.label)

    def add(self,row,member,content,*,first_note_definition=False,configure=None):
        blocks = add_chapter(self.document,row['label'],row['occurrence'],member,content,self.resources,
                             first_note_definition=first_note_definition,configure=configure)
        row['blocks'] = [item.id for item in blocks]
        row['status'] = 'needs_review' if any(item.issue for item in blocks) else 'represented' if blocks else 'empty'
        self.resources[member.name].update(role='text',status='represented')
        return blocks

    def finish(self):
        self.document.metadata['source_resources'] = list(self.resources.values())
        return self.document
