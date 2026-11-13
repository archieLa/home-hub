'use strict';
const $ = selector => document.querySelector(selector);
let entries = [], health = {}, editing = null, deleting = null, iconImage = '';
const form = $('#form');
async function api(path, method = 'GET', body) {
  const response = await fetch(path, {method, headers: {'Content-Type': 'application/json', 'X-HomeHub': '1'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}
function node(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function appIcon(entry) {
  const wrapper = node('span', 'app-icon', entry.iconImage ? '' : entry.icon);
  if (entry.iconImage) {
    const image = document.createElement('img'); image.src = entry.iconImage; image.alt = '';
    wrapper.append(image);
  }
  return wrapper;
}
function previewIcon() {
  $('#icon-preview-row').hidden = !iconImage;
  if (iconImage) $('#icon-preview').src = iconImage;
  else $('#icon-preview').removeAttribute('src');
}
$('#icon-file').onchange = async event => {
  const file = event.target.files[0];
  if (!file) return;
  const button = form.querySelector('[type=submit]'); button.disabled = true;
  $('#form-error').textContent = '';
  try {
    if (file.size > 131072) throw new Error('Choose a PNG image up to 128 KB.');
    const value = await new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('Could not read this image.')); reader.readAsDataURL(file);
    });
    if (!value.startsWith('data:image/png;base64,')) throw new Error('Choose a PNG image.');
    const image = new Image(); image.src = value;
    await image.decode();
    if (image.width > 1024 || image.height > 1024) throw new Error('Icon dimensions must be at most 1024 × 1024 pixels.');
    iconImage = value; previewIcon();
  } catch (error) { $('#form-error').textContent = error.message || 'Could not load this image.'; }
  finally { event.target.value = ''; button.disabled = false; }
};
$('#remove-icon').onclick = () => { iconImage = ''; previewIcon(); };
function statusNode(entry) {
  const state = health[entry.id] || 'Unknown';
  const status = node('span', `status ${state.toLowerCase()}`, state);
  status.dataset.entryId = entry.id;
  status.title = entry.healthUrl ? 'Reachability from the HomeHub server' : 'No health endpoint configured';
  return status;
}
function render() {
  const query = $('#search').value.toLowerCase(), category = $('#category').value;
  const visible = entries.filter(e => (!category || e.category === category) && `${e.name} ${e.description} ${e.category}`.toLowerCase().includes(query));
  $('#cards').replaceChildren();
  $('#local-cards').replaceChildren(); $('#external-cards').replaceChildren();
  $('#empty').hidden = entries.length > 0;
  document.querySelectorAll('.place-section').forEach(section => { section.hidden = !entries.length; });
  $('#count').textContent = `${visible.length} ${visible.length === 1 ? 'place' : 'places'}`;
  if (!visible.length) $('#cards').append(node('p', 'hint', entries.length ? 'No places match your search.' : 'No places yet. Add your first app or website above.'));
  entries.forEach(entry => {
    const link = node('a', 'destination'); link.href = entry.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    const top = node('div', 'card-top'); top.append(appIcon(entry), node('span', 'launch-arrow', '↗'));
    link.append(top, node('h3', '', entry.name), node('p', 'description', entry.description));
    const bottom = node('div', 'card-bottom'); bottom.append(node('span', 'category-label', entry.category));
    if (entry.kind === 'local') bottom.append(statusNode(entry));
    link.append(bottom);
    $(entry.kind === 'local' ? '#local-cards' : '#external-cards').append(link);
    if (!visible.includes(entry)) return;
    const row = node('article', 'settings-row');
    const summary = node('div', 'entry-summary');
    const details = node('div', 'entry-details'); details.append(node('h2', '', entry.name), node('p', '', entry.url));
    summary.append(appIcon(entry), details);
    const controls = node('div', 'controls');
    const edit = node('button', 'secondary', 'Edit'); edit.setAttribute('aria-label', `Edit ${entry.name}`); edit.onclick = () => open(entry);
    const remove = node('button', 'quiet', 'Remove'); remove.setAttribute('aria-label', `Remove ${entry.name}`); remove.onclick = () => { deleting = entry.id; $('#delete-dialog').showModal(); };
    controls.append(edit, remove); row.append(summary, node('span', 'entry-kind', entry.kind === 'local' ? 'Local app' : 'Website'), controls); $('#cards').append(row);
  });
  for (const [kind, selector] of [['local', '#local-cards'], ['external', '#external-cards']]) {
    if (entries.length && !entries.some(entry => entry.kind === kind)) $(selector).append(node('p', 'section-empty', kind === 'local' ? 'Your local apps will feel at home here. Add one in Configure.' : 'Keep your favorite websites close. Add one in Configure.'));
  }
}
function navigate() {
  const configuring = location.hash === '#configure';
  $('#home').hidden = configuring; $('#settings').hidden = !configuring;
  $('#configure').setAttribute('aria-current', configuring ? 'page' : 'false');
  document.title = configuring ? 'Configure — HomeHub' : 'HomeHub — Your apps. One home.';
}
window.addEventListener('hashchange', navigate);
navigate();
async function load() {
  entries = await api('/api/entries');
  const previous = $('#category').value;
  $('#category').replaceChildren(new Option('All categories', ''));
  $('#categories').replaceChildren();
  [...new Set(entries.map(e => e.category))].sort().forEach(c => { $('#category').add(new Option(c, c)); $('#categories').append(new Option(c, c)); });
  $('#category').value = previous;
  render(); refreshHealth();
}
async function refreshHealth() {
  try {
    health = await api('/api/health'); $('#message').textContent = '';
    document.querySelectorAll('[data-entry-id]').forEach(element => {
      const state = health[element.dataset.entryId] || 'Unknown';
      element.textContent = state; element.className = `status ${state.toLowerCase()}`;
    });
  }
  catch { $('#message').textContent = 'HomeHub is offline. Health information may be out of date.'; }
}
function open(entry) {
  editing = entry?.id || null; form.reset(); iconImage = entry?.iconImage || ''; previewIcon(); $('#form-error').textContent = '';
  $('#dialog-title').textContent = editing ? 'Edit your place' : 'Add a place';
  if (entry) Object.keys(entry).forEach(key => { if (form.elements.namedItem(key)) form.elements.namedItem(key).value = entry[key]; });
  $('#health-field').hidden = form.elements.kind.value === 'external';
  $('#dialog').showModal();
}
$('#add').onclick = () => open();
$('#close').onclick = $('#cancel').onclick = () => $('#dialog').close();
form.elements.kind.onchange = () => { $('#health-field').hidden = form.elements.kind.value === 'external'; };
$('#search').oninput = $('#category').onchange = render;
form.onsubmit = async event => {
  event.preventDefault(); const button = form.querySelector('[type=submit]'); button.disabled = true;
  try { await api(editing ? `/api/entries/${editing}` : '/api/entries', editing ? 'PUT' : 'POST', {...Object.fromEntries(new FormData(form)), iconImage}); $('#dialog').close(); await load(); }
  catch (error) { $('#form-error').textContent = error.message; }
  finally { button.disabled = false; }
};
$('#delete-dialog').addEventListener('close', async () => {
  if ($('#delete-dialog').returnValue !== 'delete') return;
  try { await api(`/api/entries/${deleting}`, 'DELETE'); await load(); }
  catch (error) { $('#message').textContent = error.message; }
});
load().catch(error => { $('#message').textContent = `Could not load your places: ${error.message} Reload to retry.`; });
setInterval(refreshHealth, 5000);
