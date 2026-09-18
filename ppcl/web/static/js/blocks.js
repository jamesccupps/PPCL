/* The block canvas: drag blocks from the palette, wire them, compile.
 *
 * The canvas is plain DOM for the blocks and one SVG layer for the wires.
 * That combination is deliberate -- blocks want normal form controls and
 * hit-testing, wires want curves -- and it keeps the whole pane inside the
 * browser's own layout engine with no drawing library.
 *
 * Every edit recompiles, so the PPCL and its diagnostics are always in front
 * of you while you draw.
 */

import { $, ask, clear, debounce, el, esc, post, toast } from './core.js';
import { renderDiagnostics } from './editor.js';
import { renderProgram } from './highlight.js';

const GRID = 12;

export const blocks = {
  catalog: null,
  diagram: null,
  selected: null,
  wiring: null,
  onCompiled: null,

  async init() {
    const payload = await post('/api/blocks/catalog', {});
    this.catalog = payload;
    this.renderPalette('');

    $('palq').addEventListener('input', (e) => this.renderPalette(e.target.value));
    $('btn-dia-new').onclick = () => this.starter();
    $('btn-dia-tidy').onclick = () => this.tidy();
    $('btn-dia-delete').onclick = () => this.removeSelected();
    $('btn-dia-compile').onclick = () => this.compile();

    $('dia-outtabs').addEventListener('click', (e) => {
      const button = e.target.closest('button');
      if (button) this.showOutput(button.dataset.out);
    });

    const canvas = $('canvas');
    canvas.addEventListener('mousedown', (e) => this.onCanvasDown(e));
    canvas.addEventListener('dragover', (e) => e.preventDefault());
    canvas.addEventListener('drop', (e) => this.onDrop(e));
    document.addEventListener('mousemove', (e) => this.onMove(e));
    document.addEventListener('mouseup', (e) => this.onUp(e));

    this.recompile = debounce(() => this.compile(), 260);
    await this.starter();
  },

  /* ----------------------------------------------------------- palette */

  renderPalette(query) {
    const node = $('palette');
    clear(node);
    const needle = String(query || '').trim().toLowerCase();
    this.catalog.categories.forEach((category) => {
      const entries = Object.values(this.catalog.blocks)
        .filter((b) => b.category === category)
        .filter((b) => !needle ||
          b.label.toLowerCase().includes(needle) ||
          b.summary.toLowerCase().includes(needle) ||
          b.name.includes(needle));
      if (!entries.length) return;
      node.appendChild(el('div', 'pgroup', category));
      entries.forEach((entry) => {
        const row = el('div', 'pitem', entry.label);
        row.draggable = true;
        row.setAttribute('data-tip',
          '**' + entry.label + '** — ' + entry.summary +
          (entry.emits.length ? '\n\nEmits: ' + entry.emits.join(', ') : ''));
        row.addEventListener('dragstart', (e) => {
          e.dataTransfer.setData('text/plain', entry.name);
        });
        row.addEventListener('dblclick', () => this.add(entry.name, 60, 60));
        node.appendChild(row);
      });
    });
  },

  /** Show either the findings or the generated program below the canvas. */
  showOutput(which) {
    Array.from($('dia-outtabs').children).forEach(
      (b) => b.classList.toggle('on', b.dataset.out === which));
    $('diadiags').hidden = which === 'code';
    $('diacode').hidden = which !== 'code';
  },

  onDrop(e) {
    e.preventDefault();
    const type = e.dataTransfer.getData('text/plain');
    if (!type || !this.catalog.blocks[type]) return;
    const rect = $('canvas').getBoundingClientRect();
    this.add(type, e.clientX - rect.left - 60, e.clientY - rect.top - 20);
  },

  /* ---------------------------------------------------------- diagram */

  async starter() {
    const r = await post('/api/blocks/new', {});
    this.diagram = r.diagram;
    this.selected = null;
    this.render();
    this.compile();
  },

  load(diagram) {
    this.diagram = diagram;
    this.selected = null;
    this.render();
    this.compile();
  },

  nextId() {
    let n = 1;
    const used = new Set(this.diagram.blocks.map((b) => b.id));
    while (used.has('b' + n)) n++;
    return 'b' + n;
  },

  add(type, x, y) {
    const entry = this.catalog.blocks[type];
    const params = {};
    entry.params.forEach((p) => { params[p.name] = p.default; });
    const block = {
      id: this.nextId(), type, label: '', note: '',
      x: Math.max(0, Math.round(x / GRID) * GRID),
      y: Math.max(0, Math.round(y / GRID) * GRID),
      params,
    };
    this.diagram.blocks.push(block);
    this.selected = block.id;
    this.render();
    this.recompile();
    return block;
  },

  block(id) { return this.diagram.blocks.find((b) => b.id === id); },

  removeSelected() {
    if (!this.selected) { toast('select a block first'); return; }
    this.diagram.blocks = this.diagram.blocks.filter((b) => b.id !== this.selected);
    this.diagram.wires = this.diagram.wires.filter(
      (w) => w.src !== this.selected && w.dst !== this.selected);
    this.selected = null;
    this.render();
    this.recompile();
  },

  connect(src, srcPin, dst, dstPin) {
    if (src === dst) { toast('a block cannot wire to itself', 'bad'); return; }
    this.diagram.wires = this.diagram.wires.filter(
      (w) => !(w.dst === dst && w.dst_pin === dstPin));
    this.diagram.wires.push({ src, src_pin: srcPin, dst, dst_pin: dstPin });
    this.render();
    this.recompile();
  },

  disconnect(dst, dstPin) {
    this.diagram.wires = this.diagram.wires.filter(
      (w) => !(w.dst === dst && w.dst_pin === dstPin));
    this.render();
    this.recompile();
  },

  /** Lay blocks out left to right in compilation order. */
  async tidy() {
    const r = await post('/api/blocks/compile', { diagram: this.diagram });
    const order = (r && r.order) || this.diagram.blocks.map((b) => b.id);
    const depth = {};
    this.diagram.blocks.forEach((b) => { depth[b.id] = 0; });
    order.forEach((id) => {
      this.diagram.wires.filter((w) => w.src === id).forEach((w) => {
        depth[w.dst] = Math.max(depth[w.dst] || 0, (depth[id] || 0) + 1);
      });
    });
    const rows = {};
    order.forEach((id) => {
      const column = depth[id] || 0;
      rows[column] = (rows[column] || 0) + 1;
      const block = this.block(id);
      if (!block) return;
      block.x = 40 + column * 210;
      block.y = 40 + (rows[column] - 1) * 120;
    });
    this.render();
  },

  /* ---------------------------------------------------------- drawing */

  render() {
    const canvas = $('canvas');
    clear(canvas);
    $('canvashint').hidden = this.diagram.blocks.length > 0;

    this.diagram.blocks.forEach((block) => {
      const entry = this.catalog.blocks[block.type];
      if (!entry) return;
      const node = el('div', 'blk' + (block.id === this.selected ? ' sel' : ''));
      node.style.left = block.x + 'px';
      node.style.top = block.y + 'px';
      node.dataset.id = block.id;
      node.dataset.cat = entry.category;
      node.setAttribute('data-tip', '**' + entry.label + '** — ' + entry.summary);

      const head = el('div', 'bhead');
      head.appendChild(el('span', '', entry.label));
      if (block.label) head.appendChild(el('span', 'bname', block.label));
      if (entry.time_based) {
        const tag = el('span', 'tag', 'timed');
        tag.setAttribute('data-tip',
          'Time-based: emitted unconditionally so it runs on every pass.');
        head.appendChild(tag);
      }
      if (entry.stateful) {
        const tag = el('span', 'tag', 'holds');
        tag.setAttribute('data-tip',
          'Holds state between passes, so a wire may loop back into it.');
        head.appendChild(tag);
      }
      node.appendChild(head);

      const body = el('div', 'bbody');
      const ins = el('div', 'pins in');
      entry.inputs.forEach((pin) => {
        const row = el('div', 'pin', pin.label);
        const wired = this.diagram.wires.some(
          (w) => w.dst === block.id && w.dst_pin === pin.name);
        const dot = el('div', 'dot' + (wired ? ' wired' : '') +
          (!wired && pin.required ? ' need' : ''));
        dot.dataset.block = block.id;
        dot.dataset.pin = pin.name;
        dot.dataset.dir = 'in';
        dot.setAttribute('data-tip',
          pin.doc || (pin.label + ' — ' + pin.kind +
            (pin.required ? ', required' : ', optional')));
        row.appendChild(dot);
        ins.appendChild(row);
      });
      const outs = el('div', 'pins out');
      entry.outputs.forEach((pin) => {
        const row = el('div', 'pin', pin.label);
        const dot = el('div', 'dot');
        dot.dataset.block = block.id;
        dot.dataset.pin = pin.name;
        dot.dataset.dir = 'out';
        dot.setAttribute('data-tip', pin.doc || (pin.label + ' — ' + pin.kind));
        row.appendChild(dot);
        outs.appendChild(row);
      });
      body.appendChild(ins);
      body.appendChild(outs);
      node.appendChild(body);

      const summary = this.summaryOf(block, entry);
      if (summary) node.appendChild(el('div', 'bfoot', summary));
      canvas.appendChild(node);
    });

    this.drawWires();
    this.renderInspector();
  },

  /** The one-line reminder on a block's face of what it is set to. */
  summaryOf(block, entry) {
    const p = block.params || {};
    switch (block.type) {
      case 'point_in': return p.point || '(no point)';
      case 'constant': return String(p.value);
      case 'resident': return p.which;
      case 'compare': return 'a ' + p.op + ' b';
      case 'between': return p.low + ' .. ' + p.high;
      case 'deadband': return p.low + ' / ' + p.high;
      case 'limit': return p.low + ' .. ' + p.high;
      case 'scale': return p.in_low + '-' + p.in_high + ' → ' +
                            p.out_low + '-' + p.out_high;
      case 'schedule': return p.on + ' – ' + p.off;
      case 'delay': return p.seconds + 's';
      case 'pid': return p.action + ', TR ' + p.throttling;
      case 'extreme': return p.which;
      case 'function': return p.fn;
      case 'reset': return String(p.points || '').split('\n').length + ' points';
      case 'command':
        return (p.action || 'set').toUpperCase() + ' ' + (p.point || '(no point)') +
               (p.priority ? ' ' + p.priority : '');
      case 'assign': return p.point || '(no point)';
      case 'alarm': return p.action + ' ' + (p.point || '');
      case 'note': return String(p.text || '').split('\n')[0];
      default: return entry.summary.length < 34 ? '' : '';
    }
  },

  dotCentre(blockId, pin, dir) {
    const node = document.querySelector(
      '.blk[data-id="' + blockId + '"] .dot[data-pin="' + pin +
      '"][data-dir="' + dir + '"]');
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    const canvas = $('canvas').getBoundingClientRect();
    return {
      x: rect.left - canvas.left + rect.width / 2,
      y: rect.top - canvas.top + rect.height / 2,
    };
  },

  drawWires(live) {
    const svg = $('wires');
    svg.innerHTML = '';
    const NS = 'http://www.w3.org/2000/svg';

    this.diagram.wires.forEach((wire) => {
      const a = this.dotCentre(wire.src, wire.src_pin, 'out');
      const b = this.dotCentre(wire.dst, wire.dst_pin, 'in');
      if (!a || !b) return;
      const d = curve(a, b);
      const hit = document.createElementNS(NS, 'path');
      hit.setAttribute('d', d);
      hit.setAttribute('class', 'hit');
      hit.addEventListener('click', () => this.disconnect(wire.dst, wire.dst_pin));
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', d);
      if (this.selected === wire.src || this.selected === wire.dst) {
        path.setAttribute('class', 'hot');
      }
      svg.appendChild(path);
      svg.appendChild(hit);
    });

    if (live) {
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', curve(live.from, live.to));
      path.setAttribute('class', 'hot');
      svg.appendChild(path);
    }
  },

  /* ------------------------------------------------------------ mouse */

  onCanvasDown(e) {
    const dot = e.target.closest('.dot');
    if (dot) {
      e.preventDefault();
      e.stopPropagation();
      this.wiring = {
        block: dot.dataset.block, pin: dot.dataset.pin, dir: dot.dataset.dir,
      };
      dot.classList.add('live');
      return;
    }
    const node = e.target.closest('.blk');
    if (!node) { this.selected = null; this.render(); return; }
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

    this.selected = node.dataset.id;
    const block = this.block(this.selected);
    const rect = $('canvas').getBoundingClientRect();
    this.dragging = {
      id: block.id,
      dx: e.clientX - rect.left - block.x,
      dy: e.clientY - rect.top - block.y,
    };
    this.render();
  },

  onMove(e) {
    const rect = $('canvas').getBoundingClientRect();
    if (this.dragging) {
      const block = this.block(this.dragging.id);
      if (!block) return;
      block.x = Math.max(0, Math.round(
        (e.clientX - rect.left - this.dragging.dx) / GRID) * GRID);
      block.y = Math.max(0, Math.round(
        (e.clientY - rect.top - this.dragging.dy) / GRID) * GRID);
      const node = document.querySelector('.blk[data-id="' + block.id + '"]');
      if (node) { node.style.left = block.x + 'px'; node.style.top = block.y + 'px'; }
      this.drawWires();
      return;
    }
    if (this.wiring) {
      const from = this.dotCentre(this.wiring.block, this.wiring.pin,
                                  this.wiring.dir);
      if (!from) return;
      const to = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      this.drawWires(this.wiring.dir === 'out' ? { from, to }
                                               : { from: to, to: from });
    }
  },

  onUp(e) {
    if (this.dragging) { this.dragging = null; return; }
    if (!this.wiring) return;
    const target = document.elementFromPoint(e.clientX, e.clientY);
    const dot = target && target.closest ? target.closest('.dot') : null;
    document.querySelectorAll('.dot.live').forEach(
      (d) => d.classList.remove('live'));
    const start = this.wiring;
    this.wiring = null;
    if (!dot) { this.drawWires(); return; }
    if (dot.dataset.dir === start.dir) {
      toast('wire an output to an input', 'bad');
      this.drawWires();
      return;
    }
    if (start.dir === 'out') {
      this.connect(start.block, start.pin, dot.dataset.block, dot.dataset.pin);
    } else {
      this.connect(dot.dataset.block, dot.dataset.pin, start.block, start.pin);
    }
  },

  /* -------------------------------------------------------- inspector */

  renderInspector() {
    const node = $('inspector');
    clear(node);
    if (!this.selected) {
      node.appendChild(el('div', 'empty',
        'Select a block to edit it. Drag from an output dot to an input dot ' +
        'to wire two blocks together; click a wire to remove it.'));
      return;
    }
    const block = this.block(this.selected);
    const entry = this.catalog.blocks[block.type];
    if (!block || !entry) return;

    const head = el('div', 'ihead');
    head.appendChild(el('div', 't', entry.label));
    head.appendChild(el('div', 's', entry.summary));
    node.appendChild(head);

    node.appendChild(this.field('Name', 'text', block.label,
      'Naming a block gives its value a local variable with this name, so ' +
      'you can look the wire up in the point list at the panel. Leave it ' +
      'blank and the value costs nothing.',
      (value) => { block.label = value; this.render(); this.recompile(); }));

    entry.params.forEach((param) => {
      const value = block.params[param.name];
      const kind = param.kind === 'choice' ? 'select'
        : param.kind === 'pairs' || param.kind === 'text' ? 'textarea'
        : param.kind === 'number' ? 'number' : 'text';
      node.appendChild(this.field(
        param.label, kind, value === undefined ? param.default : value,
        param.doc, (v) => {
          block.params[param.name] = v;
          this.render();
          this.recompile();
        }, param.choices));
    });

    node.appendChild(this.field('Comment', 'textarea', block.note,
      'Emitted as a PPCL comment above this block\'s statements.',
      (value) => { block.note = value; this.recompile(); }));

    if (entry.help) {
      const note = el('div', 'note');
      note.innerHTML = esc(entry.help).replace(/\n\n/g, '<br><br>');
      node.appendChild(note);
    }
    if (entry.emits.length) {
      node.appendChild(el('div', 'note',
        'Emits: ' + entry.emits.join(', ') +
        (entry.manual ? '   ·   ' + entry.manual : '')));
    }
  },

  field(label, kind, value, doc, onChange, choices) {
    const wrap = el('div', 'f');
    wrap.appendChild(el('label', '', label));
    let input;
    if (kind === 'select') {
      input = el('select');
      (choices || []).forEach((c) => {
        const o = el('option', '', c === '' ? '(none)' : c);
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
  },

  /* ---------------------------------------------------------- compile */

  async compile(then) {
    const r = await post('/api/blocks/compile', {
      diagram: this.diagram,
      firmware: $('firmware').value,
    });
    document.querySelectorAll('.blk.bad').forEach(
      (n) => n.classList.remove('bad'));

    if (r.error && !('ok' in r)) {
      $('dia-status').innerHTML = '<span class="e">' + esc(r.error) + '</span>';
      return null;
    }
    if (!r.ok) {
      $('dia-status').innerHTML = '<span class="e">' + esc(r.error) + '</span>';
      if (r.block) {
        const node = document.querySelector('.blk[data-id="' + r.block + '"]');
        if (node) node.classList.add('bad');
      }
      clear($('diadiags'));
      $('diadiags').appendChild(el('div', 'empty', r.error));
      // There is no generated program to look at, so put the reason in front
      // of the reader rather than leaving them on an empty code tab.
      this.showOutput('diags');
      return null;
    }

    this.text = r.text;
    renderProgram($('diahl'), $('diagutter'), r.text);
    const notes = (r.warnings || []).map((w) => ({
      code: 'DIA', severity: 'warning', message: w, line: null,
      detail: '', suggestion: '', manual: '',
    }));
    const findings = r.diagnostics.filter(
      (d) => d.severity === 'error' || d.severity === 'warning');
    renderDiagnostics($('diadiags'), notes.concat(findings), null);
    $('dia-status').innerHTML = findings.length
      ? '<span class="w">' + findings.length + ' finding(s)</span>'
      : '<span class="ok">compiles clean</span>';
    if (this.onCompiled) this.onCompiled(r.text);
    if (then) then(r.text);
    return r.text;
  },
};

function curve(a, b) {
  const dx = Math.max(34, Math.abs(b.x - a.x) * 0.45);
  return 'M' + a.x + ',' + a.y +
         ' C' + (a.x + dx) + ',' + a.y +
         ' ' + (b.x - dx) + ',' + b.y +
         ' ' + b.x + ',' + b.y;
}
