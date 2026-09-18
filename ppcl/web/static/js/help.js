/* The Help pane, and a markdown subset good enough for it.
 *
 * A real markdown library would be a dependency, and this project has none by
 * design. The subset here -- headings, paragraphs, lists, fenced code, tables,
 * blockquotes, inline code and bold -- is exactly what the help pages use, and
 * the renderer escapes first and marks up second so a page can never inject
 * markup.
 */

import { $, clear, debounce, el, esc, post } from './core.js';

export const help = {
  contents: [],
  onNavigate: null,

  async init() {
    const r = await post('/api/help', {});
    this.contents = r.contents || [];
    this.renderNav();
    $('helpq').addEventListener('input', debounce((e) => {
      this.search(e.target.value);
    }, 200));
    await this.open('start');
  },

  renderNav(results) {
    const node = $('helpnav');
    clear(node);
    if (results) {
      if (!results.length) {
        node.appendChild(el('div', 'empty', 'Nothing found.'));
        return;
      }
      results.forEach((hit) => {
        const row = el('div', 'row');
        row.appendChild(document.createTextNode(hit.title));
        row.setAttribute('data-tip', hit.excerpt);
        row.onclick = () => this.open(hit.id);
        node.appendChild(row);
      });
      return;
    }
    this.contents.forEach((group) => {
      node.appendChild(el('div', 'pgroup', group.category));
      group.pages.forEach((page) => {
        const row = el('div', 'row');
        row.appendChild(document.createTextNode(page.title));
        row.dataset.id = page.id;
        row.setAttribute('data-tip', page.summary);
        row.onclick = () => this.open(page.id);
        node.appendChild(row);
      });
    });
  },

  async search(query) {
    if (!String(query || '').trim()) { this.renderNav(); return; }
    const r = await post('/api/help', { query });
    this.renderNav(r.results || []);
  },

  async open(topic) {
    const r = await post('/api/help', { topic });
    const node = $('helpbody');
    clear(node);
    if (r.error || !r.page) {
      node.appendChild(el('div', 'empty', r.error || 'not found'));
      return;
    }
    node.innerHTML = markdown(r.page.body);
    if (r.page.see_also.length) {
      const also = el('div', 'seealso');
      also.appendChild(el('span', '', 'See also:  '));
      r.page.see_also.forEach((ref) => {
        const link = el('a', '', ref.title);
        link.onclick = () => this.open(ref.id);
        also.appendChild(link);
      });
      node.appendChild(also);
    }
    node.scrollTop = 0;
    Array.from($('helpnav').children).forEach((row) => {
      row.classList.toggle('on', row.dataset.id === topic);
    });
    if (this.onNavigate) this.onNavigate(topic);
  },
};

/* --------------------------------------------------------- markdown */

export function markdown(text) {
  const lines = String(text).split('\n');
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (/^```/.test(line)) {
      const body = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push('<pre><code>' + esc(body.join('\n')) + '</code></pre>');
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      out.push('<h' + level + '>' + inline(heading[2]) + '</h' + level + '>');
      i++;
      continue;
    }

    if (/^\s*\|/.test(line) && i + 1 < lines.length &&
        /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      out.push(table(rows));
      continue;
    }

    if (/^\s*>/.test(line)) {
      const body = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        body.push(lines[i++].replace(/^\s*>\s?/, ''));
      }
      out.push('<blockquote>' + inline(body.join(' ')) + '</blockquote>');
      continue;
    }

    if (/^\s*([*-]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s/.test(line);
      const items = [];
      while (i < lines.length && /^\s*([*-]|\d+\.)\s+/.test(lines[i])) {
        let item = lines[i++].replace(/^\s*([*-]|\d+\.)\s+/, '');
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) &&
               !/^\s*([*-]|\d+\.)\s+/.test(lines[i])) {
          item += ' ' + lines[i++].trim();
        }
        items.push('<li>' + inline(item) + '</li>');
      }
      const tag = ordered ? 'ol' : 'ul';
      out.push('<' + tag + '>' + items.join('') + '</' + tag + '>');
      continue;
    }

    if (!line.trim()) { i++; continue; }

    const paragraph = [];
    while (i < lines.length && lines[i].trim() &&
           !/^(#{1,4}\s|```|\s*[*-]\s|\s*\d+\.\s|\s*>|\s*\|)/.test(lines[i])) {
      paragraph.push(lines[i++]);
    }
    out.push('<p>' + inline(paragraph.join(' ')) + '</p>');
  }
  return out.join('\n');
}

function table(rows) {
  const cells = (row) => row.trim().replace(/^\||\|$/g, '')
    .split('|').map((c) => c.trim());
  const head = cells(rows[0]);
  const body = rows.slice(2).map(cells);
  let html = '<table><tr>';
  head.forEach((c) => { html += '<th>' + inline(c) + '</th>'; });
  html += '</tr>';
  body.forEach((row) => {
    html += '<tr>';
    row.forEach((c) => { html += '<td>' + inline(c) + '</td>'; });
    html += '</tr>';
  });
  return html + '</table>';
}

/* Escape first, then apply markup, so nothing in a page can produce a tag. */
function inline(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
}
