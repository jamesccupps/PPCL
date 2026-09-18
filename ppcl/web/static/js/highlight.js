/* PPCL syntax colouring.
 *
 * Deliberately approximate. The authoritative answer always comes back from
 * the linter; this only has to make a program readable at a glance, and it
 * has to be fast enough to run on every keystroke.
 */

import { esc } from './core.js';

let META = null;
export function setMeta(meta) { META = meta; }

const DISABLED = 'C [DISABLED] ';

export function highlight(text, options) {
  const opts = options || {};
  const lines = text.split('\n');
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    let html = highlightLine(lines[i], opts);
    if (opts.mark && opts.mark.test) {
      html = markMatches(lines[i], html, opts);
    }
    if (opts.current && opts.currentLine === lineNumberOf(lines[i])) {
      html = '<span class="cur">' + html + '</span>';
    }
    out.push(html);
  }
  return out.join('\n');
}

function lineNumberOf(line) {
  const m = line.match(/^\s*(\d+)[ \t]/);
  return m ? parseInt(m[1], 10) : null;
}

/* Matches are painted by re-rendering the raw line with <mark> spans, because
 * splicing them into already-generated HTML would corrupt the tags. A line
 * with a hit therefore loses its colouring, which is the right trade: while
 * you are searching, the thing you want to see is the hit. */
function markMatches(raw, html, opts) {
  const re = opts.mark.test;
  re.lastIndex = 0;
  if (!re.test(raw)) return html;
  re.lastIndex = 0;
  let out = '';
  let last = 0;
  let m;
  while ((m = re.exec(raw)) !== null) {
    if (m[0] === '') { re.lastIndex++; continue; }
    out += esc(raw.slice(last, m.index));
    out += '<span class="hit">' + esc(m[0]) + '</span>';
    last = m.index + m[0].length;
  }
  out += esc(raw.slice(last));
  return out;
}

export function highlightLine(line) {
  const m = line.match(/^(\s*)(\d+)([ \t]+)([\s\S]*)$/);
  if (!m) return esc(line);
  const head = m[1] + '<span class="t-num">' + esc(m[2]) + '</span>' + esc(m[3]);
  const body = m[4];

  if (body.startsWith(DISABLED)) {
    return head + '<span class="t-dis">' + esc(body) + '</span>';
  }
  if (/^[Cc](\s|$)/.test(body)) {
    return head + '<span class="t-com">' + esc(body) + '</span>';
  }

  let out = '';
  let i = 0;
  while (i < body.length) {
    const ch = body[i];

    if (ch === '"') {
      let end = body.indexOf('"', i + 1);
      if (end === -1) end = body.length - 1;
      out += '<span class="t-str">' + esc(body.slice(i, end + 1)) + '</span>';
      i = end + 1;
      continue;
    }
    if (ch === '%') {
      const macro = /^%[A-Za-z0-9_.]+%/.exec(body.slice(i));
      if (macro) {
        out += '<span class="t-pri">' + esc(macro[0]) + '</span>';
        i += macro[0].length;
        continue;
      }
    }
    if (ch === '@') {
      const at = /^@[A-Za-z0-9_.]+/.exec(body.slice(i));
      if (at) {
        out += '<span class="t-pri">' + esc(at[0]) + '</span>';
        i += at[0].length;
        continue;
      }
    }
    if (ch === '.') {
      const op = /^\.(EQ|NE|GT|GE|LT|LE|AND|NAND|OR|XOR|ROOT)\./i
        .exec(body.slice(i));
      if (op) {
        out += '<span class="t-op">' + esc(op[0]) + '</span>';
        i += op[0].length;
        continue;
      }
    }

    const word = /^[A-Za-z_$][A-Za-z0-9_$]*(?::[A-Za-z0-9_$.]+)?/
      .exec(body.slice(i));
    if (word) {
      const w = word[0];
      const upper = w.toUpperCase();
      let cls = '';
      if (upper === 'IF' || upper === 'THEN' || upper === 'ELSE') cls = 't-cmd';
      else if (META && META.commands.indexOf(upper) !== -1) cls = 't-cmd';
      else if (META && META.functions.indexOf(upper) !== -1) cls = 't-cmd';
      else if (META && META.status_indicators.indexOf(upper) !== -1) cls = 't-val';
      else if (META && META.resident_points.indexOf(upper) !== -1) cls = 't-val';
      out += cls ? '<span class="' + cls + '">' + esc(w) + '</span>' : esc(w);
      i += w.length;
      continue;
    }

    const num = /^\d+(\.\d+)?(:\d+)?/.exec(body.slice(i));
    if (num) {
      out += '<span class="t-val">' + esc(num[0]) + '</span>';
      i += num[0].length;
      continue;
    }

    out += esc(ch);
    i++;
  }
  return head + out;
}

/** Paint a read-only program into a <pre> with its own gutter. */
export function renderProgram(hlNode, gutterNode, text, marks, onGutterClick) {
  hlNode.innerHTML = highlight(text) + '\n';
  const lines = text.split('\n');
  gutterNode.innerHTML = '';
  for (let i = 0; i < lines.length; i++) {
    const number = lineNumberOf(lines[i]);
    const mark = marks ? marks[number] : '';
    const row = document.createElement('div');
    row.className = mark || '';
    row.textContent = number === null ? '' : String(number);
    if (number !== null && onGutterClick) {
      row.onclick = () => onGutterClick(number);
    }
    gutterNode.appendChild(row);
  }
  const height = Math.max(lines.length * 20 + 20, 200);
  hlNode.style.height = height + 'px';
  gutterNode.style.height = height + 'px';
}
