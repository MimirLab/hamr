"""Org objects projected into the normal HTML reading vocabulary."""

from html import escape
import re
from urllib.parse import unquote, urlsplit

from .core import key


OBJECT = re.compile(r'''
    \[\[ (?P<link>[^\[\]\n]+) \] (?: \[ (?P<label>[^\]]*) \] )? \]
  | \[fn:(?P<note>[\w-]*)(?::(?P<definition>(?:[^\[\]]|\[[^\[\]]*\])*))?\]
  | \{\{\{(?P<macro>[\w-]+)(?:\((?P<arguments>.*?)\))?\}\}\}
  | (?<!\w)(?P<mark>[*/_+=~])(?P<style>[^\s](?:.*?[^\s])?)(?P=mark)(?=$|[\s.!?,;:'"’”)}\]\-]|\[fn:)
  | (?P<entity>\\(?:copy|nbsp|mdash|ndash|vert)(?:\{\})?(?![a-zA-Z]))
''', re.X | re.S)
IMAGE_SUFFIX = re.compile(r'\.(?:avif|gif|jpe?g|png|svg|webp)$',re.I)
STYLES = {'*':'strong','/':'em','_':'u','+':'del','=':'code','~':'code'}
ENTITIES = {'copy':'©','nbsp':' ','mdash':'—','ndash':'–','vert':'|'}
ARGUMENT = re.compile(r'\$([1-9][0-9]*)')


class Objects:
    def __init__(self,projection,macros):
        self.projection = projection
        self.macros = macros
        self.notes = []

    def inline(self,text,part,depth=0):
        if depth>=12:
            return escape(text)
        result = []
        index = 0
        for match in OBJECT.finditer(text):
            result.append(escape(text[index:match.start()]))
            index = match.end()
            value = match.group
            if value('link'):
                result.append(self.link(value('link'),value('label'),part,depth))
            elif value('note') is not None and (value('note') or value('definition')):
                ident = self.note_id(part,value('note') or f'@{len(self.notes)}')
                if value('definition') is not None:
                    self.notes.append((part,ident,value('definition')))
                site = self.projection.locator(*part)
                result.append(f'<a role="doc-noteref" data-hamr-node="{site}" href="#{ident}">{escape(match[0])}</a>')
                self.projection.annotations[site]['inline_location'] = {
                    'source_file':part[0],'lines':[part[1],part[2]],'org_marker':match[0]}
            elif value('macro'):
                result.append(self.macro(match,part,depth))
            elif value('mark'):
                tag = STYLES[value('mark')]
                fragment = escape(value('style')) if tag=='code' else self.inline(value('style'),part,depth+1)
                result.append(f'<{tag}>{fragment}</{tag}>')
            elif value('entity'):
                result.append(ENTITIES[value('entity')[1:].removesuffix('{}')])
            else:
                result.append(escape(match[0]))
        result.append(escape(text[index:]))
        return ''.join(result)

    def note_id(self,part,name):
        return 'org-note-'+key([part[0],name])[:20]

    def link(self,target,label,part,depth):
        try:
            path = urlsplit(target.removeprefix('file:')).path
        except ValueError:
            path = target
        if label is None and IMAGE_SUFFIX.search(path):
            return f'<img src="{escape(target,quote=True)}">'
        visible = label if label is not None else unquote(target.lstrip('*#'))
        content = self.inline(visible,part,depth+1)
        return f'<a href="{escape(target,quote=True)}">{content}</a>'

    def macro(self,match,part,depth):
        name = match['macro'].casefold()
        template = self.macros.get(name)
        if template=='(eval (org-texinfo-kbd-macro $1))':
            # The standard manual macro renders only its argument as a key label.
            return '<kbd>'+escape(match['arguments'] or '')+'</kbd>'
        if template is None or template.lstrip().startswith('(eval'):
            return self.projection.inline_issue(part,match[0],f'Org macro {name!r} needs a saved reading.')
        args = [item.replace(r'\,',',') for item in re.split(r'(?<!\\),',match['arguments'] or '')]

        def substitute(marker):
            index = int(marker[1])-1
            return args[index] if index<len(args) else ''

        return self.inline(ARGUMENT.sub(substitute,template),part,depth+1)
