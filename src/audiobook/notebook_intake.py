"""Read authored notebook cells and saved results without loading a kernel."""

import re
from urllib.parse import unquote, urlsplit

from mdit_py_plugins.dollarmath import dollarmath_plugin

from . import json_source
from .core import Error, digest, key
from .document_intake import DocumentBuilder, formula_span, html_read
from .fragment_locations import rebase_blocks
from .importers import markdown
from .markdown_notes import visible_inline_html
from .tex_math import tex_math


NARRATIVE = ('text/markdown','text/html','text/latex','text/plain')
IMAGE_ENCODINGS = {
    'image/png':'base64','image/jpeg':'base64','image/gif':'base64',
    'image/webp':'base64','image/svg+xml':'utf-8',
}
LEGACY_MIME = {
    'html':'text/html','text':'text/plain','latex':'text/latex',
    'png':'image/png','jpeg':'image/jpeg','svg':'image/svg+xml',
    'json':'application/json','javascript':'application/javascript',
}
DISPLAY_REPR = re.compile(r'<(?:Figure size \d+x\d+ with \d+ Axes|[^<>\n]+ at 0x[0-9a-fA-F]+)>')
TERMINAL_STYLE = re.compile(r'\x1b\[[0-9;]*m')


def location_label(location):
    label = f"Cell {location['cell']}"
    if worksheet := location.get('worksheet'):
        label = f'Worksheet {worksheet}, {label.lower()}'
    if output := location.get('output'):
        label += f', output {output}'
    return label


def markdown_cells(text):
    def configure(parser):
        parser.options['html'] = True
        parser.use(dollarmath_plugin,allow_labels=False,allow_space=False,allow_digits=False)
        parser.core.ruler.after('inline','notebook_html',visible_inline_html)

    return markdown(text.encode(),collect_resources=True,configure=configure).blocks


def saved_html(text,sha):
    fragment = DocumentBuilder('html',None,sha)
    html_read(text.encode(),fragment,metadata=False,collect_resources=True)
    return fragment.doc.blocks


class Notebook:
    def __init__(self,builder):
        self.builder = builder
        self.document = builder.doc
        self.assets = []

    def add(self,text,role,location,*,exact=None,issue=None):
        loc = location|({'exact':exact} if exact is not None else {})
        return self.builder.add(text,role,location_label(location),loc=loc,issue=issue)

    def project(self,blocks,location,attachments):
        sha = self.document.source_sha256
        rebase_blocks(blocks,location['json_path'])
        for item in blocks:
            syntax = item.locator.get('format')
            item.locator.update(location,format='notebook',syntax=syntax,
                                location_id=key([sha,item.id])[:20],element=location_label(location))
            if item.role=='heading':
                self.builder.chapter_id = 'notebook:'+item.id
                self.builder.chapter = item.chapter
            item.chapter = self.builder.chapter
            item.locator['chapter_id'] = self.builder.chapter_id
            for ref in item.locator.pop('resource_references',()):
                href = ref['href']
                if not href.startswith('attachment:'):
                    continue
                try:
                    uri = urlsplit(href)
                    target = unquote(uri.path,errors='strict') if not uri.netloc and not uri.query else None
                except (UnicodeError,ValueError):
                    target = None
                if record := attachments.get(target):
                    record['status'] = 'referenced'
                    item.links.append({'kind':'source_resource','href':href,'label':ref.get('label') or target,
                                       'destinations':['source.html#resource-'+record['location_id']]})
                else:
                    item.issue = item.issue or 'A referenced notebook attachment has no readable local data.'
            self.document.blocks.append(item)

    def asset(self,label,mime,value,location,path):
        encoding = IMAGE_ENCODINGS[mime]
        try:
            content = json_source.decode(value,encoding)
        except Error:
            return None
        if not content:
            return None
        sha = self.document.source_sha256
        record = {**location,'role':'asset','resource_label':label,'mime':mime,'size':len(content),
                  'json_asset':{'path':path,'encoding':encoding,'sha256':digest(content)},
                  'status':'preserved','location_id':key([sha,path])[:20]}
        self.assets.append(record)
        return record

    def attachments(self,cell,location):
        selected = {}
        bundles = cell.get('attachments')
        if isinstance(bundles,dict):
            for label,bundle in bundles.items():
                if not isinstance(bundle,dict):
                    continue
                json_source.multiline(label)
                for mime in IMAGE_ENCODINGS:
                    if mime in bundle:
                        path = [*location['json_path'],'attachments',label,mime]
                        asset = self.asset(label,mime,bundle[mime],location,path)
                        if asset is not None:
                            selected.setdefault(label,asset)
        return selected

    def saved_output(self,output,location,legacy):
        row = {'kind':'notebook_output',**location,'status':'retained in original'}
        self.document.inventory.append(row)
        if not isinstance(output,dict):
            raise Error('source_notebook_output','A saved output is not a JSON object.')
        kind = json_source.multiline(output.get('output_type'))
        row['output_type'] = kind
        path = location['json_path']
        if kind=='stream':
            exact = json_source.multiline(output.get('text'))
            text = '\n'.join(line.rsplit('\r',1)[-1] for line in TERMINAL_STYLE.sub('',exact).replace('\r\n','\n').split('\n'))
            if output.get('stream' if legacy else 'name')=='stderr':
                self.add(text,'reference',location|{'json_path':[*path,'text']},exact=exact)
            elif self.add(text,'body',location|{'json_path':[*path,'text']},exact=exact):
                row.update(status='represented',selected_mime='text/plain')
            return
        if kind in ('error','pyerr'):
            summary = ': '.join(json_source.multiline(value) for field in ('ename','evalue')
                                if isinstance(value:=output.get(field),str) and value)
            self.add(summary,'reference',location,exact=summary)
            return
        if kind not in ('display_data','execute_result','pyout'):
            return
        bundle = output if legacy else output.get('data')
        if not isinstance(bundle,dict):
            raise Error('source_notebook_output','A saved display result has no readable MIME bundle.')
        files = {mime:field for field in bundle
                 if (mime:=LEGACY_MIME.get(field,field) if legacy else field) and '/' in mime}
        row['mime_types'] = sorted(files)
        prefix = path if legacy else [*path,'data']
        for mime in IMAGE_ENCODINGS:
            if mime in files:
                field = files[mime]
                label = location_label(location)+f' ({mime})'
                self.asset(label,mime,bundle[field],location,[*prefix,field])
        problem = None
        partial = None
        for mime in NARRATIVE:
            if mime not in files:
                continue
            field = files[mime]
            try:
                exact = json_source.multiline(bundle[field])
            except Error:
                problem = 'A saved result has malformed text in its MIME bundle.'
                continue
            loc = location|{'json_path':[*prefix,field],'mime':mime}
            if mime=='text/plain':
                text = TERMINAL_STYLE.sub('',exact)
                if DISPLAY_REPR.fullmatch(text.strip()) and IMAGE_ENCODINGS.keys() & files.keys():
                    continue
                represented = self.add(text,'body',loc,exact=exact)
            elif mime=='text/latex':
                source = exact.strip()
                expression = next((source[len(a):-len(b)] for a,b in (('$$','$$'),('\\[','\\]'),('\\(','\\)'),('$','$'))
                                   if source.startswith(a) and source.endswith(b)),source)
                span = formula_span(source,lambda:tex_math(expression),0,'Saved notebook equation',loc)
                if problem := span['issue']:
                    continue
                represented = self.builder.add(source,'equation',location_label(loc),
                                               loc=loc|{'inline_semantics':[span],'exact':exact})
            else:
                blocks = markdown_cells(exact) if mime=='text/markdown' else saved_html(exact,self.document.source_sha256)
                # Generated output headings describe the result inside the authored section.
                for block in blocks:
                    if block.role=='heading':
                        block.role = 'body'
                if any(block.issue for block in blocks):
                    if partial is None:
                        partial = blocks,loc
                    continue
                self.project(blocks,loc,{})
                represented = blocks
            if represented:
                row.update(status='represented',selected_mime=mime)
                return
        if partial is not None:
            blocks,loc = partial
            self.project(blocks,loc,{})
            row.update(status='needs review',selected_mime=loc['mime'])
        elif problem:
            raise Error('source_notebook_output',problem)

    def cell(self,cell,location,legacy):
        label = location_label(location)
        self.document.inventory.append({'kind':'notebook_cell',**location})
        try:
            if not isinstance(cell,dict) or not isinstance(kind:=cell.get('cell_type'),str):
                raise Error('source_notebook_cell','A notebook cell has no readable type.')
            field = 'input' if legacy and kind=='code' else 'source'
            exact = json_source.multiline(cell.get(field,''))
            loc = location|{'json_path':[*location['json_path'],field]}
            if isinstance(cell.get('id'),str):
                loc['cell_id'] = json_source.multiline(cell['id'])
            attachments = self.attachments(cell,location)
            metadata = cell.get('metadata')
            form = metadata.get('raw_mimetype',metadata.get('format')) if isinstance(metadata,dict) else None
            if kind=='markdown' or kind=='raw' and form in ('text/markdown','markdown'):
                self.project(markdown_cells(exact),loc,attachments)
            elif kind=='raw' and form in ('text/html','html'):
                self.project(saved_html(exact,self.document.source_sha256),loc,attachments)
            elif kind=='heading' and legacy:
                self.add(exact,'heading',loc,exact=exact)
            elif kind=='code':
                item = self.add(exact,'code',loc,exact=exact)
                if item:
                    item.source = exact
            elif kind=='raw':
                self.add(exact,'body' if form in (None,'','text/plain','plain') else 'reference',loc,exact=exact)
            else:
                self.add('','unreadable',loc,issue='The notebook cell uses an unrecognized source type.')
        except Error as error:
            self.builder.add('','unreadable',label,loc=location,issue=error.message)
        if not isinstance(cell,dict) or cell.get('cell_type')!='code':
            return
        outputs = cell.get('outputs',[])
        if not isinstance(outputs,list):
            self.add('','unreadable',location,issue='The notebook cell has no ordered saved output list.')
            return
        for index,output in enumerate(outputs):
            loc = location|{'json_path':[*location['json_path'],'outputs',index],'output':index+1}
            try:
                self.saved_output(output,loc,legacy)
            except Error as error:
                self.add('','unreadable',loc,issue=error.message)


def read_notebook(data,builder):
    document = json_source.load(data)
    version = document.get('nbformat') if isinstance(document,dict) else None
    if type(version) is not int or version not in (3,4):
        raise Error('unsupported_format','This notebook reader supports Jupyter notebook versions 3 and 4.',
                    'Export an earlier notebook to Markdown or a current .ipynb file.')
    notebook = Notebook(builder)
    legacy = version==3
    worksheets = document.get('worksheets') if legacy else [document]
    if not isinstance(worksheets,list):
        raise Error('source_notebook_cell','The notebook has no ordered worksheet list.')
    for index,worksheet in enumerate(worksheets):
        location = {'worksheet':index+1} if legacy else {}
        prefix = ['worksheets',index] if legacy else []
        cells = worksheet.get('cells') if isinstance(worksheet,dict) else None
        if not isinstance(cells,list):
            builder.add('','unreadable',f'Worksheet {index+1}',loc=location|{'json_path':prefix},
                        issue='The notebook has no ordered cell list at this location.')
            continue
        for cell_index,cell in enumerate(cells):
            notebook.cell(cell,location|{'cell':cell_index+1,'json_path':[*prefix,'cells',cell_index]},legacy)
    builder.doc.metadata['notebook_version'] = version
    if notebook.assets:
        builder.doc.metadata['source_resources'] = notebook.assets
