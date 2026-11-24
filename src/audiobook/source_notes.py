"""Resolve authored note links before passing text to the common reading policy."""

from dataclasses import dataclass

from .core import Block


def references(spans):
    hidden = [(span['source_start'],span['source_end']) for span in spans if span['kind']=='hidden']
    return [span for span in spans if span['kind']=='note_reference' and
            not any(start<=span['source_start'] and span['source_end']<=end for start,end in hidden)]


def target(reference):
    return reference.get('target_href',reference.get('href')) or ''


def finish_sentence(text):
    return text if text.endswith(('.', '!', '?')) else text+'.'


def note_text(block,*,terminal=False):
    """Project the visible text of note paragraphs and list items."""
    from .epub_semantics import reading

    spans = block.locator.get('inline_semantics',())
    boundaries = sorted({0,len(block.source),*(span['source_start'] for span in spans
                                               if span['kind']=='note_boundary')})
    if len(boundaries)<=2:
        text = reading(block).strip()
        return finish_sentence(text) if terminal and text else text
    parts = []
    for start,end in zip(boundaries,boundaries[1:]):
        local = []
        for span in spans:
            a,b = span['source_start'],span['source_end']
            if start<=a<=b<=end and (a<end or a<b):
                local.append({**span,'source_start':a-start,'source_end':b-start})
            elif span['kind']=='hidden' and a<end and b>start:
                low,high = max(a,start),min(b,end)
                local.append({**span,'source_start':low-start,'source_end':high-start,'source':block.source[low:high]})
        fragment = Block('',block.source[start:end],{'inline_semantics':local},'note',block.chapter)
        if text:=reading(fragment).strip():
            parts.append(finish_sentence(text))
    return ' '.join(parts)


@dataclass(frozen=True)
class Definition:
    block: Block
    reading: str
    issue: str | None = None

    @property
    def targets(self):
        return tuple(target(ref) for ref in references(self.block.locator.get('inline_semantics',())))

    @property
    def delivery(self):
        return self.reading,self.targets,self.issue


class NoteGraph:
    def __init__(self,lookup,*,first_definition=False):
        self.lookup = lookup
        self.first_definition = first_definition

    def select(self,candidates):
        if not candidates:
            return None,'The cited note is unavailable in this source.',None
        primary = candidates[0]
        if len(candidates)==1:
            return primary,primary.issue,None
        if self.first_definition:
            return primary,primary.issue,'First definition according to the source dialect.'
        if all(item.delivery==primary.delivery for item in candidates[1:]):
            return primary,primary.issue,'Definitions agree on the note reading and references.'
        return None,'The cited note has conflicting definitions. Verify the intended reading.',None

    def links_for(self,spans,*,follow=True):
        return self.link_groups((spans,),follow=follow)[0]

    def bind_cells(self,cells):
        for cell,links in zip(cells,self.link_groups(cell['spans'] for cell in cells)):
            for link in links:
                link.setdefault('reference_href',link['href'])
            cell['notes'] = links

    def link_groups(self,groups,*,follow=True):
        references_by_group = [references(spans) for spans in groups]
        roots = {target(span) for group in references_by_group for span in group}
        resolved, linked = set(), {}
        result = []
        for group in references_by_group:
            links = []
            for reference in group:
                href = target(reference)
                if href not in linked:
                    note,touched = self.expand(href,roots,resolved,follow)
                    if not note.get('issue'):
                        resolved.update(touched-{href})
                    linked[href] = note
                link = {'kind':'note','href':href,'source_start':reference['source_start'],**linked[href]}
                if href!=reference.get('href'):
                    link['reference_href'] = reference.get('href')
                links.append(link)
            result.append(links)
        return result

    def expand(self,href,roots,resolved,follow):
        pending = [href]
        visited, destinations = set(), []
        parts, issues = [], []
        primary = None
        resolution = None
        covered_elsewhere = False
        while pending:
            target = pending.pop()
            if target in visited:
                continue
            if target!=href and (target in roots or target in resolved):
                covered_elsewhere = True
                continue
            visited.add(target)
            candidates = self.lookup(target) or ()
            definition,issue,choice = self.select(candidates)
            destinations.extend('source.html#'+item.block.id for item in candidates)
            if target==href:
                primary,resolution = definition,choice
            if issue:
                issues.append(issue if target==href else f'Linked note {target}: {issue}')
            if definition:
                if definition.reading:
                    parts.append(definition.reading)
                if follow:
                    pending.extend(reversed(definition.targets))
        link = {'text':primary.block.source if primary else '', 'reading':' '.join(parts)}
        if primary:
            link['destination'] = 'source.html#'+primary.block.id
            if spans := primary.block.locator.get('inline_semantics'):
                link['inline_semantics'] = spans
            if language := primary.block.locator.get('language'):
                link['language'] = language
        if destinations:
            link['destinations'] = list(dict.fromkeys(destinations))
        if resolution:
            link['resolution'] = resolution
        if not parts and not issues:
            if not follow or covered_elsewhere:
                link['reference_only'] = True
            else:
                issues.append('The cited note contains no readable text.')
        if issues:
            link['issue'] = ' '.join(dict.fromkeys(issues))
        return link,visited
