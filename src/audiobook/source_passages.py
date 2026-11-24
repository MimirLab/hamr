"""Prepare prose and reading relationships from the evidence supplied by intake."""

from dataclasses import dataclass
from enum import IntEnum
import re


class Continuation(IntEnum):
    NONE = 0
    PAGE = 1
    ALIGNED_LINES = 2
    PARAGRAPH = 3


def has_authored_breaks(block):
    return block.locator.get('authored_line_breaks') or any(
        span['kind'] == 'line_break' for span in block.locator.get('inline_semantics', ()))


def flatten_layout(text):
    return text.replace('\n', ' ').replace('\r', ' ')


def soft_wraps(block):
    return block.role == 'body' and not has_authored_breaks(block)


def prose_text(block, *, unified, legacy_layout=True):
    """Expose layout wraps to language rules without moving source offsets."""
    soft = soft_wraps(block) if unified else legacy_layout and block.locator.get('format') == 'pdf'
    return flatten_layout(block.source) if soft else block.source


def legacy_prose_scope(block):
    return block.locator.get('format') in ('epub', 'markdown', 'text')


def paginated(block):
    return type(block.locator.get('page')) is int


def prepared_paragraph(block, *, unified):
    from .pdf_layout import spoken_paragraph

    return spoken_paragraph(block, layout=paginated(block) if unified else None)


def chapter_layout(document, policy, *, by_source, unified):
    from .pdf_layout import structure_policy

    return structure_policy(document,policy,by_source=by_source,
                            paginated_source=any(paginated(block) for block in document.blocks) if unified else None)


def chapter_at(block, position, chapters, *, by_source, unified):
    location = block.locator
    chapter, chapter_id = block.chapter, location.get('chapter_id')
    if not (paginated(block) if unified else location.get('format') in ('pdf','scan')):
        return chapter, chapter_id
    if by_source and not chapters and chapter_id is None:
        if unified or location.get('format') == 'pdf':
            return 'Opening','pdf:unclassified'
    if by_source and chapters:
        chapter, chapter_id = 'Opening','pdf:opening'
    for start,title in chapters.items():
        if start <= (position if by_source else location['page']):
            chapter = title
            if by_source:
                chapter_id = 'pdf-source:'+str(start)
    return chapter, chapter_id


def layout_disputes(document, overrides, *, expanded, margin_numbers, unified):
    from .pdf_flow import insertions

    if unified:
        pages = [row for row in document.inventory if isinstance(row,dict) and type(row.get('page')) is int
                 and isinstance(row.get('size'),(tuple,list)) and len(row['size'])==2]
    else:
        pages = document.inventory if document.format == 'pdf' else []
    if not pages:
        return {}
    blocked = {}
    page_label = 'printed pages' if unified else 'PDF pages'
    for finding in insertions(document.blocks,pages,expanded=expanded,margin_numbers=margin_numbers,
                              page_label=page_label):
        choice = overrides.get(finding['block'],{})
        if choice.get('action')=='companion' or choice.get('placement')=='caption_after_paragraph':
            continue
        for ident in finding['source_blocks']:
            blocked[ident] = finding['reason']
    return blocked


def split_printed_word(left, right, speech, *, unified):
    right_page = right.locator.get('page')
    if unified:
        left_page = left.locator.get('page')
        if not (paginated(left) and paginated(right) and left_page != right_page):
            return False
    elif right.locator.get('format') != 'pdf' or left.locator.get('page') == right_page:
        return False
    return re.search(r'[A-Za-z]{2,}-$',speech) is not None


@dataclass(frozen=True)
class ReadingEvidence:
    soft_wraps: bool
    logical_order: bool
    reliable_repetition: bool
    printed_layout: bool
    quoted_material: bool


def reading_evidence(block, *, unified):
    loc = block.locator
    native_line = 'line' in loc and not block.issue
    old_quote = loc.get('format') == 'markdown' and loc.get('exact','').lstrip().startswith('>')
    if unified:
        logical = loc.get('page') is None or loc.get('verified_transcription',False)
        return ReadingEvidence(soft_wraps(block),logical,
                               logical or native_line and loc.get('page') is not None,loc.get('page') is not None,
                               bool(loc.get('block_quote') or old_quote))
    logical = legacy_prose_scope(block)
    pdf = loc.get('format') == 'pdf'
    return ReadingEvidence(loc.get('format') == 'text',logical,logical or pdf and native_line,pdf,old_quote)


class SourcePassages:
    """Shared source facts for a single immutable planning view."""

    def __init__(self, document, *, unified):
        self.unified = unified
        self.legacy_paginated_prose = document.format == 'pdf'
        self.blocks = document.blocks
        self.links = {}
        if unified or self.legacy_paginated_prose:
            self.links = {(left.id,right.id): self.continuation(left,right)
                          for left,right in zip(self.blocks,self.blocks[1:])
                          if left.locator.get('page') is not None}

    def continuation(self, left, right):
        from .pdf_layout import adjacent_native_lines

        a,b = left.locator,right.locator
        if (left.role != 'body' or right.role != 'body' or left.issue or right.issue
                or left.chapter != right.chapter or a.get('page') != b.get('page')):
            return Continuation.NONE
        if self.unified and (a.get('chapter_id') != b.get('chapter_id')
                            or a.get('member') != b.get('member')
                            or a.get('occurrence') != b.get('occurrence')
                            or has_authored_breaks(left) or has_authored_breaks(right)):
            return Continuation.NONE
        if adjacent_native_lines(left,right):
            return Continuation.PARAGRAPH if a.get('tagged_paragraph') is not None else Continuation.ALIGNED_LINES
        return Continuation.PAGE

    def between(self, left, right):
        return self.links.get((left.id,right.id),Continuation.NONE)

    def authored_paragraphs(self):
        run = []
        for left,right in zip(self.blocks,self.blocks[1:]):
            if self.between(left,right) == Continuation.PARAGRAPH:
                if not run:
                    run.append(left)
                run.append(right)
            elif run:
                yield run
                run = []
        if run:
            yield run

    def joined_prose(self, blocks):
        if len(blocks) < 2:
            return False
        if not self.unified:
            return all(block.locator.get('format') == 'pdf' for block in blocks)
        return all(self.between(left,right) for left,right in zip(blocks,blocks[1:]))

    def text(self, block, *, legacy_layout=True):
        return prose_text(block,unified=self.unified,legacy_layout=legacy_layout)
