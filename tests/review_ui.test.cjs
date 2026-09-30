const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.events = {};
    this.value = ''; this.checked = false; this.textContent = ''; this.hidden = false;
    this.paused = true; this.readyState = 1; this.currentTime = 0; this.duration = 5;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { (this.events[name] ??= []).push(callback); }
  emit(name) { for (const callback of this.events[name] || []) callback(); }
  closest(selector) {
    const tags = selector.split(',').map(tag => tag.trim().toUpperCase());
    return tags.includes(this.tagName) ? this : this.parentElement?.closest(selector);
  }
  click() { return this.onclick?.(); }
  removeAttribute() {}
  load() {}
  pause() { this.paused = true; }
  play() {
    if (this.failPlayback) return Promise.reject(new Error('preview decode failed'));
    this.paused = false; return Promise.resolve();
  }
}

function app(protocol = 'http:') {
  const elements = new Map();
  const documentEvents = {};
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById: get, createElement: tag => new Element(tag), addEventListener(name, callback) { documentEvents[name] = callback; }},
    window: {location: {protocol}, devicePixelRatio: 1},
    location: {protocol}, HTMLInputElement: class {}, HTMLElement: Element,
    requestAnimationFrame() {}, ResizeObserver: class {observe() {} disconnect() {}},
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../openbot_data/review_assets/review.js'), 'utf8'), context);
  vm.runInContext(`state.overview = {ffmpeg_available:true, episodes:[]};
    state.episode = {index:0, videos:[
      {key:'left',from_timestamp:0,to_timestamp:5,preview_seconds:5},
      {key:'right',from_timestamp:0,to_timestamp:5,preview_seconds:5}
    ]}; renderCameras();`, context);
  return {get, run: code => vm.runInContext(code, context), keydown: event => documentEvents.keydown(event)};
}

test('failed camera playback stops the group and reports the failure', async () => {
  const ui = app();
  ui.run('state.videos[1].failPlayback = true');
  await ui.get('play').onclick();
  assert.equal(ui.run('state.videos.every(video => video.paused)'), true);
  assert.equal(ui.get('play').textContent, '▶');
  assert.equal(ui.get('errorBanner').hidden, false);
});

test('events from a previous episode cannot seek or relabel the current episode', () => {
  const ui = app();
  ui.run('var previousVideo = state.videos[0]; renderCameras(); previousVideo.currentTime = 4; previousVideo.emit("timeupdate"); previousVideo.emit("error");');
  assert.equal(ui.run('state.videos[1].currentTime'), 0);
  assert.equal(ui.get('clock').textContent, '0.00s');
  assert.equal(ui.get('cameraStatus').textContent, '2 cameras');
});

test('the shortest preview ending pauses every camera', async () => {
  const ui = app();
  await ui.get('play').onclick();
  ui.run('state.videos[1].emit("ended")');
  assert.equal(ui.run('state.videos.every(video => video.paused)'), true);
  assert.equal(ui.get('play').textContent, '▶');
});

test('next episode respects the findings filter', () => {
  const ui = app();
  ui.get('issuesOnly').checked = true;
  ui.run(`state.overview.episodes = [
    {index:0,tasks:['pick'],findings:{error:1,warning:0}},
    {index:1,tasks:['pick'],findings:{error:0,warning:0}},
    {index:2,tasks:['pick'],findings:{error:1,warning:0}}
  ]; state.selected = 0; var opened = null; openEpisode = index => { opened = index; }; navigate(1);`);
  assert.equal(ui.run('opened'), 2);
});

test('opening the HTML file directly explains how to start the server', () => {
  const ui = app('file:');
  assert.equal(ui.get('errorBanner').hidden, false);
  assert.match(ui.get('errorBanner').textContent, /openbot-data review/);
});

test('zero findings still shows skipped audit rules and their reasons', () => {
  const ui = app();
  ui.run(`renderAuditScope({summary:{error:0,warning:0}, audit:{integrity:'metadata',
    skipped_checks:[{rule_id:'video.preview.decode.failed',reason:'Decode requires higher integrity.'}]}})`);
  assert.match(ui.get('auditSummary').textContent, /Metadata audit.*0 reported errors/);
  assert.equal(ui.get('skippedAudit').hidden, false);
  assert.match(ui.get('skippedChecks').children[0].textContent, /Decode requires higher integrity/);
});

test('keyboard shortcuts preserve activation of focused controls and their children', () => {
  const ui = app();
  const button = new Element('button'), child = new Element('span');
  child.parentElement = button;
  for (const target of [button, child, new Element('summary'), new Element('input')]) {
    let prevented = false;
    ui.keydown({target, key:' ', preventDefault() { prevented = true; }});
    assert.equal(prevented, false);
    assert.equal(ui.run('state.videos.every(video => video.paused)'), true);
  }
  let prevented = false;
  ui.keydown({target:new Element('body'), key:' ', preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(ui.run('state.videos.every(video => !video.paused)'), true);
});

test('rapid navigation advances from the pending selection and ignores stale responses', async () => {
  const ui = app();
  ui.run(`state.overview.episodes = [0,1,2].map(index => ({index,tasks:[],findings:{error:0,warning:0}}));
    state.selected = 0;
    var pending = [];
    getJson = url => new Promise((resolve, reject) => pending.push({url,resolve,reject}));
    renderList = renderCameras = renderFindings = renderCharts = () => {};
    navigate(1); navigate(1);`);
  assert.deepEqual(Array.from(ui.run('pending.map(item => item.url)')), ['/api/episode?index=1', '/api/episode?index=2']);
  ui.run('pending[1].resolve({index:2,tasks:[],videos:[],length:1})');
  await Promise.resolve(); await Promise.resolve();
  ui.run('pending[0].resolve({index:1,tasks:[],videos:[],length:1})');
  await Promise.resolve(); await Promise.resolve();
  assert.equal(ui.get('episodeIndex').textContent, '#2');
});

test('failed navigation recovers from the displayed episode', async () => {
  const ui = app();
  ui.run(`state.overview.episodes = [0,1,2].map(index => ({index,tasks:[],findings:{error:0,warning:0}}));
    state.selected = 0; var pending = [];
    getJson = url => new Promise((resolve, reject) => pending.push({url,resolve,reject}));
    navigate(1); pending[0].reject(new Error('unavailable'));`);
  await Promise.resolve(); await Promise.resolve();
  ui.run('navigate(1)');
  assert.equal(ui.run('pending[1].url'), '/api/episode?index=1');
});
