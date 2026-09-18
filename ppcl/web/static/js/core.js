/* Shared plumbing: DOM helpers, the API client, tooltips, toasts, modals.
 *
 * Everything that needs to be *correct* -- parsing, linting, compiling,
 * simulating -- happens in Python. This file only handles presentation.
 */

export const $ = (id) => document.getElementById(id);
export const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

export function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

export function debounce(fn, ms) {
  let t = null;
  return function (...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), ms);
  };
}

/* ------------------------------------------------------------------ api */

/** POST JSON. A transport failure resolves to `{error}` so callers have one
 *  shape to handle rather than two. */
export async function post(path, body) {
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    return await r.json();
  } catch (err) {
    return { error: 'could not reach the workbench server: ' + err.message };
  }
}

export async function get(path) {
  try {
    return await (await fetch(path)).json();
  } catch (err) {
    return { error: 'could not reach the workbench server: ' + err.message };
  }
}

/* -------------------------------------------------------------- tooltips */

/* One listener on the document rather than one per control, so a tooltip
 * works on anything with data-tip including elements created later. */
let tipTimer = null;
let tipEnabled = true;

export function setTooltipsEnabled(on) {
  tipEnabled = !!on;
  if (!on) hideTip();
}

function showTip(target) {
  const text = target.getAttribute('data-tip');
  if (!text || !tipEnabled) return;
  const tip = $('tooltip');
  tip.innerHTML = text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`(.+?)`/g, '<code>$1</code>');
  tip.hidden = false;
  const r = target.getBoundingClientRect();
  const box = tip.getBoundingClientRect();
  let left = r.left;
  let top = r.bottom + 7;
  if (left + box.width > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - box.width - 8);
  }
  if (top + box.height > window.innerHeight - 8) top = r.top - box.height - 7;
  tip.style.left = left + 'px';
  tip.style.top = Math.max(8, top) + 'px';
}

function hideTip() {
  clearTimeout(tipTimer);
  const tip = $('tooltip');
  if (tip) tip.hidden = true;
}

export function initTooltips() {
  document.addEventListener('mouseover', (e) => {
    const target = e.target.closest('[data-tip]');
    if (!target) return;
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => showTip(target), 380);
  });
  document.addEventListener('mouseout', (e) => {
    if (e.target.closest('[data-tip]')) hideTip();
  });
  document.addEventListener('mousedown', hideTip);
  window.addEventListener('blur', hideTip);
}

/* ---------------------------------------------------------------- toasts */

export function toast(message, kind) {
  const node = el('div', 'toast' + (kind ? ' ' + kind : ''), message);
  $('toasts').appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .3s';
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 320);
  }, kind === 'bad' ? 6500 : 3200);
}

export function status(text, kind) {
  const node = $('status');
  if (!node) return;
  node.innerHTML = kind
    ? '<span class="' + kind + '">' + esc(text) + '</span>'
    : esc(text);
}

/* ---------------------------------------------------------------- modal */

/** A small form in a sheet. Resolves to an object of values, or null.
 *
 *  `fields` is a list of {name, label, type, value, choices, doc, placeholder}.
 *  Used instead of window.prompt so a request for several values -- a clone
 *  with renames, a conditional breakpoint -- can be asked for at once and
 *  explained while it is being asked.
 */
export function ask(title, fields, okLabel) {
  return new Promise((resolve) => {
    const modal = $('modal');
    $('modal-title').textContent = title;
    const body = $('modal-body');
    clear(body);

    const inputs = {};
    fields.forEach((f) => {
      const wrap = el('div', 'field');
      if (f.label) wrap.appendChild(el('label', '', f.label));
      let input;
      if (f.type === 'select') {
        input = el('select');
        (f.choices || []).forEach((c) => {
          const value = typeof c === 'string' ? c : c.value;
          const label = typeof c === 'string' ? c : c.label;
          const o = el('option', '', label);
          o.value = value;
          if (value === f.value) o.selected = true;
          input.appendChild(o);
        });
      } else if (f.type === 'textarea') {
        input = el('textarea');
        input.value = f.value == null ? '' : f.value;
      } else if (f.type === 'checkbox') {
        input = el('input');
        input.type = 'checkbox';
        input.checked = !!f.value;
      } else {
        input = el('input');
        input.type = f.type || 'text';
        input.value = f.value == null ? '' : f.value;
        if (f.placeholder) input.placeholder = f.placeholder;
      }
      inputs[f.name] = input;
      wrap.appendChild(input);
      if (f.doc) wrap.appendChild(el('div', 'doc', f.doc));
      body.appendChild(wrap);
    });

    $('modal-ok').textContent = okLabel || 'OK';
    modal.hidden = false;
    const first = Object.values(inputs)[0];
    if (first) setTimeout(() => first.focus(), 30);

    function close(result) {
      modal.hidden = true;
      document.removeEventListener('keydown', onKey, true);
      $('modal-ok').onclick = null;
      $('modal-cancel').onclick = null;
      resolve(result);
    }
    function collect() {
      const out = {};
      fields.forEach((f) => {
        const input = inputs[f.name];
        out[f.name] = f.type === 'checkbox' ? input.checked : input.value;
      });
      return out;
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); close(null); }
      if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') {
        e.preventDefault();
        close(collect());
      }
    }
    document.addEventListener('keydown', onKey, true);
    $('modal-ok').onclick = () => close(collect());
    $('modal-cancel').onclick = () => close(null);
  });
}

export function confirmAsk(title, message, okLabel) {
  return ask(title, [{ name: '_', label: '', type: 'hidden', doc: message }],
             okLabel || 'Yes').then((r) => r !== null);
}

/* --------------------------------------------------------------- misc */

export function fillSelect(node, values, chosen) {
  clear(node);
  values.forEach((v) => {
    const value = typeof v === 'string' ? v : v.value;
    const label = typeof v === 'string' ? v : v.label;
    const o = el('option', '', label);
    o.value = value;
    if (value === chosen) o.selected = true;
    node.appendChild(o);
  });
}

/** Attach a dropdown menu's open/close behaviour. */
export function initMenu(node) {
  const trigger = node.querySelector('button');
  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const wasOpen = node.classList.contains('open');
    $$('.menu.open').forEach((m) => m.classList.remove('open'));
    node.classList.toggle('open', !wasOpen);
  });
  document.addEventListener('click', () => node.classList.remove('open'));
}

export function fmt(value, places) {
  const n = Number(value);
  if (!isFinite(n)) return String(value);
  return n.toFixed(places === undefined ? 1 : places);
}
