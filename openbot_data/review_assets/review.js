'use strict';
const $ = id => document.getElementById(id);
const state = {overview:null, episode:null, selected:null, pendingSelection:null, videos:[], observers:[], requestId:0, mediaVersion:0, playVersion:0};
const colors = ['#80dfbf','#f7b16e','#7faeff','#d9a0eb','#f1798d','#d3de83','#99c0ce','#edbdc7'];
const formatTime = n => `${Number(n || 0).toFixed(2)}s`;

function node(tag, className, content) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (content !== undefined) item.textContent = String(content);
  return item;
}
async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function showError(message) { $('errorBanner').textContent = message; $('errorBanner').hidden = false; }
function clearError() { $('errorBanner').hidden = true; }

function renderAuditScope(view) {
  const skipped = view.audit.skipped_checks;
  $('auditSummary').textContent = `Metadata audit · ${view.summary.error} reported errors · ${view.summary.warning} reported warnings`;
  $('skippedAudit').hidden = !skipped.length;
  $('skippedCount').textContent = `${skipped.length} audit rule${skipped.length === 1 ? '' : 's'} skipped`;
  $('skippedChecks').replaceChildren(...skipped.map(check =>
    node('p', '', `${check.rule_id}: ${check.reason}`)));
}

function visibleEpisodes() {
  const query = $('search').value.trim().toLowerCase();
  const issuesOnly = $('issuesOnly').checked;
  return (state.overview?.episodes || []).filter(ep => {
    const task = ep.tasks.join(' · ') || 'Unlabeled';
    return (!issuesOnly || ep.findings.error + ep.findings.warning > 0) &&
      (!query || `${ep.index} ${task}`.toLowerCase().includes(query));
  });
}
function renderList() {
  const overview = state.overview;
  if (!overview) return;
  const list = $('episodeList');
  list.replaceChildren();
  let shown = 0;
  for (const ep of visibleEpisodes()) {
    const issues = ep.findings.error + ep.findings.warning;
    const task = ep.tasks.join(' · ') || 'Unlabeled';
    const row = node('button', `episode-row${ep.index === state.selected ? ' active' : ''}`);
    row.type = 'button';
    row.append(node('span', 'episode-number', `#${ep.index}`));
    const label = node('span', 'episode-label');
    label.append(node('span', 'episode-task', task));
    label.append(node('span', 'episode-detail', `${ep.length ?? '?'} frames · ${ep.cameras} camera${ep.cameras === 1 ? '' : 's'}`));
    row.append(label);
    if (issues) row.append(node('span', 'badge', issues));
    row.onclick = () => openEpisode(ep.index);
    list.append(row);
    shown++;
  }
  $('listCount').textContent = `${shown} of ${overview.episodes.length} episodes shown`;
}
function findingCard(finding) {
  const card = node('div', `finding ${finding.severity || ''}`);
  card.append(node('div', 'finding-code', `${(finding.severity || '').toUpperCase()} · ${finding.code || 'FINDING'}`));
  card.append(node('div', 'finding-message', finding.message || 'No description'));
  if (finding.path) card.append(node('div', 'finding-path', finding.path));
  return card;
}
function renderFindings() {
  const findings = state.episode?.findings || [];
  $('findingCount').textContent = findings.length ? `${findings.length} affecting this episode` : 'No findings affecting this episode';
  $('episodeFindings').replaceChildren(...findings.map(findingCard));
}
function pausePlayback() {
  state.playVersion++;
  state.videos.forEach(video => video.pause());
  $('play').textContent = '▶';
}
function renderCameras() {
  const ep = state.episode;
  const mediaVersion = ++state.mediaVersion;
  const current = () => mediaVersion === state.mediaVersion;
  pausePlayback();
  for (const video of state.videos) {
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  state.videos = [];
  const container = $('cameras');
  container.replaceChildren();
  if (!ep.videos.length) {
    container.append(node('div', 'empty', 'No camera segment is available for this episode.'));
  } else if (!state.overview.ffmpeg_available) {
    container.append(node('div', 'empty', 'Install ffmpeg to enable browser video previews. Motion traces and audit findings remain available.'));
  } else {
    for (const segment of ep.videos) {
      const box = node('div', 'camera');
      const video = node('video');
      video.preload = 'metadata'; video.muted = true; video.playsInline = true;
      video.src = `/api/clip?index=${ep.index}&camera=${encodeURIComponent(segment.key)}`;
      const caption = node('div', 'camera-label');
      caption.append(node('span', '', segment.key));
      const trimmed = segment.to_timestamp - segment.from_timestamp > segment.preview_seconds;
      caption.append(node('span', '', `${formatTime(segment.from_timestamp)}–${formatTime(segment.to_timestamp)}${trimmed ? ` · preview first ${formatTime(segment.preview_seconds)}` : ''}`));
      box.append(video, caption); container.append(box);
      state.videos.push(video);
    }
    const master = state.videos[0];
    master.addEventListener('timeupdate', () => {
      if (!current()) return;
      const time = Math.min(master.currentTime, duration());
      $('timeline').value = String(Math.round((time / Math.max(0.01, duration())) * 1000));
      $('clock').textContent = formatTime(time);
      for (const other of state.videos.slice(1)) {
        if (other.readyState >= 1 && Math.abs(other.currentTime - time) > .12) other.currentTime = time;
      }
      if (master.currentTime >= duration()) pausePlayback();
    });
    for (const video of state.videos) {
      video.addEventListener('ended', () => { if (current()) pausePlayback(); });
      video.addEventListener('error', () => {
        if (!current()) return;
        pausePlayback();
        $('cameraStatus').textContent = 'Preview unavailable for one or more cameras';
      });
    }
  }
  $('cameraStatus').textContent = `${ep.videos.length} camera${ep.videos.length === 1 ? '' : 's'}`;
  const limit = ep.videos.length ? Math.min(...ep.videos.map(v => v.preview_seconds)) : 0;
  $('duration').textContent = formatTime(limit);
  $('clock').textContent = '0.00s'; $('timeline').value = '0'; $('play').textContent = '▶';
}
function duration() {
  return state.episode?.videos?.length ? Math.min(...state.episode.videos.map(v => v.preview_seconds)) : 0;
}
function chart(title, frames, values, totalFrames) {
  const wrapper = node('div', 'chart');
  wrapper.append(node('div', 'chart-title', title));
  const canvas = node('canvas'); wrapper.append(canvas);
  const dimensions = Math.min(8, Math.max(0, ...values.map(row => row.length)));
  wrapper.append(node('div', 'chart-legend', dimensions ? `Showing ${dimensions} of up to 16 dimensions · ${frames.length} sampled frames` : 'No numeric trace'));
  const draw = () => {
    const width = canvas.clientWidth, height = 170, ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(width * ratio)); canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = '#2e4356'; ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) { ctx.beginPath(); ctx.moveTo(0, i * height / 4); ctx.lineTo(width, i * height / 4); ctx.stroke(); }
    const numbers = values.flatMap(row => row.slice(0, dimensions));
    if (!numbers.length) return;
    let low = Math.min(...numbers), high = Math.max(...numbers);
    if (low === high) { low -= 1; high += 1; }
    for (let dim = 0; dim < dimensions; dim++) {
      ctx.strokeStyle = colors[dim]; ctx.lineWidth = 1.6; ctx.beginPath(); let started = false;
      for (let i = 0; i < frames.length; i++) {
        if (values[i]?.[dim] === undefined) continue;
        const x = (frames[i] / Math.max(1, totalFrames - 1)) * width;
        const y = 8 + (1 - (values[i][dim] - low) / (high - low)) * (height - 16);
        if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
      }
      ctx.stroke();
    }
  };
  requestAnimationFrame(draw);
  const observer = new ResizeObserver(draw);
  observer.observe(canvas);
  state.observers.push(observer);
  return wrapper;
}
function renderCharts() {
  const traces = state.episode.traces, container = $('charts');
  state.observers.forEach(observer => observer.disconnect());
  state.observers = [];
  container.replaceChildren();
  if (traces.error) {
    container.append(node('div', 'empty', traces.error));
    return;
  }
  for (const [key, values] of Object.entries(traces.series)) {
    container.append(chart(key, traces.frames, values, state.episode.length || 0));
  }
  if (!Object.keys(traces.series).length) container.append(node('div', 'empty', 'No state or action columns are available for this episode.'));
}
async function openEpisode(index) {
  const requestId = ++state.requestId;
  state.pendingSelection = index;
  clearError();
  try {
    const episode = await getJson(`/api/episode?index=${index}`);
    if (requestId !== state.requestId) return;
    state.selected = index; state.episode = episode;
    $('episodeIndex').textContent = `#${index}`;
    $('episodeTask').textContent = episode.tasks.join(' · ') || 'Unlabeled episode';
    $('episodeMeta').textContent = `${episode.length ?? '?'} frames · ${episode.videos.length} camera stream${episode.videos.length === 1 ? '' : 's'}`;
    renderList(); renderCameras(); renderFindings(); renderCharts();
  } catch (error) { if (requestId === state.requestId) showError(error.message); }
  finally { if (requestId === state.requestId) state.pendingSelection = null; }
}
function navigate(direction) {
  const list = visibleEpisodes();
  const position = list.findIndex(item => item.index === (state.pendingSelection ?? state.selected));
  const next = list[position < 0 ? (direction > 0 ? 0 : list.length - 1) : position + direction];
  if (next) openEpisode(next.index);
}
$('search').addEventListener('input', renderList);
$('issuesOnly').addEventListener('change', renderList);
$('previous').onclick = () => navigate(-1);
$('next').onclick = () => navigate(1);
$('play').onclick = async () => {
  const master = state.videos[0]; if (!master) return;
  if (master.paused) {
    const mediaVersion = state.mediaVersion, playVersion = ++state.playVersion;
    const current = () => mediaVersion === state.mediaVersion && playVersion === state.playVersion;
    const videos = [...state.videos];
    if (videos.some(video => video.ended) || master.currentTime >= duration()) {
      videos.forEach(video => { if (video.readyState >= 1) video.currentTime = 0; });
    }
    clearError();
    try {
      await Promise.all(videos.map(async video => {
        await video.play();
        if (!current()) video.pause();
      }));
      if (current() && !master.paused) $('play').textContent = 'Ⅱ';
    } catch (error) {
      if (!current()) return;
      pausePlayback();
      showError('Could not play all camera previews. Check the video findings and try again.');
    }
  } else { pausePlayback(); }
};
$('timeline').addEventListener('input', () => {
  const time = (Number($('timeline').value) / 1000) * duration();
  state.videos.forEach(video => { if (video.readyState >= 1) video.currentTime = time; });
  $('clock').textContent = formatTime(time);
});
document.addEventListener('keydown', event => {
  if (event.target instanceof HTMLElement && event.target.closest('input, button, textarea, select, summary, a, [contenteditable]')) return;
  if (event.key === 'ArrowUp') { event.preventDefault(); navigate(-1); }
  if (event.key === 'ArrowDown') { event.preventDefault(); navigate(1); }
  if (event.key === ' ') { event.preventDefault(); $('play').click(); }
});
(async () => {
  if (window.location.protocol === 'file:') {
    $('datasetName').textContent = 'Start the local reviewer';
    $('topSummary').textContent = 'Local server required';
    showError('Run openbot-data review /path/to/lerobot_dataset and open the localhost URL printed in your terminal. This HTML file cannot load a dataset by itself.');
    return;
  }
  try {
    state.overview = await getJson('/api/overview');
    const view = state.overview;
    $('datasetName').textContent = view.dataset;
    $('datasetMeta').textContent = `${view.format} · ${view.episodes.length} episodes`;
    $('topSummary').textContent = `${view.summary.error} errors · ${view.summary.warning} warnings · ${view.episodes.length} episodes`;
    renderAuditScope(view);
    $('generalCount').textContent = `(${view.general_findings.length})`;
    $('generalFindings').replaceChildren(...view.general_findings.map(findingCard));
    renderList();
    if (view.episodes.length) await openEpisode(view.episodes[0].index);
  } catch (error) { showError(error.message); }
})();
