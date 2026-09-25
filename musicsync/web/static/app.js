// What the pages do: start exports, imports and transfers, and show how they are getting on.
'use strict';

const $ = (id) => document.getElementById(id);

// Asking for JSON makes the server answer errors as JSON too, instead of as an error page.
const api = (url, init = {}) => fetch(url, { ...init, headers: { Accept: 'application/json', ...init.headers } });

async function json(resp) {
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.message);
  return data;
}

const PHASES = { read: 'Read', match: 'Match', check: 'Compare', add: 'Add' };
const pageTitle = document.title;
const count = (n) => n.toLocaleString();
const percent = (done, total) => Math.floor((100 * done) / Math.max(total, 1));

// 72 seconds as "1:12".
function clock(seconds) {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// Time left, rounded the way people say it.
const roughly = (seconds) => (seconds < 60 ? `${Math.max(10, Math.round(seconds / 10) * 10)} s` : `${Math.round(seconds / 60)} min`);

// A job runs one at a time: its buttons are off until it ends.
function busy(on) {
  for (const button of document.querySelectorAll('[data-job-button]')) button.disabled = on;
}

// A plain message in the job panel, for a job that is starting or could not start.
function show(text, { error = false } = {}) {
  $('job').hidden = false;
  $('job-title').textContent = error ? 'Something went wrong' : 'Starting';
  for (const id of ['job-clock', 'job-numbers']) $(id).textContent = '';
  for (const id of ['job-steps', 'job-current', 'job-unmatched', 'job-download']) $(id).hidden = true;
  $('job-bar').hidden = error;
  $('job-bar').removeAttribute('value'); // no value: a bar that moves without saying how far
  $('job-text').textContent = text;
  $('job-text').classList.toggle('error', error);
}

// One track that was not found, why, and what came closest. textContent, never innerHTML: track titles
// come from other people's playlists.
function miss({ track, reason, closest }) {
  const item = document.createElement('li');
  const why = document.createElement('span');
  why.className = 'muted';
  why.textContent = ` — ${reason}${closest ? `; closest: ${closest}` : ''}`;
  item.append(track, why);
  return item;
}

function render(id, job) {
  const running = job.status === 'running';
  $('job').hidden = false;
  $('job-title').textContent = job.title;
  $('job-clock').textContent = clock(job.elapsed);

  // The row of steps: the ones before the current step are done.
  const at = running ? Math.max(job.phases.indexOf(job.phase), 0) : job.phases.indexOf(job.phase);
  const failed = job.status === 'error';
  $('job-steps').hidden = job.phases.length < 2;
  $('job-steps').replaceChildren(
    ...job.phases.map((phase, i) => {
      const step = document.createElement('li');
      step.textContent = PHASES[phase] ?? phase;
      if (job.status === 'done' || i < at) step.className = 'done';
      else if (i === at) step.className = failed ? 'failed' : 'current';
      return step;
    }),
  );

  $('job-text').textContent = running ? job.text : job.message;
  $('job-text').classList.toggle('error', failed);
  const bar = $('job-bar');
  bar.hidden = !running;
  if (job.total === null) {
    bar.removeAttribute('value');
  } else {
    bar.max = Math.max(job.total, 1);
    bar.value = job.total ? job.done : 1;
  }

  const facts = [];
  if (running && job.total === null) facts.push(`${count(job.done)} so far`);
  if (running && job.total !== null) facts.push(`${count(job.done)} of ${count(job.total)}`, `${percent(job.done, job.total)}%`);
  if (running && job.eta !== null) facts.push(`about ${roughly(job.eta)} left`);
  if (job.found || job.not_found) facts.push(`${count(job.found)} found`, `${count(job.not_found)} not found`);
  if (!running) facts.push(`took ${clock(job.elapsed)}`);
  $('job-numbers').textContent = facts.join(' · ');
  $('job-current').hidden = !(running && job.phase === 'match');
  $('job-current').textContent = job.current;

  $('job-unmatched').hidden = !job.unmatched_count;
  $('job-unmatched-title').textContent = `${running ? 'Not found so far' : 'Not found'} (${count(job.unmatched_count)})`;
  $('job-unmatched-list').replaceChildren(...job.unmatched.map(miss));
  const more = job.unmatched_count - job.unmatched.length;
  $('job-unmatched-more').textContent = more > 0 ? `… and ${count(more)} more in the CSV` : '';
  $('job-unmatched-link').href = `/jobs/${id}/unmatched.csv`;
  $('job-unmatched-link').hidden = running; // the list is complete only at the end

  $('job-download').hidden = !job.download;
  $('job-download-link').href = `/jobs/${id}/download`;

  // The browser tab says how far it is, so it can be followed from another tab.
  const phase = PHASES[job.phase] ?? 'Starting';
  const progress = job.total ? `${phase} ${percent(job.done, job.total)}%` : `${phase}…`;
  document.title = `${running ? progress : failed ? 'Failed' : 'Done'} · ${pageTitle}`;
}

// Poll a background job until it ends. Returns what it ended as.
async function follow(id) {
  for (;;) {
    const job = await json(await api(`/jobs/${id}`));
    render(id, job);
    if (job.status !== 'running') return job;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

// Start a background job (an export, an import or a transfer) and follow it to the end.
async function start(url, init) {
  busy(true);
  show('Starting…');
  $('job').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  try {
    const { id } = await json(await api(url, { method: 'POST', ...init }));
    const job = await follow(id);
    if (job.download) window.location.href = `/jobs/${id}/download`; // saves the file; the page stays
  } catch (err) {
    show(err.message, { error: true });
    document.title = pageTitle;
  } finally {
    busy(false);
  }
}

const postJson = (body) => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const exportFrom = (service, playlist) => start(`/${service}/export`, postJson({ playlist: playlist || null }));

// Fill a playlist picker with the readable playlists of a service. Empty ones cannot be picked.
async function fillPlaylists(service, select) {
  select.replaceChildren(new Option('Liked songs', ''));
  if (!service) return;
  try {
    for (const p of await json(await api(`/${service}/playlists`))) {
      if (!p.readable) continue;
      const empty = p.track_count === 0;
      const option = new Option(`${p.name} (${empty ? 'empty' : (p.track_count ?? '?')})`, p.id);
      option.disabled = empty;
      select.add(option);
    }
  } catch (err) {
    show(err.message, { error: true });
  }
}

// The dashboard.
$('export-service')?.addEventListener('change', (e) => fillPlaylists(e.target.value, $('export-playlist')));
$('transfer-source')?.addEventListener('change', (e) => fillPlaylists(e.target.value, $('transfer-playlist')));

$('export-form')?.addEventListener('submit', (e) => {
  e.preventDefault();
  exportFrom($('export-service').value, $('export-playlist').value);
});

$('import-form')?.addEventListener('submit', (e) => {
  e.preventDefault();
  const form = new FormData();
  form.append('file', $('import-file').files[0]);
  form.append('playlist', $('import-playlist').value);
  start(`/${$('import-service').value}/import`, { body: form });
});

$('transfer-form')?.addEventListener('submit', (e) => {
  e.preventDefault();
  start(
    '/transfer',
    postJson({
      source: $('transfer-source').value,
      target: $('transfer-target').value,
      playlist: $('transfer-playlist').value || null,
      name: $('transfer-name').value,
    }),
  );
});

// The account page: an export button for the liked songs and one per playlist.
for (const button of document.querySelectorAll('[data-export]')) {
  button.addEventListener('click', () => exportFrom(button.dataset.service, button.dataset.export));
}
