"""Use repeated print alignment to recover simple borderless tables."""

from dataclasses import dataclass
from functools import cached_property
from itertools import pairwise
import re
from statistics import median

from .table_grid import Grid, passage, source_rows, source_text


VALUE = re.compile(
    r'[~≈+\-−]?\s*[$£€¥]?\s*(?:'
    r'\d+(?:[/-]\d+){1,2}|'
    r'(?:\d{1,3}(?:[,\u00a0 ]\d{3})+|\d+)(?:[.,]\d+)?|\.\d+'
    r')\s*%?'
)
MAX_TOKENS = 30_000
MAX_COLUMNS = 16


def bounds(tokens):
    return (min(t.rect[0] for t in tokens), min(t.rect[1] for t in tokens),
            max(t.rect[2] for t in tokens), max(t.rect[3] for t in tokens))


@dataclass(frozen=True)
class Run:
    tokens: tuple
    rect: tuple
    text: str
    provenance: dict
    issue: str | None

    @cached_property
    def value(self):
        text = self.text.strip()
        if text.startswith('(') and text.endswith(')'):
            text = text[1:-1]
        return VALUE.fullmatch(text) is not None

    @cached_property
    def label(self):
        return (not self.value and 0 < len(self.text.split()) <= 6
                and not self.text.endswith(('?', '!'))
                and (not self.text.endswith('.') or '.' in self.text[:-1]))


@dataclass(frozen=True)
class Line:
    y: float
    tokens: tuple
    runs: tuple


def lines(tokens, baseline, size, read_cell):
    """Find word runs separated by space large enough to hold a cell gutter."""
    bands = []
    for token in sorted(tokens, key=lambda t:(baseline(t),t.rect[0],t.key)):
        y = baseline(token)
        if not bands or y-bands[-1][0]>.46*size:
            bands.append((y, []))
        bands[-1][1].append(token)

    for _, band in bands:
        band.sort(key=lambda t:(t.rect[0],t.key))
        groups = []
        end = float('-inf')
        for token in band:
            if not groups or token.rect[0]-end>.55*size:
                groups.append([])
            groups[-1].append(token)
            end = max(end,token.rect[2])
        runs = []
        if 2<=len(groups)<=MAX_COLUMNS:
            for group in groups:
                text,detail,issue = read_cell(group,None)
                runs.append(Run(tuple(group),bounds(group),text,detail,issue))
        yield Line(median(baseline(t) for t in band),tuple(band),tuple(runs))


@dataclass(frozen=True)
class Track:
    """Bounds of observed left, center and right text alignment."""
    low: tuple
    high: tuple

    @classmethod
    def observe(cls, run):
        left,_,right,_ = run.rect
        anchors = (left,(left+right)/2,right)
        return cls(anchors,anchors)

    def include(self, other):
        return Track(tuple(map(min,zip(self.low,other.low))),
                     tuple(map(max,zip(self.high,other.high))))


def exclusive(tracks, size):
    return all(right.low[0]-left.high[2]>=.3*size for left,right in pairwise(tracks))


def compatible_header(header, rows, tracks, size):
    """A short label must meet a unique column without crossing a stable gap."""
    if len(header.runs)!=len(rows[0].runs) or not all(run.label for run in header.runs):
        return False
    stride = median(right.y-left.y for left,right in pairwise(rows))
    if not .45*stride<=rows[0].y-header.y<=1.6*stride:
        return False
    return exclusive([track.include(Track.observe(run)) for track,run in zip(tracks,header.runs)],size)


def data_runs(observed, size):
    """Track runs of equally partitioned, typed rows before choosing labels."""
    start = 0
    current = []
    signatures = None
    tracks = []
    for index,row in enumerate(observed):
        kinds = tuple(item.value for item in row.runs)
        typed = (len(kinds)>=3 and sum(kinds)>=2 or
                 len(kinds)==2 and kinds==(False,True) and row.runs[0].label)
        incoming = [Track.observe(run) for run in row.runs] if typed else []
        combined = [a.include(b) for a,b in zip(tracks,incoming)] if kinds==signatures else []
        if current and (not typed or kinds!=signatures or row.y-current[-1].y>4.9*size or
                        not exclusive(combined,size) or
                        any(min(b-a for a,b in zip(t.low,t.high))>1.2*size for t in combined)):
            yield start,current,signatures,tracks
            current = []
        if typed:
            if not current:
                start,signatures = index,kinds
            tracks = combined if current else incoming
            current.append(row)
    if current:
        yield start,current,signatures,tracks


def project(rows, locate):
    grid = Grid()
    claim = set()
    observed = []
    for number,row in enumerate(rows):
        grid.begin_row()
        for run in row.runs:
            role = 'column' if number==0 else 'data'
            grid.place([passage(run.text)] if run.text else [],{**locate(run.rect),**run.provenance},role=role)
            if run.issue:
                grid.issue(run.issue)
            claim.update(t.key for t in run.tokens)
            observed.extend(run.tokens)
        grid.end_row()
    table = grid.finish()
    locator = {**locate(bounds(observed)),'table_grid':table,'rows':source_rows(table),
               'table_evidence':'repeated alignment of typed rows'}
    return claim,{'text':source_text(table),'locator':locator,'issue':table['issue'],'role':'table'}


def compact_label(row, read_cell):
    text,_,issue = read_cell(row.tokens,None)
    return not issue and 0<len(text.split())<=4 and text[0].isupper() and not text.endswith(('.', '!', '?'))


def preceding_labels(observed, start, left, divider, right, size, read_cell):
    first = start
    outer = []
    grouped = False
    while first and observed[first].y-observed[first-1].y<=4.9*size:
        row = observed[first-1]
        rect = bounds(row.tokens)
        beside = rect[2]<left
        spanning = left<=rect[0]<divider<rect[2]<=right
        if not (beside or spanning) or not compact_label(row,read_cell):
            break
        if beside:
            outer.append(rect[0])
        grouped |= spanning
        first -= 1
    return first,outer if grouped else []


def grouped_regions(observed, size, read_cell):
    """Find a table whose grouped and spanning relationships conflict."""
    for i in range(2,len(observed)-2):
        labels,paired = observed[i:i+2]
        if len(labels.runs)!=2 or len(paired.runs)!=2:
            continue
        if not all(run.label and run.text[0].isupper() for run in labels.runs):
            continue
        if not all(run.text.endswith(('.', '?', '!')) for run in paired.runs):
            continue
        if not all(cell.rect[0] <= (label.rect[0]+label.rect[2])/2 <= cell.rect[2]
                   for label,cell in zip(labels.runs,paired.runs)):
            continue
        content_left = paired.runs[0].rect[0]
        content_right = paired.runs[1].rect[2]
        divider = (paired.runs[0].rect[2]+paired.runs[1].rect[0])/2
        start,side_labels = preceding_labels(observed,i,content_left,divider,content_right,size,read_cell)
        if not side_labels:
            continue
        end = i+2
        span = side = continuation = False
        while end<len(observed) and observed[end].y-observed[end-1].y<=4.9*size:
            row = observed[end]
            rect = bounds(row.tokens)
            if abs(rect[0]-content_left)<=.55*size and rect[2]<=content_right+size:
                span |= rect[2]>paired.runs[1].rect[0]
                continuation |= side
            elif span and rect[2]<content_left and any(abs(x-rect[0])<2*size for x in side_labels):
                if not compact_label(row,read_cell):
                    break
                side = True
            else:
                break
            end += 1
        if continuation:
            yield observed[start:end]


def unresolved(region, locate, read_cell):
    parts = []
    tokens = [token for row in region for token in row.tokens]
    for row in region:
        readings = [(run.text,{**locate(run.rect),**run.provenance}) for run in row.runs]
        if not readings:
            text,detail,_ = read_cell(row.tokens,None)
            readings = [(text,{**locate(bounds(row.tokens)),**detail})]
        parts.append([{'text':text,'location':detail} for text,detail in readings])
    locator = {**locate(bounds(tokens)),'observed_table_rows':parts,
               'table_evidence':'aligned headings with conflicting cell ownership'}
    issue = 'The printed table has grouped labels and overlapping reading orders. Verify the marked table before narration.'
    text = '\n'.join(' | '.join(run['text'] for run in row) for row in parts)
    return {t.key for t in tokens},{'text':text,'locator':locator,'issue':issue,'role':'table'}


def aligned_tables(tokens, baseline, size, locate, read_cell):
    if not 8<=len(tokens)<=MAX_TOKENS or size<=0:
        return []
    observed = list(lines(tokens,baseline,size,read_cell))
    result = []
    for start,rows,kinds,tracks in data_runs(observed,size):
        minimum = 4 if len(kinds)==2 else 3
        if len(rows)<minimum:
            continue
        header = start>0 and compatible_header(observed[start-1],rows,tracks,size)
        if not header:
            # A header also distinguishes a table from a block of aligned
            # timestamps, code, or unordered data on the same page.
            continue
        result.append(project([observed[start-1],*rows],locate))
    claimed = set().union(*(claim for claim,_ in result))
    for region in grouped_regions(observed,size,read_cell):
        claim,part = unresolved(region,locate,read_cell)
        if not claimed.intersection(claim):
            result.append((claim,part))
            claimed.update(claim)
    return result
