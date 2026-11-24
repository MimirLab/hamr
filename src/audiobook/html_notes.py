"""Recognize linked notes and generated reader controls in rendered documents."""

from urllib.parse import unquote


class RenderedNotes:
    def __init__(self,root,*,docutils=False,sphinx=False):
        definitions = [
            node for node in root.select('ol.footnote-definition > li[id]')
            if node['id'].startswith('footnote-')
        ]
        self.definitions = {id(node) for node in definitions}
        self.wrappers = {}
        self.anchors = set()
        references = set()
        for marker in root.select('sup.footnote-reference[id]'):
            links = marker.find_all('a',href=True,recursive=False)
            if len(links)!=1 or not marker['id'].startswith('fr-'):
                continue
            link, = links
            href = link['href']
            if not href.startswith('#footnote-') or marker.get_text(strip=True)!=link.get_text(strip=True):
                continue
            self.wrappers[id(marker)] = href
            self.anchors.add(id(link))
            references.add(marker['id'])
        self.backlinks = set()
        for note in definitions:
            for link in note.find_all('a',href=True):
                target = unquote(link['href']).removeprefix('#')
                if link['href'].startswith('#') and target in references and link.get_text(strip=True).startswith('↩'):
                    self.backlinks.add(id(link))
        self.structural = set()
        if docutils:
            self.structural.update(id(node) for node in root.select(
                'aside[role="doc-footnote"] > span.label, span.sphinx-navigation'))
        if sphinx:
            self.structural.update(id(node) for node in root.select('a.headerlink, span.section-number'))
            generated = {'genindex.html','modindex.html','py-modindex.html','search.html'}
            self.structural.update(id(node) for node in root.select('a.reference.internal[href]')
                                   if node['href'] in generated)
