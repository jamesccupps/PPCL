/* The Builder pane: a sequence document edited as forms, not as raw text.
 *
 * The decision table is the primary surface because that is Siemens' own
 * recommended design artifact. Everything a table cannot express -- modes,
 * interlocks, loops, resets, free-form rules -- gets a form of its own rather
 * than sending you back to the text file, which was the previous version's
 * main shortcoming.
 *
 * The text form is still there, and the two stay in step: editing text
 * reparses into the document, editing the document re-renders the text.
 */

import { $, ask, clear, debounce, el, esc, post, toast } from './core.js';
import { renderDiagnostics } from './editor.js';
import { renderProgram } from './highlight.js';

const CELL_CHOICES = ['-', 'on', 'off', 'auto', 'fast', 'slow', 'open',
                      'closed', 'modulate', 'hold'];

const PRIORITIES = ['@NONE', '@PDL', '@EMER', '@SMOKE', '@OPER'];

export const builder = {
  doc: null,
  section: 'table',
  text: '',
  onCompiled: null,

  init() {
    $('btn-seq-compile').onclick = () => this.compile();
    $('btn-seq-new').onclick = () => this.starter();
    $('btn-seq-save').onclick = () => this.save();

    $('seq-subtabs').addEventListener('click', (e) => {
      const button = e.target.closest('button');
      if (!button) return;
      this.section = button.dataset.sec;
      Array.from($('seq-subtabs').children).forEach(
        (b) => b.classList.toggle('on', b === button));
      this.renderSection();
    });

    this.recompile = debounce(() => this.compile(), 280);
    this.starter();
  },

  /* -------------------------------------------------------- documents */

  async starter() {
    await this.fromText(STARTER);
  },

  async fromText(text) {
    const r = await post('/api/seq/parse', { text });
    if (!r.ok) {
      $('seq-status').innerHTML =
        '<span class="e">line ' + r.line + ': ' + esc(r.error) + '</span>';
      return false;
    }
    $('seq-status').textContent = '';
    this.doc = r.document;
    this.text = text;
    this.renderSection();
    this.compile();
    return true;
  },

  /** Push document edits back out to the text form, then recompile. */
  async sync() {
    const r = await post('/api/seq/render', { document: this.doc });
    if (r.ok) this.text = r.text;
    this.renderSection();
    this.recompile();
  },

  /* --------------------------------------------------------- sections */

  renderSection() {
    const node = $('seq-body');
    clear(node);
    if (!this.doc) return;
    const render = {
      table: () => this.renderTable(node),
      points: () => this.renderPoints(node),
      modes: () => this.renderModes(node),
      interlocks: () => this.renderInterlocks(node),
      loops: () => this.renderLoops(node),
      resets: () => this.renderResets(node),
      rules: () => this.renderRules(node),
      text: () => this.renderText(node),
    }[this.section];
    if (render) render();
  },

  head(node, title, addLabel, onAdd, tip) {
    const bar = el('div', 'bar');
    const heading = el('b', '', title);
    bar.appendChild(heading);
    bar.appendChild(el('div', 'spacer'));
    if (onAdd) {
      const button = el('button', 'act', addLabel);
      if (tip) button.setAttribute('data-tip', tip);
      button.onclick = onAdd;
      bar.appendChild(button);
    }
    node.appendChild(bar);
  },

  /* ----------------------------------------------------------- table */

  renderTable(node) {
    this.head(node, 'Decision table', '+ Equipment row',
      () => this.addRow(),
      'Add a piece of equipment. Each cell then says what it does in that mode.');

    const modes = this.doc.modes.map((m) => m.name);
    if (!modes.length) {
      node.appendChild(el('div', 'empty',
        'Declare some modes first — the table has one column per mode.'));
      return;
    }
    const cells = this.doc.table.cells || {};
    const points = Object.keys(cells);

    const wrap = el('div');
    const table = el('table', 'dt');
    const head = el('tr');
    head.appendChild(el('th', 'pt', 'Equipment'));
    modes.forEach((mode, index) => {
      const th = el('th');
      th.appendChild(document.createTextNode(mode + '  (' + (index + 1) + ')'));
      th.setAttribute('data-tip',
        'Mode ' + (index + 1) + '. Modes are tested lowest priority first ' +
        'and the last match wins, so a later column overrides an earlier one.');
      head.appendChild(th);
    });
    table.appendChild(head);

    points.forEach((point) => {
      const row = el('tr');
      const th = el('th', 'pt');
      th.appendChild(document.createTextNode(point));
      const x = el('span', 'x', '×');
      x.setAttribute('data-tip', 'Remove this row');
      x.onclick = () => {
        delete this.doc.table.cells[point];
        this.sync();
      };
      th.appendChild(x);
      row.appendChild(th);

      modes.forEach((mode) => {
        const value = (cells[point] || {})[mode] || '-';
        const td = el('td', cellClass(value));
        td.appendChild(this.cell(point, mode, value));
        row.appendChild(td);
      });
      table.appendChild(row);
    });
    wrap.appendChild(table);
    node.appendChild(wrap);

    node.appendChild(el('div', 'empty',
      'on / off / auto / fast / slow / open / closed / modulate / hold, a ' +
      'number, or - for no command. "modulate" emits nothing and leaves the ' +
      'point to its control loop; every other value overrides the loop, ' +
      'because the table is compiled after the loops.'));
  },

  cell(point, mode, value) {
    const isNumber = CELL_CHOICES.indexOf(value) === -1;
    if (isNumber) {
      const input = el('input');
      input.type = 'text';
      input.value = value;
      input.onchange = () => this.setCell(point, mode, input.value.trim());
      return input;
    }
    const select = el('select');
    CELL_CHOICES.concat(['number…']).forEach((choice) => {
      const o = el('option', '', choice);
      o.value = choice;
      if (choice === value) o.selected = true;
      select.appendChild(o);
    });
    select.onchange = async () => {
      if (select.value === 'number…') {
        const answer = await ask('Value for ' + point + ' in ' + mode, [
          { name: 'value', label: 'Value', type: 'text', value: '50',
            doc: 'Compiled to SET(value, point).' },
        ]);
        this.setCell(point, mode, answer ? answer.value.trim() : value);
      } else {
        this.setCell(point, mode, select.value);
      }
    };
    return select;
  },

  setCell(point, mode, value) {
    if (!this.doc.table.cells[point]) this.doc.table.cells[point] = {};
    if (value === '-' || value === '') delete this.doc.table.cells[point][mode];
    else this.doc.table.cells[point][mode] = value;
    this.sync();
  },

  async addRow() {
    const known = (this.doc.points || []).map((p) => p.name);
    const answer = await ask('Add equipment to the table', [
      { name: 'point', label: 'Point', type: 'text',
        doc: known.length ? 'Declared points: ' + known.join(', ') : '' },
    ], 'Add');
    if (!answer || !answer.point.trim()) return;
    this.doc.table.cells[answer.point.trim()] = {};
    this.sync();
  },

  /* ---------------------------------------------------------- points */

  renderPoints(node) {
    this.head(node, 'Points', '+ Point', () => this.addPoint(),
      'Declare a point so the compiler knows whether it is analog or digital, ' +
      'and whether it is a program local.');
    const cards = el('div', 'cards');
    (this.doc.points || []).forEach((point, index) => {
      const card = el('div', 'card');
      const head = el('div', 'cardhead');
      head.appendChild(el('span', 'name', point.name));
      head.appendChild(el('div', 'spacer'));
      head.appendChild(this.removeButton(() => {
        this.doc.points.splice(index, 1);
        this.sync();
      }));
      card.appendChild(head);
      const grid = el('div', 'grid2');
      grid.appendChild(field('Name', 'text', point.name, '',
        (v) => { point.name = v; this.sync(); }));
      grid.appendChild(field('Kind', 'select', point.kind,
        'Digital points read 1.0 when ON.',
        (v) => { point.kind = v; this.sync(); }, ['analog', 'digital']));
      grid.appendChild(field('Role', 'select', point.role,
        'A local is a program-only virtual point, referenced as $NAME. PPCL ' +
        'allows sixteen of them.',
        (v) => { point.role = v; this.sync(); },
        ['input', 'output', 'virtual', 'local']));
      grid.appendChild(field('Units', 'text', point.units, '',
        (v) => { point.units = v; this.sync(); }));
      card.appendChild(grid);
      cards.appendChild(card);
    });
    node.appendChild(cards);
  },

  async addPoint() {
    const answer = await ask('Add a point', [
      { name: 'name', label: 'Name', type: 'text' },
      { name: 'kind', label: 'Kind', type: 'select', value: 'analog',
        choices: ['analog', 'digital'] },
      { name: 'role', label: 'Role', type: 'select', value: 'output',
        choices: ['input', 'output', 'virtual', 'local'],
        doc: 'A local is program-only and is referenced as $NAME.' },
    ], 'Add');
    if (!answer || !answer.name.trim()) return;
    this.doc.points.push({
      name: answer.name.trim(), kind: answer.kind, role: answer.role,
      units: '', description: '',
    });
    this.sync();
  },

  /* ----------------------------------------------------------- modes */

  renderModes(node) {
    this.head(node, 'Modes', '+ Mode', () => this.addMode(),
      'Modes are tested lowest priority first and the last match wins, so a ' +
      'shutdown mode placed last overrides everything above it.');
    node.appendChild(el('div', 'empty',
      'Order matters. Put the fallback mode first and the highest-priority ' +
      'mode last.'));
    const cards = el('div', 'cards');
    (this.doc.modes || []).forEach((mode, index) => {
      const card = el('div', 'card');
      const head = el('div', 'cardhead');
      head.appendChild(el('span', 'name', (index + 1) + '.  ' + mode.name));
      head.appendChild(el('div', 'spacer'));
      head.appendChild(this.moveButtons(this.doc.modes, index));
      head.appendChild(this.removeButton(() => {
        this.doc.modes.splice(index, 1);
        this.sync();
      }));
      card.appendChild(head);
      const grid = el('div', 'grid2');
      grid.appendChild(field('Name', 'text', mode.name, '',
        (v) => { mode.name = v; this.sync(); }));
      grid.appendChild(field('Description', 'text', mode.description, '',
        (v) => { mode.description = v; this.sync(); }));
      card.appendChild(grid);
      card.appendChild(conditionField('When', mode.when,
        index === 0 ? 'The first mode is the fallback; leave it as "always".'
                    : 'The condition that selects this mode.',
        (c) => { mode.when = c; this.sync(); }));
      cards.appendChild(card);
    });
    node.appendChild(cards);
  },

  async addMode() {
    const answer = await ask('Add a mode', [
      { name: 'name', label: 'Name', type: 'text',
        doc: 'Day, Unoccupied, Warm-up, Shutdown, Smoke — whatever the ' +
             'sequence of operation calls it.' },
      { name: 'when', label: 'When', type: 'text',
        placeholder: 'TIME between 6:00 and 18:00',
        doc: 'Plain text: "MAT < 38", "SFAN is on", "TIME between 6:00 and ' +
             '18:00", or leave blank for "always".' },
    ], 'Add');
    if (!answer || !answer.name.trim()) return;
    this.doc.modes.push({
      name: answer.name.trim(),
      when: parseCondition(answer.when),
      description: '',
    });
    this.sync();
  },

  /* ------------------------------------------------------ interlocks */

  renderInterlocks(node) {
    this.head(node, 'Interlocks', '+ Interlock', () => this.addInterlock(),
      'A latching safety. The compiler always writes the matching RELEAS at ' +
      'the same priority, because omitting it is the most common PPCL field ' +
      'defect.');
    const cards = el('div', 'cards');
    (this.doc.interlocks || []).forEach((lock, index) => {
      const card = el('div', 'card');
      const head = el('div', 'cardhead');
      head.appendChild(el('span', 'name', lock.name));
      head.appendChild(el('div', 'spacer'));
      head.appendChild(this.removeButton(() => {
        this.doc.interlocks.splice(index, 1);
        this.sync();
      }));
      card.appendChild(head);
      const grid = el('div', 'grid2');
      grid.appendChild(field('Name', 'text', lock.name, '',
        (v) => { lock.name = v; this.sync(); }));
      grid.appendChild(field('Priority', 'select', lock.priority,
        'Commanded and released at this priority. A release below the ' +
        'point\'s current priority silently does nothing.',
        (v) => { lock.priority = v; this.sync(); }, PRIORITIES));
      card.appendChild(grid);
      card.appendChild(conditionField('Trip when', lock.trip,
        'Latches the interlock.', (c) => { lock.trip = c; this.sync(); }));
      card.appendChild(conditionField('Reset when', lock.reset,
        'Clears the latch. Required — a latch that never clears leaves its ' +
        'points stuck forever.',
        (c) => { lock.reset = c; this.sync(); }));
      card.appendChild(field('Forces', 'textarea',
        (lock.forces || []).map((f) => f[0] + ' ' + f[1]).join('\n'),
        'One per line: POINT ACTION, e.g. "SFAN off" or "OADPR closed". ' +
        'Each gets a matching release at the same priority.',
        (v) => {
          lock.forces = v.split('\n').map((line) => line.trim())
            .filter(Boolean)
            .map((line) => {
              const at = line.lastIndexOf(' ');
              return at > 0
                ? [line.slice(0, at).trim(), line.slice(at + 1).trim()]
                : [line, 'off'];
            });
          this.sync();
        }));
      cards.appendChild(card);
    });
    node.appendChild(cards);
  },

  async addInterlock() {
    const answer = await ask('Add an interlock', [
      { name: 'name', label: 'Name', type: 'text', placeholder: 'Freeze' },
      { name: 'trip', label: 'Trip when', type: 'text', placeholder: 'MAT < 38' },
      { name: 'reset', label: 'Reset when', type: 'text', placeholder: 'MAT > 45',
        doc: 'Required. Give the reset real hysteresis or the interlock will ' +
             'chatter.' },
      { name: 'priority', label: 'Priority', type: 'select', value: '@EMER',
        choices: PRIORITIES },
      { name: 'forces', label: 'Forces', type: 'textarea',
        placeholder: 'SFAN off',
        doc: 'One per line: POINT ACTION.' },
    ], 'Add');
    if (!answer || !answer.name.trim()) return;
    this.doc.interlocks.push({
      name: answer.name.trim(),
      trip: parseCondition(answer.trip),
      reset: parseCondition(answer.reset),
      priority: answer.priority,
      forces: String(answer.forces || '').split('\n').map((l) => l.trim())
        .filter(Boolean)
        .map((line) => {
          const at = line.lastIndexOf(' ');
          return at > 0
            ? [line.slice(0, at).trim(), line.slice(at + 1).trim()]
            : [line, 'off'];
        }),
      description: '',
    });
    this.sync();
  },

  /* ----------------------------------------------------------- loops */

  renderLoops(node) {
    this.head(node, 'Control loops', '+ Loop', () => this.addLoop(),
      'Compiled to LOOP, emitted unconditionally so the sample timing holds.');
    node.appendChild(el('div', 'empty',
      'Gains are computed from the throttling range using the manual\'s own ' +
      'formula. The simulated loop output is approximated — never take a ' +
      'tuning constant from the bench to a panel.'));
    const cards = el('div', 'cards');
    (this.doc.loops || []).forEach((loop, index) => {
      const card = el('div', 'card');
      const head = el('div', 'cardhead');
      head.appendChild(el('span', 'name', loop.name));
      head.appendChild(el('div', 'spacer'));
      head.appendChild(this.removeButton(() => {
        this.doc.loops.splice(index, 1);
        this.sync();
      }));
      card.appendChild(head);
      const grid = el('div', 'grid2');
      [
        ['Name', 'name', 'text', ''],
        ['Measured point', 'process', 'text', 'The process variable.'],
        ['Output point', 'output', 'text', ''],
        ['Setpoint', 'setpoint', 'text', 'A point or a number.'],
        ['Action', 'action', 'select',
         'Reverse raises the output as the measurement falls — heating.'],
        ['Throttling range', 'throttling_range', 'number',
         'The change in measurement that drives the output full range.'],
        ['Output low', 'output_low', 'number', ''],
        ['Output high', 'output_high', 'number', ''],
        ['Sample seconds', 'sample_seconds', 'number', ''],
      ].forEach(([label, key, kind, doc]) => {
        grid.appendChild(field(label, kind, loop[key], doc, (v) => {
          loop[key] = kind === 'number' ? Number(v) : v;
          this.sync();
        }, kind === 'select' ? ['reverse', 'direct'] : null));
      });
      card.appendChild(grid);
      cards.appendChild(card);
    });
    node.appendChild(cards);
  },

  async addLoop() {
    const answer = await ask('Add a control loop', [
      { name: 'name', label: 'Name', type: 'text', placeholder: 'Discharge' },
      { name: 'process', label: 'Measured point', type: 'text', placeholder: 'DAT' },
      { name: 'output', label: 'Output point', type: 'text', placeholder: 'HVLV' },
      { name: 'setpoint', label: 'Setpoint', type: 'text', placeholder: 'DASP' },
      { name: 'action', label: 'Action', type: 'select', value: 'reverse',
        choices: ['reverse', 'direct'] },
      { name: 'throttling_range', label: 'Throttling range', type: 'number',
        value: 10 },
    ], 'Add');
    if (!answer || !answer.name.trim()) return;
    this.doc.loops.push({
      name: answer.name.trim(), process: answer.process,
      output: answer.output, setpoint: answer.setpoint,
      action: answer.action,
      throttling_range: Number(answer.throttling_range) || 10,
      output_low: 0, output_high: 100, integral: true,
      sample_seconds: 10, bias: null, description: '',
    });
    this.sync();
  },

  /* ---------------------------------------------------------- resets */

  renderResets(node) {
    this.head(node, 'Reset schedules', '+ Reset', () => this.addReset(),
      'A piecewise-linear transfer curve, compiled to TABLE. Up to seven ' +
      'breakpoints, x ascending.');
    const cards = el('div', 'cards');
    (this.doc.resets || []).forEach((reset, index) => {
      const card = el('div', 'card');
      const head = el('div', 'cardhead');
      head.appendChild(el('span', 'name', reset.output + ' from ' + reset.source));
      head.appendChild(el('div', 'spacer'));
      head.appendChild(this.removeButton(() => {
        this.doc.resets.splice(index, 1);
        this.sync();
      }));
      card.appendChild(head);
      const grid = el('div', 'grid2');
      grid.appendChild(field('Output point', 'text', reset.output, '',
        (v) => { reset.output = v; this.sync(); }));
      grid.appendChild(field('Source point', 'text', reset.source, '',
        (v) => { reset.source = v; this.sync(); }));
      card.appendChild(grid);
      card.appendChild(field('Breakpoints', 'textarea',
        (reset.points || []).map((p) => p[0] + ' -> ' + p[1]).join('\n'),
        'One per line, "x -> y", x ascending. Outside the first and last ' +
        'pair the output holds flat.',
        (v) => {
          reset.points = v.split('\n').map((line) => line.trim())
            .filter(Boolean)
            .map((line) => line.split(/->|,/).map((n) => Number(n.trim())))
            .filter((pair) => pair.length === 2 && pair.every(isFinite));
          this.sync();
        }));
      cards.appendChild(card);
    });
    node.appendChild(cards);
  },

  async addReset() {
    const answer = await ask('Add a reset schedule', [
      { name: 'output', label: 'Output point', type: 'text', placeholder: 'DASP' },
      { name: 'source', label: 'Source point', type: 'text', placeholder: 'OAT' },
      { name: 'points', label: 'Breakpoints', type: 'textarea',
        value: '0 -> 95\n60 -> 55',
        doc: 'One per line, "x -> y", x ascending.' },
    ], 'Add');
    if (!answer || !answer.output.trim()) return;
    this.doc.resets.push({
      output: answer.output.trim(), source: answer.source.trim(),
      points: String(answer.points).split('\n').map((l) => l.trim())
        .filter(Boolean)
        .map((line) => line.split(/->|,/).map((n) => Number(n.trim())))
        .filter((pair) => pair.length === 2 && pair.every(isFinite)),
      description: '',
    });
    this.sync();
  },

  /* ----------------------------------------------------------- rules */

  renderRules(node) {
    this.head(node, 'Rules', '+ Rule', () => this.addRule(),
      'The escape hatch for logic a decision table cannot express.');
    const cards = el('div', 'cards');
    (this.doc.rules || []).forEach((rule, index) => {
      const card = el('div', 'card');
      const head = el('div', 'cardhead');
      head.appendChild(el('span', 'name', 'Rule ' + (index + 1)));
      head.appendChild(el('div', 'spacer'));
      head.appendChild(this.removeButton(() => {
        this.doc.rules.splice(index, 1);
        this.sync();
      }));
      card.appendChild(head);
      card.appendChild(conditionField('When', rule.when, '',
        (c) => { rule.when = c; this.sync(); }));
      card.appendChild(field('Then', 'textarea',
        (rule.then || []).map((a) => a[0] + ' ' + a[1]).join('\n'),
        'One per line: POINT ACTION.',
        (v) => { rule.then = parseActions(v); this.sync(); }));
      card.appendChild(field('Otherwise', 'textarea',
        (rule.otherwise || []).map((a) => a[0] + ' ' + a[1]).join('\n'),
        'Optional. Several actions here need an invertible condition.',
        (v) => { rule.otherwise = parseActions(v); this.sync(); }));
      cards.appendChild(card);
    });
    node.appendChild(cards);
  },

  async addRule() {
    const answer = await ask('Add a rule', [
      { name: 'when', label: 'When', type: 'text', placeholder: 'OAT > 65' },
      { name: 'then', label: 'Then', type: 'textarea', placeholder: 'CWP on' },
      { name: 'otherwise', label: 'Otherwise', type: 'textarea' },
    ], 'Add');
    if (!answer) return;
    this.doc.rules.push({
      when: parseCondition(answer.when),
      then: parseActions(answer.then),
      otherwise: parseActions(answer.otherwise),
      description: '',
    });
    this.sync();
  },

  /* ------------------------------------------------------------ text */

  renderText(node) {
    const area = el('textarea', 'seqtext');
    area.value = this.text;
    area.spellcheck = false;
    area.setAttribute('data-tip',
      'The document as text. Edits here reparse into the forms.');
    area.addEventListener('input', debounce(() => {
      this.fromText(area.value);
    }, 400));
    node.appendChild(area);
  },

  /* -------------------------------------------------------- controls */

  removeButton(onClick) {
    const button = el('button', 'act danger', 'Remove');
    button.onclick = onClick;
    return button;
  },

  moveButtons(list, index) {
    const wrap = el('span');
    const up = el('button', 'icon', '↑');
    up.setAttribute('data-tip', 'Move earlier — lower priority');
    up.onclick = () => {
      if (index === 0) return;
      const [item] = list.splice(index, 1);
      list.splice(index - 1, 0, item);
      this.sync();
    };
    const down = el('button', 'icon', '↓');
    down.setAttribute('data-tip', 'Move later — higher priority, wins ties');
    down.onclick = () => {
      if (index >= list.length - 1) return;
      const [item] = list.splice(index, 1);
      list.splice(index + 1, 0, item);
      this.sync();
    };
    wrap.appendChild(up);
    wrap.appendChild(down);
    return wrap;
  },

  /* --------------------------------------------------------- compile */

  async compile(then) {
    const body = this.doc ? { document: this.doc } : { text: this.text };
    body.firmware = $('firmware').value;
    const r = await post('/api/seq/compile', body);
    if (r.error || !r.ok) {
      const message = r.error || 'compilation failed';
      $('seq-status').innerHTML = '<span class="e">' + esc(message) + '</span>';
      renderProgram($('seqhl'), $('seqgutter'), '');
      clear($('seqdiags'));
      $('seqdiags').appendChild(el('div', 'empty', message));
      return null;
    }
    renderProgram($('seqhl'), $('seqgutter'), r.text);
    this.output = r.text;

    const notes = (r.warnings || []).map((w) => ({
      code: 'DOC', severity: 'warning', message: w, line: null,
      detail: '', suggestion: '', manual: '',
    }));
    const findings = r.diagnostics.filter(
      (d) => d.severity === 'error' || d.severity === 'warning');
    renderDiagnostics($('seqdiags'), notes.concat(findings), null);
    $('seq-status').innerHTML = findings.length
      ? '<span class="w">' + findings.length + ' finding(s)</span>'
      : '<span class="ok">compiles clean</span>';
    if (this.onCompiled) this.onCompiled(r.text);
    if (then) then(r.text);
    return r.text;
  },

  async save() {
    const answer = await ask('Save sequence document', [{
      name: 'path', label: 'Path', type: 'text', value: 'sequences/new.seq',
      doc: 'Relative to the workspace directory.',
    }], 'Save');
    if (!answer || !answer.path) return;
    const r = await post('/api/save', { path: answer.path, text: this.text });
    if (r.error) { toast(r.error, 'bad'); return; }
    toast('saved ' + answer.path, 'good');
  },
};

/* ------------------------------------------------------------ helpers */

function cellClass(value) {
  if (value === 'modulate' || value === 'hold') return 'mod';
  if (value === 'on' || value === 'open') return 'on';
  if (value === 'off' || value === 'closed') return 'off';
  return '';
}

function field(label, kind, value, doc, onChange, choices) {
  const wrap = el('div', 'f');
  wrap.appendChild(el('label', '', label));
  let input;
  if (kind === 'select') {
    input = el('select');
    (choices || []).forEach((c) => {
      const o = el('option', '', c);
      o.value = c;
      if (String(c) === String(value)) o.selected = true;
      input.appendChild(o);
    });
    input.onchange = () => onChange(input.value);
  } else if (kind === 'textarea') {
    input = el('textarea');
    input.value = value == null ? '' : value;
    input.onchange = () => onChange(input.value);
  } else {
    input = el('input');
    input.type = kind;
    input.value = value == null ? '' : value;
    input.onchange = () => onChange(input.value);
  }
  wrap.appendChild(input);
  if (doc) wrap.appendChild(el('div', 'doc', doc));
  return wrap;
}

/** A condition edited as the plain text the .seq DSL uses. */
function conditionField(label, condition, doc, onChange) {
  return field(label, 'text', renderCondition(condition),
    doc || 'Plain text: "MAT < 38", "SFAN is on", ' +
           '"TIME between 6:00 and 18:00", or "always".',
    (v) => onChange(parseCondition(v)));
}

function renderCondition(c) {
  if (!c || c.type === 'always') return 'always';
  if (c.type === 'compare') return c.left + ' ' + c.op + ' ' + c.right;
  if (c.type === 'between') {
    return c.left + ' between ' + c.low + ' and ' + c.high;
  }
  if (c.type === 'all') return c.parts.map(renderCondition).join(' and ');
  if (c.type === 'any') return c.parts.map(renderCondition).join(' or ');
  if (c.type === 'not') return 'not ' + renderCondition(c.part);
  return '';
}

/* Deliberately the same small grammar the .seq text form uses, so a condition
 * typed in a form and one typed in the document mean the same thing. Anything
 * this cannot read comes back as a plain comparison and the compiler reports
 * it, rather than being silently dropped. */
function parseCondition(text) {
  const raw = String(text || '').trim();
  if (!raw || raw.toLowerCase() === 'always') return { type: 'always' };

  const lower = raw.toLowerCase();
  const andAt = splitOn(lower, ' and ');
  const orAt = splitOn(lower, ' or ');
  if (orAt.length > 1) {
    return { type: 'any', parts: orAt.map((p) => parseCondition(raw.substr(p.at, p.len))) };
  }
  const between = /^(.+?)\s+between\s+(\S+)\s+and\s+(\S+)$/i.exec(raw);
  if (between) {
    return {
      type: 'between', left: between[1].trim(),
      low: coerce(between[2]), high: coerce(between[3]),
    };
  }
  if (andAt.length > 1) {
    return { type: 'all', parts: andAt.map((p) => parseCondition(raw.substr(p.at, p.len))) };
  }
  const isNot = /^(.+?)\s+is\s+not\s+(.+)$/i.exec(raw);
  if (isNot) {
    return { type: 'compare', left: isNot[1].trim(), op: '!=',
             right: coerce(isNot[2]) };
  }
  const is = /^(.+?)\s+is\s+(.+)$/i.exec(raw);
  if (is) {
    return { type: 'compare', left: is[1].trim(), op: '=',
             right: coerce(is[2]) };
  }
  const compare = /^(.+?)\s*(<=|>=|<>|!=|==|<|>|=)\s*(.+)$/.exec(raw);
  if (compare) {
    return { type: 'compare', left: compare[1].trim(), op: compare[2],
             right: coerce(compare[3]) };
  }
  return { type: 'compare', left: raw, op: '=', right: 'on' };
}

function splitOn(lower, token) {
  const out = [];
  let at = 0;
  let index = lower.indexOf(token);
  while (index !== -1) {
    out.push({ at, len: index - at });
    at = index + token.length;
    index = lower.indexOf(token, at);
  }
  out.push({ at, len: lower.length - at });
  return out;
}

function coerce(text) {
  const raw = String(text).trim();
  const n = Number(raw);
  return raw !== '' && isFinite(n) && !/:/.test(raw) ? n : raw;
}

function parseActions(text) {
  return String(text || '').split('\n').map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const at = line.lastIndexOf(' ');
      return at > 0
        ? [line.slice(0, at).trim(), line.slice(at + 1).trim()]
        : [line, 'on'];
    });
}

export const STARTER = [
  'sequence "New sequence"',
  '  equipment AHU1',
  '',
  'points',
  '  SFAN    digital output',
  '  OADPR   analog  output',
  '  HVLV    analog  output',
  '  MAT     analog  input',
  '  DAT     analog  input',
  '  ZNT     analog  input',
  '  DASP    analog  virtual',
  '',
  'modes',
  '  Unoccupied  otherwise',
  '  Occupied    when TIME between 6:00 and 18:00',
  '  Shutdown    when Freeze is on',
  '',
  'table',
  '              Unoccupied  Occupied  Shutdown',
  '  SFAN        off         on        off',
  '  OADPR       closed      20        closed',
  '  HVLV        closed      modulate  closed',
  '',
  'interlock Freeze',
  '  trip when MAT < 38',
  '  reset when MAT > 45',
  '  force SFAN off at emer',
  '',
  'reset DASP from ZNT',
  '  68 -> 95',
  '  74 -> 55',
  '',
  'loop Discharge',
  '  measure DAT',
  '  output HVLV',
  '  setpoint DASP',
  '  acting reverse',
  '  throttling 10',
  '  range 0 to 100',
  '',
].join('\n');
