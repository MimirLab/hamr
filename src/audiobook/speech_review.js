'use strict';

const get = id => document.getElementById(id);
const filters = ['reason', 'chapter', 'pattern', 'search'];
const pageSize = 12;
let review, page = 0, sampled = false, visibleDecisions = 12;

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(label, action) {
  const node = element('button', label);
  node.type = 'button';
  node.addEventListener('click', action);
  return node;
}

function audio(row) {
  const container = element('div');
  const url = `audio/${row.clip_key}/${row.pcm_sha256}.wav`;
  const load = button('Play recording', () => {
    const player = element('audio');
    player.controls = true;
    player.src = url;
    player.addEventListener('error', () => get('message').textContent = 'The exact recording could not be loaded. Reload the review or use the WAV export.');
    const link = element('a', 'Save WAV');
    link.href = url;
    link.download = `${row.utterance}.wav`;
    container.replaceChildren(player, element('br'), link);
    player.play().catch(() => {});
  });
  container.append(load);
  return container;
}

function passages() {
  return review.groups.flatMap(group => group.passages.map(row => ({...row, reason: group.reason, label: group.label})));
}

function options(id, items) {
  const select = get(id);
  const previous = select.value;
  for (const option of Array.from(select.options).slice(1)) option.remove();
  for (const [value, name] of items) select.add(new Option(name, value));
  if (Array.from(select.options).some(option => option.value === previous)) select.value = previous;
}

function distinct(rows, field) {
  return [...new Set(rows.map(row => row[field]))].map(value => [value, value]);
}

async function request(url, init) {
  const response = await fetch(url, init);
  const data = await response.json();
  if (!response.ok) throw Error([data.error, data.action].filter(Boolean).join(' '));
  return data;
}

async function decision(row, action, input, controls) {
  if (!input.value.trim()) {
    get('message').textContent = 'Add a note describing what you checked or heard.';
    input.focus();
    return;
  }
  for (const control of controls) control.disabled = true;
  try {
    const {utterance, clip_key, pcm_sha256} = row;
    review = await request('decision', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action, reason: input.value, utterance, clip_key, pcm_sha256, revision: review.revision})});
    get('message').textContent = `Saved your decision for ${utterance}.`;
    display();
  } catch (error) {
    get('message').textContent = error.message;
    for (const control of controls) control.disabled = false;
  }
}

function notes(row, actions) {
  const controls = [];
  const label = element('label', 'Review note');
  const input = element('input');
  input.maxLength = 1200;
  label.append(input);
  for (const [name, action] of actions) controls.push(button(name, () => decision(row, action, input, controls)));
  const container = element('div');
  container.append(label, ...controls);
  return container;
}

function commands(row) {
  const panel = element('details');
  panel.append(element('summary', 'Playback, WAV export and repair commands'), element('pre', row.play), element('pre', row.export),
    element('p', row.reason === 'planned_reading' ? 'Correct the planned reading from the source before generating a replacement.' : 'Retake if listening confirms a recording defect.'),
    element('pre', row.repair));
  return panel;
}

function passage(row) {
  const article = element('article');
  article.append(element('h3', `${row.utterance} · ${row.chapter}`), element('p', row.label));
  if (row.listen_for.length) article.append(element('p', 'Listen for “' + row.listen_for.join('” · “') + '”'));
  const reading = element('div');
  reading.className = 'reading';
  const fields = [['Source', row.source.map(block => block.text).join('\n\n')],
    ['Intended reading', row.intended], ['Verifier transcript' + (row.observer ? ' · ' + row.observer : ''),
      row.transcript ?? 'Unavailable for this recording.']];
  if (row.second_transcript !== null) fields.push(['Additional verifier transcript', row.second_transcript]);
  for (const [label, text] of fields) {
    const column = element('div');
    column.append(element('h4', label), element('p', text || 'No words recovered.'));
    reading.append(column);
  }
  article.append(reading, audio(row));
  const evidence = element('details');
  evidence.append(element('summary', 'Flag details and uncertainty'));
  for (const text of [...row.flags, ...row.warnings]) evidence.append(element('p', text));
  evidence.append(element('p', 'PCM SHA-256 ' + row.pcm_sha256));
  article.append(evidence, commands(row), notes(row, [['Accept this take', 'accept'],
    ['Report recording problem', 'recording'], ['Report planned reading problem', 'planned_reading']]));
  return article;
}

function renderFlags() {
  const query = get('search').value.trim().toLowerCase();
  const rows = passages().filter(row => ['reason', 'chapter', 'pattern'].every(field => !get(field).value || row[field] === get(field).value)
    && (!query || [row.utterance, row.intended, row.transcript, ...row.source.map(b => b.text)]
      .some(value => value && value.toLowerCase().includes(query))));
  const patterns = new Map();
  for (const row of rows) {
    const id = row.reason + '\n' + row.pattern;
    if (!patterns.has(id)) patterns.set(id, row);
  }
  const displayed = sampled ? [...patterns.values()] : rows;
  const total = Math.max(1, Math.ceil(displayed.length / pageSize));
  page = Math.min(page, total - 1);
  get('counts').textContent = `${rows.length} matching passages, ${patterns.size} patterns` + (sampled ? ' · one passage per pattern' : '');
  get('sample').textContent = sampled ? 'Show all matching passages' : 'Sample each current pattern';
  get('sample').disabled = !displayed.length && !sampled;
  get('passages').replaceChildren(...displayed.slice(page * pageSize, (page + 1) * pageSize).map(passage));
  get('page').textContent = `${page + 1} / ${total}`;
  get('previous').disabled = page === 0;
  get('next').disabled = page + 1 >= total;
}

function renderHistory() {
  const cards = review.history.slice(0, visibleDecisions).map(row => {
    const node = element('article');
    node.append(element('h3', `${row.utterance} · ${row.decision}` + (row.status ? ` · ${row.status}` : '')),
      element('p', row.reason), element('p', 'PCM SHA-256 ' + row.pcm_sha256), audio(row));
    if (row.current_key && (row.decision === 'Accepted' || row.status === 'accepted'))
      node.append(notes(row, [['Reopen as recording problem', 'recording'], ['Reopen as planned reading problem', 'planned_reading']]));
    return node;
  });
  get('decisions').replaceChildren(...(cards.length ? cards : [element('p', 'No decisions saved yet.')]));
  get('more').hidden = visibleDecisions >= review.history.length;
}

function display() {
  get('title').textContent = review.title;
  get('status').textContent = `${review.job} · revision ${review.revision} · ${review.count} passages awaiting review`;
  options('reason', review.groups.map(group => [group.reason, `${group.label} (${group.count})`]));
  const rows = passages();
  options('chapter', distinct(rows, 'chapter'));
  options('pattern', distinct(rows, 'pattern'));
  get('commands').replaceChildren(element('p', 'Planned file: ' + review.planned_output),
    ...Object.entries(review.commands).map(([label, command]) => {
      const section = element('div');
      section.append(element('p', label.charAt(0).toUpperCase() + label.slice(1)), element('pre', command));
      return section;
    }));
  renderFlags();
  renderHistory();
}

async function reload() {
  try {
    review = await request('data');
    display();
  } catch (error) {
    get('message').textContent = error.message;
  }
}

for (const id of filters) get(id).addEventListener('input', () => { if (review) { page = 0; renderFlags(); } });
get('sample').onclick = () => { if (review) { sampled = !sampled; page = 0; renderFlags(); } };
get('previous').onclick = () => { if (review) { page--; renderFlags(); } };
get('next').onclick = () => { if (review) { page++; renderFlags(); } };
get('more').onclick = () => { if (review) { visibleDecisions += pageSize; renderHistory(); } };
get('reload').onclick = reload;
reload();
