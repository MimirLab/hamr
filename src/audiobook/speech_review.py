"""Local review of uncertain speech evidence and exact recordings."""
from collections import defaultdict
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import wave

from .core import Error, RATE, digest, loadjson
from .installation import command
from .jobs import clip_key, final_path, valid_clip
from .speech_compare import comparison_tokens


REASONS = {
    'planned_reading': (0, 'Reported planned reading problem'),
    'recording': (1, 'Reported recording problem'),
    'cutoff': (2, 'Possible clipped ending'),
    'omission': (3, 'Possible missing phrase'),
    'meaning': (4, 'Possible change in value or meaning'),
    'extra_audio': (5, 'Possible extra speech'),
    'notation': (7, 'Same values with a notation disagreement'),
    'words': (6, 'Other word disagreement'),
}


def category(flags, findings, edits, intended, row):
    open_kinds = {finding.get('kind', 'recording') for finding in findings if finding['status'] == 'open'}
    if 'planned_reading' in open_kinds:
        return 'planned_reading'
    flags = ' '.join(flags).casefold()
    if 'recording' in open_kinds or 'listening finding:' in flags:
        return 'recording'
    if 'missing final clause' in flags or 'mid-sentence truncation' in flags:
        return 'cutoff'
    if edits:
        length = len(comparison_tokens(intended))
        first, end = edits[-1]['expected_token_range']
        earlier_edits = sum(max(len(edit['expected']), len(edit['observed'])) for edit in edits[:-1])
        if (end == length and end-first >= 3 and first >= 3 and len(edits[-1]['observed']) <= 1
                and earlier_edits <= min(2, first//5)):
            return 'cutoff'
    if 'internal source phrase' in flags or 'opening verb' in flags:
        return 'omission'
    if any(term in flags for term in ('quantity', 'negation', 'count not recovered', 'operator', 'checklist status')):
        return 'meaning'
    if any(term in flags for term in ('extra speech', 'unplanned words', 'reference-only phrase')):
        return 'extra_audio'
    values = row.get('comparison_quantities_expected')
    both_values_match = values and values == row.get('comparison_quantities_observed')
    if (both_values_match and edits and
            all(set(edit['expected'] + edit['observed']) <= {'to', 'through', 'and', 'dash', 'hyphen'}
                for edit in edits)):
        return 'notation'
    return 'words'


def snapshot(store, job):
    jid, rev = job['id'], job['revision']
    directory = store.path(job)
    report_path = directory / f'qa-r{rev}.json'
    reports = {}
    verifier = None
    try:
        if not report_path.is_symlink():
            report = loadjson(report_path)
            if (report['job'], report['revision'], report['plan_sha256']) == (jid, rev, job['plan']['plan_sha256']):
                reports = {row['utterance']: row for row in report['rows']}
                verifier = report.get('primary_profile')
    except (OSError, ValueError, TypeError, KeyError):
        pass
    blocks = {block['id']: block for block in job['document']['blocks']}
    passages = {u['id']: u for u in job['plan']['utterances']}
    by_take = defaultdict(list)
    history = []
    for field, decision in (('audio_acceptances', 'Accepted'), ('revoked_audio_acceptances', 'Acceptance withdrawn'),
                            ('listening_findings', 'Reported')):
        for row in job.get(field, []):
            uid, clip, pcm = (row.get(field) for field in ('utterance', 'clip_key', 'pcm_sha256'))
            if not pcm:
                continue
            u = passages.get(uid)
            current = u is not None and clip_key(u, job) == clip
            history.append({'utterance': uid, 'decision': decision, 'reason': row['reason'],
                            'kind': row.get('kind'), 'status': row.get('status'),
                            'at': row.get('at', row.get('created', 0)), 'current_key': current,
                            'clip_key': clip, 'pcm_sha256': pcm})
            if field == 'listening_findings':
                by_take[clip, pcm].append(row)

    groups = defaultdict(list)
    for pending in job.get('audio_review', []):
        uid = pending['utterance']
        if uid not in passages or not pending.get('pcm_sha256'):
            continue
        u = passages[uid]
        clip, pcm = pending['clip_key'], pending['pcm_sha256']
        row = reports.get(uid, {})
        if row.get('pcm_sha256') != pcm:
            row = {}
        intended = u['text']
        findings = by_take[clip, pcm] + row.get('listening_findings', [])
        reading = pending.get('reading_sha256')
        findings = [finding for finding in findings if not finding.get('reading_sha256')
                    or finding.get('reading_sha256')==reading]
        edits = row.get('residual_word_edits') or pending.get('residual_word_edits', [])
        reason = pending.get('kind') or category(pending['flags'], findings, edits, intended, row)
        listen_for = []
        for edit in edits:
            if edit['expected']:
                listen_for.append(' '.join(word.removeprefix('#') for word in edit['expected']))
        if not listen_for:
            listen_for = list(dict.fromkeys(finding['reason'] for finding in findings if finding['status']=='open'))
        source = [blocks[source_id] for source_id in u['sources'] if source_id in blocks]
        chapter = u.get('chapter') or next((b['chapter'] for b in source if b.get('chapter')), None) or 'Unassigned'
        export = command(store.root, 'excerpt', jid, '--start', uid, '--count', '1',
                         '--output', f'hamr-review-{jid}-{uid}-r{rev}')
        repair = (command(store.root, 'show', jid, '--content') if reason == 'planned_reading'
                  else command(store.root, 'repair', jid, '--retake', uid, '--expect-revision', str(rev)))
        pattern = REASONS[reason][1]
        if reason == 'words' and len(edits) == 1:
            edit = edits[0]
            if 0 < len(edit['expected']) + len(edit['observed']) <= 4:
                pattern = (' '.join(edit['expected']) or 'No planned words') + ' → ' + (' '.join(edit['observed']) or 'Omitted')
        elif reason == 'meaning':
            pattern = pending['flags'][0]
        groups[reason].append({
            'utterance': uid, 'clip_key': clip, 'pcm_sha256': pcm, 'chapter': chapter,
            'source': [{'id': b['id'], 'text': b['source']} for b in source],
            'intended': intended, 'transcript': row.get('text'), 'observer': verifier if row else None,
            'play': command(store.root, 'review', jid, '--play', uid),
            'pattern': pattern,
            'comparison_text': row.get('comparison_text'), 'listen_for': listen_for,
            'second_transcript': (row.get('challenger_observation') or {}).get('text'),
            'flags': pending['flags'], 'warnings': row.get('observation_warnings', []),
            'export': export, 'repair': repair,
        })

    ordered = [{'reason': reason, 'label': label, 'count': len(groups[reason]), 'passages': groups[reason]}
               for reason, (_, label) in sorted(REASONS.items(), key=lambda pair: pair[1][0]) if groups[reason]]
    return {
        'schema_version': 1, 'job': jid, 'revision': rev, 'title': job['document']['title'],
        'status': job['status'], 'count': sum(group['count'] for group in ordered), 'groups': ordered,
        'history': sorted(history, key=lambda row: row['at'], reverse=True),
        'report': str(report_path) if reports else None, 'planned_output': str(final_path(job) / 'book.m4b'),
        'source_review_count': len(job['plan']['issues']),
        'commands': {
            'review': command(store.root, 'review', jid, '--serve'),
            'report': command(store.root, 'review', jid, '--report', f'hamr-review-{jid}-r{rev}.md'),
            'recheck': command(store.root, 'qa', jid, '--recheck'),
            'resume': command(store.root, 'resume', jid),
            'status': command(store.root, 'status', jid),
        },
    }


def print_summary(data):
    print(f'{data["job"]} · revision {data["revision"]} · {data["title"]}')
    print(f'{data["count"]} passages awaiting speech review.')
    if data['report']:
        print(f'Verifier report: {data["report"]}')
    for group in data['groups']:
        first = group['passages'][0]['utterance']
        print(f'  {group["count"]:>4}  {group["label"]} · first {first}')
    commands = data['commands']
    if data['count']:
        first = data['groups'][0]['passages'][0]
        print(f'Publication is on hold. Planned file: {data["planned_output"]}')
        print(f'Play {first["utterance"]}:\n{first["play"]}')
        print(f'Export it as a WAV:\n{first["export"]}')
        print(f'Write the full review report:\n{commands["report"]}')
        print(f'Next, open the local listening review:\n{commands["review"]}')
    else:
        next_command = commands['status'] if data['source_review_count'] or data['status']=='ready' else commands['resume']
        print(f'Next, check publication or continue the job:\n{next_command}')


def markdown(data):
    lines = [f'# Speech review · {data["title"]}', '',
             f'Job {data["job"]} · revision {data["revision"]} · {data["count"]} pending passages', '',
             'Verifier transcripts are uncertain. Listen to the recording before deciding.',
             'A decision applies to the recording identified by its PCM checksum.', '']
    if data['report']:
        lines.extend((f'Verifier report: {data["report"]}', ''))
    for group in data['groups']:
        lines.extend((f'## {group["label"]} ({group["count"]})', ''))
        for row in group['passages']:
            lines.extend((f'### {row["utterance"]} · {row["chapter"]}', '',
                          'PCM SHA-256: ' + row['pcm_sha256'], '',
                          'Source', '', *[f'> {block["id"]} · {block["text"]}' for block in row['source']], '',
                          'Intended reading', '', row['intended'], '',
                          'Verifier transcript', '', row['transcript'] if row['transcript'] is not None else 'Unavailable for this recording.', '',
                          *(['Additional verifier transcript', '', row['second_transcript'], ''] if row['second_transcript'] is not None else []),
                          *(f'- Listen for: {phrase}' for phrase in row['listen_for']),
                          *(f'- {flag}' for flag in row['flags'] + row['warnings']), '',
                          'Play or export the recording', '', f'    {row["play"]}', '', f'    {row["export"]}', '',
                          ('Correct the planned reading before generating a replacement' if group['reason']=='planned_reading'
                           else 'Retake if listening confirms a recording defect'), '', f'    {row["repair"]}', ''))
    if data['history']:
        lines.extend(('## Decision history', ''))
        for row in data['history']:
            lines.append(f'- {row["utterance"]} · {row["decision"]} · {row["reason"]} · {row["pcm_sha256"]}')
        lines.append('')
    lines.extend(('## Commands', '', *(f'    {cmd}' for cmd in data['commands'].values()), ''))
    return '\n'.join(lines)


def read_audio(store, clip, pcm):
    receipt = valid_clip(store, clip)
    if not receipt or receipt['sha256'] != pcm:
        raise Error('review_changed', 'The exact recording is unavailable. Reload the review for the current take.')
    audio = (store.root / 'clips' / clip / 'audio.pcm').read_bytes()
    if digest(audio) != pcm:
        raise Error('review_changed', 'The recording changed during playback. Reload the review.')
    target = BytesIO()
    with wave.open(target, 'wb') as writer:
        writer.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        writer.writeframes(audio)
    return target.getvalue()


def play(store, job, uid):
    unit = next((u for u in job['plan']['utterances'] if u['id']==uid), None)
    if unit is None:
        raise Error('unknown_utterance', 'That passage is outside the current reading plan.')
    clip = clip_key(unit, job)
    row = next((r for r in job.get('audio_review', []) if r['utterance']==uid), None)
    receipt = valid_clip(store, clip)
    if not receipt:
        raise Error('review_missing_clip', 'This passage has no verified recording.')
    pcm = (row or {}).get('pcm_sha256', receipt['sha256'])
    audio = read_audio(store, clip, pcm)
    with tempfile.TemporaryDirectory(prefix='hamr-review-') as scratch:
        path = Path(scratch) / 'audio.wav'
        path.write_bytes(audio)
        try:
            subprocess.run(['mpv', '--no-video', '--really-quiet', str(path)], check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            export = command(store.root, 'excerpt', job['id'], '--start', uid, '--count', '1',
                             '--output', f'hamr-review-{job["id"]}-{uid}-r{job["revision"]}')
            raise Error('playback_failed', 'mpv could not play the recording.',
                        'Check mpv or export a WAV to play in your audio player.\n'+export) from exc
    return {'schema_version': 1, 'job': job['id'], 'revision': job['revision'], 'utterance': uid, 'pcm_sha256': pcm}
