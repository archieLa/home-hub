'use strict';
const $ = selector => document.querySelector(selector);
let entries = [], health = {}, editing = null, deleting = null;
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
function render() {
  const query = $('#search').value.toLowerCase(), category = $('#category').value;
  const visible = entries.filter(e => (!category || e.category === category) && `${e.name} ${e.description} ${e.category}`.toLowerCase().includes(query));
  $('#cards').replaceChildren();
  $('#empty').hidden = entries.length > 0;
  $('#count').textContent = `${visible.length} ${visible.length === 1 ? 'place' : 'places'}`;
  if (entries.length && !visible.length) $('#cards').append(node('p', 'hint', 'No places match. Try another search or category.'));
  visible.forEach(entry => {
    const card = node('article', 'card');
    const link = node('a', 'destination'); link.href = entry.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    const top = node('div', 'card-top'); top.append(node('span', 'app-icon', entry.icon), node('span', 'category-label', entry.category));
    link.append(top, node('h2', '', entry.name), node('p', 'description', entry.description || 'Your next destination.'));
    const bottom = node('div', 'card-bottom');
    const state = entry.kind === 'external' ? 'Website' : health[entry.id] || 'Unknown';
    bottom.append(node('span', `status ${state.toLowerCase()}`, state), node('span', 'arrow', '↗'));
    link.append(bottom);
    const controls = node('div', 'controls');
    const edit = node('button', 'quiet', 'Edit'); edit.setAttribute('aria-label', `Edit ${entry.name}`); edit.onclick = () => open(entry);
    const remove = node('button', 'quiet', 'Remove'); remove.setAttribute('aria-label', `Remove ${entry.name}`); remove.onclick = () => { deleting = entry.id; $('#delete-dialog').showModal(); };
    controls.append(edit, remove); card.append(link, controls); $('#cards').append(card);
  });
}
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
  try { health = await api('/api/health'); $('#message').textContent = ''; render(); }
  catch { $('#message').textContent = 'HomeHub is offline. Health information may be out of date.'; }
}
function open(entry) {
  editing = entry?.id || null; form.reset(); $('#form-error').textContent = '';
  $('#dialog-title').textContent = editing ? 'Edit your place' : 'Add a place';
  if (entry) Object.keys(entry).forEach(key => { if (form.elements.namedItem(key)) form.elements.namedItem(key).value = entry[key]; });
  $('#health-field').hidden = form.elements.kind.value === 'external';
  $('#dialog').showModal();
}
$('#add').onclick = $('#first').onclick = () => open();
$('#close').onclick = $('#cancel').onclick = () => $('#dialog').close();
form.elements.kind.onchange = () => { $('#health-field').hidden = form.elements.kind.value === 'external'; };
$('#search').oninput = $('#category').onchange = render;
form.onsubmit = async event => {
  event.preventDefault(); const button = form.querySelector('[type=submit]'); button.disabled = true;
  try { await api(editing ? `/api/entries/${editing}` : '/api/entries', editing ? 'PUT' : 'POST', Object.fromEntries(new FormData(form))); $('#dialog').close(); await load(); }
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
