"""Project authored LaTeX structure into the shared manuscript preparation."""

from bisect import bisect_right
from dataclasses import dataclass
from html import escape
from pathlib import PurePosixPath
import re

from pylatexenc.latexwalker import (
    LatexCharsNode, LatexCommentNode, LatexEnvironmentNode, LatexGroupNode,
    LatexMacroNode, LatexMathNode, LatexSpecialsNode, LatexWalker,
    LatexWalkerParseError, get_default_latex_context_db,
)
from pylatexenc.macrospec import (
    EnvironmentSpec, MacroSpec, MacroStandardArgsParser, ParsedMacroArgs,
    ParsedVerbatimArgs,
)
from pylatexenc.latex2text import LatexNodes2Text

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, formula_span, html_read
from .tex_math import tex_math


DEPENDENCIES = re.compile(r'\\(?:input|include|includegraphics|bibliography|addbibresource)\b')
ENTRY = re.compile(rb'\\documentclass\s*(?:\[[^\]\n]*\]\s*)?\{')
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
SPECIALS = {'~':' ','---':'—','--':'–',"``":'“',"''":'”'}
ACCENTS = frozenset(("'",'`','^','"','~','=','.', 'u','v','H','c','k','r','b','d','t'))
METADATA = {'title','author','date'}
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
    'newenvironment':'*{[[{{','renewenvironment':'*{[[{{',
    'bibliography':'{','bibliographystyle':'{','addbibresource':'[{','printbibliography':'[',
    'bibitem':'[{','addcontentsline':'{{{','pdfbookmark':'[{{',
    'framebox':'[[{','parbox':'[[[{{','resizebox':'*{{{','scalebox':'{[{',
    'textcolor':'[{{', 'ifthenelse':'{{{', 'InputIfFileExists':'{{{', 'IfFileExists':'{{{',
    'hspace':'*{','vspace':'*{','setlength':'{{','addtolength':'{{','enlargethispage':'*{',
    'thispagestyle':'{','pagestyle':'{','multicolumn':'{{{','cline':'{',
}
PARAGRAPH = re.compile(r'\n[ \t\r]*\n')
CONTROLS = re.compile(r'[\x00-\x08\x0b\x0e-\x1f]')
UNBRACED_PATH = re.compile(r'[^\s{}\\%#$&^~]+(?=\s|%|$)')


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
    def __init__(self,name,argspec):
        super().__init__(argspec)
        self.end = r'\end{'+name+'}'

    def parse_args(self,w,pos,parsing_state=None):
        _, start, size = super().parse_args(w,pos,parsing_state)
        start += size
        end = w.s.find(self.end,start)
        if end<0:
            raise LatexWalkerParseError(s=w.s,pos=start,msg=f'Expected {self.end}')
        chars = w.make_node(LatexCharsNode,parsing_state=parsing_state,
                            chars=w.s[start:end],pos=start,len=end-start)
        return ParsedVerbatimArgs(verbatim_chars_node=chars), pos, end-pos


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
    db.add_context_category('hamr-source',macros=macros,environments=environments,prepend=True)
    return db


def arguments(node):
    return tuple(arg for arg in (node.nodeargd.argnlist if node.nodeargd else ()) if arg is not None)


def group(node):
    return node.nodelist if isinstance(node,LatexGroupNode) else [node]


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
        self.inventory = []
        self.selected = None
        self.remaining = 4*MAX_STRUCTURE
        self.nodes_left = 500_000
        self.inside_notes = False
        self.files = {}

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
            self.files[file.name] = LatexWalker(file.text,latex_context=self.db,
                tolerant_parsing=False).get_latex_nodes()[0]
        return self.files[file.name]

    def formula(self,file,node,raw,expression):
        loc = file.location(node)
        span = formula_span(raw,lambda:tex_math(expression),0,'Authored LaTeX math',loc)
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
            owner,value = self.definitions[name]
            return self.inline(owner,group(value),depth+1)
        if name=='verb' and args:
            return '<code>'+escape(getattr(args[-1],'chars',file.raw(args[-1])))+'</code>'
        if name=='footnote' and args and not self.inside_notes:
            ident = f'latex-note-{len(self.notes)+1}'
            self.notes.append((ident,file,args[-1]))
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
            raise Error('latex_depth','Nested LaTeX text exceeds the supported reading depth.')
        pieces = []
        for node in nodes:
            if isinstance(node,LatexCharsNode):
                pieces.append(escape(re.sub(r'[\r\n\t ]+',' ',node.chars)))
            elif isinstance(node,LatexGroupNode):
                pieces.append(self.inline(file,node.nodelist,depth+1))
            elif isinstance(node,LatexMathNode):
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
        elif name in ('newcommand','renewcommand','providecommand') and len(args)==2:
            lhs = group(args[0])
            if len(lhs)==1 and isinstance(lhs[0],LatexMacroNode):
                macro = lhs[0].macroname
                if name!='providecommand' or macro not in self.definitions:
                    self.definitions[macro] = (file,args[-1])
        return ''

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
            nodes = self.parsed(child)
        except (Error,LatexWalkerParseError,RecursionError) as exc:
            return self.issue(file,node,f'LaTeX include needs source review. {exc}')
        self.inventory.append({'kind':'latex_include','status':'included','target':name,**file.location(node)})
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
        if name in VERBATIM:
            arg = getattr(node.nodeargd,'verbatim_text','')
            return self.tag('pre',where,escape(arg))
        if name in EQUATIONS:
            nodes = node.nodelist
            expression = file.text[nodes[0].pos:nodes[-1].pos+nodes[-1].len] if nodes else ''
            return self.tag('p',where,self.formula(file,node,file.raw(node),expression),source_role='equation')
        if name in ('itemize','enumerate','description','thebibliography'):
            return self.items(file,node,chain)
        if name in ('tabular','tabular*'):
            return self.table(file,node)
        if name in TRANSPARENT:
            return self.tag('div',where,self.render(file,node.nodelist,chain))
        if name in ('quote','quotation','verse'):
            return self.tag('blockquote',where,self.render(file,node.nodelist,chain))
        return self.issue(file,node,f'The LaTeX {name!r} environment needs a verified reading.')

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
        for node in nodes:
            name = node.macroname if isinstance(node,LatexMacroNode) else None
            args = arguments(node) if name else ()
            opens_condition = name and (name=='if' or name.startswith('if') and name!='ifthenelse')
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
            elif name in METADATA|{'includeonly','newcommand','renewcommand','providecommand'}:
                output.append(self.setup(file,node))
            elif name in {'addbibresource','bibliography'}:
                flush()
                output.append(self.assets(file,node))
                if name=='bibliography' and not preamble:
                    output.append(self.issue(file,node,'Generated bibliography needs an authored or exported reading.'))
            elif preamble or isinstance(node,LatexCommentNode):
                continue
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
        nodes = renderer.parsed(source)
        preamble = any(isinstance(node,LatexEnvironmentNode) and node.environmentname=='document' or
                       isinstance(node,LatexMacroNode) and node.macroname=='documentclass' for node in nodes)
        body = renderer.render(source,nodes,[name],preamble=preamble)
        notes = []
        renderer.inside_notes = True
        for ident,file,arg in renderer.notes:
            notes.append(renderer.tag('aside',file.location(arg),'<p>'+renderer.inline(file,group(arg))+'</p>',
                                      attrs=f' role="doc-footnote" id="{ident}"'))
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
