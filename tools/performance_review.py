"""Run a private listening review inbox or serve a single calibration pack."""
import argparse
from contextlib import closing
import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from string import Template
import sys
from tempfile import NamedTemporaryFile
from threading import Lock


COMMENT_LIMIT = 4000

PAGE = Template("""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; media-src 'self' file:; connect-src 'self'; base-uri 'none'; form-action 'none'">
<title>$title</title>
<style>
body { font: 1.05rem/1.55 system-ui; background: #f8f2e6; color: #25342d; max-width: 46rem; padding: 0 1rem 2rem; margin: 2rem auto; }
section { border-top: 1px solid #a89b83; margin-top: 1.4rem; padding-top: 1rem; }
h1, h2 { line-height: 1.15; }
h2 { margin: 0; font-size: 1.3rem; }
blockquote { margin: 0 0 1rem; border-left: .2rem solid #a89b83; padding-left: .8rem; }
.take { margin: .7rem 0; }
.take-note { margin: 0 0 .25rem; }
.comment-label { display: block; min-height: 0; border: 0; margin: .85rem 0 .25rem; padding: 0; font-weight: 600; cursor: text; }
textarea { box-sizing: border-box; display: block; width: 100%; padding: .55rem; font: inherit; color: inherit;
  background: #fffaf1; border: 1px solid #897c61; border-radius: .35rem; resize: vertical; }
.save-comment { margin-top: .5rem; }
audio { width: min(100%, 30rem); }
fieldset { border: none; padding: 0; margin: 1rem 0 0; }
legend { padding: 0; font-weight: 600; }
label, button { display: inline-flex; align-items: center; gap: .4rem; min-height: 2.75rem; font: inherit;
  padding: 0 .8rem; margin: .25rem .35rem .25rem 0; border: 1px solid #897c61; border-radius: .35rem; cursor: pointer; }
label:has(:checked) { background: #d9e8d7; border-color: #426349; }
button { color: #25342d; background: #fffaf1; }
input { accent-color: #426349; }
footer { margin-top: 1.5rem; }
.state { margin: 0 0 .25rem; font-size: .9rem; color: #546457; }
</style>
<main>
<h1>$title</h1>
<p>$intro</p>
$sections
<footer>
<p id="status" role="status"></p>
<button type="button" id="export">Download feedback</button>
$refresh
</footer>
</main>
<script>
const pack = $pack;
let picks = $picks;
let comments = $comments;
const fields = document.querySelectorAll('input[type="radio"]');
const commentFields = document.querySelectorAll('textarea[data-review]');
const status = document.querySelector('#status');
const online = location.protocol === 'http:';
const storageKey = 'hamr-performance:' + pack;
const unsaved = { picks: {}, comments: {} };
let pending = Promise.resolve();
let edit = 0;

function markSaved(key) {
  const state = document.getElementById('state-' + key);
  if (!state) return;
  const labels = [];
  if (picks[key]) labels.push('Pick saved');
  if (comments[key]) labels.push('Comment saved');
  state.textContent = labels.length ? labels.join(' · ') + ' · awaiting follow-up' : 'Ready to listen';
}

if (!online) {
  try {
    picks = { ...picks, ...JSON.parse(localStorage.getItem(storageKey) || '{}') };
    comments = { ...comments, ...JSON.parse(localStorage.getItem(storageKey + ':comments') || '{}') };
  } catch {}
}

function save(group, key, value) {
  const current = group === 'picks' ? picks : comments;
  current[key] = value;
  if (!online) {
    try {
      localStorage.setItem(storageKey, JSON.stringify(picks));
      localStorage.setItem(storageKey + ':comments', JSON.stringify(comments));
      status.textContent = 'Saved in this browser. Download feedback to share it.';
    } catch {
      status.textContent = 'Download feedback to keep these changes.';
    }
    return;
  }
  const version = ++edit;
  unsaved[group][key] = value;
  status.textContent = 'Saving…';
  pending = pending.then(async () => {
    const changes = { picks: { ...unsaved.picks }, comments: { ...unsaved.comments } };
    if (!Object.keys(changes.picks).length && !Object.keys(changes.comments).length) return;
    try {
      const result = await fetch('feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes)
      });
      if (!result.ok) throw new Error('Could not save feedback');
      for (const kind of Object.keys(changes)) {
        for (const [id, saved] of Object.entries(changes[kind])) {
          if (unsaved[kind][id] === saved) delete unsaved[kind][id];
        }
      }
      for (const id of new Set([...Object.keys(changes.picks), ...Object.keys(changes.comments)])) {
        if (!(id in unsaved.picks) && !(id in unsaved.comments)) markSaved(id);
      }
      if (!Object.keys(unsaved.picks).length && !Object.keys(unsaved.comments).length) status.textContent = 'Feedback saved.';
    } catch {
      if (edit === version) status.textContent = 'Could not save to disk. Download feedback to keep it.';
    }
  });
}

for (const field of fields) {
  field.checked = picks[field.name] === field.value;
  field.addEventListener('change', () => save('picks', field.name, field.value));
}
for (const field of commentFields) {
  const key = field.dataset.review;
  field.value = comments[key] || '';
  field.addEventListener('change', () => save('comments', key, field.value));
  document.getElementById('save-comment-' + key).addEventListener('click', () => save('comments', key, field.value));
}
status.textContent = online ? 'Picks save as you go. Comments save when you leave the field or select Save comment.' : 'Download feedback to share it.';
for (const audio of document.querySelectorAll('audio')) {
  audio.addEventListener('play', () => {
    for (const other of document.querySelectorAll('audio')) if (other !== audio) other.pause();
  });
}
document.querySelector('#export').addEventListener('click', () => {
  const notes = { ...comments };
  for (const field of commentFields) if (field.value || notes[field.dataset.review] !== undefined) notes[field.dataset.review] = field.value;
  const file = new Blob([JSON.stringify({ pack, picks, comments: notes }, null, 2) + '\\n'], { type: 'application/json' });
  const url = URL.createObjectURL(file);
  const link = document.createElement('a');
  link.href = url;
  link.download = pack + '-picks.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
</script>
</html>
""")


def load_pack(root):
    pack = json.loads((root / 'listening-pack.json').read_text(encoding='utf-8'))
    if not re.fullmatch(r'[a-z0-9-]+', pack['id']):
        raise ValueError('Invalid listening pack ID.')
    keys, files = set(), {}
    for pair in pack['pairs']:
        key = pair['id']
        if key in keys or not re.fullmatch(r'[a-z0-9-]+', key):
            raise ValueError('Invalid or duplicate listening pair ID.')
        keys.add(key)
        if not all(isinstance(pair[k], str) for k in ('title', 'prompt', 'words')):
            raise ValueError('Each entry needs a title, a listening prompt, and the words being read.')
        sides = take_options(pair)
        if 'variant_notes' in pair:
            notes = pair['variant_notes']
            if (not isinstance(notes, dict) or notes.keys() != set(sides)
                    or not all(isinstance(note, str) and note.strip() for note in notes.values())):
                raise ValueError('Variant notes must describe every audio option in the entry.')
        for side in sides:
            name = pair[side]
            if not re.fullmatch(r'[a-zA-Z0-9_-]+\.wav', name):
                raise ValueError('A take must name a WAV file in the pack directory.')
            path = (root / name).resolve(strict=True)
            if path.parent != root or not path.is_file():
                raise ValueError('A take must be a local file in the pack directory.')
            files['/' + name] = path
    return pack, files


def take_options(pair):
    if 'sample' in pair:
        return ('sample',)
    return ('A', 'B', 'C') if 'C' in pair else ('A', 'B')


def choices(pair):
    sides = take_options(pair)
    if sides == ('sample',):
        return (('accept', 'Sounds good'), ('flag', 'Needs work'))
    return tuple((side, side) for side in sides) + (('tie', 'Tie'),)


def check_picks(picks, pairs):
    return isinstance(picks, dict) and all(key in pairs and isinstance(choice, str) and choice in dict(choices(pairs[key]))
                                           for key, choice in picks.items())


def load_picks(path, pairs):
    picks = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not check_picks(picks, pairs):
        raise ValueError('Invalid saved listening picks.')
    return picks


def check_comments(comments, pairs):
    return isinstance(comments, dict) and all(
        key in pairs and isinstance(note, str) and len(note) <= COMMENT_LIMIT
        for key, note in comments.items())


def load_comments(path, pairs):
    comments = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not check_comments(comments, pairs):
        raise ValueError('Invalid saved listening comments.')
    return comments


def render(pack, picks, comments=None, *, inbox=False):
    comments = comments or {}
    sections = []
    for pair in pack['pairs']:
        key = pair['id']
        title, prompt, words = (escape(pair[k]) for k in ('title', 'prompt', 'words'))
        notes = pair.get('variant_notes', {})
        players = []
        for side in take_options(pair):
            label = side if side != 'sample' else ''
            if note := notes.get(side):
                label = f'{label} · {note}' if label else note
            caption = f'<p class="take-note">{escape(label)}</p>' if label else ''
            players.append(f'<div class="take">{caption}<audio controls preload="none" '
                           f'aria-label="{title}, {escape(label or side)}" src="{pair[side]}"></audio></div>')
        options = ''.join(
            f'<label><input type="radio" name="{key}" value="{value}">{label}</label>'
            for value, label in choices(pair))
        state = ''
        if inbox:
            labels = [label for value, label in ((picks.get(key), 'Pick saved'), (comments.get(key), 'Comment saved')) if value]
            status = ' · '.join([*labels, 'awaiting follow-up']) if labels else 'Ready to listen'
            state = f'<p class="state" id="state-{key}">{status}</p>'
        sections.append(f'<section>{state}<h2>{title}</h2><p>{prompt}</p><blockquote>{words}</blockquote>'
                        f'{"".join(players)}<fieldset><legend>Your pick</legend>{options}</fieldset>'
                        f'<label class="comment-label" for="comment-{key}">Comment</label>'
                        f'<textarea id="comment-{key}" data-review="{key}" rows="3" maxlength="{COMMENT_LIMIT}" '
                        f'placeholder="What did you hear? A timestamp can help."></textarea>'
                        f'<button class="save-comment" type="button" id="save-comment-{key}">Save comment</button></section>')
    title = 'Hamr listening inbox' if inbox else 'Hamr performance calibration'
    intro = ('Your feedback saves on this computer. Entries stay here until I’ve processed it.' if inbox else
             'Use the choices and comments to record what you heard.')
    return PAGE.substitute(title=title, intro=intro, sections='\n'.join(sections) or '<p>The inbox is clear.</p>',
                           pack=json.dumps(pack['id']), picks=json.dumps(picks),
                           comments=json.dumps(comments).replace('<', r'\u003c'),
                           refresh='<a href="listen.html">Refresh inbox</a>' if inbox else '')


def atomic_write(path, content):
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(content)
    Path(temp.name).replace(path)


def save_picks(path, picks):
    atomic_write(path, (json.dumps(picks, indent=2) + '\n').encode())


def serve(snapshot, record, port, *, inbox=False):
    origin = f'http://127.0.0.1:{port}'
    write_lock = Lock()

    class ReviewHandler(BaseHTTPRequestHandler):
        def send_data(self, data, mime, *, start=None, end=None, length=None):
            self.send_response(206 if start is not None else 200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            if mime == 'audio/wav':
                self.send_header('Accept-Ranges', 'bytes')
            if start is not None:
                self.send_header('Content-Range', f'bytes {start}-{end}/{length}')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get('Host') != f'127.0.0.1:{port}':
                self.send_error(403)
            elif self.path in ('/', '/listen.html'):
                pack, files, picks, comments = snapshot()
                self.send_data(render(pack, picks, comments, inbox=inbox).encode(), 'text/html; charset=utf-8')
            elif file := snapshot()[1].get(self.path):
                if not (header := self.headers.get('Range')):
                    self.send_data(file.read_bytes(), 'audio/wav')
                    return
                length = file.stat().st_size
                match = re.fullmatch(r'bytes=(\d{1,18})-(\d{0,18})', header)
                if not match:
                    self.send_error(416)
                    return
                start = int(match[1])
                end = min(int(match[2]), length - 1) if match[2] else length - 1
                if start > end:
                    self.send_error(416)
                    return
                with file.open('rb') as take:
                    take.seek(start)
                    self.send_data(take.read(end - start + 1), 'audio/wav', start=start, end=end, length=length)
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path not in ('/picks', '/feedback'):
                self.send_error(404)
                return
            if self.headers.get('Origin') != origin or self.headers.get('Host') != f'127.0.0.1:{port}':
                self.send_error(403)
                return
            length = self.headers.get('Content-Length', '')
            if self.headers.get_content_type() != 'application/json' or not re.fullmatch(r'[0-9]{1,5}', length):
                self.send_error(400)
                return
            try:
                feedback = json.loads(self.rfile.read(int(length)))
            except (ValueError, RecursionError):
                self.send_error(400)
                return
            if self.path == '/feedback':
                if not isinstance(feedback, dict) or feedback.keys() - {'picks', 'comments'}:
                    self.send_error(400)
                    return
                picks, comments = feedback.get('picks', {}), feedback.get('comments', {})
                if not isinstance(picks, dict) or not isinstance(comments, dict):
                    self.send_error(400)
                    return
            else:
                picks, comments = feedback, {}
            with write_lock:
                error = record(picks, comments=comments)
            if error:
                self.send_error(error)
                return
            self.send_data(b'{}', 'application/json')

    with ThreadingHTTPServer(('127.0.0.1', port), ReviewHandler) as server:
        print(f'{origin}/listen.html', flush=True)
        server.serve_forever()


class Inbox:
    def __init__(self, root):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = root / 'reviews.sqlite3'
        self.audio = root / 'audio'
        self.audio.mkdir(mode=0o700, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY, sample TEXT NOT NULL, source TEXT NOT NULL, pick TEXT, resolution TEXT,
                comment TEXT NOT NULL DEFAULT ''
            )''')
            if 'comment' not in {row['name'] for row in db.execute('PRAGMA table_info(entries)')}:
                db.execute("ALTER TABLE entries ADD COLUMN comment TEXT NOT NULL DEFAULT ''")

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def entries(self, *, completed=False):
        query = 'SELECT id, sample, source, pick, comment, resolution FROM entries'
        if not completed:
            query += ' WHERE resolution IS NULL'
        with closing(self.connect()) as db:
            return [{**row, 'sample': json.loads(row['sample'])} for row in db.execute(query + ' ORDER BY rowid')]

    def add(self, directory):
        directory = directory.resolve()
        pack, files = load_pack(directory)
        pairs = {pair['id']: pair for pair in pack['pairs']}
        picks = load_picks(directory / 'listening-picks.json', pairs)
        comments = load_comments(directory / 'listening-comments.json', pairs)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for key, pair in pairs.items():
                review_id = f'{pack["id"]}:{key}'
                sample = {**pair, 'id': review_id}
                for side in take_options(pair):
                    data = files['/' + pair[side]].read_bytes()
                    digest = hashlib.sha256(data).hexdigest() + '.wav'
                    target = self.audio / digest
                    if not target.exists():
                        atomic_write(target, data)
                    sample[side] = '/audio/' + digest
                value = json.dumps(sample, sort_keys=True)
                prior = db.execute('SELECT sample FROM entries WHERE id = ?', (review_id,)).fetchone()
                if prior and prior['sample'] != value:
                    raise ValueError(f'{review_id} already exists with different material. Use a new ID for changed audio or prose.')
                db.execute('INSERT OR IGNORE INTO entries (id, sample, source) VALUES (?, ?, ?)',
                           (review_id, value, str(directory)))
                if key in picks:
                    db.execute('UPDATE entries SET pick = ? WHERE id = ? AND pick IS NULL AND resolution IS NULL',
                               (picks[key], review_id))
                if key in comments:
                    db.execute("UPDATE entries SET comment = ? WHERE id = ? AND comment = '' AND resolution IS NULL",
                               (comments[key], review_id))

    def snapshot(self, *, include_comments=False):
        entries = self.entries()
        pack = {'id': 'hamr-listening-inbox', 'pairs': [row['sample'] for row in entries]}
        names = {pair[side] for pair in pack['pairs'] for side in take_options(pair)}
        files = {name: self.audio / Path(name).name for name in names}
        snapshot = (pack, files, {row['id']: row['pick'] for row in entries if row['pick']})
        if include_comments:
            return (*snapshot, {row['id']: row['comment'] for row in entries if row['comment']})
        return snapshot

    def record(self, picks, *, imported=False, comments=None):
        comments = {} if comments is None else comments
        if not isinstance(picks, dict) or not isinstance(comments, dict):
            return 400
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for review_id in picks.keys() | comments.keys():
                row = db.execute('SELECT sample, pick, comment, resolution FROM entries WHERE id = ?', (review_id,)).fetchone()
                if not row:
                    return 400
                pair = {review_id: json.loads(row['sample'])}
                if review_id in picks and not check_picks({review_id: picks[review_id]}, pair):
                    return 400
                if review_id in comments and not check_comments({review_id: comments[review_id]}, pair):
                    return 400
                if row['resolution'] is not None:
                    return 409
                if imported and ((review_id in picks and row['pick'] not in (None, picks[review_id]))
                                 or (review_id in comments and row['comment'] not in ('', comments[review_id]))):
                    return 409
            db.executemany('UPDATE entries SET pick = ? WHERE id = ?', ((v, k) for k, v in picks.items()))
            db.executemany('UPDATE entries SET comment = ? WHERE id = ?', ((v, k) for k, v in comments.items()))

    def complete(self, review_id, pick, result, *, expected_comment=None):
        if not result.strip():
            raise ValueError('Record what was done with this feedback before completing it.')
        if pick is None and not (expected_comment or '').strip():
            raise ValueError('Use the saved pick or exact comment to complete the entry.')
        sql = 'UPDATE entries SET resolution = ? WHERE id = ? AND pick IS ? AND resolution IS NULL'
        params = (result, review_id, pick)
        if expected_comment is not None:
            sql += ' AND comment = ?'
            params += (expected_comment,)
        with closing(self.connect()) as db, db:
            if db.execute(sql, params).rowcount != 1:
                raise ValueError('No open entry matches that pick and comment. Read the current inbox before completing it.')


def inbox_main(argv):
    parser = argparse.ArgumentParser(description='Manage a private, persistent listening inbox.')
    parser.add_argument('directory', type=Path, help='Directory for the private review database and audio')
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('add', help='Enqueue one pack without resetting existing picks')
    add.add_argument('pack', type=Path)
    listing = commands.add_parser('list', help='Read open entries and picks')
    listing.add_argument('--all', action='store_true', help='Include completed entries')
    importer = commands.add_parser('import-picks', help='Import picks downloaded from a registered pack or the inbox')
    importer.add_argument('file', type=Path)
    complete = commands.add_parser('complete', help='Remove a processed entry from the inbox and retain its history')
    complete.add_argument('entry')
    complete.add_argument('--pick', help='Require the saved pick to match')
    complete.add_argument('--result', required=True)
    complete.add_argument('--expect-comment', help='Reject completion if the saved comment changed')
    web = commands.add_parser('serve', help='Serve the inbox on loopback')
    web.add_argument('--port', type=int, default=8772)
    args = parser.parse_args(argv)
    inbox = Inbox(args.directory.resolve())
    try:
        if args.command == 'add':
            inbox.add(args.pack)
        elif args.command == 'list':
            print(json.dumps(inbox.entries(completed=args.all), ensure_ascii=False, indent=2))
        elif args.command == 'import-picks':
            ballot = json.loads(args.file.read_text(encoding='utf-8'))
            picks, comments = ballot['picks'], ballot.get('comments', {})
            if not isinstance(picks, dict) or not isinstance(comments, dict):
                raise ValueError('The download does not contain valid listening feedback.')
            if ballot['pack'] != 'hamr-listening-inbox':
                prefix = ballot['pack'] + ':'
                picks = {prefix+k: v for k, v in picks.items()}
                comments = {prefix+k: v for k, v in comments.items()}
            if inbox.record(picks, imported=True, comments=comments):
                raise ValueError('The download conflicts with existing feedback or a completed or unknown entry.')
        elif args.command == 'complete':
            inbox.complete(args.entry, args.pick, args.result, expected_comment=args.expect_comment)
        else:
            serve(lambda: inbox.snapshot(include_comments=True), inbox.record, args.port, inbox=True)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


def main():
    if sys.argv[1:2] == ['inbox']:
        return inbox_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Directory containing listening-pack.json and the paired WAV files')
    parser.add_argument('--serve', type=int, metavar='PORT', help='Serve the pack locally and save changes as you vote')
    args = parser.parse_args()
    root = args.directory.resolve()
    pack, files = load_pack(root)
    pairs = {pair['id']: pair for pair in pack['pairs']}
    path = root / 'listening-picks.json'
    comments_path = root / 'listening-comments.json'

    def snapshot():
        return pack, files, load_picks(path, pairs), load_comments(comments_path, pairs)

    (root / 'listen.html').write_text(render(pack, *snapshot()[2:]), encoding='utf-8')
    if args.serve:
        def record(picks, *, comments):
            if not check_picks(picks, pairs) or not check_comments(comments, pairs):
                return 400
            if picks:
                save_picks(path, {**load_picks(path, pairs), **picks})
            if comments:
                save_picks(comments_path, {**load_comments(comments_path, pairs), **comments})

        serve(snapshot, record, args.serve)


if __name__ == '__main__':
    main()
