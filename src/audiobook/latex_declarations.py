"""Locate literal LaTeX declarations without interpreting package programs."""

import re


CLASSIC = {'newcommand','renewcommand','providecommand'}
DOCUMENT = {'NewDocumentCommand','RenewDocumentCommand','ProvideDocumentCommand','DeclareDocumentCommand'}
DECLARATIONS = CLASSIC | DOCUMENT
PACKAGE_COMMANDS = {'usepackage','RequirePackage'}
OPAQUE_DEFINITIONS = {'def','gdef','edef','xdef','let','futurelet'}
PACKAGE_TOKEN = re.compile(
    r'\\begin\s*\{(?P<literal>verbatim\*?|lstlisting|minted)\}|\\(?:[A-Za-z@]+|[^\r\n])|%[^\r\n]*|[{}]')
PARAMETER = re.compile(r'#([1-9])')
ARGUMENT_KINDS = {'m':'{','o':'[','s':'*'}


def package_statements(text):
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
                elif not local_groups and name in DECLARATIONS|PACKAGE_COMMANDS|OPAQUE_DEFINITIONS:
                    yield token.start(),expl3


def document_signature(raw):
    types = ''.join(raw.split())
    if len(types)<=9 and all(kind in ARGUMENT_KINDS for kind in types):
        return ''.join(ARGUMENT_KINDS[kind] for kind in types)
    return None
