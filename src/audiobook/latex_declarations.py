"""Locate literal LaTeX declarations without interpreting package programs."""

import re


CLASSIC = {'newcommand','renewcommand','providecommand'}
DOCUMENT = {'NewDocumentCommand','RenewDocumentCommand','ProvideDocumentCommand','DeclareDocumentCommand'}
DECLARATIONS = CLASSIC | DOCUMENT
CLASSIC_ENVIRONMENTS = {'newenvironment','renewenvironment'}
DOCUMENT_ENVIRONMENTS = {'NewDocumentEnvironment','RenewDocumentEnvironment',
                         'ProvideDocumentEnvironment','DeclareDocumentEnvironment'}
ENVIRONMENT_DECLARATIONS = CLASSIC_ENVIRONMENTS | DOCUMENT_ENVIRONMENTS
PACKAGE_COMMANDS = {'usepackage','RequirePackage'}
INLINE_CODE = {'verb','Verb','lstinline','mintinline'}
CODE_SETTINGS = {'setminted','setmintedinline','lstset'}
INLINE_DECLARATIONS = {'newmintinline'}
OPAQUE_DEFINITIONS = {'def','gdef','edef','xdef','let','futurelet'}
PACKAGE_TOKEN = re.compile(
    r'\\begin\s*\{(?P<literal>verbatim\*?|lstlisting|minted)\}|\\(?:[A-Za-z@]+|[^\r\n])|%[^\r\n]*|[{}]')
PARAMETER = re.compile(r'#([1-9])')
ARGUMENT_KINDS = {'m':'{','o':'[','s':'*'}
COMMENT = re.compile(r'\\(?:[A-Za-z@]+|[^\r\n])|%[^\r\n]*(?:\r?\n)?')
DIMENSION = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt|pc|in|bp|cm|mm|dd|cc|sp|em|ex)'
GLUE = DIMENSION + rf'(?:\s*(?:plus|minus)\s*{DIMENSION}){{0,2}}'
LAYOUT_TOKEN = rf'''(?:
    \\(?:rmfamily|sffamily|ttfamily|normalfont|bfseries|mdseries|upshape|itshape|
          slshape|centering|raggedleft|raggedright|footnotesize|small|large|relax|ignorespaces)\b
    | \\vspace\*?\s*\{{\s*{GLUE}\s*\}}
)'''
LAYOUT = rf'(?:\s*{LAYOUT_TOKEN})*\s*'
LAYOUT_ONLY = re.compile(LAYOUT,re.X)
BLOCK_OPEN = re.compile(rf'{LAYOUT}\\begin\s*\{{(quote|quotation|center|flushleft|flushright)\}}{LAYOUT}',re.X)
FRAMED_OPEN = re.compile(r'''
    \s*\\begin\s*\{trivlist\}\s*
    (?:\\@(?:begin|end)parpenalty\s*=\s*[+-]?\d+\s*)*
    \\item\s*(?:\\relax\b\s*)?
    \\begin\s*\{tabular\}\s*\{\s*\|[lcr]\|\s*\}
    \s*\\hline\s*(?:\\rule\s*\{\s*0pt\s*\}\s*\{\s*''' + DIMENSION + r'''\s*\}\s*)?
    (?:\\ignorespaces\b\s*)?
''',re.X)
FRAMED_CLOSE = re.compile(r'\s*\\\\\s*\\hline\s*\\end\s*\{tabular\}\s*\\end\s*\{trivlist\}\s*')


def package_statements(text,literal_end,aliases):
    """Yield literal top-level statements while skipping groups and TeX conditionals."""
    groups = local_groups = conditions = until = 0
    expl3 = False
    for token in PACKAGE_TOKEN.finditer(text):
        if token.start()<until:
            continue
        raw = token[0]
        if literal:=token['literal']:
            end = r'\end{'+literal+'}'
            close = text.find(end,token.end())
            until = len(text) if close<0 else close+len(end)
        elif raw.startswith('\\') and (raw[1:] in INLINE_CODE or raw[1:] in aliases):
            until = literal_end(token.start(),token.end())
        elif raw=='{':
            groups += 1
        elif raw=='}':
            groups = max(0,groups-1)
        elif groups==0 and raw.startswith('\\'):
            name = raw[1:]
            if name.startswith('if'):
                conditions += 1
            elif name=='fi':
                conditions = max(0,conditions-1)
            elif not conditions:
                if name in ('begingroup','bgroup'):
                    local_groups += 1
                elif name in ('endgroup','egroup'):
                    local_groups = max(0,local_groups-1)
                elif name in ('ExplSyntaxOn','ExplSyntaxOff') and not local_groups:
                    expl3 = name=='ExplSyntaxOn'
                elif not local_groups and name in (
                        DECLARATIONS|ENVIRONMENT_DECLARATIONS|PACKAGE_COMMANDS|OPAQUE_DEFINITIONS
                        |CODE_SETTINGS|INLINE_DECLARATIONS):
                    yield token.start(),expl3


def document_signature(raw):
    types = ''.join(raw.split())
    if len(types)<=9 and all(kind in ARGUMENT_KINDS for kind in types):
        return ''.join(ARGUMENT_KINDS[kind] for kind in types)
    return None


def layout_environment(opening,closing):
    """Recognize presentation-only shells that leave the authored body intact."""
    opening,closing = (uncomment(text) for text in (opening,closing))
    if not opening.strip() and not closing.strip():
        return 'transparent'
    if LAYOUT_ONLY.fullmatch(opening) and LAYOUT_ONLY.fullmatch(closing):
        return 'div'
    if match:=BLOCK_OPEN.fullmatch(opening):
        if re.fullmatch(rf'{LAYOUT}\\end\s*\{{{match[1]}\}}{LAYOUT}',closing,re.X):
            return 'blockquote' if match[1] in ('quote','quotation') else 'div'
    if FRAMED_OPEN.fullmatch(opening) and FRAMED_CLOSE.fullmatch(closing):
        return 'framed_code'
    return None


def uncomment(text):
    return COMMENT.sub(lambda token: ' ' if token[0].startswith('%') else token[0],text)


def verbatim_environment(opening,closing,spec):
    signature = {'':'', 'o':'[', '!o':'['}.get(''.join(spec.split()))
    if signature is None:
        return None
    # The verbatim package takes ownership of the source lines at this call.
    # Preserve the body even if the end template computes different visible output.
    template = uncomment(opening)
    if len(template)>16384 or re.search(r'\\(?:if[a-z]*|catcode|endinput)\b',template):
        return None
    if re.search(r'\\verbatim_set_processline:N\s+\\[A-Za-z_:@]+',template) and re.search(
            r'\\verbatim_start:\s*\Z',template) and uncomment(closing).strip():
        return signature
    return None
