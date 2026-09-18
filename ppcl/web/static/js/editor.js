/* The editor pane: tabs, Command Assist, find and replace, gutter marks.
 *
 * The interactions mirror the Desigo CC PPCL Editor where it has one, so
 * muscle memory transfers -- Ctrl+G for go-to-statement, quick numbering on
 * Enter, Command Assist with the same filter categories, and the same red U
 * in the gutter for a point the database does not contain.
 */

import {
  $, ask, clear, debounce, el, esc, fmt, post, status, toast,
} from './core.js';
import { highlight, renderProgram } from './highlight.js';

const LINE_HEIGHT = 20;

export const editor = {
  meta: null,
  settings: {},
  tabs: [],
  active: -1,
  diagnostics: [],
  unresolved: [],
  onBench: null,
  onFirmwareChange: null,

  /* ---------------------------------------------------------- lifecycle */

  init(context) {
    this.meta = context.meta;
    this.settings = context.settings || {};
    this.code = $('code');
    this.hl = $('hl');
    this.gutter = $('gutter');

    const relint = debounce(() => this.lint(), this.settings.lint_delay || 220);
    this.code.addEventListener('input', () => {
      this.touch();
      this.paint();
      relint();
      this.assist();
    });
    this.code.addEventListener('scroll', () => {
      this.hl.style.transform = 'translate(' + -this.code.scrollLeft + 'px,0)';
    });
    this.code.addEventListener('keydown', (e) => this.onKey(e));
    this.code.addEventListener('blur', () => setTimeout(() => this.hideAssist(), 160));
    this.code.addEventListener('click', () => this.assist());

    this.initFind();
    this.newTab('untitled.ppcl', '');
  },

  /* -------------------------------------------------------------- tabs */

  newTab(path, text) {
    const existing = this.tabs.findIndex((t) => t.path === path);
    if (existing >= 0) { this.select(existing); return existing; }
    this.tabs.push({ path, text, saved: text, scroll: 0, caret: 0 });
    this.renderTabs();
    this.select(this.tabs.length - 1);
    return this.tabs.length - 1;
  },

  get tab() { return this.tabs[this.active]; },

  select(index) {
    if (index < 0 || index >= this.tabs.length) return;
    if (this.active >= 0 && this.tabs[this.active]) {
      const previous = this.tabs[this.active];
      previous.text = this.code.value;
      previous.caret = this.code.selectionStart;
      previous.scroll = $('editwrap').scrollTop;
    }
    this.active = index;
    const tab = this.tabs[index];
    this.code.value = tab.text;
    this.paint();
    this.renderTabs();
    this.lint();
    setTimeout(() => {
      this.code.setSelectionRange(tab.caret || 0, tab.caret || 0);
      $('editwrap').scrollTop = tab.scroll || 0;
    }, 0);
  },

  close(index) {
    const tab = this.tabs[index];
    if (tab && tab.text !== tab.saved) {
      if (!window.confirm(tab.path + ' has unsaved changes. Close it anyway?')) {
        return;
      }
    }
    this.tabs.splice(index, 1);
    if (!this.tabs.length) { this.newTab('untitled.ppcl', ''); return; }
    this.select(Math.min(index, this.tabs.length - 1));
  },

  renderTabs() {
    const node = $('tabs');
    clear(node);
    this.tabs.forEach((tab, index) => {
      const row = el('div', 'tab' + (index === this.active ? ' on' : ''));
      row.setAttribute('data-tip', tab.path);
      row.appendChild(el('span', '', tab.path.split('/').pop()));
      if (tab.text !== tab.saved) row.appendChild(el('span', 'dirty', '●'));
      const x = el('span', 'x', '×');
      x.onclick = (e) => { e.stopPropagation(); this.close(index); };
      row.appendChild(x);
      row.onclick = () => this.select(index);
      node.appendChild(row);
    });
  },

  touch() {
    if (this.tab) this.tab.text = this.code.value;
    this.renderTabs();
  },

  markSaved(path) {
    if (!this.tab) return;
    this.tab.path = path || this.tab.path;
    this.tab.saved = this.code.value;
    this.tab.text = this.code.value;
    this.renderTabs();
  },

  /* ------------------------------------------------------------ content */

  get() { return this.code.value; },

  set(text, path) {
    if (path) {
      this.newTab(path, text);
      this.code.value = text;
      if (this.tab) { this.tab.text = text; this.tab.saved = text; }
    } else {
      this.code.value = text;
      this.touch();
    }
    this.paint();
    this.lint();
  },

  paint() {
    const text = this.code.value;
    this.hl.innerHTML = highlight(text, {
      mark: this.find && this.find.re ? { test: this.find.re } : null,
    }) + '\n';
    const count = text.split('\n').length;
    if (this.gutter.childElementCount !== count) {
      clear(this.gutter);
      for (let i = 1; i <= count; i++) {
        const row = el('div', '', String(i));
        row.onclick = () => this.toggleBreakpointAt(i);
        this.gutter.appendChild(row);
      }
    }
    const height = Math.max(count * LINE_HEIGHT + 20, 200);
    this.code.style.height = height + 'px';
    this.hl.style.height = height + 'px';
    this.gutter.style.height = height + 'px';
    this.applyMarks();
  },

  /* --------------------------------------------------------------- lint */

  async lint() {
    const text = this.code.value;
    const body = {
      text,
      firmware: $('firmware').value,
      disabled: this.settings.disabled_rules || [],
    };
    const r = await post('/api/lint', body);
    if (r.error) { status(r.error, 'e'); return; }
    this.diagnostics = r.diagnostics;
    this.stats = r.stats;
    this.renderDiags();
    this.showCounts(r.counts, r.stats);
    this.applyMarks();
    this.checkPoints(text);
  },

  async checkPoints(text) {
    const r = await post('/api/points/check', { text });
    this.unresolved = (r && r.loaded && r.unresolved) ? r.unresolved : [];
    this.applyMarks();
  },

  applyMarks() {
    const rows = this.gutter.children;
    for (let i = 0; i < rows.length; i++) rows[i].className = '';
    const sourceLineOf = {};
    this.code.value.split('\n').forEach((line, i) => {
      const m = line.match(/^\s*(\d+)[ \t]/);
      if (m) sourceLineOf[parseInt(m[1], 10)] = i + 1;
    });
    this.diagnostics.forEach((d) => {
      const at = d.source_line || sourceLineOf[d.line];
      const row = rows[at - 1];
      if (!row) return;
      if (d.severity === 'error') row.classList.add('e');
      else if (d.severity === 'warning' && !row.classList.contains('e')) {
        row.classList.add('w');
      }
    });
    this.unresolved.forEach((u) => {
      u.lines.forEach((n) => {
        const row = rows[sourceLineOf[n] - 1];
        if (row) row.classList.add('u');
      });
    });
    (this.breakpoints || []).forEach((n) => {
      const row = rows[sourceLineOf[n] - 1];
      if (row) row.classList.add('bp');
    });
  },

  renderDiags() {
    renderDiagnostics($('diags'), this.diagnostics, (line) => this.goto(line));
    if (this.unresolved.length) {
      const node = $('diags');
      const first = node.firstChild;
      this.unresolved.slice(0, 12).forEach((u) => {
        const row = el('div', 'diag warning');
        row.appendChild(el('span', 'code', 'U'));
        row.appendChild(el('span', 'ln', String(u.lines[0])));
        const msg = el('div', 'msg');
        msg.appendChild(document.createTextNode(
          u.name + ' is not in the loaded point database'));
        msg.appendChild(el('small', '',
          'Referenced at line' + (u.lines.length > 1 ? 's ' : ' ') +
          u.lines.join(', ') +
          '. The panel marks these with a red U and will not resolve them.'));
        row.appendChild(msg);
        row.onclick = () => this.goto(u.lines[0], true);
        node.insertBefore(row, first);
      });
    }
  },

  showCounts(counts, stats) {
    const bits = [];
    if (counts.error) bits.push('<span class="e">' + counts.error + ' error</span>');
    if (counts.warning) bits.push('<span class="w">' + counts.warning + ' warning</span>');
    if (this.unresolved.length) {
      bits.push('<span class="w">' + this.unresolved.length + ' unresolved</span>');
    }
    if (!counts.error && !counts.warning && !this.unresolved.length) {
      bits.push('<span class="ok">clean</span>');
    }
    if (stats) {
      bits.push('<b>' + stats.executable + '</b> exec lines');
      bits.push('<b>' + stats.steady_state + '</b> in main loop');
      if (stats.one_shot) bits.push('<b>' + stats.one_shot + '</b> run once');
    }
    $('status').innerHTML = bits.join(' &middot; ');
  },

  /* ----------------------------------------------------------- movement */

  /** Jump to a source line, or to a PPCL line number when `byNumber`. */
  goto(target, byNumber) {
    if (!target) return;
    const lines = this.code.value.split('\n');
    let index = target - 1;
    if (byNumber) {
      index = lines.findIndex((line) => {
        const m = line.match(/^\s*(\d+)[ \t]/);
        return m && parseInt(m[1], 10) === Number(target);
      });
      if (index < 0) { toast('no line ' + target + ' in this program', 'bad'); return; }
    }
    let pos = 0;
    for (let i = 0; i < index && i < lines.length; i++) pos += lines[i].length + 1;
    this.code.focus();
    this.code.setSelectionRange(pos, pos + (lines[index] || '').length);
    $('editwrap').scrollTop = Math.max(0, (index - 6) * LINE_HEIGHT);
  },

  async gotoDialog() {
    const answer = await ask('Go to program statement', [{
      name: 'line', label: 'Line number', type: 'number',
      doc: 'The PPCL statement number, not the position in the file.',
    }], 'Go');
    if (answer && answer.line) this.goto(Number(answer.line), true);
  },

  caretLine() {
    const before = this.code.value.slice(0, this.code.selectionStart);
    return before.split('\n').length;
  },

  /** PPCL line numbers covered by the selection, for the transforms. */
  selectedLineNumbers() {
    const value = this.code.value;
    const from = value.slice(0, this.code.selectionStart).split('\n').length;
    const to = value.slice(0, this.code.selectionEnd).split('\n').length;
    const out = [];
    value.split('\n').forEach((line, i) => {
      if (i + 1 < from || i + 1 > to) return;
      const m = line.match(/^\s*(\d+)[ \t]/);
      if (m) out.push(parseInt(m[1], 10));
    });
    return out;
  },

  toggleBreakpointAt(sourceLine) {
    const line = this.code.value.split('\n')[sourceLine - 1] || '';
    const m = line.match(/^\s*(\d+)[ \t]/);
    if (!m) return;
    const number = parseInt(m[1], 10);
    this.breakpoints = this.breakpoints || [];
    const at = this.breakpoints.indexOf(number);
    if (at >= 0) this.breakpoints.splice(at, 1);
    else this.breakpoints.push(number);
    this.applyMarks();
  },

  /* ---------------------------------------------------------- keyboard */

  onKey(e) {
    if (this.completeOpen && this.onCompleteKey(e)) return;

    if (e.key === 'Tab') {
      e.preventDefault();
      this.insert('\t');
      return;
    }
    if (e.key === 'Enter' && this.settings.quick_numbering !== false) {
      const start = this.code.selectionStart;
      const before = this.code.value.slice(0, start);
      const line = before.slice(before.lastIndexOf('\n') + 1);
      const m = line.match(/^\s*(\d+)[ \t]/);
      const atEnd = this.code.value[start] === '\n' ||
                    start === this.code.value.length;
      if (m && atEnd) {
        e.preventDefault();
        const step = Number(this.settings.line_step || 10);
        const next = parseInt(m[1], 10) + step;
        this.insert('\n' + String(next).padStart(5, '0') + '\t');
        this.lint();
      }
    }
  },

  insert(text) {
    const start = this.code.selectionStart;
    const end = this.code.selectionEnd;
    const value = this.code.value;
    this.code.value = value.slice(0, start) + text + value.slice(end);
    this.code.selectionStart = this.code.selectionEnd = start + text.length;
    this.touch();
    this.paint();
  },

  /* ----------------------------------------------- Command Assist */

  async assist() {
    if (this.settings.autocomplete === false) return;
    const offset = this.code.selectionStart;
    if (this.code.selectionEnd !== offset) { this.hideAssist(); return; }
    const r = await post('/api/complete', { text: this.code.value, offset });
    if (r.error) return;
    this.signature(r.signature);
    if (!r.word || r.word.length < 1 || !r.items.length) {
      this.hideComplete();
      return;
    }
    this.completeItems = r.items;
    this.completeFrom = r.replace_from;
    this.completeIndex = 0;
    this.renderComplete();
  },

  renderComplete() {
    const node = $('complete');
    clear(node);
    let category = null;
    this.completeItems.forEach((item, index) => {
      if (item.category && item.category !== category) {
        category = item.category;
        node.appendChild(el('div', 'cat', category));
      }
      const row = el('div', 'item' + (index === this.completeIndex ? ' on' : ''));
      row.setAttribute('data-kind', item.kind);
      row.appendChild(el('span', 'name', item.text));
      row.appendChild(el('span', 'kind', item.kind));
      row.appendChild(el('span', 'doc', item.detail || ''));
      row.onmousedown = (e) => { e.preventDefault(); this.accept(index); };
      node.appendChild(row);
    });
    this.placeAt(node);
    node.hidden = false;
    this.completeOpen = true;
  },

  placeAt(node) {
    const line = this.caretLine();
    const wrap = $('editwrap');
    node.style.left = '90px';
    node.style.top = (line * LINE_HEIGHT + 26 - wrap.scrollTop) + 'px';
  },

  onCompleteKey(e) {
    const keys = {
      ArrowDown: 1, ArrowUp: -1, PageDown: 6, PageUp: -6,
    };
    if (keys[e.key] !== undefined) {
      e.preventDefault();
      const count = this.completeItems.length;
      this.completeIndex =
        (this.completeIndex + keys[e.key] + count * 2) % count;
      this.renderComplete();
      return true;
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      this.accept(this.completeIndex);
      return true;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      this.hideComplete();
      return true;
    }
    return false;
  },

  accept(index) {
    const item = this.completeItems[index];
    if (!item) return;
    const value = this.code.value;
    const caret = this.code.selectionStart;
    const insert = item.insert || item.text;
    this.code.value = value.slice(0, this.completeFrom) + insert +
                      value.slice(caret);
    const at = this.completeFrom + insert.length;
    this.code.selectionStart = this.code.selectionEnd = at;
    this.hideComplete();
    this.touch();
    this.paint();
    this.lint();
    this.code.focus();
    setTimeout(() => this.assist(), 10);
  },

  hideComplete() {
    $('complete').hidden = true;
    this.completeOpen = false;
  },

  hideAssist() {
    this.hideComplete();
    $('sighelp').hidden = true;
  },

  /** The Command Attribute Editor equivalent: which argument you are on. */
  signature(sig) {
    const node = $('sighelp');
    if (!sig) { node.hidden = true; return; }
    clear(node);

    const params = sig.params || [];
    const parts = params.map((p, i) => {
      /* A repeating parameter is shown as pt1,...,ptN rather than as a bare
       * name, because "how many points can I list here" is the question the
       * signature is being read to answer. */
      const label = p.repeat && p.max
        ? p.name + '1,...,' + p.name + p.max
        : p.name;
      const at = i === sig.argument ||
        (sig.argument >= params.length && p.repeat &&
         i === params.length - 1);
      return at ? '<span class="at">' + esc(label) + '</span>' : esc(label);
    });
    const line = el('div', 'sig');
    line.innerHTML = esc(sig.name) + '(' + parts.join(',') + ')';
    node.appendChild(line);
    node.appendChild(el('div', 'sum', sig.summary));

    const current = params[Math.min(sig.argument, params.length - 1)];
    if (current) {
      node.appendChild(el('div', 'arg', current.name + '  —  ' + current.kind));
      if (current.doc) node.appendChild(el('div', 'argdoc', current.doc));
      if (current.choices && current.choices.length) {
        node.appendChild(el('div', 'argdoc',
          'one of: ' + current.choices.join(', ')));
      }
    }
    if (sig.priority_arg) {
      node.appendChild(el('div', 'argdoc',
        'Accepts a leading @priority. Release at the same priority or the ' +
        'release does nothing.'));
    }
    if (sig.time_based) {
      node.appendChild(el('div', 'flag',
        'Time-based: must be evaluated on every program pass.'));
    }
    if (sig.subroutine_safe === false) {
      node.appendChild(el('div', 'flag', 'Not allowed inside a GOSUB.'));
    }
    if (sig.if_target_safe === false) {
      node.appendChild(el('div', 'flag', 'Not allowed as an IF action.'));
    }
    if (sig.signature_known === false) {
      node.appendChild(el('div', 'flag',
        'Signature not published; treat with care.'));
    }

    const wrap = $('editwrap');
    node.style.left = '90px';
    node.style.top = (this.caretLine() * LINE_HEIGHT + 26 - wrap.scrollTop) + 'px';
    node.hidden = false;
  },

  /* -------------------------------------------------------------- find */

  initFind() {
    this.find = { re: null, matches: [], index: 0 };
    const run = () => this.runFind();
    $('find-q').addEventListener('input', debounce(run, 120));
    $('find-case').addEventListener('change', run);
    $('find-next').onclick = () => this.step(1);
    $('find-prev').onclick = () => this.step(-1);
    $('find-rep').onclick = () => this.replaceOne();
    $('find-all').onclick = () => this.replaceAll();
    $('find-close').onclick = () => this.closeFind();
    $('find-q').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); this.step(e.shiftKey ? -1 : 1); }
      if (e.key === 'Escape') { e.preventDefault(); this.closeFind(); }
    });
    $('find-r').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); this.replaceOne(); }
      if (e.key === 'Escape') { e.preventDefault(); this.closeFind(); }
    });
  },

  openFind() {
    $('findbar').hidden = false;
    const selection = this.code.value
      .slice(this.code.selectionStart, this.code.selectionEnd);
    if (selection && selection.indexOf('\n') === -1) $('find-q').value = selection;
    $('find-q').focus();
    $('find-q').select();
    this.runFind();
  },

  closeFind() {
    $('findbar').hidden = true;
    this.find.re = null;
    this.paint();
    this.code.focus();
  },

  runFind() {
    const query = $('find-q').value;
    if (!query) {
      this.find.re = null;
      this.find.matches = [];
      $('find-count').textContent = '';
      this.paint();
      return;
    }
    const flags = 'g' + ($('find-case').checked ? '' : 'i');
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    this.find.re = new RegExp(escaped, flags);
    this.find.matches = [];
    let m;
    this.find.re.lastIndex = 0;
    while ((m = this.find.re.exec(this.code.value)) !== null) {
      this.find.matches.push(m.index);
      if (m[0] === '') this.find.re.lastIndex++;
    }
    this.find.index = 0;
    $('find-count').textContent = this.find.matches.length
      ? '1 of ' + this.find.matches.length
      : 'no matches';
    this.paint();
    if (this.find.matches.length) this.showMatch();
  },

  step(direction) {
    if (!this.find.matches.length) { this.runFind(); return; }
    const count = this.find.matches.length;
    this.find.index = (this.find.index + direction + count) % count;
    $('find-count').textContent = (this.find.index + 1) + ' of ' + count;
    this.showMatch();
  },

  showMatch() {
    const at = this.find.matches[this.find.index];
    const length = $('find-q').value.length;
    this.code.focus();
    this.code.setSelectionRange(at, at + length);
    const line = this.code.value.slice(0, at).split('\n').length;
    $('editwrap').scrollTop = Math.max(0, (line - 8) * LINE_HEIGHT);
  },

  replaceOne() {
    if (!this.find.matches.length) return;
    const at = this.find.matches[this.find.index];
    const length = $('find-q').value.length;
    const value = this.code.value;
    this.code.value = value.slice(0, at) + $('find-r').value +
                      value.slice(at + length);
    this.touch();
    this.paint();
    this.lint();
    this.runFind();
  },

  replaceAll() {
    if (!this.find.re) return;
    const before = this.find.matches.length;
    if (!before) return;
    this.code.value = this.code.value.replace(
      new RegExp(this.find.re.source, this.find.re.flags),
      $('find-r').value,
    );
    this.touch();
    this.paint();
    this.lint();
    this.runFind();
    toast('replaced ' + before + ' occurrence' + (before === 1 ? '' : 's'), 'good');
  },

  /* -------------------------------------------------------- transforms */

  async transform(op) {
    const body = { op, text: this.code.value };
    if (op === 'disable' || op === 'enable' || op === 'toggle_comment') {
      body.lines = this.selectedLineNumbers();
      if (!body.lines.length) {
        toast('select the statements first', 'bad');
        return;
      }
    }
    if (op === 'clone') {
      const answer = await ask('Clone lines with rename', [
        { name: 'first', label: 'First line', type: 'number',
          doc: 'The first PPCL statement number to copy.' },
        { name: 'last', label: 'Last line', type: 'number' },
        { name: 'start', label: 'Copy starts at line', type: 'number',
          doc: 'Leave blank to place the copy after the end of the program.' },
        { name: 'renames', label: 'Renames', type: 'textarea',
          placeholder: 'AHU1.SFAN = AHU2.SFAN',
          doc: 'One per line, OLD = NEW. The manual is explicit that reused ' +
               'code must have its points renamed, because each point name ' +
               'has to be unique.' },
      ], 'Clone');
      if (!answer) return;
      body.first = Number(answer.first);
      body.last = Number(answer.last);
      if (answer.start) body.start = Number(answer.start);
      body.renames = {};
      String(answer.renames || '').split('\n').forEach((line) => {
        const at = line.indexOf('=');
        if (at > 0) {
          body.renames[line.slice(0, at).trim()] = line.slice(at + 1).trim();
        }
      });
    }

    const r = await post('/api/transform', body);
    if (r.error || !r.ok) { toast(r.error || 'the transform failed', 'bad'); return; }
    if (r.changed) {
      this.code.value = r.text;
      this.touch();
      this.paint();
      this.lint();
    }
    (r.notes || []).forEach((note) => toast(note, r.changed ? 'good' : null));
    (r.warnings || []).forEach((note) => toast(note, 'bad'));
    if (!r.changed && !(r.warnings || []).length) toast('nothing changed');
  },
};

/* ------------------------------------------------------- shared render */

export function renderDiagnostics(node, diags, onClick) {
  clear(node);
  if (!diags || !diags.length) {
    node.appendChild(el('div', 'empty', 'No findings.'));
    return;
  }
  diags.forEach((d) => {
    const row = el('div', 'diag ' + d.severity);
    row.appendChild(el('span', 'code', d.code));
    row.appendChild(el('span', 'ln', d.line === null ? '' : String(d.line)));
    const msg = el('div', 'msg');
    msg.appendChild(document.createTextNode(d.message));
    const extra = [
      d.detail,
      d.suggestion ? 'Fix: ' + d.suggestion : '',
      d.manual ? 'Manual: ' + d.manual : '',
    ].filter(Boolean).join('   ');
    if (extra) msg.appendChild(el('small', '', extra));
    row.appendChild(msg);
    if (onClick) row.onclick = () => onClick(d.source_line || d.line);
    node.appendChild(row);
  });
}
