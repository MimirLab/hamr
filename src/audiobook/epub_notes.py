"""Bind EPUB note references to the exact definition in their content document."""

from urllib.parse import quote

from .core import Error
from .epub_semantics import EMBEDDED, describe, hidden, language, uncertainty
from .epub_styles import issues as style_issues
from .pronunciation import annotate, unresolved
from .source_notes import Definition, NoteGraph, note_text


class EpubNotes:
    def __init__(self,soup,resolve,asset,cover_assets,language_code):
        self.soup = soup
        self.resolve = resolve
        self.asset = asset
        self.cover_assets = cover_assets
        self.language = language_code
        self.addresses = {}
        self.definitions = {}
        self.index = {}
        self.nodes = {}
        self.graph = NoteGraph(self.load)

    def bind(self,path,spans):
        bound = []
        for span in spans:
            if span['kind']!='note_reference':
                bound.append(span)
                continue
            href = ''
            try:
                target,anchor = self.resolve(path,span.get('href') or '')
                if anchor:
                    href = quote(target,safe='/')+'#'+quote(anchor)
                    self.addresses[href] = (target,anchor)
            except (Error,ValueError):
                pass
            bound.append({**span,'target_href':href})
        return bound

    def load(self,href):
        from .importers import block

        if href in self.definitions:
            return self.definitions[href]
        address = self.addresses.get(href)
        if address is None:
            return ()
        path,anchor = address
        try:
            parsed = self.soup(path)
        except Error:
            self.definitions[href] = ()
            return ()
        if path not in self.index:
            by_id = {}
            for element in parsed.find_all(id=True):
                ident = element['id']
                by_id[ident] = None if ident in by_id else element
            self.index[path] = by_id
        node = self.index[path].get(anchor)
        if node is None:
            self.definitions[href] = ()
            return ()
        lang = language(node) or self.language
        text,spans = describe(node,lambda uri:self.asset(path,uri),self.cover_assets,note_body=True)
        spans += annotate(text,spans,vars(parsed).get('_audiobook_lexicons',[]),lang)
        loc = {'format':'epub','path':path,'anchor':anchor,'auxiliary':True,'exact':str(node)}
        note = block(text,loc,'note',evidence=['Authored internal link target retained for offline lookup'])
        note.locator.update(language=lang,inline_semantics=self.bind(path,spans))
        problems = [uncertainty(spans),*style_issues(node),*vars(parsed).get('_audiobook_lexicon_problems',[]),*unresolved(note)]
        if any(not any(hidden(tag) for tag in [item,*item.parents]) for item in node.find_all(EMBEDDED)):
            problems.append('This EPUB note contains embedded content that needs a verified reading.')
        issue = ' '.join(dict.fromkeys(problem for problem in problems if problem)) or None
        if issue:
            note.locator['note_issue'] = issue
        self.nodes[address] = node,note
        result = (Definition(note,note_text(note),issue),)
        self.definitions[href] = result
        return result

    def links_for(self,path,spans):
        return self.graph.links_for(self.bind(path,spans))
