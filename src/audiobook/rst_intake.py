"""Read reStructuredText into the shared HTML semantics with source annotations."""

import ast
import io
from pathlib import Path
import re
from threading import Lock

from docutils import nodes, statemachine
from docutils.core import publish_parts
from docutils.parsers.rst import Directive, directives, roles
from docutils.parsers.rst.directives.misc import Include
from docutils.utils import unescape
from docutils.writers.html5_polyglot import HTMLTranslator, Writer

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read, html_read


_PARSE = Lock()
EXPLICIT = re.compile(r'(.+?)\s*<([^<>]+)>\s*$', re.S)


class SnapshotInclude(Include):
    option_spec = {name:value for name,value in Include.option_spec.items() if name!='parser'}

    def run(self):
        self.settings = self.state.document.settings
        self.tab_width = self.options.get('tab-width',self.settings.tab_width)
        current, _ = self.state_machine.get_source_and_line(self.lineno)
        requested = directives.path(self.arguments[0])
        try:
            source,text = self.settings.hamr_files.read(current,requested,self.options.get('encoding','utf-8-sig'))
        except (Error, UnicodeError, LookupError) as exc:
            raise self.error(str(exc)) from exc
        start,end = slice(self.options.get('start-line'),self.options.get('end-line')).indices(len(text.splitlines()))[:2]
        start_after = self.options.get('start-after')
        end_before = self.options.get('end-before')
        if self.options.get('start-line') is not None or self.options.get('end-line') is not None:
            text = '\n'.join(text.splitlines()[start:end])
        if start_after is not None:
            marker = start_after or '\n\n'
            _,found,text_after = text.partition(marker)
            if not found:
                raise self.error(f'Include start marker is absent in {source}.')
            start += text[:len(text)-len(text_after)].count('\n')
            text = text_after
        if end_before is not None:
            before,found,_ = text.partition(end_before or '\n\n')
            if not found and end_before:
                raise self.error(f'Include end marker is absent in {source}.')
            if found:
                text = before
        self.options.update(source=source,**{'start-line':start})
        if 'literal' in self.options:
            return self.as_literal_block(text)
        if 'code' in self.options:
            return self.as_code_block(text)
        self.insert_snapshot(source,text,start,current)
        return []

    def insert_snapshot(self,source,text,start,current):
        log = self.state.document.include_log
        if not log:
            log.append((current,None))
        if len(log)>=32 or any(name==source for name,_ in log):
            raise self.error(f'Circular or nested include exceeds the reading limit: {source}.')
        lines = statemachine.string2lines(text,self.tab_width,convert_whitespace=True)
        if any(len(line)>self.settings.line_length_limit for line in lines):
            raise self.error(f'An included line in {source} exceeds the line length limit.')
        log.append((source,None))
        content = ['',*lines,'',f'.. end of inclusion from "{source}"','']
        info = [(source,start+i-1) for i in range(len(content))]
        self.state_machine.input_lines.insert(self.state_machine.line_offset+1,
                                             statemachine.StringList(content,items=info))


class SourceFiles:
    def __init__(self,reader=None):
        self.reader = reader
        self.available = 4*MAX_STRUCTURE

    def read(self,current,name,encoding):
        if name.startswith('<') and name.endswith('>'):
            filename = name[1:-1]
            if not re.fullmatch(r'[a-z][a-z0-9_-]*\.txt',filename):
                raise Error('rst_include',f'Invalid Docutils standard include {name!r}.')
            standard = Include.standard_include_path/filename
            if not standard.is_file():
                raise Error('rst_include',f'Unknown Docutils standard include {name!r}.')
            source,raw = name,standard.read_bytes()
        elif self.reader:
            source,raw = self.reader(current,name)
        else:
            raise Error('rst_include',f'Include {name!r} needs its local source package.')
        self.available -= len(raw)
        if self.available<0:
            raise Error('source_structure_size','Expanded includes exceed this document’s reading limit.')
        return source,raw.decode(encoding).replace('\r\n','\n').replace('\r','\n')


class Toctree(Directive):
    has_content = True
    option_spec = {'caption':directives.unchanged,'maxdepth':int,'numbered':directives.unchanged,
                   'name':directives.unchanged,'class':directives.class_option,**{name:directives.flag
                   for name in ('titlesonly','glob','reversed','hidden','includehidden')}}

    def run(self):
        source,line = self.state_machine.get_source_and_line(self.lineno)
        marker = nodes.paragraph('',self.options.get('caption') or 'Contents')
        marker.source,marker.line = source,line
        marker['hamr_control'] = {'type':'toctree','options':self.options,'entries':[
            {'entry':text.strip(),'line':offset+1}
            for text,(_,offset) in zip(self.content,self.content.items,strict=True) if text.strip()]}
        return [marker]


class HtmlOnly(Directive):
    has_content = True
    required_arguments = 1
    final_argument_whitespace = True

    def run(self):
        try:
            enabled = self.selected(ast.parse(self.arguments[0],mode='eval').body)
        except (SyntaxError,ValueError,RecursionError) as exc:
            raise self.error(f'Cannot evaluate Sphinx output condition {self.arguments[0]!r}.') from exc
        if not enabled:
            return []
        content = nodes.container()
        memo = self.state.memo
        styles,level = memo.title_styles,memo.section_level
        memo.title_styles,memo.section_level = [],0
        try:
            self.state.nested_parse(self.content,self.content_offset,content,match_titles=True)
        finally:
            memo.title_styles,memo.section_level = styles,level
        return [content]

    @classmethod
    def selected(cls,expr):
        if isinstance(expr,ast.Name) and expr.id in ('html','singlehtml','format_html','builder_singlehtml'):
            return True
        if isinstance(expr,ast.Name) and expr.id in ('latex','man','epub','text','linkcheck',
                                                      'format_latex','format_text','builder_html'):
            return False
        if isinstance(expr,ast.UnaryOp) and isinstance(expr.op,ast.Not):
            return not cls.selected(expr.operand)
        if isinstance(expr,ast.BoolOp):
            values = [cls.selected(value) for value in expr.values]
            return all(values) if isinstance(expr.op,ast.And) else any(values)
        raise ValueError('unknown output tag')


def sphinx_reference(name,raw,text,lineno,inliner,options=None,content=None):
    text = unescape(text)
    if match:=EXPLICIT.fullmatch(text):
        label,target = match.groups()
    else:
        label = target = text.lstrip('!~')
        if text.startswith('~'):
            label = label.rsplit('.',1)[-1]
    cls = ('sphinx-navigation' if name in ('ref','doc') and target in ('genindex','modindex','py-modindex','search')
           else 'sphinx-reference')
    return [nodes.inline(raw,label,classes=[cls])],[]


class LocatedHtml(HTMLTranslator):
    def __init__(self,document):
        super().__init__(document)
        self.annotations = document.settings.hamr_annotations

    def starttag(self,node,tagname,suffix='\n',empty=False,**attributes):
        source = getattr(node,'source',None) or node.get('source')
        line = getattr(node,'line',None) or node.get('line')
        if source and line:
            annotation = {'source_file':source,'lines':[line,line]}
            if 'hamr_control' in node:
                annotation['source_control'] = node['hamr_control']
            if isinstance(node,nodes.system_message):
                annotation['source_issue'] = node[0].astext()
            label = str(len(self.annotations)+1)
            self.annotations[label] = annotation
            attributes['data-hamr-node'] = label
        return super().starttag(node,tagname,suffix,empty,**attributes)

    def visit_system_message(self,node):
        if node['level']>=2:
            self.body.append(self.starttag(node,'p')+'</p>')
        raise nodes.SkipNode


class LocatedWriter(Writer):
    def __init__(self):
        super().__init__()
        self.translator_class = LocatedHtml


def rst_blocks(text,source,sha,*,title='',include=None,sphinx=False,language='en',preamble='',postamble='',config=''):
    annotations = {}
    leading = preamble.rstrip()+'\n\n' if preamble else ''
    ending = '\n\n'+postamble if postamble else ''
    offset = len(leading.splitlines())
    last = offset+len(text.splitlines())
    settings = {'_disable_config':True,'raw_enabled':False,'file_insertion_enabled':False,
                'embed_stylesheet':False,'stylesheet_path':[],'syntax_highlight':'none',
                'halt_level':5,'report_level':2,'warning_stream':io.StringIO(),
                'doctitle_xform':not sphinx,'sectsubtitle_xform':False,
                'math_output':'MathML','language_code':language,'smart_quotes':sphinx,
                'hamr_annotations':annotations,'hamr_files':SourceFiles(include)}
    try:
        # Docutils keeps locally defined roles and registered directives in process state.
        with _PARSE:
            previous = directives._directives.copy(),roles._roles.copy()
            try:
                directives.register_directive('include',SnapshotInclude)
                if sphinx:
                    directives.register_directive('toctree',Toctree)
                    directives.register_directive('only',HtmlOnly)
                    for role in ('doc','ref','any','term','download','numref','py:class','py:func','py:meth',
                                 'py:attr','py:mod','py:exc','py:obj','class','func','meth','attr','mod'):
                        roles.register_local_role(role,sphinx_reference)
                parts = publish_parts(source=leading+text+ending,source_path=source,writer=LocatedWriter(),
                                      settings_overrides=settings)
            finally:
                directives._directives.clear()
                directives._directives.update(previous[0])
                roles._roles.clear()
                roles._roles.update(previous[1])
    except RecursionError as exc:
        raise Error('source_rst_depth','This reStructuredText document is nested too deeply to read.') from exc
    if leading or ending:
        for annotation in annotations.values():
            if annotation['source_file']!=source:
                continue
            start,end = annotation['lines']
            if offset<start<=last:
                annotation['lines'] = [start-offset,end-offset]
                if control:=annotation.get('source_control'):
                    for entry in control['entries']:
                        entry['line'] -= offset
            else:
                annotation.pop('lines')
                annotation.update(source_file=config,setting='rst_prolog' if start<=offset else 'rst_epilog')
    builder = DocumentBuilder('rst',title or parts.get('title'),sha)
    # The fragment excludes the writer's standalone document title.
    body = (parts.get('html_title','')+parts['fragment']) if not sphinx else parts['fragment']
    html_read(body,builder,where='rst',collect_resources=True,source_annotations=annotations,docutils=True)
    builder.doc.language = language
    return builder.doc


def read_rst(path,source,sha,title):
    try:
        text = bounded_read(path).decode(source.encoding or 'utf-8-sig')
    except (UnicodeError,LookupError) as exc:
        raise Error('text_encoding','The reStructuredText source does not decode with the selected encoding.') from exc
    doc = rst_blocks(text,Path(title).name+path.suffix,sha)
    if doc.title=='Book':
        doc.title = title
    for block in doc.blocks:
        block.locator.pop('resource_references',None)
    return doc
