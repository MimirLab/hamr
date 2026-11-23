"""Evidence and sample-level edits for audiobook joins and isolated gain faults."""
import re
from statistics import median

import numpy as np

from .core import RATE, key
from .performance import QUOTATION


RECIPE = 'continuity-1'
EDGE_METER = 'quiet-edges-5ms-v1'
WINDOW = RATE // 200
EDGE_LIMIT = RATE // 5
QUIET_PEAK = 24  # About -63 dBFS on signed 16-bit PCM.
GUARD = RATE // 100  # Keep at least 10 ms per edge out of pause accounting.
MAX_COMPENSATION = RATE * 120 // 1000
STEP_SAMPLES = 8
MIN_STEP = 700


def _quiet_samples(edge):
    count = len(edge) // WINDOW
    windows = edge[:count * WINDOW].reshape(count, WINDOW)
    found = np.flatnonzero(np.max(np.abs(windows), axis=1) > QUIET_PEAK)
    return int(found[0] if found.size else count) * WINDOW


def _isolated_step(edge):
    # Require a short one-sided discontinuity at the actual file edge and
    # 40 ms of already quiet audio between it and the nearest audible speech.
    # A transient followed by immediately adjacent speech is not evidence.
    if (len(edge) < STEP_SAMPLES + RATE // 25 or abs(edge[0] - edge[1]) < MIN_STEP
            or np.max(np.abs(edge[1:STEP_SAMPLES + RATE // 25])) > QUIET_PEAK):
        return 0
    return STEP_SAMPLES


def observe_edges(path, samples):
    width = min(samples, EDGE_LIMIT)
    with path.open('rb') as stream:
        head = np.frombuffer(stream.read(width * 2), dtype='<i2').astype(np.int32)
        stream.seek((samples - width) * 2)
        tail = np.frombuffer(stream.read(width * 2), dtype='<i2').astype(np.int32)[::-1]
    return {'leading_quiet': _quiet_samples(head), 'trailing_quiet': _quiet_samples(tail),
            'leading_step': _isolated_step(head), 'trailing_step': _isolated_step(tail)}


def valid_edges(reading, samples):
    fields = ('leading_quiet', 'trailing_quiet', 'leading_step', 'trailing_step')
    return (isinstance(reading, dict) and set(reading) == set(fields)
            and all(type(reading[field]) is int and 0 <= reading[field] <= min(samples, EDGE_LIMIT)
                    for field in fields)
            and reading['leading_step'] in (0, STEP_SAMPLES)
            and reading['trailing_step'] in (0, STEP_SAMPLES))


def _comparable(left, right):
    return (left['protection'] is None and right['protection'] is None
            and (left['chapter'], left['scene']) == (right['chapter'], right['scene']))


def _protected_form(unit):
    return (unit.get('delivery_boundary') in ('authored_lines', 'incomplete')
            or any(mark in unit['text'] for mark in ('"', '“', '”', '‘'))
            or re.search(QUOTATION, unit['text']) is not None)


def _reading_offset(index, levels, units):
    nearby = levels[index - 2:index] + levels[index + 1:index + 3]
    current = levels[index]
    # Four contiguous anchors exclude a change of register throughout a paragraph.
    # Unattributed quoted speech is a poor level reference even without a cue.
    if (not all(_comparable(current, other) for other in nearby)
            or any(_protected_form(unit) for unit in units[index - 2:index + 3])):
        return None
    baselines = [row['active_rms_dbfs'] for row in nearby]
    if max(baselines) - min(baselines) > 3:
        return None
    offset = current['active_rms_dbfs'] - median(baselines)
    peak = current['sample_peak_dbfs']
    peak_offset = peak - median(row['sample_peak_dbfs'] for row in nearby)
    if abs(offset) < 7 or abs(offset - peak_offset) > 3:
        return None
    gain = min(3, -offset, -3 - peak) if offset < 0 else -3
    if abs(gain) < .25:
        return None
    return {'gain_db': round(gain, 2), 'offset_db': round(offset, 2),
            'anchors': [row['utterance'] for row in nearby]}


def plan(job, receipts, levels, edges):
    units = job['plan']['utterances']
    edits = {}
    protected = [_protected_form(unit) for unit in units]
    for index, (unit, receipt, level) in enumerate(zip(units, receipts, levels, strict=True)):
        uid = unit['id']
        if level['protection'] or protected[index]:
            continue
        transition = {}
        observation = edges[receipt['sha256']]
        # Enforce the same protection on both sides of an edited join.
        if 0 < index and not protected[index - 1] and _comparable(levels[index - 1], level) and observation['leading_step']:
            transition['fade_in_samples'] = observation['leading_step']
        if index + 1 < len(units) and not protected[index + 1] and _comparable(level, levels[index + 1]):
            if observation['trailing_step']:
                transition['fade_out_samples'] = observation['trailing_step']
            following = edges[receipts[index + 1]['sha256']]
            pause = unit['pause_samples']
            if pause in (4320, 7200, 8400):
                trailing = max(0, observation['trailing_quiet'] - GUARD)
                leading = max(0, following['leading_quiet'] - GUARD)
                reduction = min(trailing + leading, MAX_COMPENSATION, pause // 2)
                if reduction >= 2 * WINDOW:
                    transition.update(pause_samples=pause - reduction, planned_pause_samples=pause,
                                      trailing_quiet_samples=observation['trailing_quiet'],
                                      leading_quiet_samples=following['leading_quiet'])
        if 2 <= index < len(units) - 2:
            if correction := _reading_offset(index, levels, units):
                transition.update(correction)
        if transition:
            edits[uid] = transition
    return edits


def identity(receipts, units, edits):
    return key({'recipe': RECIPE,
                'takes': [(r['sha256'], r['samples'], u['pause_samples'], edits.get(u['id']))
                          for u, r in zip(units, receipts, strict=True)]})


def render(takes, units, edits):
    """Apply the plan in floating point so per-take gain never clips intermediate PCM."""
    for (path, pause), unit in zip(takes, units, strict=True):
        edit = edits.get(unit['id'], {})
        gain = 10 ** (edit.get('gain_db', 0) / 20) / 32768
        first = edit.get('fade_in_samples', 0)
        last = edit.get('fade_out_samples', 0)
        with path.open('rb') as stream:
            remaining = path.stat().st_size // 2
            while remaining:
                chunk = stream.read(min(remaining, RATE) * 2)
                samples = np.frombuffer(chunk, dtype='<i2').astype('<f4')
                if samples.size == 0:
                    raise OSError(f'Unexpected EOF in accepted take: {path}')
                samples *= gain
                if first:
                    samples[:first] *= np.linspace(0, 1, first)
                    first = 0
                remaining -= samples.size
                if last and remaining < last:
                    # The final block can be shorter than the fade. Start in
                    # the preceding block when necessary and finish at EOF.
                    fade = np.linspace(1, 0, last)
                    edge = min(last - remaining, samples.size)
                    samples[-edge:] *= fade[last - remaining - edge:last - remaining]
                yield samples.tobytes()
        pause = edit.get('pause_samples', pause)
        while pause:
            size = min(pause, RATE)
            yield bytes(size * 4)
            pause -= size
