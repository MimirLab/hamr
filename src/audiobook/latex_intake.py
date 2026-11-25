"""Project authored LaTeX structure into the shared manuscript preparation."""

from bisect import bisect_right
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass
from html import escape
from pathlib import PurePosixPath
import re

from pylatexenc.latexwalker import (
    LatexCharsNode, LatexCommentNode, LatexEnvironmentNode, LatexGroupNode,
    LatexMacroNode, LatexMathNode, LatexSpecialsNode, LatexWalker,
    LatexWalkerEndOfStream, LatexWalkerParseError, get_default_latex_context_db,
)
from pylatexenc.macrospec import (
    EnvironmentSpec, MacroSpec, MacroStandardArgsParser, ParsedMacroArgs,
    ParsedVerbatimArgs,
)
from pylatexenc.latex2text import LatexNodes2Text

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, formula_span, html_read
from .latex_declarations import (
    CLASSIC, CLASSIC_ENVIRONMENTS, CODE_SETTINGS, DECLARATIONS, ENVIRONMENT_DECLARATIONS,
    INLINE_CODE, INLINE_DECLARATIONS, OPAQUE_DEFINITIONS, PACKAGE_COMMANDS, PARAMETER,
    document_signature, layout_environment, package_statements, verbatim_environment,
)
from .tex_math import MathMacro, STANDARD_COMMANDS, tex_math


DEPENDENCIES = re.compile(r'\\(?:input|include|includegraphics|bibliography|addbibresource|usepackage|RequirePackage)\b')
ENTRY = re.compile(rb'\\documentclass\s*(?:\[[^\]\n]*\]\s*)?\{')
DOCUMENT_START = re.compile(r'\\begin\s*\{document\}')
HEADINGS = {name:f'h{min(level,6)}' for level,name in enumerate(
    ('part','chapter','section','subsection','subsubsection','paragraph','subparagraph'),1)}
STYLE = {'emph':'em','textit':'em','textsl':'em','textbf':'strong','texttt':'code',
         'textnormal':'span','textrm':'span','textsf':'span','textsc':'span',
         'textsuperscript':'sup','textsubscript':'sub','mbox':'span','text':'span',
         'fbox':'span','framebox':'span','parbox':'span','resizebox':'span','scalebox':'span',
         'textmd':'span','textup':'span','textcolor':'span','underline':'span'}
LITERAL = {'TeX':'TeX','LaTeX':'LaTeX','LaTeXe':'LaTeX two e',
           '%':'%','#':'#','&':'&','_':'_','$':'$','{':'{','}':'}',' ':' ',
           'ldots':'…','dots':'…','textellipsis':'…','textemdash':'—','textendash':'–',
           'textbackslash':'\\','textasciitilde':'~','textasciicircum':'^',
           'aa':'å','AA':'Å','ae':'æ','AE':'Æ','oe':'œ','OE':'Œ','o':'ø','O':'Ø',
           'ss':'ß','l':'ł','L':'Ł','i':'ı','j':'ȷ','enspace':' ','quad':' ','qquad':' ',
           'hfill':' ','hfil':' ', ',':' ',':':' ',';':' ','!':'','/':'',
           '-':'','@':'','\\':' ','newline':' ','linebreak':' '}
LITERAL.update(textlangle='〈',textrangle='〉')
SPECIALS = {'~':' ','---':'—','--':'–',"``":'“',"''":'”'}
ACCENTS = frozenset(("'",'`','^','"','~','=','.', 'u','v','H','c','k','r','b','d','t'))
METADATA = {'title','author','date'}
BRANCHES = {**dict.fromkeys(('IfBooleanTF','IfValueTF','IfNoValueTF'),'{{{'),
            **dict.fromkeys(('IfBooleanT','IfBooleanF','IfValueT','IfValueF','IfNoValueT','IfNoValueF'),'{{')}
INCLUDES = {'input','include'}
REFERENCES = {'ref','pageref','autoref','eqref','cite','citep','citet','parencite','textcite','footcite'}
NAVIGATION = {'tableofcontents','listoffigures','listoftables','printindex','addcontentsline',
              'pdfbookmark','bibliographystyle'}
LAYOUT = {'par','clearpage','cleardoublepage','newpage','pagebreak','nopagebreak','noindent',
          'indent','smallskip','medskip','bigskip','vspace','hspace','vfill','vfil',
          'centering','raggedright','raggedleft','frontmatter','mainmatter','backmatter','appendix',
          'maketitle','label','index','thispagestyle','pagestyle','setlength','addtolength',
          'small','footnotesize','scriptsize','normalsize','large','Large','LARGE','huge','Huge',
          'tiny','bfseries','mdseries','itshape','slshape','upshape','sffamily','ttfamily',
          'rmfamily','normalfont','begingroup','endgroup','restoregeometry',
          'enlargethispage','protect','relax','leavevmode','null','ignorespaces'}
VERBATIM = {'verbatim':'','verbatim*':'','lstlisting':'[','minted':'[{'}
TRANSPARENT = {'abstract','titlepage','center','flushleft','flushright','figure','figure*',
               'table','table*','small','footnotesize','large','minipage','em'}
EQUATIONS = {'equation','equation*','displaymath','math','align','align*','gather','gather*'}
ARGUMENTS = {
    **dict.fromkeys(HEADINGS,'*[{'), **dict.fromkeys(STYLE,'{'),
    **dict.fromkeys(METADATA|INCLUDES|{'label','index','includeonly','url','nolinkurl','Description'},'{'),
    **dict.fromkeys(REFERENCES,'*[[{'),
    'documentclass':'[{','usepackage':'[{','RequirePackage':'[{','inputencoding':'{',
    'includegraphics':'*[{','caption':'*[{','footnote':'[{','item':'[',
    'href':'{{','enquote':'*{','newcommand':'*{[[{','renewcommand':'*{[[{','providecommand':'*{[[{',
    **dict.fromkeys(DECLARATIONS-CLASSIC,'{{{'), **BRANCHES,
    'bibliography':'{','bibliographystyle':'{','addbibresource':'[{','printbibliography':'[',
    'bibitem':'[{','addcontentsline':'{{{','pdfbookmark':'[{{',
    'framebox':'[[{','parbox':'[[[{{','resizebox':'*{{{','scalebox':'{[{',
    'textcolor':'[{{', 'ifthenelse':'{{{', 'InputIfFileExists':'{{{', 'IfFileExists':'{{{',
    'hspace':'*{','vspace':'*{','setlength':'{{','addtolength':'{{','enlargethispage':'*{',
    'thispagestyle':'{','pagestyle':'{','multicolumn':'{{{','cline':'{',
    'setminted':'[{','setmintedinline':'[{','lstset':'{','newmintinline':'[{{',
}
PARAGRAPH = re.compile(r'\n[ \t\r]*\n')
CONTROLS = re.compile(r'[\x00-\x08\x0b\x0e-\x1f]')
UNBRACED_PATH = re.compile(r'[^\s{}\\%#$&^~]+(?=\s|%|$)')
DEFINITION_TARGET = re.compile(r'\s*\\([A-Za-z]+|[^\r\n])')
CODE_ESCAPE = re.compile(r'(?:^|,)\s*(escapeinside|escapechar|mathescape|texcl|texcomments)\s*(?:=\s*(\{[^{}]*\}|[^,]*))?(?=,|$)')


def project_entry(prefix):
    prefix = prefix.removeprefix(b'\xef\xbb\xbf')
    offset = 0
    for line in prefix.splitlines(keepends=True):
        body = line.lstrip()
        if body and not body.startswith(b'%'):
            return bool(ENTRY.match(prefix,offset+len(line)-len(body)))
        offset += len(line)
    return False


class InputArgs(MacroStandardArgsParser):
    def parse_args(self,w,pos,parsing_state=None):
        token = w.get_token(pos)
        while token.tok=='comment':
            token = w.get_token(token.pos+token.len)
        start = token.pos
        if w.s[start:start+1]=='{':
            return super().parse_args(w,pos,parsing_state)
        match = UNBRACED_PATH.match(w.s,start)
        if match is None:
            return ParsedMacroArgs(), pos, 0
        chars = w.make_node(LatexCharsNode,parsing_state=parsing_state,
                            chars=match[0],pos=start,len=len(match[0]))
        return ParsedMacroArgs(argspec='{',argnlist=[chars]), start, len(match[0])


class LiteralEnvironmentArgs(MacroStandardArgsParser):
    def __init__(self,name,argspec,*,line_end=False):
        super().__init__(argspec)
        self.end = r'\end{'+name+'}'
        self.line_end = (re.compile(r'(?m)^[ \t]*(?P<end>'+re.escape(self.end)+r')[ \t]*(?:%[^\r\n]*)?\r?$')
                         if line_end else None)

    def parse_args(self,w,pos,parsing_state=None):
        _, start, size = super().parse_args(w,pos,parsing_state)
        start += size
        if self.line_end:
            marker = self.line_end.search(w.s,start)
            end = marker.start('end') if marker else -1
        else:
            end = w.s.find(self.end,start)
        if end<0:
            raise LatexWalkerParseError(s=w.s,pos=start,msg=f'Expected {self.end}')
        chars = w.make_node(LatexCharsNode,parsing_state=parsing_state,
                            chars=w.s[start:end],pos=start,len=end-start)
        return ParsedVerbatimArgs(verbatim_chars_node=chars), pos, end-pos


class EnvironmentDeclarationArgs(MacroStandardArgsParser):
    def parse_args(self,w,pos,parsing_state=None):
        args,start,size = super().parse_args(w,pos,parsing_state)
        end = start+size
        templates = []
        # Opening and closing templates can contain unmatched environment tokens.
        # Only their two enclosing TeX groups need to balance here.
        for _ in range(2):
            token = w.get_token(end,environments=False,parsing_state=parsing_state)
            while token.tok=='comment':
                token = w.get_token(token.pos+token.len,environments=False,parsing_state=parsing_state)
            if token.tok!='brace_open':
                raise LatexWalkerParseError(s=w.s,pos=token.pos,msg='Expected a LaTeX environment template')
            left = token.pos
            levels = 1
            while levels:
                token = w.get_token(token.pos+token.len,environments=False,parsing_state=parsing_state)
                levels += (token.tok=='brace_open') - (token.tok=='brace_close')
            end = token.pos+token.len
            templates.append(w.make_node(LatexCharsNode,parsing_state=parsing_state,
                                         chars=w.s[left+1:token.pos],pos=left,len=end-left))
        return ParsedMacroArgs(argspec=self.argspec+'{{',argnlist=args.argnlist+templates),start,end-start


class InlineCodeArgs(MacroStandardArgsParser):
    def parse_args(self,w,pos,parsing_state=None):
        args,start,size = super().parse_args(w,pos,parsing_state)
        left = start+size
        while left<len(w.s) and w.s[left] in ' \t':
            left += 1
        limit = w.s.find('\n',left)
        if limit<0:
            limit = len(w.s)
        opening = w.s[left:left+1]
        if left>=limit or opening.isspace() or opening.isalnum() or opening in ('\\','%','}'):
            return args,start,left-start
        if opening=='{':
            levels = 1
            right = left+1
            while right<limit and levels:
                levels += (w.s[right]=='{') - (w.s[right]=='}')
                if levels:
                    right += 1
            if levels:
                return args,start,left-start
        else:
            right = w.s.find(opening,left+1,limit)
            if right<0:
                return args,start,left-start
        chars = w.make_node(LatexCharsNode,parsing_state=parsing_state,
                            chars=w.s[left+1:right],pos=left+1,len=right-left-1)
        return ParsedMacroArgs(argspec=self.argspec+'{',argnlist=args.argnlist+[chars]),start,right+1-start


def context():
    db = get_default_latex_context_db()
    environments = [EnvironmentSpec(name,args) for name,args in {
        'document':'','figure':'[','figure*':'[','table':'[','table*':'[',
        'tabular':'[{','tabular*':'{[{','thebibliography':'{','minipage':'[[[{',
    }.items()]
    environments.extend(EnvironmentSpec(name,args_parser=LiteralEnvironmentArgs(name,args))
                        for name,args in VERBATIM.items())
    macros = [MacroSpec(name,args) for name,args in ARGUMENTS.items() if name!='input']
    macros.append(MacroSpec('input',args_parser=InputArgs('{')))
    macros.extend(MacroSpec(name,args_parser=EnvironmentDeclarationArgs(
        '*{[[' if name in CLASSIC_ENVIRONMENTS else '{{')) for name in ENVIRONMENT_DECLARATIONS)
    macros.extend(MacroSpec(name,args_parser=InlineCodeArgs(signature)) for name,signature in
                  {'mintinline':'*[{','Verb':'*[','lstinline':'['}.items())
    db.add_context_category('hamr-source',macros=macros,environments=environments,prepend=True)
    return db


def arguments(node):
    return tuple(arg for arg in (node.nodeargd.argnlist if node.nodeargd else ()) if arg is not None)


def group(node):
    return node.nodelist if isinstance(node,LatexGroupNode) else [node]


def condition_start(name):
    return bool(name and name.startswith('if') and name!='ifthenelse')


@dataclass(frozen=True)
class File:
    name: str
    text: str
    newlines: tuple

    @classmethod
    def make(cls,name,text):
        return cls(name,text,tuple(match.start() for match in re.finditer(r'\n',text)))

    def raw(self,node):
        return self.text[node.pos:node.pos+node.len]

    def location(self,node,end=None):
        start = node if isinstance(node,int) else node.pos
        end = end if end is not None else start+(0 if isinstance(node,int) else node.len)
        return {'source_file':self.name,'character_range':[start,end],
                'lines':[bisect_right(self.newlines,start-1)+1,bisect_right(self.newlines,max(start,end-1))+1]}


@dataclass(frozen=True)
class ProseCommand:
    file: File
    value: LatexGroupNode
    signature: str
    default: LatexGroupNode | None = None
    expl3: bool = False


@dataclass(frozen=True)
class Expansion:
    bindings: list
    expl3: bool


@dataclass(frozen=True)
class EnvironmentLayout:
    tag: str | None
    definition: dict


class Projection:
    def __init__(self,include,encoding,root):
        self.include = include
        self.encoding = encoding
        self.root = root
        self.db = context()
        self.annotations = {}
        self.notes = []
        self.metadata = {}
        self.definitions = {}
        self.layouts = {}
        self.code_settings = {}
        self.inline_aliases = {}
        self.math_macros = {}
        self.macro_origins = {}
        self.inventory = []
        self.selected = None
        self.remaining = 4*MAX_STRUCTURE
        self.nodes_left = 500_000
        self.inside_notes = False
        self.files = {}
        self.walkers = {}
        self.packages = set()
        self.parameters = None
        self.expansion_depth = 0

    def count(self,nodes):
        self.nodes_left -= len(nodes)
        if self.nodes_left<0:
            raise Error('latex_complexity','Expanded LaTeX content exceeds the structured reading budget.')

    def tag(self,name,where,body,attrs='',**fields):
        ident = str(len(self.annotations)+1)
        self.annotations[ident] = {**where,'source_element':
            f"{where['source_file']}:{where['character_range'][0]}-{where['character_range'][1]}",**fields}
        return f'<{name} data-hamr-node="{ident}"{attrs}>{body}</{name}>'

    def issue(self,file,node,message,inline=False):
        if self.expansion_depth:
            raise ValueError(message)
        where = file.location(node)
        if inline:
            raw = file.raw(node)
            return self.tag('span',where,escape(raw),reading_span={
                'kind':'source_review','source':raw,'issue':message,'source_locator':where},
                inline_location=where)
        return self.tag('p',where,'',source_issue=message)

    def source(self,node,file,extensions):
        value = self.literal_arg(node,file)
        path = PurePosixPath(value)
        if not value or path.is_absolute() or any(char in value for char in '\\{}#:%$~\0'):
            raise Error('latex_include','The LaTeX resource needs a literal relative path.')
        if path.suffix:
            return [value]
        return [value+extension for extension in extensions]

    def literal_arg(self,node,file):
        args = arguments(node)
        if not args:
            return ''
        arg = args[-1]
        if node.macroname=='input' and isinstance(arg,LatexCharsNode):
            return arg.chars
        if not isinstance(arg,LatexGroupNode) or any(not isinstance(n,LatexCharsNode) for n in arg.nodelist):
            return ''
        return ''.join(n.chars for n in arg.nodelist).strip()

    def load(self,paths):
        if self.include is None:
            raise Error('latex_include',f'The saved source has no local resource {paths[0]!r}.')
        error = None
        for path in paths:
            try:
                name,data = self.include(self.root,path)
            except Error as exc:
                if exc.code!='latex_include' or 'is repeated' in exc.message:
                    raise
                error = exc
                continue
            self.remaining -= len(data)
            if self.remaining<0:
                raise Error('source_structure_size','Expanded LaTeX sources exceed the structured reading budget.')
            return name,data
        raise error

    def parsed(self,file):
        if file.name not in self.files:
            self.files[file.name] = self.walker(file).get_latex_nodes()[0]
        return self.files[file.name]

    def walker(self,file):
        if file.name not in self.walkers:
            self.walkers[file.name] = LatexWalker(file.text,latex_context=self.db,tolerant_parsing=False)
        return self.walkers[file.name]

    def render_entry(self,file,chain,*,preamble=False):
        # Bootstrap literal readers declared before the document begins.
        if DOCUMENT_START.search(file.text):
            walker = self.walker(file)
            prefix = []
            start = 0
            conditional = 0
            while start<len(file.text):
                try:
                    token = walker.get_token(start)
                except LatexWalkerEndOfStream:
                    break
                if not conditional and token.tok=='begin_environment' and token.arg=='document':
                    front = self.render(file,prefix,chain,preamble=True)
                    body = walker.get_latex_nodes(pos=token.pos,read_max_nodes=1)[0]
                    return front+self.render(file,body,chain,preamble=True)
                part,pos,size = walker.get_latex_nodes(pos=start,read_max_nodes=1)
                if not size:
                    break
                for node in part:
                    if isinstance(node,LatexMacroNode):
                        name = node.macroname
                        conditional = max(0,conditional+condition_start(name)-(name=='fi'))
                prefix.extend(part)
                start = pos+size
        nodes = self.parsed(file)
        return self.render(file,nodes,chain,preamble=preamble or any(
            isinstance(node,LatexMacroNode) and node.macroname=='documentclass' for node in nodes))

    def bound_nodes(self,file,nodes):
        """Bind calls in reading order, retaining the original source interval."""
        cursor = 0
        while cursor<len(nodes):
            node = nodes[cursor]
            cursor += 1
            command = self.definitions.get(node.macroname) if isinstance(node,LatexMacroNode) else None
            if not command or not command.signature or getattr(node,'hamr_bound',False):
                yield node
                continue
            start = node.pos+1+len(node.macroname)
            try:
                args,pos,size = MacroStandardArgsParser(command.signature).parse_args(
                    self.walker(file),start,parsing_state=node.parsing_state)
            except (LatexWalkerEndOfStream,LatexWalkerParseError,ValueError):
                yield node
                continue
            end = max(pos+size,node.pos+node.len)
            if end>nodes[-1].pos+nodes[-1].len:
                yield node
                continue
            stop = cursor
            while stop<len(nodes) and nodes[stop].pos+nodes[stop].len<=end:
                stop += 1
            tail = nodes[stop] if stop<len(nodes) and nodes[stop].pos<end else None
            if tail is not None and not isinstance(tail,LatexCharsNode):
                yield node
                continue
            call = copy(node)
            call.nodeargd,call.len,call.hamr_bound = args,end-node.pos,True
            cursor = stop
            yield call
            if tail is not None:
                cursor += 1
                rest = copy(tail)
                rest.pos,rest.len = end,tail.pos+tail.len-end
                rest.chars = file.raw(rest)
                yield rest

    def formula(self,file,node,raw,expression):
        loc = file.location(node)
        used = set()
        span = formula_span(raw,lambda:tex_math(expression,self.math_macros,used),0,'Authored LaTeX math',loc)
        if used:
            span['macro_definitions'] = [self.macro_origins[name] for name in sorted(used)]
        return self.tag('span',loc,escape(raw),reading_span=span,inline_location=loc)

    def macro(self,file,node,depth):
        name = node.macroname
        args = arguments(node)
        if name in LITERAL:
            return escape(LITERAL[name])
        if name in STYLE and args:
            return f'<{STYLE[name]}>{self.inline(file,group(args[-1]),depth+1)}</{STYLE[name]}>'
        if name=='enquote' and args:
            return '“'+self.inline(file,group(args[-1]),depth+1)+'”'
        if name in ACCENTS and args:
            return escape(LatexNodes2Text().nodelist_to_text([node]))
        if name in LAYOUT:
            return ''
        if name in self.definitions:
            command = self.definitions[name]
            if not command.signature and not command.expl3 and not self.expansion_depth:
                return self.inline(command.file,group(command.value),depth+1)
            return self.invoke(file,node,command,depth)
        if name in BRANCHES and self.expansion_depth:
            return self.branch(file,node,depth)
        if self.expansion_depth:
            return self.issue(file,node,f'The LaTeX command \\{name} needs a verified reading.',True)
        if name in INLINE_CODE or name in self.inline_aliases:
            value = self.inline_code(file,node)
            if value is None:
                return self.issue(file,node,'This LaTeX code example needs its literal boundaries or escapes verified.',True)
            where = file.location(node)
            declaration = self.inline_aliases.get(name)
            if declaration:
                self.inventory.append({'kind':'latex_inline_code','definition':declaration[2],**where})
            return self.tag('code',where,escape(value),inline_location=where)
        if name=='footnote' and args and not self.inside_notes:
            ident = f'latex-note-{len(self.notes)+1}'
            self.inside_notes = True
            try:
                note = self.inline(file,group(args[-1]),depth+1)
            finally:
                self.inside_notes = False
            self.notes.append(self.tag('aside',file.location(args[-1]),'<p>'+note+'</p>',
                                       attrs=f' role="doc-footnote" id="{ident}"'))
            loc = file.location(node)
            return self.tag('a',loc,'note',attrs=f' role="doc-noteref" href="#{ident}"',inline_location=loc)
        if name in ('href','url','nolinkurl') and args:
            target = self.literal_arg(node,file) if name!='href' else file.raw(args[0])[1:-1]
            visible = self.inline(file,group(args[-1]),depth+1) if name=='href' else escape(target)
            return f'<a href="{escape(target,quote=True)}">{visible}</a>'
        if name in REFERENCES:
            return self.issue(file,node,'This LaTeX reference has no verified resolved reading.',True)
        return self.issue(file,node,f'The LaTeX command \\{name} needs a verified reading.',True)

    def inline(self,file,nodes,depth=0):
        self.count(nodes)
        if depth>=24:
            if self.expansion_depth:
                raise ValueError('The LaTeX text expansion exceeds its reading depth.')
            raise Error('latex_depth','Nested LaTeX text exceeds the supported reading depth.')
        pieces = []
        for node in self.bound_nodes(file,nodes):
            if isinstance(node,LatexCharsNode):
                pieces.append(self.template_text(node.chars,depth))
            elif isinstance(node,LatexGroupNode):
                pieces.append(self.inline(file,node.nodelist,depth+1))
            elif isinstance(node,LatexMathNode):
                if self.expansion_depth:
                    raise ValueError('A generated LaTeX formula needs its source reading verified.')
                raw = file.raw(node)
                left,right = node.delimiters
                pieces.append(self.formula(file,node,raw,raw[len(left):-len(right)]))
            elif isinstance(node,LatexSpecialsNode):
                pieces.append(escape(SPECIALS.get(node.specials_chars,node.specials_chars)))
            elif isinstance(node,LatexMacroNode):
                pieces.append(self.macro(file,node,depth))
            elif not isinstance(node,LatexCommentNode):
                pieces.append(self.issue(file,node,'This LaTeX inline structure needs a verified reading.',True))
        return ''.join(pieces)

    def argument(self,index):
        if self.parameters is None or index>len(self.parameters.bindings):
            raise ValueError('The LaTeX command has an unbound parameter.')
        return self.parameters.bindings[index-1]

    def template_text(self,text,depth):
        def plain(value):
            space = '' if self.parameters is not None and self.parameters.expl3 else ' '
            return escape(re.sub(r'[\r\n\t ]+',space,value))
        if self.parameters is None:
            return plain(text)
        result = []
        start = 0
        for match in PARAMETER.finditer(text):
            result.append(plain(text[start:match.start()]))
            owner,value,parameters,mode = self.argument(int(match[1]))
            if value is None or mode=='*':
                raise ValueError('This LaTeX template prints a control value that needs review.')
            outer = self.parameters
            self.parameters = parameters
            try:
                result.append(self.inline(owner,group(value),depth+1))
            finally:
                self.parameters = outer
            start = match.end()
        result.append(plain(text[start:]))
        return ''.join(result)

    def branch(self,file,node,depth):
        parts = arguments(node)
        if not parts or len(parts)!=len(BRANCHES[node.macroname]):
            raise ValueError('A LaTeX branch has missing content.')
        condition,*branches = parts
        slot = PARAMETER.fullmatch(file.raw(condition)[1:-1].strip())
        if not slot:
            raise ValueError('A LaTeX branch needs a literal parameter test.')
        _,value,_,mode = self.argument(int(slot[1]))
        name = node.macroname
        if name.startswith('IfBoolean') and mode!='*':
            raise ValueError('A LaTeX boolean test needs a declared star argument.')
        if name.startswith('IfBoolean'):
            matched = value is not None
        else:
            missing = value is None and mode!='*'
            matched = missing if name.startswith('IfNoValue') else not missing
        if len(branches)==2:
            return self.inline(file,group(branches[0 if matched else 1]),depth+1)
        return self.inline(file,group(branches[0]),depth+1) if matched==name.endswith('T') else ''

    def invoke(self,file,node,command,depth):
        if command.signature and not getattr(node,'hamr_bound',False):
            return self.issue(file,node,'A LaTeX command is missing its declared arguments.',True)
        if self.expansion_depth>=12:
            raise ValueError('The LaTeX text expansion exceeds its reading depth.')
        previous = self.parameters
        bindings = []
        supplied = node.nodeargd.argnlist if command.signature else ()
        for index,(mode,value) in enumerate(zip(command.signature,supplied)):
            if index==0 and value is None and command.default is not None:
                bindings.append((command.file,command.default,None,mode))
            elif previous is not None and mode!='*' and isinstance(value,LatexGroupNode) and (
                    slot:=PARAMETER.fullmatch(file.raw(value)[1:-1])):
                bindings.append(self.argument(int(slot[1])))
            else:
                bindings.append((file,value,previous,mode))
        self.parameters = Expansion(bindings,command.expl3)
        self.expansion_depth += 1
        problem = None
        try:
            output = self.inline(command.file,group(command.value),depth+1)
        except (ValueError,LatexWalkerParseError) as error:
            problem = str(error)
        finally:
            self.expansion_depth -= 1
            self.parameters = previous
        if problem is not None:
            return self.issue(file,node,problem,True)
        self.inventory.append({'kind':'latex_declared_text','command':node.macroname,
                               'definition':self.macro_origins[node.macroname],**file.location(node)})
        return output

    def setup(self,file,node):
        name = node.macroname
        args = arguments(node)
        if name in METADATA and args:
            self.metadata[name] = (file,args[-1])
        elif name=='includeonly':
            allowed = self.literal_arg(node,file)
            if allowed or args and file.raw(args[-1])=='{}':
                self.selected = {s.strip().removesuffix('.tex') for s in allowed.split(',') if s.strip()}
            else:
                return self.issue(file,node,'LaTeX includeonly needs a literal list of source members.')
        elif name in DECLARATIONS:
            self.define(file,node)
        elif name in ENVIRONMENT_DECLARATIONS:
            self.define_environment(file,node)
        elif name in CODE_SETTINGS:
            self.set_code_options(file,node)
        elif name in INLINE_DECLARATIONS:
            self.define_inline_code(file,node)
        return ''

    def set_code_options(self,file,node):
        args = node.nodeargd.argnlist
        language = file.raw(args[0])[1:-1].strip() if len(args)==2 and args[0] is not None else ''
        options = self.code_settings.setdefault((node.macroname,language),{})
        options.update(self.code_options(file.raw(args[-1])[1:-1]))

    @staticmethod
    def code_options(text):
        options = {}
        for key,value in CODE_ESCAPE.findall(text):
            value = (value or 'true').strip()
            braced = re.fullmatch(r'\{([^{}]*)\}',value)
            options[key] = braced[1] if braced else value
        return options

    def define_inline_code(self,file,node):
        alias,lexer,option_set = node.nodeargd.argnlist
        language = file.raw(lexer)[1:-1].strip()
        name = file.raw(alias)[1:-1].strip() if alias is not None else language+'inline'
        if not re.fullmatch(r'[A-Za-z]+',name) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+-]*',language):
            return
        if self.db.get_macro_spec(name) is not None or name in self.definitions:
            return
        options = self.code_options(file.raw(option_set)[1:-1])
        self.inline_aliases[name] = (language,options,file.location(node))
        self.db.add_context_category('hamr-inline-'+name,prepend=True,
                                     macros=[MacroSpec(name,args_parser=InlineCodeArgs('*['))])

    def inline_code(self,file,node):
        name = node.macroname
        if name=='verb':
            return getattr(node.nodeargd,'verbatim_text',None)
        if not isinstance(node.nodeargd,ParsedMacroArgs):
            return None
        args = node.nodeargd.argnlist
        alias = self.inline_aliases.get(name)
        count = 3 if alias else {'mintinline':4,'Verb':3,'lstinline':2}[name]
        if len(args)!=count or not isinstance(args[-1],LatexCharsNode):
            return None
        opt = args[-3] if name=='mintinline' else args[-2]
        options = {}
        if name=='mintinline' or alias:
            language = alias[0] if alias else file.raw(args[-2])[1:-1].strip()
            for key in ('setminted','setmintedinline'):
                options.update(self.code_settings.get((key,''),{}))
                options.update(self.code_settings.get((key,language),{}))
            if alias:
                options.update(alias[1])
        elif name=='lstinline':
            options.update(self.code_settings.get(('lstset',''),{}))
        if opt is not None:
            options.update(self.code_options(file.raw(opt)[1:-1]))
        code = args[-1].chars
        for key,value in options.items():
            if value in ('','false','none'):
                continue
            if key=='mathescape' and '$' in code:
                return None
            if key in ('texcl','texcomments'):
                return None
            if key in ('escapeinside','escapechar'):
                marks = re.findall(r'\{([^{}]+)\}',value) or list(value)
                if any(mark in code for mark in marks):
                    return None
        return code

    def define_environment(self,file,node):
        args = node.nodeargd.argnlist
        opening,closing = args[-2:]
        signature = None
        if node.macroname in CLASSIC_ENVIRONMENTS:
            _,target,count,default = args[:-2]
            argless = default is None and (count is None or file.raw(count)[1:-1].strip()=='0')
        else:
            target,spec = args[:-2]
            spec = file.raw(spec)[1:-1]
            argless = not spec.strip()
            signature = verbatim_environment(opening.chars,closing.chars,spec)
        name = file.raw(target)[1:-1].strip()
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9*@-]*',name):
            return
        if node.macroname=='ProvideDocumentEnvironment' and (
                name in self.layouts or name in TRANSPARENT|EQUATIONS|VERBATIM.keys()
                or self.db.get_environment_spec(name) is not None):
            return
        tag = layout_environment(opening.chars,closing.chars) if (
            argless and opening.len+closing.len<=4096) else None
        if signature is not None and self.db.get_environment_spec(name) is None:
            self.db.add_context_category('hamr-verbatim-'+name,prepend=True,
                                         environments=[EnvironmentSpec(name,args_parser=LiteralEnvironmentArgs(
                                             name,signature,line_end=True))])
            tag = 'verbatim_source'
        self.layouts[name] = EnvironmentLayout(tag,file.location(node))

    def define(self,file,node,*,expl3=False):
        if node.macroname in CLASSIC:
            _,target,count,default,value = node.nodeargd.argnlist
            arity = file.raw(count)[1:-1].strip() if count is not None else '0'
            signature = None
            if re.fullmatch(r'[0-9]',arity) and (default is None or arity!='0'):
                signature = ('['+'{'*(int(arity)-1)) if default is not None else '{'*int(arity)
        else:
            target,spec,value = node.nodeargd.argnlist
            signature = document_signature(file.raw(spec)[1:-1])
            default = None
        lhs = group(target)
        if len(lhs)!=1 or not isinstance(lhs[0],LatexMacroNode):
            return
        name = lhs[0].macroname
        if node.macroname in ('providecommand','ProvideDocumentCommand') and (name in self.math_macros or name in STANDARD_COMMANDS
                                                  or name in ARGUMENTS or name in LITERAL or name in LAYOUT):
            return
        self.definitions.pop(name,None)
        macro = None
        if signature is not None and isinstance(value,LatexGroupNode):
            self.definitions[name] = ProseCommand(file,value,signature,default,expl3)
            if all(kind=='{' or index==0 and default is not None and kind=='[' for index,kind in enumerate(signature)):
                macro = MathMacro.bound(len(signature),file.raw(value)[1:-1],file.raw(default)[1:-1] if default else None)
        self.math_macros[name] = macro
        self.macro_origins[name] = {'command':name,**file.location(node)}

    def undefine_reading(self,file,node):
        start = node.pos+node.len
        if target:=DEFINITION_TARGET.match(file.text,start):
            name = target[1]
            self.definitions.pop(name,None)
            self.layouts.pop(name,None)
            if name.startswith('end'):
                self.layouts.pop(name[3:],None)
            self.math_macros[name] = None
            self.macro_origins[name] = {'command':name,**file.location(node.pos,target.end())}

    @contextmanager
    def macro_scope(self):
        definitions, macros, origins, layouts = self.definitions,self.math_macros,self.macro_origins,self.layouts
        settings = self.code_settings
        self.definitions,self.math_macros,self.macro_origins = definitions.copy(),macros.copy(),origins.copy()
        self.layouts = layouts.copy()
        self.code_settings = {key:value.copy() for key,value in settings.items()}
        try:
            yield
        finally:
            self.definitions,self.math_macros,self.macro_origins,self.layouts = definitions,macros,origins,layouts
            self.code_settings = settings

    def literal_end(self,file,start,end):
        try:
            nodes,_,_ = self.walker(file).get_latex_nodes(pos=start,read_max_nodes=1)
            if nodes and nodes[0].nodeargd and nodes[0].nodeargd.argnlist[-1:] and (
                    isinstance(nodes[0].nodeargd.argnlist[-1],LatexCharsNode)):
                return nodes[0].pos+nodes[0].len
        except (LatexWalkerParseError,RecursionError):
            pass
        line = file.text.find('\n',end)
        return len(file.text) if line<0 else line

    def package(self,file,node,chain):
        names = self.literal_arg(node,file)
        if not names:
            return
        for part in names.split(','):
            name = part.strip()
            if not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*',name) or '..' in PurePosixPath(name).parts:
                continue
            member = name if name.endswith('.sty') else name+'.sty'
            if member in self.packages or len(chain)>=24:
                continue
            self.packages.add(member)
            try:
                path,data = self.load([member])
                style = File.make(path,decode_latex(data,self.encoding))
            except Error:
                continue
            self.inventory.append({'kind':'latex_style_package','target':path,**file.location(node)})
            for position,expl3 in package_statements(
                    style.text,lambda start,end:self.literal_end(style,start,end),self.inline_aliases):
                try:
                    statements,_,_ = self.walker(style).get_latex_nodes(pos=position,read_max_nodes=1)
                except (LatexWalkerParseError,RecursionError):
                    continue
                if len(statements)!=1 or not isinstance(statements[0],LatexMacroNode):
                    continue
                declaration = statements[0]
                if declaration.macroname in PACKAGE_COMMANDS:
                    self.package(style,declaration,[*chain,path])
                elif declaration.macroname in DECLARATIONS:
                    self.define(style,declaration,expl3=expl3)
                elif declaration.macroname in ENVIRONMENT_DECLARATIONS:
                    self.define_environment(style,declaration)
                elif declaration.macroname in CODE_SETTINGS:
                    self.set_code_options(style,declaration)
                elif declaration.macroname in INLINE_DECLARATIONS:
                    self.define_inline_code(style,declaration)
                elif declaration.macroname in OPAQUE_DEFINITIONS:
                    self.undefine_reading(style,declaration)

    def include_text(self,file,node,chain,preamble,flow=None):
        try:
            paths = self.source(node,file,('.tex',))
            if node.macroname=='include' and self.selected is not None and paths[0].removesuffix('.tex') not in self.selected:
                self.inventory.append({'kind':'latex_include','status':'excluded','target':paths[0],**file.location(node)})
                return ''
            name,data = self.load(paths)
            if name in chain or len(chain)>=24:
                raise Error('latex_include',f'Circular or deeply nested LaTeX include {name!r}.')
            child = File.make(name,decode_latex(data,self.encoding))
            if preamble:
                body = self.render_entry(child,[*chain,name],preamble=True)
            else:
                nodes = self.parsed(child)
        except (Error,LatexWalkerParseError,RecursionError) as exc:
            return self.issue(file,node,f'LaTeX include needs source review. {exc}')
        self.inventory.append({'kind':'latex_include','status':'included','target':name,**file.location(node)})
        if preamble:
            return body
        return self.render(child,nodes,[*chain,name],preamble=preamble,flow=flow)

    def table(self,file,node):
        rows = []
        cells = []
        content = []
        for item in node.nodelist:
            if isinstance(item,LatexSpecialsNode) and item.specials_chars=='&':
                cells.append(content)
                content = []
            elif isinstance(item,LatexMacroNode) and item.macroname in ('\\','tabularnewline'):
                rows.append([*cells,content])
                cells,content = [],[]
            elif isinstance(item,LatexMacroNode) and item.macroname in ('hline','toprule','midrule','bottomrule'):
                continue
            else:
                content.append(item)
        if cells or any(file.raw(item).strip() for item in content):
            rows.append([*cells,content])
        markup = ''.join('<tr>'+''.join('<td>'+self.inline(file,cell)+'</td>' for cell in row)+'</tr>' for row in rows)
        return self.tag('table',file.location(node),markup)

    def items(self,file,node,chain):
        items = []
        current = None
        for child in node.nodelist:
            if isinstance(child,LatexMacroNode) and child.macroname in ('item','bibitem'):
                current = [child,[]]
                items.append(current)
            elif current:
                current[1].append(child)
            elif not isinstance(child,LatexCommentNode) and file.raw(child).strip():
                return self.issue(file,node,'The LaTeX list has content before its first item.')
        if node.environmentname=='thebibliography':
            return ''.join(self.tag('p',file.location(start),self.inline(file,body),source_role='reference')
                           for start,body in items)
        tag = 'ol' if node.environmentname=='enumerate' else 'ul'
        markup = []
        number = 1
        for start,body in items:
            explicit = arguments(start)
            label = self.inline(file,group(explicit[0])) if explicit else ''
            if label:
                label = '<p>'+label+'.</p>'
            attrs = ''
            if tag=='ol':
                if explicit:
                    attrs = ' style="list-style-type:none"'
                else:
                    attrs = f' value="{number}"'
                    number += 1
            end = body[-1].pos+body[-1].len if body else start.pos+start.len
            markup.append(self.tag('li',file.location(start.pos,end),label+self.render(file,body,chain),attrs=attrs))
        return f'<{tag}>'+''.join(markup)+f'</{tag}>'

    def environment(self,file,node,chain):
        name = node.environmentname
        where = file.location(node)
        if layout:=self.layouts.get(name):
            if layout.tag=='verbatim_source' and isinstance(node.nodeargd,ParsedVerbatimArgs):
                code = self.tag('pre',where,escape(node.nodeargd.verbatim_text),source_control=True,
                                environment_definition=layout.definition)
                return code+self.issue(file,node,f'The generated output of LaTeX {name!r} needs a verified reading.')
            if layout.tag and name not in VERBATIM and (body:=self.code_display(file,node.nodelist)):
                return self.tag('pre',where,body,environment_definition=layout.definition)
            if layout.tag in ('div','blockquote') and name not in VERBATIM:
                with self.macro_scope():
                    body = self.render(file,node.nodelist,chain)
                return self.tag(layout.tag,where,body,environment_definition=layout.definition)
            return self.issue(file,node,f'The LaTeX {name!r} environment needs a verified reading.')
        if name in VERBATIM:
            arg = getattr(node.nodeargd,'verbatim_text','')
            return self.tag('pre',where,escape(arg))
        if name in EQUATIONS:
            nodes = node.nodelist
            expression = file.text[nodes[0].pos:nodes[-1].pos+nodes[-1].len] if nodes else ''
            return self.tag('p',where,self.formula(file,node,file.raw(node),expression),source_role='equation')
        if name in ('itemize','enumerate','description','thebibliography'):
            with self.macro_scope():
                return self.items(file,node,chain)
        if name in ('tabular','tabular*'):
            return self.table(file,node)
        if name in TRANSPARENT or name in ('quote','quotation','verse'):
            with self.macro_scope():
                return self.tag('div' if name in TRANSPARENT else 'blockquote',where,self.render(file,node.nodelist,chain))
        return self.issue(file,node,f'The LaTeX {name!r} environment needs a verified reading.')

    def code_display(self,file,nodes):
        pieces = []
        for item in nodes:
            if isinstance(item,LatexCommentNode) or isinstance(item,LatexCharsNode) and not item.chars.strip():
                continue
            if not isinstance(item,LatexMacroNode):
                return None
            if item.macroname=='\\':
                pieces.append('\n')
            elif (item.macroname in INLINE_CODE or item.macroname in self.inline_aliases) and (
                    code:=self.inline_code(file,item)) is not None:
                pieces.append(escape(code))
            elif item.macroname=='texttt' and arguments(item) and all(
                    isinstance(child,LatexCharsNode) for child in group(arguments(item)[-1])):
                pieces.append(self.inline(file,group(arguments(item)[-1])))
            else:
                return None
        return ''.join(pieces).strip('\n')

    def assets(self,file,node):
        try:
            paths = self.source(node,file,('.pdf','.png','.jpg','.jpeg','.eps') if
                                node.macroname=='includegraphics' else ('.bib',))
            name,_ = self.load(paths)
            self.inventory.append({'kind':'latex_resource','target':name,**file.location(node)})
        except Error as exc:
            return self.issue(file,node,str(exc))
        if node.macroname=='includegraphics':
            return self.tag('p',file.location(node),f'<img src="{escape(name,quote=True)}">')
        return ''

    def render(self,file,nodes,chain,*,preamble=False,flow=None):
        self.count(nodes)
        output, paragraph = flow if flow is not None else ([],[])
        conditional = 0
        def flush():
            if paragraph:
                text = ''.join(item[3] for item in paragraph)
                if text.strip():
                    origins = []
                    for owner,start,end,_ in paragraph:
                        if origins and origins[-1][0] is owner:
                            origins[-1][2] = end
                        else:
                            origins.append([owner,start,end])
                    locations = [owner.location(start,end) for owner,start,end in origins]
                    fragments = {'source_fragments':locations} if len(locations)>1 else {}
                    output.append(self.tag('p',locations[0],text,**fragments))
                paragraph.clear()
        def append(node,text):
            paragraph.append((file,node.pos,node.pos+node.len,text))
        for node in self.bound_nodes(file,nodes):
            name = node.macroname if isinstance(node,LatexMacroNode) else None
            args = arguments(node) if name else ()
            opens_condition = condition_start(name)
            if conditional:
                conditional += bool(opens_condition) - (name=='fi')
                continue
            if opens_condition:
                flush()
                conditional = 1
                output.append(self.issue(file,node,'This LaTeX conditional needs its selected reading verified.'))
                continue
            if isinstance(node,LatexEnvironmentNode) and node.environmentname=='document':
                flush()
                output.append(self.render(file,node.nodelist,chain))
                break
            if name=='input' and not preamble:
                if issue:=self.include_text(file,node,chain,preamble,(output,paragraph)):
                    flush()
                    output.append(issue)
            elif name in INCLUDES:
                flush()
                output.append(self.include_text(file,node,chain,preamble))
            elif name in PACKAGE_COMMANDS:
                self.package(file,node,chain)
            elif name in METADATA|{'includeonly'}|DECLARATIONS|ENVIRONMENT_DECLARATIONS|CODE_SETTINGS:
                output.append(self.setup(file,node))
            elif name in INLINE_DECLARATIONS:
                if preamble:
                    output.append(self.setup(file,node))
                else:
                    append(node,self.issue(file,node,'This inline LaTeX code declaration needs source review.',True))
            elif name in OPAQUE_DEFINITIONS:
                self.undefine_reading(file,node)
                if not preamble:
                    append(node,self.issue(file,node,'This LaTeX declaration needs a verified reading.',True))
            elif name in {'addbibresource','bibliography'}:
                flush()
                output.append(self.assets(file,node))
                if name=='bibliography' and not preamble:
                    output.append(self.issue(file,node,'Generated bibliography needs an authored or exported reading.'))
            elif preamble or isinstance(node,LatexCommentNode):
                continue
            elif isinstance(node,LatexEnvironmentNode) and (
                    layout:=self.layouts.get(node.environmentname)) and layout.tag=='transparent':
                self.inventory.append({'kind':'latex_layout_environment','definition':layout.definition,
                                       **file.location(node)})
                with self.macro_scope():
                    self.render(file,node.nodelist,chain,flow=(output,paragraph))
            elif name=='printbibliography':
                flush()
                output.append(self.issue(file,node,'Generated bibliography needs an authored or exported reading.'))
            elif name in NAVIGATION:
                continue
            elif name in HEADINGS or name in ('caption','maketitle','includegraphics','par') or isinstance(node,LatexEnvironmentNode):
                flush()
                if name in HEADINGS or name=='caption':
                    if args:
                        tag = HEADINGS.get(name,'figcaption')
                        value = self.inline(file,group(args[-1]))
                        output.append(self.tag(tag,file.location(node),value))
                    else:
                        output.append(self.issue(file,node,'The LaTeX heading or caption lacks its text argument.'))
                elif name=='maketitle':
                    for field in ('title','author','date'):
                        if field in self.metadata:
                            owner,value = self.metadata[field]
                            output.append(self.tag('h1' if field=='title' else 'p',owner.location(value),
                                                   self.inline(owner,group(value))))
                elif name=='includegraphics':
                    output.append(self.assets(file,node))
                elif isinstance(node,LatexEnvironmentNode):
                    output.append(self.environment(file,node,chain))
            elif isinstance(node,LatexCharsNode):
                start = 0
                for br in PARAGRAPH.finditer(node.chars):
                    paragraph.append((file,node.pos+start,node.pos+br.start(),escape(node.chars[start:br.start()])))
                    flush()
                    start = br.end()
                if start:
                    paragraph.append((file,node.pos+start,node.pos+node.len,escape(node.chars[start:])))
                else:
                    append(node,self.inline(file,[node]))
            else:
                append(node,self.inline(file,[node]))
        if flow is not None:
            return ''
        flush()
        return ''.join(output)


def decode_latex(raw,encoding):
    try:
        result = raw.decode(encoding or 'utf-8-sig')
    except (UnicodeError,LookupError) as exc:
        raise Error('text_encoding','The LaTeX source does not decode with the selected encoding.') from exc
    if CONTROLS.search(result):
        raise Error('source_latex_text','The LaTeX source contains non-text control bytes.')
    return result


def latex_document(text,name,sha,title,*,include=None,encoding=None):
    renderer = Projection(include,encoding,name)
    source = File.make(name,text)
    try:
        body = renderer.render_entry(source,[name])
        notes = renderer.notes
    except (LatexWalkerParseError,RecursionError) as exc:
        start = getattr(exc,'pos',0) or 0
        body = renderer.issue(source,start,f'The LaTeX syntax needs source review. {exc}')
        notes = []
    except Error as exc:
        body = renderer.issue(source,0,str(exc))
        notes = []
    builder = DocumentBuilder('latex',title,sha)
    if 'title' in renderer.metadata:
        from bs4 import BeautifulSoup
        owner,node = renderer.metadata['title']
        try:
            value = BeautifulSoup(renderer.inline(owner,group(node)),'html.parser').get_text(' ',strip=True)
            if value:
                builder.doc.title = value
        except Error:
            pass
    html_read(body+''.join(notes),builder,where=name,metadata=False,source_annotations=renderer.annotations,
              collect_links=True,collect_resources=True)
    builder.doc.inventory.extend(renderer.inventory)
    return builder.doc
