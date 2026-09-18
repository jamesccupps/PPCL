/* The Settings pane.
 *
 * The form is generated from the schema the server publishes, so a setting
 * added in Python appears here with its help text and its range without any
 * change to this file. Validation is done by the server as well -- a value
 * out of range comes back rejected with the limit named, rather than being
 * quietly clamped.
 */

import { $, clear, el, post, toast } from './core.js';

export const settingsPane = {
  values: {},
  schema: [],
  onChange: null,

  async init() {
    $('set-save').onclick = () => this.save();
    $('set-reset').onclick = () => this.restore();
    await this.load();
    return this.values;
  },

  async load() {
    const r = await post('/api/settings', {});
    if (r.error) { toast(r.error, 'bad'); return {}; }
    this.values = r.values;
    this.schema = r.schema;
    this.groups = r.groups;
    this.path = r.path;
    (r.problems || []).forEach((p) => toast(p, 'bad'));
    this.render();
    return this.values;
  },

  render() {
    const node = $('settingsbody');
    clear(node);

    this.groups.forEach((group) => {
      const section = el('div', 'group');
      section.appendChild(el('h3', '', group));
      this.schema.filter((s) => s.group === group).forEach((setting) => {
        section.appendChild(this.row(setting));
      });
      if (group === 'Files') section.appendChild(this.databaseBox());
      node.appendChild(section);
    });

    node.appendChild(el('div', 'empty',
      'Settings are stored in ' + this.path + ', inside the workspace. ' +
      'Delete that file to start again.'));
  },

  row(setting) {
    const wrap = el('div', 'srow');
    const label = el('div', 'lab');
    label.appendChild(el('b', '', setting.label));
    if (setting.doc) label.appendChild(el('div', 'doc', setting.doc));
    wrap.appendChild(label);

    const holder = el('div');
    let input;
    if (setting.kind === 'bool') {
      input = el('input');
      input.type = 'checkbox';
      input.checked = !!setting.value;
      input.onchange = () => this.set(setting.key, input.checked);
    } else if (setting.kind === 'choice') {
      input = el('select');
      setting.choices.forEach((choice) => {
        const o = el('option', '', choice);
        o.value = choice;
        if (choice === setting.value) o.selected = true;
        input.appendChild(o);
      });
      input.onchange = () => this.set(setting.key, input.value);
    } else if (setting.kind === 'list') {
      input = el('input');
      input.type = 'text';
      input.value = (setting.value || []).join(', ');
      input.placeholder = 'S601, P707';
      input.onchange = () => this.set(setting.key, input.value);
    } else {
      input = el('input');
      input.type = setting.kind === 'text' ? 'text' : 'number';
      input.value = setting.value;
      if (setting.min !== null) input.min = setting.min;
      if (setting.max !== null) input.max = setting.max;
      input.onchange = () => this.set(setting.key, input.value);
    }
    if (setting.min !== null && setting.max !== null) {
      input.setAttribute('data-tip',
        'Between ' + setting.min + ' and ' + setting.max + '.');
    }
    holder.appendChild(input);
    wrap.appendChild(holder);
    return wrap;
  },

  set(key, value) {
    this.values[key] = value;
    if (this.onChange) this.onChange(key, value, this.values);
    $('set-status').innerHTML = '<span class="w">unsaved changes</span>';
  },

  async save() {
    const r = await post('/api/settings/save', { values: this.values });
    if (r.error || !r.ok) {
      (r.rejected || [r.error]).forEach((m) => toast(m, 'bad'));
      $('set-status').innerHTML = '<span class="e">not saved</span>';
      return;
    }
    this.values = r.values;
    $('set-status').innerHTML = '<span class="ok">saved</span>';
    toast('settings saved to ' + r.path, 'good');
    if (this.onChange) this.onChange(null, null, this.values);
  },

  async restore() {
    const r = await post('/api/settings/reset', {});
    if (r.error) { toast(r.error, 'bad'); return; }
    this.values = r.values;
    this.schema = r.schema;
    this.render();
    $('set-status').innerHTML = '<span class="w">defaults restored, not saved</span>';
    if (this.onChange) this.onChange(null, null, this.values);
  },

  /* ------------------------------------------------- point database */

  databaseBox() {
    const box = el('div', 'dbbox');
    box.appendChild(el('b', '', 'Point database'));
    box.appendChild(el('div', 'r',
      'Import a CSV or JSON export from Desigo CC or Insight. Loading one ' +
      'turns on unresolved-point marking in the editor gutter — the same red ' +
      'U the Desigo PPCL Editor shows — plus type-aware rules and real point ' +
      'names in Command Assist. Columns are matched by name, and anything ' +
      'not recognised is reported rather than guessed at.'));

    const bar = el('div', 'bar');
    const path = el('input');
    path.type = 'text';
    path.placeholder = 'points.csv';
    path.value = this.values.point_database || '';
    path.setAttribute('data-tip', 'A path inside the workspace directory.');
    bar.appendChild(path);

    const load = el('button', 'act primary', 'Import');
    load.onclick = async () => {
      const r = await post('/api/points/import', { path: path.value });
      if (r.error || !r.ok) { toast(r.error || 'import failed', 'bad'); return; }
      this.set('point_database', path.value);
      toast('imported ' + r.count + ' point(s) from ' + r.source, 'good');
      (r.problems || []).forEach((p) => toast(p, 'bad'));
      if (r.ignored_columns.length) {
        toast('columns not recognised, and ignored: ' +
              r.ignored_columns.join(', '), 'bad');
      }
      if (this.onChange) this.onChange('point_database', path.value, this.values);
    };
    bar.appendChild(load);

    const clearButton = el('button', 'act', 'Clear');
    clearButton.onclick = async () => {
      await post('/api/points/clear', {});
      this.set('point_database', '');
      toast('point database cleared');
      if (this.onChange) this.onChange('point_database', '', this.values);
    };
    bar.appendChild(clearButton);
    box.appendChild(bar);
    return box;
  },
};
