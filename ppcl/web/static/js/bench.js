/* The Bench pane: simulate, and debug.
 *
 * Simulate runs the program against modelled equipment and charts the result.
 * Debug drives the same interpreter one statement at a time, with the three
 * kinds of breakpoint that match how these programs actually misbehave: a
 * line, a write to a point, and a condition in the panel's own expression
 * language.
 *
 * The one thing you can do here that you cannot do on a panel is override a
 * point mid-run and carry on. That is the reason to test here first.
 */

import { $, ask, clear, el, esc, fmt, post, toast } from './core.js';
import { renderProgram } from './highlight.js';

const CHART_LABELS = {
  'AHU1.actual_zone': 'Zone temperature',
  'AHU1.actual_dat': 'Discharge air',
  'AHU1.actual_mat': 'Mixed air',
  'AHU1.coil_face': 'Coil face',
  'AHU1.hw_position': 'Heating valve %',
  'AHU1.cw_position': 'Cooling valve %',
  'AHU1.oa_position': 'Outside air damper %',
  OUTSIDE: 'Outside air',
};

export const bench = {
  source: '',
  session: null,
  breakpoints: [],
  settings: {},

  init(context) {
    this.settings = context.settings || {};
    $('btn-run').onclick = () => this.run();
    $('bench-mode').addEventListener('click', (e) => {
      const button = e.target.closest('button');
      if (!button) return;
      Array.from($('bench-mode').children).forEach(
        (b) => b.classList.toggle('on', b === button));
      const debugging = button.dataset.mode === 'debug';
      $('bench-run').hidden = debugging;
      $('bench-debug').hidden = !debugging;
    });

    $('dbg-start').onclick = () => this.startDebug();
    $('dbg-run').onclick = () => this.stepDebug('run');
    $('dbg-step').onclick = () => this.stepDebug('into');
    $('dbg-over').onclick = () => this.stepDebug('over');
    $('dbg-out').onclick = () => this.stepDebug('out');
    $('dbg-reset').onclick = () => this.resetDebug();
    $('dbg-add-watch').onclick = () => this.addWatch();
    $('dbg-add-bp').onclick = () => this.addBreakpoint();
  },

  setSource(text) { this.source = text; },

  /* ------------------------------------------------------- simulate */

  async run() {
    if (!this.source.trim()) {
      clear($('b-notes'));
      $('b-notes').appendChild(el('div', 'note',
        'Nothing to run. Open a program in the Editor, or compile one in the ' +
        'Builder or Blocks pane, and press "Bench".'));
      return;
    }
    const faults = [];
    if ($('b-fault').value) {
      faults.push({
        system: 'AHU1',
        kind: $('b-fault').value,
        at: Number($('b-fault-at').value) * 60,
        value: Number($('b-fault-val').value),
      });
    }
    clear($('charts'));
    $('charts').appendChild(el('div', 'empty', 'Running…'));
    clear($('b-notes'));
    clear($('checks'));
    $('bench-status').textContent = 'running';

    const r = await post('/api/bench', {
      text: this.source,
      weather: $('b-weather').value,
      preset: $('b-preset').value,
      start_hour: Number($('b-hour').value),
      seconds: Number($('b-hours').value) * 3600,
      dt: Number(this.settings.bench_dt || 10),
      faults,
    });

    if (r.error || !r.ok) {
      clear($('charts'));
      $('bench-status').innerHTML = '<span class="e">failed</span>';
      $('b-notes').appendChild(el('div', 'note err',
        r.error || 'the run failed'));
      (r.errors || []).forEach((e) => {
        $('b-notes').appendChild(el('div', 'note err',
          'line ' + e.line + ': ' + e.message));
      });
      return;
    }
    this.renderCharts(r);
    this.renderChecks(r);
    const failed = (r.checks || []).filter((c) => !c.passed).length;
    $('bench-status').innerHTML = failed
      ? '<span class="e">' + failed + ' check(s) failed</span>'
      : '<span class="ok">all checks passed</span>';
  },

  renderCharts(result) {
    const node = $('charts');
    clear(node);
    const keys = Object.keys(result.series);
    if (!keys.length) {
      node.appendChild(el('div', 'empty', 'The run produced no data.'));
      return;
    }
    keys.forEach((key) => node.appendChild(chart(key, result.series[key])));
  },

  renderChecks(result) {
    const notes = $('b-notes');
    (result.faults || []).forEach(
      (f) => notes.appendChild(el('div', 'note', 'Fault injected: ' + f)));
    (result.blocked || []).forEach(
      (b) => notes.appendChild(el('div', 'note',
        'Blocked by point priority: ' + b)));
    (result.warnings || []).forEach(
      (w) => notes.appendChild(el('div', 'note', w)));

    const node = $('checks');
    clear(node);
    (result.checks || []).forEach((check) => {
      const row = el('div', 'check ' + (check.passed ? 'pass' : 'fail'));
      row.appendChild(el('span', 'tag', check.passed ? 'PASS' : 'FAIL'));
      const body = el('div', 'body');
      body.appendChild(document.createTextNode(check.name));
      body.appendChild(el('small', '', check.detail));
      row.appendChild(body);
      node.appendChild(row);
    });
  },

  /* ---------------------------------------------------------- debug */

  async startDebug() {
    if (!this.source.trim()) { toast('nothing to debug', 'bad'); return; }
    const r = await post('/api/debug/start', {
      text: this.source,
      firmware: $('firmware').value,
      start_hour: Number($('b-hour').value),
    });
    if (r.error || !r.ok) {
      toast(r.error || 'could not start', 'bad');
      return;
    }
    this.session = r.session;
    renderProgram($('dbghl'), $('dbggutter'), this.source, {},
                  (line) => this.toggleLine(line));
    for (const line of this.breakpoints) {
      await post('/api/debug/breakpoint', {
        session: this.session, kind: 'line', line,
      });
    }
    const state = await this.refresh();
    toast('debug session started', 'good');
    return state;
  },

  async refresh() {
    if (!this.session) return null;
    const r = await post('/api/debug/state', { session: this.session });
    if (r.error) { toast(r.error, 'bad'); this.session = null; return null; }
    this.renderState(r.state);
    return r.state;
  },

  async stepDebug(mode) {
    if (!this.session) { await this.startDebug(); if (!this.session) return; }
    const r = await post('/api/debug/step', { session: this.session, mode });
    if (r.error) { toast(r.error, 'bad'); return; }
    this.renderState(r.state);
    const stop = r.stop || {};
    $('dbg-status').innerHTML = stop.reason === 'breakpoint'
      ? '<span class="w">stopped: ' + esc(stop.detail) + '</span>'
      : esc(stop.detail || '');
  },

  async resetDebug() {
    if (!this.session) return;
    const r = await post('/api/debug/reset', { session: this.session });
    if (!r.error) this.renderState(r.state);
  },

  async toggleLine(line) {
    const at = this.breakpoints.indexOf(line);
    if (at >= 0) this.breakpoints.splice(at, 1);
    else this.breakpoints.push(line);
    if (!this.session) { this.paintGutter(); return; }
    const state = await this.refresh();
    const existing = (state.breakpoints || [])
      .findIndex((b) => b.kind === 'line' && b.line === line);
    if (existing >= 0) {
      await post('/api/debug/breakpoint', {
        session: this.session, action: 'remove', index: existing,
      });
    } else {
      await post('/api/debug/breakpoint', {
        session: this.session, action: 'add', kind: 'line', line,
      });
    }
    this.refresh();
  },

  async addWatch() {
    const answer = await ask('Watch a point', [{
      name: 'name', label: 'Point', type: 'text',
      doc: 'Any point the program touches, including a local written as ' +
           '$NAME. The value and its current priority are both shown.',
    }], 'Watch');
    if (!answer || !answer.name.trim()) return;
    if (!this.session) { await this.startDebug(); }
    const r = await post('/api/debug/point', {
      session: this.session, action: 'watch', name: answer.name.trim(),
    });
    if (!r.error) this.renderState(r.state);
  },

  async addBreakpoint() {
    const answer = await ask('Add a breakpoint', [
      { name: 'kind', label: 'Stop on', type: 'select', value: 'write',
        choices: [
          { value: 'write', label: 'a write to a point' },
          { value: 'condition', label: 'a condition becoming true' },
          { value: 'line', label: 'a line number' },
        ],
        doc: 'A write breakpoint answers "something is commanding this and I ' +
             'cannot find what" — it stops on the statement that did it.' },
      { name: 'point', label: 'Point (for a write)', type: 'text' },
      { name: 'expression', label: 'Condition (PPCL expression)', type: 'text',
        placeholder: '"$FLAG".EQ.1.0',
        doc: 'Evaluated against live point values using the panel\'s own ' +
             'expression grammar.' },
      { name: 'line', label: 'Line (for a line breakpoint)', type: 'number' },
      { name: 'skip', label: 'Skip this many hits first', type: 'number',
        value: 0,
        doc: 'What makes a breakpoint usable inside a main loop.' },
    ], 'Add');
    if (!answer) return;
    if (!this.session) { await this.startDebug(); if (!this.session) return; }
    const r = await post('/api/debug/breakpoint', {
      session: this.session, action: 'add', kind: answer.kind,
      point: answer.point, expression: answer.expression,
      line: answer.line ? Number(answer.line) : null,
      skip: Number(answer.skip || 0),
    });
    if (r.error || !r.ok) { toast(r.error || 'could not add it', 'bad'); return; }
    this.renderState(r.state);
  },

  /* --------------------------------------------------------- render */

  renderState(state) {
    if (!state) return;
    this.state = state;
    $('dbg-clock').textContent = state.clock.clock_text;
    $('dbg-status').textContent = state.executed + ' statements';
    this.paintGutter(state);

    const watch = $('dbg-watch');
    clear(watch);
    if (!state.watch.length) {
      watch.appendChild(el('div', 'empty', 'Nothing watched yet.'));
    }
    state.watch.forEach((point) => {
      const row = el('div', 'w');
      row.appendChild(el('span', 'nm', point.name));
      row.appendChild(el('span', 'v', fmt(point.value, 2)));
      const priority = el('span',
        'p' + (point.priority !== '@NONE' ? ' owned' : ''), point.priority);
      priority.setAttribute('data-tip',
        point.priority === '@NONE'
          ? 'Not owned. Any command will take.'
          : 'Owned at ' + point.priority + '. A command below this priority ' +
            'is silently refused, and so is a release below it.');
      row.appendChild(priority);
      const override = el('span', 'x', '✎');
      override.setAttribute('data-tip', 'Override this value and continue');
      override.onclick = () => this.override(point.name, point.value);
      row.appendChild(override);
      const remove = el('span', 'x', '×');
      remove.onclick = async () => {
        const r = await post('/api/debug/point', {
          session: this.session, action: 'unwatch', name: point.name,
        });
        if (!r.error) this.renderState(r.state);
      };
      row.appendChild(remove);
      watch.appendChild(row);
    });

    const bps = $('dbg-bps');
    clear(bps);
    if (!state.breakpoints.length) {
      bps.appendChild(el('div', 'empty',
        'Click a line number to break there, or use + for a write or a ' +
        'condition.'));
    }
    state.breakpoints.forEach((bp) => {
      const row = el('div', 'b' + (bp.enabled ? '' : ' off'));
      row.appendChild(el('span', 'd', bp.label));
      if (bp.hits) row.appendChild(el('span', 'hits', bp.hits + ' hits'));
      const toggle = el('span', 'x', bp.enabled ? '◉' : '○');
      toggle.setAttribute('data-tip', bp.enabled ? 'Disable' : 'Enable');
      toggle.onclick = async () => {
        const r = await post('/api/debug/breakpoint', {
          session: this.session, action: 'toggle', index: bp.index,
        });
        if (!r.error) this.renderState(r.state);
      };
      row.appendChild(toggle);
      const remove = el('span', 'x', '×');
      remove.onclick = async () => {
        const r = await post('/api/debug/breakpoint', {
          session: this.session, action: 'remove', index: bp.index,
        });
        if (!r.error) this.renderState(r.state);
      };
      row.appendChild(remove);
      bps.appendChild(row);
    });

    const stack = $('dbg-stack');
    clear(stack);
    if (!state.stack.length) {
      stack.appendChild(el('div', 'empty', 'Not inside a subroutine.'));
    }
    state.stack.forEach((frame) => {
      const row = el('div', 's');
      row.appendChild(el('span', 'ln', String(frame.entry)));
      row.appendChild(el('span', '', 'called from ' + frame.called_from));
      stack.appendChild(row);
    });

    /* A blocked command in a main loop repeats every pass, so the raw trace is
     * the same line forty times. Collapsing runs keeps the one thing that
     * matters -- that it happened, and how persistently -- on screen. */
    const events = $('dbg-events');
    clear(events);
    const collapsed = [];
    state.events.slice().reverse().forEach((event) => {
      const last = collapsed[collapsed.length - 1];
      if (last && last.line === event.line && last.text === event.text) {
        last.count += 1;
        return;
      }
      collapsed.push({ line: event.line, text: event.text, kind: event.kind,
                       count: 1 });
    });
    collapsed.slice(0, 30).forEach((event) => {
      const row = el('div', 'e' + (event.kind === 'blocked' ? ' blocked' : ''));
      row.appendChild(el('span', 'ln', String(event.line)));
      row.appendChild(el('span', 'tx',
        event.text + (event.count > 1 ? '   ×' + event.count : '')));
      events.appendChild(row);
    });
    if (state.starved.length) {
      const row = el('div', 'e blocked');
      row.appendChild(el('span', 'ln', ''));
      row.appendChild(el('span', 'tx',
        state.starved.length + ' line(s) have never run: ' +
        state.starved.slice(0, 10).join(', ')));
      events.appendChild(row);
    }
  },

  paintGutter(state) {
    const gutter = $('dbggutter');
    if (!gutter.children.length) return;
    const numbers = this.source.split('\n').map((line) => {
      const m = line.match(/^\s*(\d+)[ \t]/);
      return m ? parseInt(m[1], 10) : null;
    });
    const covered = new Set((state && state.coverage) || []);
    const here = state ? state.line : null;
    Array.from(gutter.children).forEach((row, index) => {
      const number = numbers[index];
      row.className = '';
      if (number === null) return;
      if (covered.has(number)) row.classList.add('cov');
      if (this.breakpoints.indexOf(number) >= 0) row.classList.add('bp');
      if (number === here) row.classList.add('here');
    });
    if (here !== null && here !== undefined) {
      const index = numbers.indexOf(here);
      if (index >= 0) {
        const wrap = gutter.closest('.editwrap');
        if (wrap) wrap.scrollTop = Math.max(0, (index - 8) * 20);
      }
    }
  },

  async override(name, current) {
    const answer = await ask('Override ' + name, [
      { name: 'value', label: 'Value', type: 'text', value: String(current),
        doc: 'A number, ON/OFF, or HH:MM.' },
      { name: 'priority', label: 'Priority', type: 'select', value: '@NONE',
        choices: ['@NONE', '@PDL', '@EMER', '@SMOKE', '@OPER'],
        doc: 'Set this to reproduce a point the program cannot command — an ' +
             'operator command at @OPER is the usual case.' },
    ], 'Set');
    if (!answer) return;
    const r = await post('/api/debug/point', {
      session: this.session, action: 'set', name,
      value: answer.value, priority: answer.priority,
    });
    if (r.error || !r.ok) { toast(r.error || 'could not set it', 'bad'); return; }
    this.renderState(r.state);
  },
};

/* --------------------------------------------------------------- charts */

/* Hand-drawn SVG, one series per chart. Mixing a temperature and a valve
 * position on one axis hides both. */
function chart(key, data) {
  const box = el('div', 'chart');
  const head = el('div', 'hd');
  head.appendChild(el('b', '', CHART_LABELS[key] || key));

  const values = data.map((p) => p[1]);
  const low = Math.min.apply(null, values);
  const high = Math.max.apply(null, values);
  const readout = el('span', '',
    'min ' + fmt(low) + '   max ' + fmt(high) +
    '   end ' + fmt(values[values.length - 1]));
  head.appendChild(readout);
  box.appendChild(head);

  const W = 1000;
  const H = 96;
  const pad = 6;
  const top = high - low < 1e-6 ? low + 1 : high;
  const t0 = data[0][0];
  const t1 = data[data.length - 1][0];
  const span = (t1 - t0) || 1;

  const points = data.map((p) => {
    const x = pad + ((p[0] - t0) / span) * (W - 2 * pad);
    const y = H - pad - ((p[1] - low) / (top - low)) * (H - 2 * pad);
    return x.toFixed(1) + ',' + y.toFixed(1);
  }).join(' ');

  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  svg.setAttribute('preserveAspectRatio', 'none');

  const mid = document.createElementNS(NS, 'line');
  mid.setAttribute('x1', pad);
  mid.setAttribute('x2', W - pad);
  mid.setAttribute('y1', H / 2);
  mid.setAttribute('y2', H / 2);
  mid.setAttribute('stroke', 'currentColor');
  mid.setAttribute('stroke-opacity', '.18');
  svg.appendChild(mid);

  const line = document.createElementNS(NS, 'polyline');
  line.setAttribute('points', points);
  line.setAttribute('fill', 'none');
  line.setAttribute('stroke', /position/.test(key) ? '#ffb454' : '#6fb1ff');
  line.setAttribute('stroke-width', '1.6');
  line.setAttribute('vector-effect', 'non-scaling-stroke');
  svg.appendChild(line);

  const marker = document.createElementNS(NS, 'line');
  marker.setAttribute('y1', 0);
  marker.setAttribute('y2', H);
  marker.setAttribute('stroke', '#6fb1ff');
  marker.setAttribute('stroke-opacity', '.5');
  marker.setAttribute('x1', -10);
  marker.setAttribute('x2', -10);
  svg.appendChild(marker);

  /* A cursor readout, because "what was the discharge at 09:40" is the
   * question a trend is actually asked. */
  svg.addEventListener('mousemove', (e) => {
    const rect = svg.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    const index = Math.max(0, Math.min(data.length - 1,
      Math.round(ratio * (data.length - 1))));
    const at = pad + ((data[index][0] - t0) / span) * (W - 2 * pad);
    marker.setAttribute('x1', at);
    marker.setAttribute('x2', at);
    readout.className = 'cursor';
    readout.textContent =
      clock(data[index][0]) + '   ' + fmt(data[index][1], 2);
  });
  svg.addEventListener('mouseleave', () => {
    marker.setAttribute('x1', -10);
    marker.setAttribute('x2', -10);
    readout.className = '';
    readout.textContent =
      'min ' + fmt(low) + '   max ' + fmt(high) +
      '   end ' + fmt(values[values.length - 1]);
  });

  box.appendChild(svg);
  return box;
}

function clock(seconds) {
  const total = Math.round(seconds / 60);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
}
