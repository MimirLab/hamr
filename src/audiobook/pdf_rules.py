"""Identify connected table rules encoded as stretched single-pixel images."""

from collections import defaultdict

from pypdfium2 import PdfiumError


TOLERANCE = .6  # PDF points, including the thickness of the painted rules.


def thin_axis(obj, rect):
    left, bottom, right, top = rect
    width, height = right-left, top-bottom
    if obj.level or min(width, height) > .75 or max(width, height) < 2:
        return None
    if min(width, height) <= 0 or max(width, height) < 5*min(width, height):
        return None
    try:
        matrix = obj.get_matrix()
        if abs(matrix.b) <= .01 and abs(matrix.c) <= .01 and obj.get_px_size() == (1, 1):
            return 'horizontal' if width > height else 'vertical'
    except PdfiumError:
        pass
    return None


def overlaid_glyphs(rect, glyphs):
    left, bottom, right, top = rect
    return [index for ch, (x0, y0, x1, y1), index in glyphs if ch.strip()
            and min(right, x1)-max(left, x0) > .04 and min(top, y1)-max(bottom, y0) > .04]


def stacked_digits(rect, glyphs):
    left, bottom, right, top = rect
    above, below = [], []
    for ch, (x0, y0, x1, y1), index in glyphs:
        if ch not in '0123456789' or min(right, x1)-max(left, x0) <= .2:
            continue
        if 0 <= y0-top <= 2:
            above.append(index)
        if 0 <= bottom-y1 <= 2:
            below.append(index)
    return above + below if above and below else []


def vertical_runs(segments):
    runs = []
    by_x = defaultdict(list)
    for index, (left, bottom, right, top) in segments.items():
        by_x[round((left+right)*2)/4].append((bottom, top, index))
    for x, group in sorted(by_x.items()):
        start, end, indices = 0, 0, []
        for low, high, index in sorted(group):
            if indices and low-end > TOLERANCE:
                runs.append((x, start, end, indices))
                indices = []
            if not indices:
                start, end = low, high
            else:
                end = max(end, high)
            indices.append(index)
        if indices:
            runs.append((x, start, end, indices))
    return [row for row in runs if row[2]-row[1] >= 24]


def framed_panels(vertical, horizontal):
    """Use a continuous pair of sides and two connected end rules as witnesses."""
    runs = vertical_runs(vertical)
    panels = []
    accepted = set()
    for at, (left, bottom, top, sides) in enumerate(runs):
        neighbor = next((r for r in runs[at+1:] if r[0] > left
                         and abs(bottom-r[1]) <= TOLERANCE and abs(top-r[2]) <= TOLERANCE), None)
        if neighbor is None:
            continue
        right, _, _, other_sides = neighbor
        if right-left < 24:
            continue
        levels = []
        for index, (x0, y0, x1, y1) in horizontal.items():
            height = (y0+y1)/2
            if x0 <= left+TOLERANCE and x1 >= right-TOLERANCE and bottom-TOLERANCE <= height <= top+TOLERANCE:
                levels.append((height, index))
        if not levels or min(y for y, _ in levels) > bottom+TOLERANCE or max(y for y, _ in levels) < top-TOLERANCE:
            continue
        accepted.update((*sides, *other_sides, *(index for _, index in levels)))
        panels.append({'rect':[left, bottom, right, top], 'rule_levels':sorted((y for y, _ in levels), reverse=True)})
    return panels, accepted


def classify(images, glyphs):
    """Return layout witnesses, local text paint and images needing visual OCR."""
    horizontal, vertical, overlays = {}, {}, {}
    for index, (obj, rect) in enumerate(images):
        axis = thin_axis(obj, rect)
        if axis is None:
            continue
        touched = overlaid_glyphs(rect, glyphs)
        if touched:
            overlays[index] = {'rect':rect, 'native_characters':touched, 'relation':'overlapping_paint'}
        elif axis == 'horizontal' and rect[2]-rect[0] < 18 and (stacked := stacked_digits(rect, glyphs)):
            overlays[index] = {'rect':rect, 'native_characters':stacked, 'relation':'stacked_digits'}
        else:
            (horizontal if axis == 'horizontal' else vertical)[index] = rect
    panels, rules = framed_panels(vertical, horizontal)
    return (panels, [images[i][1] for i in sorted(rules)], list(overlays.values()),
            [rect for i, (_, rect) in enumerate(images) if i not in rules and i not in overlays])
