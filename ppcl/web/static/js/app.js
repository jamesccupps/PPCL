/* Boot: load metadata and settings, wire the panes together, own the
 * keyboard.
 *
 * Keyboard shortcuts match the Desigo CC PPCL Editor wherever it has one --
 * Ctrl+G for go-to-statement, Ctrl+Z/Y, Ctrl+mouse wheel to zoom -- so an
 * engineer coming from that editor does not have to relearn anything.
 */

import {
  $, $$, ask, clear, debounce, el, esc, fillSelect, get, initMenu,
  initTooltips, post, setTooltipsEnabled, status, toast,
} from './core.js';
import { setMeta } from './highlight.js';
import { editor } from './editor.js';
import { builder } from './builder.js';
import { blocks } from './blocks.js';
import { bench } from './bench.js';
import { help } from './help.js';
import { settingsPane } from './settings.js';

const PANES = ['editor', 'builder', 'blocks', 'bench', 'settings', 'help'];

let META = null;
let SETTINGS = {};
let CURRENT_FILE = null;

/* ------------------------------------------------------------- panes */

function switchPane(name) {
  PANES.forEach((pane) => {
    $('pane-' + pane).classList.toggle('on', pane === name);
  });
  $$('#nav button').forEach((b) => {
    b.classList.toggle('on', b.dataset.pane === name);
  });
  if (name === 'blocks' && blocks.diagram) blocks.render();
}

/* ------------------------------------------------------------- files */

async function loadFiles() {
  const r = await get('/api/files');
  const node = $('files');
  clear(node);
  if (r.error) { node.appendChild(el('div', 'empty', r.error)); return; }
  if (!r.files.length) {
    node.appendChild(el('div', 'empty',
      'No .ppcl, .seq or .blocks.json files in ' + r.root + '.'));
    return;
  }
  r.files.forEach((file) => {
    const row = el('div', 'row');
    row.appendChild(document.createTextNode(file.path));
    if (file.kind === 'sequence') row.appendChild(el('span', 'k', '[seq]'));
    row.setAttribute('data-tip', file.path + '  ·  ' + file.size + ' bytes');
    row.onclick = () => openFile(file);
    node.appendChild(row);
  });
}

async function openFile(file) {
  const r = await post('/api/open', { path: file.path });
  if (r.error) { toast(r.error, 'bad'); return; }
  CURRENT_FILE = file.path;
  if (/\.blocks\.json$/i.test(file.path)) {
    try {
      blocks.load(JSON.parse(r.text));
      switchPane('blocks');
    } catch (err) {
      toast('that file is not a block diagram: ' + err.message, 'bad');
    }
    return;
  }
  if (file.kind === 'sequence') {
    await builder.fromText(r.text);
    builder.section = 'table';
    builder.renderSection();
    switchPane('builder');
    return;
  }
  editor.set(r.text, file.path);
  switchPane('editor');
}

async function saveActive() {
  const suggestion = CURRENT_FILE ||
    (editor.tab ? editor.tab.path : 'programs/new.ppcl');
  const answer = await ask('Save program', [{
    name: 'path', label: 'Path', type: 'text', value: suggestion,
    doc: 'Relative to the workspace directory. A .bak of the previous ' +
         'contents is kept unless that is switched off in Settings.',
  }], 'Save');
  if (!answer || !answer.path) return;
  const r = await post('/api/save', {
    path: answer.path, text: editor.get(),
    backup: SETTINGS.backup_on_save !== false,
  });
  if (r.error) { toast(r.error, 'bad'); return; }
  CURRENT_FILE = answer.path;
  editor.markSaved(answer.path);
  loadFiles();
  toast('saved ' + answer.path + ' (' + r.bytes + ' bytes)', 'good');
}

/* ------------------------------------------------------------ points */

async function searchPoints(query) {
  const r = await post('/api/points/search', { query, limit: 60 });
  const node = $('pointlist');
  clear(node);
  $('pointcount').textContent = r.loaded ? r.count + ' points' : 'none';
  $('pointcount').setAttribute('data-tip', r.loaded
    ? 'Loaded from ' + r.source + '. Unresolved references are marked U.'
    : 'No point database loaded. Import one in Settings to turn on ' +
      'unresolved-point marking and type-aware rules.');
  if (!r.loaded) {
    node.appendChild(el('div', 'empty',
      'No point database. Import one in Settings.'));
    return;
  }
  if (!r.points.length) {
    node.appendChild(el('div', 'empty', 'No match.'));
    return;
  }
  r.points.forEach((point) => {
    const row = el('div', 'row');
    row.appendChild(el('span', 'nm', point.name));
    if (point.ptype) row.appendChild(el('span', 'ty', point.ptype));
    row.setAttribute('data-tip',
      point.description || point.name +
      (point.units ? '  (' + point.units + ')' : ''));
    row.onclick = () => { editor.insert(point.name); };
    node.appendChild(row);
  });
}

/* -------------------------------------------------------- reference */

async function explain(topic) {
  const node = $('help');
  if (!topic) { clear(node); return; }
  const r = await post('/api/explain', { topic });
  clear(node);
  if (r.error) { node.appendChild(el('div', 'empty', r.error)); return; }

  node.appendChild(el('h4', '', r.name));
  if (r.signature) node.appendChild(el('div', 'sig', r.signature));
  node.appendChild(el('div', '', r.summary));

  const params = (r.params || []).concat(r.repeat || [], r.trailing || []);
  if (params.length) {
    const list = el('dl');
    params.forEach((p) => {
      list.appendChild(el('dt', '', p.name + '  ' + p.kind));
      list.appendChild(el('dd', '', p.doc +
        (p.choices && p.choices.length ? '  (' + p.choices.join(', ') + ')' : '')));
    });
    node.appendChild(list);
  }
  const flags = [];
  if (r.time_based) flags.push('Time-based: must run on every pass.');
  if (r.subroutine_safe === false) flags.push('NOT allowed inside a GOSUB.');
  if (r.if_target_safe === false) flags.push('NOT allowed as an IF action.');
  if (r.signature_known === false) flags.push('Signature not published.');
  flags.forEach((f) => node.appendChild(el('div', 'flag', f)));

  if (r.order) {
    const list = el('ul');
    r.order.forEach((o) => list.appendChild(el('li', '', o.name + ' — ' + o.doc)));
    node.appendChild(list);
  }
  if (r.notes && r.notes.length) {
    const list = el('ul');
    r.notes.forEach((n) => list.appendChild(el('li', '', n)));
    node.appendChild(list);
  }
  if (r.detail) node.appendChild(el('div', '', r.detail));
  if (r.see_also && r.see_also.length) {
    node.appendChild(el('div', 'sig', 'See also: ' + r.see_also.join(', ')));
  }
}

/* ---------------------------------------------------------- keyboard */

function initKeyboard() {
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
    const inEditor = e.target === $('code');

    if (e.key === 'F1') {
      e.preventDefault();
      switchPane('help');
      return;
    }
    if (e.key === 'F5') {
      e.preventDefault();
      toBench(editor.get());
      return;
    }
    if (e.key === 'F8' && !typing) { e.preventDefault(); bench.stepDebug('run'); return; }
    if (e.key === 'F10' && !typing) { e.preventDefault(); bench.stepDebug('over'); return; }
    if (e.key === 'F11' && !typing) {
      e.preventDefault();
      bench.stepDebug(e.shiftKey ? 'out' : 'into');
      return;
    }
    if (e.key === 'F9' && inEditor) {
      e.preventDefault();
      const line = editor.caretLine();
      editor.toggleBreakpointAt(line);
      const numbers = editor.get().split('\n')[line - 1].match(/^\s*(\d+)[ \t]/);
      if (numbers) bench.toggleLine(parseInt(numbers[1], 10));
      return;
    }
    if (e.key === 'Delete' && !typing && $('pane-blocks').classList.contains('on')) {
      e.preventDefault();
      blocks.removeSelected();
      return;
    }

    if (!(e.ctrlKey || e.metaKey)) return;

    const digit = Number(e.key);
    if (digit >= 1 && digit <= PANES.length) {
      e.preventDefault();
      switchPane(PANES[digit - 1]);
      return;
    }
    switch (e.key.toLowerCase()) {
      case 's':
        e.preventDefault();
        saveActive();
        break;
      case 'f':
        e.preventDefault();
        switchPane('editor');
        editor.openFind();
        break;
      case 'g':
        e.preventDefault();
        switchPane('editor');
        editor.gotoDialog();
        break;
      case ' ':
        if (inEditor) { e.preventDefault(); editor.assist(); }
        break;
      case '/':
        if (inEditor) { e.preventDefault(); editor.transform('toggle_comment'); }
        break;
      default:
        break;
    }
  });

  /* Ctrl and the wheel zooms the code, the way the Desigo editor does. */
  document.addEventListener('wheel', (e) => {
    if (!e.ctrlKey) return;
    const inCode = e.target.closest('.editwrap');
    if (!inCode) return;
    e.preventDefault();
    const current = parseInt(
      getComputedStyle(document.documentElement)
        .getPropertyValue('--code-size'), 10) || 13;
    const next = Math.max(9, Math.min(28, current + (e.deltaY < 0 ? 1 : -1)));
    document.documentElement.style.setProperty('--code-size', next + 'px');
    editor.paint();
  }, { passive: false });
}

/* --------------------------------------------------------- plumbing */

function toBench(text) {
  if (!text || !text.trim()) { toast('nothing to run', 'bad'); return; }
  bench.setSource(text);
  bench.session = null;
  switchPane('bench');
  if ($('bench-run').hidden) bench.startDebug();
  else bench.run();
}

function applySettings(values) {
  SETTINGS = values || {};
  document.documentElement.setAttribute('data-theme', SETTINGS.theme || 'dark');
  document.documentElement.style.setProperty(
    '--code-size', (SETTINGS.font_size || 13) + 'px');
  setTooltipsEnabled(SETTINGS.show_tooltips !== false);
  editor.settings = SETTINGS;
  bench.settings = SETTINGS;
  if (SETTINGS.firmware) $('firmware').value = SETTINGS.firmware;
  if (SETTINGS.line_start) $('rn-start').value = SETTINGS.line_start;
  if (SETTINGS.line_step) $('rn-step').value = SETTINGS.line_step;
  if (SETTINGS.bench_hours) $('b-hours').value = SETTINGS.bench_hours;
  if (SETTINGS.bench_start_hour !== undefined) {
    $('b-hour').value = SETTINGS.bench_start_hour;
  }
  if (SETTINGS.bench_weather) $('b-weather').value = SETTINGS.bench_weather;
  if (SETTINGS.bench_preset) $('b-preset').value = SETTINGS.bench_preset;
}

/* -------------------------------------------------------------- boot */

async function boot() {
  initTooltips();
  initKeyboard();
  initMenu($('menu-transform'));

  META = await get('/api/meta');
  if (META.error) {
    status('cannot reach the workbench server', 'e');
    return;
  }
  setMeta(META);

  fillSelect($('firmware'), META.firmwares, 'apogee');
  fillSelect($('b-weather'), META.weather, 'design_winter');
  fillSelect($('b-preset'), META.plant_presets, 'single_zone_ahu');
  const faults = $('b-fault');
  META.faults.forEach((kind) => {
    const option = el('option', '', kind);
    option.value = kind;
    faults.appendChild(option);
  });

  const values = await settingsPane.init();
  settingsPane.onChange = (key, value, all) => {
    applySettings(all);
    if (key === 'point_database' || key === null) {
      searchPoints($('pointq').value);
      editor.lint();
    }
    if (key === 'firmware' || key === null) editor.lint();
  };

  editor.init({ meta: META, settings: values });
  builder.init();
  bench.init({ settings: values });
  await blocks.init();
  await help.init();
  applySettings(values);

  /* Panes hand their generated program to each other through the bench. */
  builder.onCompiled = (text) => bench.setSource(text);
  blocks.onCompiled = (text) => bench.setSource(text);

  $$('#nav button').forEach((button) => {
    button.onclick = () => switchPane(button.dataset.pane);
  });

  $('btn-new').onclick = () => editor.newTab('untitled.ppcl', '');
  $('btn-save').onclick = saveActive;
  $('btn-refresh-files').onclick = loadFiles;

  $('btn-fmt').onclick = async () => {
    const r = await post('/api/format', { text: editor.get(), sort: true });
    if (r.text) { editor.set(r.text); toast('formatted', 'good'); }
  };

  $('btn-renumber').onclick = async () => {
    const r = await post('/api/renumber', {
      text: editor.get(),
      start: Number($('rn-start').value),
      step: Number($('rn-step').value),
      preserve_blocks: $('rn-blocks').checked,
    });
    if (!r.ok) {
      const answer = await ask('Renumbering refused', [
        { name: 'how', label: 'How should duplicates be handled?',
          type: 'select', value: 'cancel',
          choices: [
            { value: 'cancel', label: 'Cancel — fix the duplicates by hand' },
            { value: 'split', label: 'Keep every line, shifting duplicates apart' },
            { value: 'drop', label: 'Match the panel, dropping shadowed lines' },
          ],
          doc: (r.warnings || []).join('  ') +
               '  Keeping every line changes what the panel runs. Matching ' +
               'the panel loses code. Only you know which is right.' },
      ], 'Renumber');
      if (!answer || answer.how === 'cancel') return;
      const retry = await post('/api/renumber', {
        text: editor.get(),
        start: Number($('rn-start').value),
        step: Number($('rn-step').value),
        preserve_blocks: $('rn-blocks').checked,
        split_duplicates: answer.how === 'split',
        allow_duplicates: answer.how === 'drop',
      });
      if (!retry.ok) { toast((retry.warnings || []).join(' '), 'bad'); return; }
      editor.set(retry.text);
      toast('renumbered, ' + retry.rewritten + ' reference(s) rewritten', 'good');
      return;
    }
    editor.set(r.text);
    toast('renumbered, ' + r.rewritten + ' reference(s) rewritten', 'good');
  };

  $('btn-tobench').onclick = () => toBench(editor.get());
  $('btn-seq-tobench').onclick = () => builder.compile((text) => toBench(text));
  $('btn-dia-tobench').onclick = () => blocks.compile((text) => toBench(text));
  $('btn-seq-toeditor').onclick = () => builder.compile((text) => {
    editor.set(text, (builder.doc && builder.doc.name ? builder.doc.name : 'sequence') + '.ppcl');
    switchPane('editor');
  });
  $('btn-dia-toeditor').onclick = () => blocks.compile((text) => {
    editor.set(text, (blocks.diagram.name || 'diagram') + '.ppcl');
    switchPane('editor');
  });

  $('btn-dia-save').onclick = async () => {
    const answer = await ask('Save block diagram', [{
      name: 'path', label: 'Path', type: 'text',
      value: 'diagrams/' + slug(blocks.diagram.name) + '.blocks.json',
      doc: 'Saved as JSON. Reopen it from the workspace list.',
    }], 'Save');
    if (!answer || !answer.path) return;
    const r = await post('/api/save', {
      path: answer.path, text: JSON.stringify(blocks.diagram, null, 2),
    });
    if (r.error) { toast(r.error, 'bad'); return; }
    loadFiles();
    toast('saved ' + answer.path, 'good');
  };

  $('btn-dia-open').onclick = () => {
    switchPane('editor');
    toast('pick a .blocks.json file from the workspace list on the left');
  };

  $('firmware').onchange = () => {
    editor.lint();
    builder.compile();
    blocks.compile();
  };

  $('explain-q').addEventListener('input',
    debounce((e) => explain(e.target.value.trim()), 200));
  $('pointq').addEventListener('input',
    debounce((e) => searchPoints(e.target.value), 200));

  $$('#menu-transform .items button').forEach((button) => {
    button.onclick = () => {
      $('menu-transform').classList.remove('open');
      editor.transform(button.dataset.op);
    };
  });

  initSplitter();
  loadFiles();
  searchPoints('');
  editor.paint();
}

function initSplitter() {
  const bar = $('splitbar');
  const panel = $('diags');
  let dragging = false;
  bar.addEventListener('mousedown', () => { dragging = true; });
  document.addEventListener('mouseup', () => { dragging = false; });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const height = Math.max(60, Math.min(
      window.innerHeight - 200, window.innerHeight - e.clientY - 10));
    panel.style.height = height + 'px';
  });
}

function slug(text) {
  return String(text || 'diagram').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'diagram';
}

document.addEventListener('DOMContentLoaded', boot);
