"""Recover cell ownership from visible ruling, independently of text order."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise
from statistics import median
import re

from PIL import Image

from .table_grid import Grid, passage, source_rows, source_text

MAX_RULES = 400
MAX_SLOTS = 2500


@dataclass(frozen=True)
class Rule:
    at: float
    start: float
    end: float


@dataclass(frozen=True)
class Token:
    key: int
    text: str
    rect: tuple


def runs(image, min_length, max_weight):
    """Return the centerlines of thin, long foreground runs."""
    width, height = image.size
    foreground = image.tobytes()
    pattern = re.compile(b'\xff{%d,}' % min_length)
    active = []
    rules = []

    def finish(band):
        if len(band) <= max_weight:
            rules.append(Rule(median(row for row, _, _ in band),
                              median(left for _, left, _ in band),
                              median(right for _, _, right in band)))

    for row in range(height):
        pending = active
        active = []
        for match in pattern.finditer(foreground, row*width, (row+1)*width):
            left, right = match.start()-row*width, match.end()-row*width-1
            matches = [band for band in pending
                       if abs(left-band[-1][1]) <= max_weight and abs(right-band[-1][2]) <= max_weight]
            if len(matches)==1:
                band = matches[0]
                pending.remove(band)
                band.append((row,left,right))
            else:
                band = [(row,left,right)]
            active.append(band)
        for band in pending:
            finish(band)
    for band in active:
        finish(band)
    return rules


def raster_rules(image, type_height):
    """Read painted rules within a bounded, monochrome analysis image."""
    with image.convert('L') as grayscale:
        grayscale.thumbnail((1600,1600),Image.Resampling.LANCZOS)
        # A subpixel rule can render as a mid-gray stroke despite being
        # painted black in the source. Intersections qualify the rails.
        with grayscale.point([255]*176+[0]*80) as mask:
            sx, sy = image.width/mask.width, image.height/mask.height
            horizontal = runs(mask,max(12,round(1.8*type_height/sx)),max(2,round(.38*type_height/sy)))
            with mask.transpose(Image.Transpose.TRANSPOSE) as columns:
                vertical = runs(columns,max(12,round(1.8*type_height/sy)),max(2,round(.38*type_height/sx)))
    return ([Rule(r.at*sy,r.start*sx,r.end*sx) for r in horizontal],
            [Rule(r.at*sx,r.start*sy,r.end*sy) for r in vertical])


def connections(horizontal, vertical, epsilon):
    """Group only the rules with a witnessed perpendicular intersection."""
    links = defaultdict(list)
    for i, h in enumerate(horizontal):
        for j, v in enumerate(vertical,len(horizontal)):
            if h.start-epsilon<=v.at<=h.end+epsilon and v.start-epsilon<=h.at<=v.end+epsilon:
                links[i].append(j)
                links[j].append(i)
    visited = set()
    for start in links:
        if start in visited:
            continue
        todo, group = [start], []
        visited.add(start)
        while todo:
            index = todo.pop()
            group.append(index)
            for other in links[index]:
                if other not in visited:
                    todo.append(other)
                    visited.add(other)
        yield ([horizontal[i] for i in group if i<len(horizontal)],
               [vertical[i-len(horizontal)] for i in group if i>=len(horizontal)])


def positions(rules, epsilon):
    groups = []
    for rule in sorted(rules,key=lambda r:r.at):
        if not groups or rule.at-groups[-1][0].at>epsilon:
            groups.append([])
        groups[-1].append(rule)
    return {median(r.at for r in group):group for group in groups}


def edge(rules, start, end, tolerance):
    """A border is full, absent, or only partially supported."""
    intervals = sorted((max(start,r.start),min(end,r.end)) for r in rules
                       if r.end>start+tolerance and r.start<end-tolerance)
    if not intervals:
        return False
    covered = start
    for left,right in intervals:
        if left>covered+tolerance:
            return None
        covered = max(covered,right)
    return True if covered>=end-tolerance else None


def cells(xs, ys, horizontal, vertical, tolerance):
    """Join only slots separated by a completely absent rule."""
    rows, columns = len(ys)-1,len(xs)-1
    if rows*columns>MAX_SLOTS:
        return None
    slots = list(range(rows*columns))

    def root(i):
        while slots[i]!=i:
            slots[i] = slots[slots[i]]
            i = slots[i]
        return i

    for row,(top,bottom) in enumerate(pairwise(ys)):
        for column,(left,right) in enumerate(pairwise(xs)):
            i = row*columns+column
            if column:
                boundary = edge(vertical[left],top,bottom,tolerance)
                if boundary is None:
                    return None
                if not boundary:
                    slots[root(i)] = root(i-1)
            if row:
                boundary = edge(horizontal[top],left,right,tolerance)
                if boundary is None:
                    return None
                if not boundary:
                    slots[root(i)] = root(i-columns)
    groups = defaultdict(list)
    for i in range(len(slots)):
        groups[root(i)].append(divmod(i,columns))
    result = []
    for group in groups.values():
        first_row,first_column = map(min,zip(*group))
        last_row,last_column = map(max,zip(*group))
        if len(group)!=(last_row-first_row+1)*(last_column-first_column+1):
            return None
        # A drawn edge inside an otherwise connected component is still an
        # unresolved separator, even if its endpoints can be walked around.
        for row in range(first_row,last_row+1):
            for column in range(first_column,last_column+1):
                if (column>first_column and edge(vertical[xs[column]],ys[row],ys[row+1],tolerance)
                        or row>first_row and edge(horizontal[ys[row]],xs[column],xs[column+1],tolerance)):
                    return None
        result.append((first_row,first_column,last_row+1,last_column+1))
    return sorted(result)


def contains(box, other, tolerance=0):
    return all(a-tolerance<=b for a,b in zip(box[:2],other[:2])) and all(a+tolerance>=b for a,b in zip(box[2:],other[2:]))


def intersects(box, other):
    return all(max(a,b)<min(c,d) for a,b,c,d in zip(box[:2],other[:2],box[2:],other[2:]))


def cell_observations(shape, xs, ys, tokens, tolerance):
    """Index cell origins by slot, then prove ownership for each token."""
    owners = {}
    rectangles = {}
    for row,column,bottom,right in shape:
        origin = row,column
        rectangles[origin] = xs[column],ys[row],xs[right],ys[bottom]
        for y in range(row,bottom):
            for x in range(column,right):
                owners[y,x] = origin
    groups = defaultdict(list)
    for token in tokens:
        left,top,right,bottom = token.rect
        column = bisect_right(xs,(left+right)/2)-1
        row = bisect_right(ys,(top+bottom)/2)-1
        origin = owners.get((row,column))
        if origin is None or not contains(rectangles[origin],token.rect,tolerance/4):
            return None
        groups[origin].append(token)
    return groups


def ruled_tables(horizontal, vertical, tokens, locate, read_cell, tolerance):
    """Return shared grids and the exact observations they replace."""
    if len(horizontal)+len(vertical)>MAX_RULES:
        return []
    frames = []
    for h_rules,v_rules in connections(horizontal,vertical,tolerance):
        horizontal_axes,vertical_axes = positions(h_rules,tolerance),positions(v_rules,tolerance)
        xs,ys = list(vertical_axes),list(horizontal_axes)
        if len(xs)<3 or len(ys)<3:
            continue
        box = xs[0],ys[0],xs[-1],ys[-1]
        if not (all(edge(horizontal_axes[y],xs[0],xs[-1],tolerance) for y in (ys[0],ys[-1]))
                and all(edge(vertical_axes[x],ys[0],ys[-1],tolerance) for x in (xs[0],xs[-1]))):
            continue
        observed = [token for token in tokens if intersects(box,token.rect)]
        if not observed:
            continue
        shape = cells(xs,ys,horizontal_axes,vertical_axes,tolerance)
        frames.append((box,xs,ys,shape,observed))

    result = []
    for box,xs,ys,shape,observed in frames:
        overlapping = any(other!=box and intersects(other,box) for other,*_ in frames)
        claim = {token.key for token in observed}
        loc = locate(box)
        assigned = cell_observations(shape,xs,ys,observed,tolerance) if shape and not overlapping else None
        if assigned is not None:
            grid = Grid()
            for row,column,bottom,right in shape:
                bounds = (xs[column],ys[row],xs[right],ys[bottom])
                while grid.row<row:
                    if grid.row>=0:
                        grid.end_row()
                    grid.begin_row()
                grid.column = column
                text, provenance, issue = read_cell(assigned.get((row,column),()),bounds)
                if issue:
                    grid.issue(issue)
                grid.place([passage(text)] if text else [],{**locate(bounds),**provenance},
                           columns=right-column,rows=bottom-row)
            while grid.row<len(ys)-2:
                grid.end_row()
                grid.begin_row()
            grid.end_row()
        if assigned is None:
            # Keep the observed words available to the companion and scope
            # the hold to the exact region where competing owners remain.
            text,provenance,_ = read_cell(observed,None)
            loc.update(provenance)
            issue = 'Visible table boundaries do not establish a unique cell for all of this text. Verify the marked table before narration.'
        else:
            table = grid.finish()
            text = source_text(table)
            loc.update(table_grid=table,rows=source_rows(table))
            issue = table['issue']
        loc['table_evidence'] = 'visible intersecting rules'
        result.append((claim,{'text':text,'locator':loc,'issue':issue,'role':'table'}))
    return result
