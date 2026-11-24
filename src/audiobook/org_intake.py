"""Read saved Org manuscripts through the shared structured HTML preparation."""

from dataclasses import dataclass
from html import escape
import re
import shlex

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, html_read
from .org_markup import Objects


DEPENDENCIES = re.compile(r'^\s*#\+(?:include|setupfile):',re.I | re.M)
HEADING = re.compile(r'^(\*+)(?:[ \t]+(.*)|$)')
TAGS = re.compile(r'(?:^|[ \t]+)((?::[\w@#%]+)+:)\s*$')
KEYWORD = re.compile(r'^[ \t]*#\+([\w-]+(?:\[[^\]]+\])?):\s*(.*)',re.I)
BEGIN = re.compile(r'^\s*#\+begin_([\w-]+)(?:\s.*)?$',re.I)
END = re.compile(r'^\s*#\+end_([\w-]+)\s*$',re.I)
DRAWER = re.compile(r'^\s*:([\w-]+):\s*$',re.I)
CUSTOM_ID = re.compile(r'^\s*:CUSTOM_ID:\s+(.+?)\s*$',re.I)
DEFINITION = re.compile(r'^\[fn:([\w-]+)\](?:[ \t]*(.*))$')
BULLET = re.compile(r'^([ \t]*)([-+*]|[1-9][0-9]*[.)])(?:\s+|$)(.*)')
COMMENT = re.compile(r'^\s*#(?:\s|$)')
FIXED = re.compile(r'^\s*:(?:[ \t]+|$)(.*)')
RULE = re.compile(r'^\s*-{5,}\s*$')
TABLE_RULE = re.compile(r'^\s*\|[-+:| \t]+\|?\s*$')
PLANNING = re.compile(r'^\s*(?:SCHEDULED|DEADLINE|CLOSED):[ \t]+[<\[]\d{4}-')
OPTIONS = re.compile(r'(?<!\S)([\w\\*<>-]+):([^\s]+)')
BLOCKS = {'src':'pre','example':'pre','quote':'blockquote','center':'div','verse':'div'}
RAW_BLOCKS = frozenset(('src','example','export','comment','verse'))


@dataclass(frozen=True)
class Element:
    kind: str
    first: int
    last: int
    values: tuple


def classify(line):
    for kind,pattern in (('heading',HEADING),('keyword',KEYWORD),('block',BEGIN),('drawer',DRAWER),
                         ('note',DEFINITION),('comment',COMMENT),('fixed',FIXED),('rule',RULE),
                         ('planning',PLANNING),('bullet',BULLET)):
        if found:=pattern.match(line):
            return kind,found.groups()
    if line.lstrip().startswith('|'):
        return 'table',()
    return ('paragraph',()) if line.strip() else ('blank',())


def block_end(lines,start,name):
    level = 1
    for index in range(start+1,len(lines)):
        text = lines[index]
        if (closing:=END.fullmatch(text)) and closing[1].casefold()==name:
            level -= 1
            if not level:
                return index
        if name not in RAW_BLOCKS and (opening:=BEGIN.fullmatch(text)) and opening[1].casefold()==name:
            level += 1


def elements(lines,start=1):
    index = 0
    while index<len(lines):
        line = lines[index]
        kind,values = classify(line)
        end = index+1
        if kind=='block':
            name = values[0].casefold()
            found = block_end(lines,index,name)
            if found is None:
                kind,values,end = 'issue',(f'An Org {name!r} block has no closing delimiter.',),len(lines)
            else:
                values,end = (name,lines[end:found]),found+1
        elif kind=='drawer':
            name = values[0].casefold()
            while end<len(lines) and not HEADING.match(lines[end]):
                if (mark:=DRAWER.fullmatch(lines[end])) and mark[1].casefold()=='end':
                    break
                end += 1
            if end==len(lines) or not DRAWER.fullmatch(lines[end]):
                kind,values = 'issue',(f'An Org {name!r} drawer has no closing delimiter.',)
                end = index+1
            else:
                values,end = (name,lines[index+1:end]),end+1
        elif kind=='note':
            while end<len(lines) and not (DEFINITION.match(lines[end]) or HEADING.match(lines[end])
                    or not lines[end].strip() and (end+1==len(lines) or not lines[end+1].strip())):
                end += 1
            values = values[0], '\n'.join([values[1] or '',*lines[index+1:end]]).strip()
        elif kind in ('paragraph','table','fixed'):
            while end<len(lines) and classify(lines[end])[0]==kind:
                end += 1
            values = lines[index:end],
        elif kind=='bullet':
            indent,marker,first = values
            width = len(indent.expandtabs(8))
            while (end<len(lines) and classify(lines[end])[0]=='paragraph' and
                   len(lines[end].expandtabs(8))-len(lines[end].expandtabs(8).lstrip())>width):
                end += 1
            values = width,marker,' '.join([first,*[text.strip() for text in lines[index+1:end]]])
        if kind not in ('blank','comment','rule','planning'):
            yield Element(kind,index+start,end+start-1,values)
        index = end


def include_spec(value):
    args = shlex.split(value)
    if not args:
        raise ValueError('The Org include needs a local filename.')
    path,arguments = args[0],args[1:]
    path,separator,selector = path.partition('::')
    if separator and (len(selector)<2 or selector[0] not in '*#'):
        raise ValueError('Use an exact heading or custom ID for an Org include target.')
    mode = 'org'
    if arguments and arguments[0] in ('example','src'):
        mode = arguments.pop(0)
        if mode=='src' and arguments and not arguments[0].startswith(':'):
            arguments.pop(0)
    if len(arguments)%2 or any(flag not in (':lines',':minlevel',':only-contents') for flag in arguments[::2]):
        raise ValueError('This Org include mode or selector needs a verified reading.')
    options = dict(zip(arguments[::2],arguments[1::2]))
    first,last = 1,None
    if limit:=options.get(':lines'):
        match = re.fullmatch(r'([1-9][0-9]*)?-([1-9][0-9]*)?',limit)
        if not match or not any(match.groups()):
            raise ValueError('The Org include line range is invalid.')
        first = int(match[1] or '1')
        last = int(match[2]) if match[2] else None
        if last is not None and last<first:
            raise ValueError('The Org include line range runs backwards.')
    level = int(options[':minlevel']) if ':minlevel' in options else None
    if level is not None and not 1<=level<=64:
        raise ValueError('The Org include heading level is out of range.')
    contents = options.get(':only-contents','nil')
    if contents not in ('t','nil') or contents=='t' and not selector:
        raise ValueError('An Org contents-only include needs a heading or custom ID.')
    return path,mode,first,last,level,selector,contents=='t'


def heading(element,todos):
    stars,text = element.values
    text = (text or '').rstrip()
    match = TAGS.search(text)
    tags = set(match[1].strip(':').split(':')) if match else set()
    if match:
        text = text[:match.start()]
    first,_,rest = text.partition(' ')
    if first in todos:
        text = rest.lstrip()
    text = re.sub(r'^\[#[A-Z0-9]+\]\s*','',text)
    return len(stars),text,tags


def subtree(lines,selector,contents,todos):
    items = list(elements(lines))
    matches = []
    for index,item in enumerate(items):
        if item.kind!='heading':
            continue
        level,label,_ = heading(item,todos)
        following = items[index+1] if index+1<len(items) else None
        properties = (following if following and following.kind=='drawer' and
                      following.values[0]=='properties' else None)
        if selector.startswith('*'):
            found = label==selector.lstrip('*').strip()
        else:
            found = properties and any((match:=CUSTOM_ID.fullmatch(line)) and match[1]==selector[1:]
                                       for line in properties.values[1])
        if found:
            begin = (properties.last+1 if properties else item.last+1) if contents else item.first
            end = next((row.first for row in items[index+1:]
                        if row.kind=='heading' and len(row.values[0])<=level),len(lines)+1)
            matches.append((begin,end))
    if len(matches)!=1:
        raise ValueError(f'The Org target {selector!r} must identify one section.')
    return matches[0]


def selected(items,select,exclude,todos):
    """Keep selected subtrees and their ancestors in original order."""
    headings,stack,keep = {},[],set()
    selective = False
    for index,item in enumerate(items):
        if item.kind!='heading':
            continue
        level,text,tags = heading(item,todos)
        while stack and stack[-1][0]>=level:
            stack.pop()
        explicit = bool(tags & select)
        selective |= explicit
        denied = bool(tags & exclude) or text=='COMMENT' or text.startswith('COMMENT ')
        blocked = denied or bool(stack and stack[-1][2])
        inherited = explicit or bool(stack and stack[-1][3])
        stack.append((level,index,blocked,inherited))
        headings[index] = blocked,inherited,text,level
        if explicit and not any(row[2] for row in stack[:-1]):
            keep.update(row[1] for row in stack)
    allow = not selective
    for index,item in enumerate(items):
        if index in headings:
            blocked,inherited,text,level = headings[index]
            allow = not blocked and (not selective or inherited or index in keep)
            if allow:
                yield item,(text,level)
        elif allow:
            yield item,None


class Org:
    def __init__(self,include,encoding):
        self.include = include
        self.encoding = encoding
        self.remaining = 4*MAX_STRUCTURE
        self.annotations = {}
        self.settings = {'select_tags':'export','exclude_tags':'noexport','todo':'TODO DONE'}
        self.options = {}
        self.macros = {}
        self.objects = Objects(self,self.macros)

    def locator(self,source,first,last,**fields):
        ident = str(len(self.annotations)+1)
        self.annotations[ident] = {'source_file':source,'lines':[first,last],
                                   'source_element':f'{source}:{first}-{last}',**fields}
        return ident

    def tag(self,tag,part,body,attributes='',**fields):
        ident = self.locator(*part,**fields)
        return f'<{tag} data-hamr-node="{ident}"{attributes}>{body}</{tag}>'

    def inline_issue(self,part,text,issue):
        return self.tag('span',part,escape(text),reading_span={'kind':'source_review','source':text,
                        'issue':issue,'source_locator':{'source_file':part[0],'lines':[part[1],part[2]]}})

    def issue(self,part,message):
        return self.tag('p',part,'',source_issue=str(message))

    def load(self,ancestors,path):
        if self.include is None:
            raise Error('org_include',f'Local Org resource {path!r} is unavailable in this snapshot.')
        name,data = self.include(ancestors[-1],path)
        if name in ancestors or len(ancestors)>=24:
            raise Error('org_include',f'Circular or deeply nested Org resource {name!r}.')
        self.remaining -= len(data)
        if self.remaining<0:
            raise Error('source_structure_size','Expanded Org resources exceed the structured reading limit.')
        return name, decode_org(data,self.encoding).splitlines()

    def configure(self,items,ancestors):
        for item in items:
            if item.kind!='keyword':
                continue
            key,value = item.values
            key = key.casefold()
            if key=='setupfile':
                part = ancestors[-1],item.first,item.last
                try:
                    args = shlex.split(value)
                    if len(args)!=1:
                        raise ValueError('An Org setup file must name one local source.')
                    name,lines = self.load(ancestors,args[0])
                except (Error,ValueError) as error:
                    yield self.issue(part,error)
                else:
                    yield from self.configure(elements(lines),[*ancestors,name])
            elif key in ('select_tags','exclude_tags','language','todo','seq_todo','typ_todo','title'):
                prior = self.settings.get('title')
                self.settings[key] = prior+' '+value if key=='title' and prior else value
            elif key=='options':
                self.options.update(OPTIONS.findall(value))
            elif key=='macro':
                parts = value.split(maxsplit=1)
                if len(parts)==2:
                    self.macros[parts[0].casefold()] = parts[1]

    def todos(self):
        declared = ' '.join(self.settings.get(k,'') for k in ('todo','seq_todo','typ_todo'))
        return set(re.findall(r'[\w-]+',declared))

    def included(self,part,value,ancestors,level):
        try:
            path,mode,first,last,minlevel,selector,contents = include_spec(value)
            name,lines = self.load(ancestors,path)
            offset = 0
            if selector:
                begin,end = subtree(lines,selector,contents,self.todos())
                offset = begin-1
                lines = lines[offset:end-1]
            if first>len(lines):
                raise ValueError('The requested Org include begins beyond the end of its source.')
        except (Error,ValueError) as error:
            return self.issue(part,error)
        lines = lines[first-1:last-1 if last is not None else None]
        first += offset
        if mode!='org':
            return self.tag('pre',(name,first,first+len(lines)-1),escape('\n'.join(lines)))
        items = list(elements(lines,first))
        lowest = min((len(item.values[0]) for item in items if item.kind=='heading'),default=1)
        return self.render(items,[*ancestors,name],(minlevel or level+1)-lowest)

    def table(self,rows,part,caption=None):
        rule = next((index for index,row in enumerate(rows) if TABLE_RULE.fullmatch(row)),0)
        result = [caption] if caption else []
        for index,row in enumerate(rows):
            if TABLE_RULE.fullmatch(row):
                continue
            cells = row.strip().removeprefix('|').removesuffix('|').split('|')
            celltag = 'th' if index<rule else 'td'
            location = part[0],part[1]+index,part[1]+index
            result.append('<tr>'+''.join(f'<{celltag}>{self.objects.inline(cell.strip(),location)}</{celltag}>'
                                         for cell in cells)+'</tr>')
        return self.tag('table',part,''.join(result))

    def listing(self,items,index,part,depth=0):
        first = index
        width,marker,_ = items[index][0].values
        ordered = marker[0].isdigit()
        tag = 'ol' if ordered else 'ul'
        body = []
        while index<len(items) and items[index][0].kind=='bullet':
            item = items[index][0]
            indent,mark,text = item.values
            if indent!=width or mark[0].isdigit()!=ordered:
                break
            index += 1
            loc = part,item.first,item.last
            counter = re.match(r'^\[@([1-9][0-9]*)\]\s*',text) if ordered else None
            value = counter[1] if counter else mark[:-1]
            if counter:
                text = text[counter.end():]
            text = re.sub(r'^\[[ Xx-]\]\s*','',text)
            label = self.objects.inline(text,loc)
            nested = ''
            while (index<len(items) and items[index][0].kind=='bullet' and
                   items[index][0].values[0]>width and depth<24):
                fragment,index = self.listing(items,index,part,depth+1)
                nested += fragment
            body.append(self.tag('li',loc,f'<p>{label}</p>'+nested,f' value="{value}"' if ordered else ''))
        return self.tag(tag,(part,items[first][0].first,items[index-1][0].last),''.join(body)),index

    def render(self,items,ancestors,shift=0,*,fragment=False):
        use = list(selected(items,set(self.settings['select_tags'].split()),set(self.settings['exclude_tags'].split()),
                            self.todos()))
        result = []
        index,level = 0,0
        caption = None
        while index<len(use):
            item,head = use[index]
            part = ancestors[-1],item.first,item.last
            values = item.values
            kind = item.kind
            index += 1
            if kind=='keyword' and values[0].casefold().split('[',1)[0]=='caption':
                caption = item,part
                continue
            if head is not None:
                text,level = head[0],max(1,head[1]+shift)
                if text!='Footnotes':
                    result.append(self.tag('p' if fragment else f'h{min(6,level)}',part,
                                           self.objects.inline(text,part)))
            elif kind=='bullet':
                markup,index = self.listing(use,index-1,ancestors[-1])
                result.append(markup)
            elif kind=='table':
                mark = None
                if caption and caption[0].last+1==item.first:
                    mark = caption
                elif index<len(use):
                    following = use[index][0]
                    if (following.kind=='keyword' and following.values[0].casefold().split('[',1)[0]=='caption'
                            and following.first==item.last+1):
                        mark = following,(part[0],following.first,following.last)
                        index += 1
                label = self.tag('caption',mark[1],self.objects.inline(mark[0].values[1],mark[1])) if mark else None
                result.append(self.table(values[0],part,label))
            elif kind=='paragraph':
                text = ' '.join(line.strip() for line in values[0])
                result.append(self.tag('p',part,self.objects.inline(text,part)))
            elif kind=='fixed':
                text = '\n'.join(FIXED.match(line)[1] for line in values[0])
                result.append(self.tag('pre',part,escape(text)))
            elif kind=='note':
                name,text = values
                ident = self.objects.note_id(part,name)
                self.objects.notes.append((part,ident,text))
            elif kind=='issue':
                result.append(self.issue(part,values[0]))
            elif kind=='keyword' and values[0].casefold()=='include':
                result.append(self.included(part,values[1],ancestors,level))
            elif kind=='drawer' and values[0]!='properties' and self.options.get('d')=='t':
                result.append(self.render(list(elements(values[1],item.first+1)),ancestors,fragment=True))
            elif kind=='block':
                name,body = values
                if name in ('comment','export'):
                    continue
                if name in ('src','example'):
                    inner = escape('\n'.join(line[1:] if re.match(r'^,+(?:\*|#\+)',line) else line for line in body))
                elif name=='verse':
                    verse = '<br>'.join(self.objects.inline(line,(part[0],item.first+offset,item.first+offset))
                                        for offset,line in enumerate(body,1))
                    result.append(self.tag('p',part,verse,source_role='verse'))
                    continue
                else:
                    inner = self.render(list(elements(body,item.first+1)),ancestors,fragment=True)
                result.append(self.tag(BLOCKS.get(name,'div'),part,inner))
        return ''.join(result)

    def definitions(self):
        seen = set()
        result = []
        for part,ident,text in self.objects.notes:
            marker = part,ident,text
            if marker in seen:
                continue
            if len(seen)>=10000:
                raise Error('source_structure_size','Org note definitions exceed the structured reading limit.')
            seen.add(marker)
            body = self.objects.inline(' '.join(text.split()),part)
            result.append(self.tag('aside',part,body,f' role="doc-footnote" id="{ident}"'))
        return ''.join(result)


def decode_org(raw,encoding):
    try:
        return raw.decode(encoding or 'utf-8-sig')
    except (UnicodeError,LookupError) as error:
        raise Error('text_encoding','The Org source does not decode with the selected encoding.') from error


def org_document(text,name,sha,title,*,include=None,encoding=None):
    parser = Org(include,encoding)
    items = list(elements(text.splitlines()))
    findings = ''.join(parser.configure(items,[name]))
    try:
        content = parser.render(items,[name])
        definitions = parser.definitions()
    except RecursionError as error:
        raise Error('source_org_depth','This Org document is nested too deeply to read.') from error
    builder = DocumentBuilder('org',parser.settings.get('title') or title,sha)
    builder.doc.language = parser.settings.get('language') or 'en'
    html_read(findings+content+definitions,builder,where='org',metadata=False,
              source_annotations=parser.annotations,collect_links=True)
    return builder.doc
