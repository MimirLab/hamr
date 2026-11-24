"""Resolve authored note links before passing text to the common reading policy."""

from dataclasses import dataclass

from .core import Block


def references(spans):
    hidden = [(span['source_start'],span['source_end']) for span in spans if span['kind']=='hidden']
    return [span for span in spans if span['kind']=='note_reference' and
            not any(start<=span['source_start'] and span['source_end']<=end for start,end in hidden)]


@dataclass(frozen=True)
class Definition:
    block: Block
    reading: str
    issue: str | None = None

    @property
    def targets(self):
        return tuple(ref.get('href') or '' for ref in references(self.block.locator.get('inline_semantics',())))

    @property
    def delivery(self):
        return self.reading,self.targets,self.issue


class NoteGraph:
    def __init__(self,definitions,*,first_definition=False):
        self.definitions = definitions
        self.first_definition = first_definition

    def select(self,href):
        candidates = self.definitions.get(href,())
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
        cited = references(spans)
        roots = {span.get('href') or '' for span in cited}
        resolved, linked = set(), {}
        links = []
        for reference in cited:
            href = reference.get('href') or ''
            if href not in linked:
                note,touched = self.expand(href,roots,resolved,follow)
                if not note.get('issue'):
                    resolved.update(touched-{href})
                linked[href] = note
            links.append({'kind':'note','href':href,'source_start':reference['source_start'],**linked[href]})
        return links

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
            definition,issue,choice = self.select(target)
            destinations.extend('source.html#'+item.block.id for item in self.definitions.get(target,()))
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
