'use strict';

document.querySelectorAll('time[datetime]').forEach(element => {
  const date = new Date(element.dateTime);
  if (!Number.isNaN(date.getTime())) element.textContent = date.toLocaleString();
});
document.querySelectorAll('form[method="post"]').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.submitting || (form.dataset.confirm && !window.confirm(form.dataset.confirm))) {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = 'true';
    form.querySelectorAll('button').forEach(button => { button.disabled = true; });
  });
});
// Back/forward navigation can restore disabled controls from the browser cache.
window.addEventListener('pageshow', event => {
  if (event.persisted) window.location.reload();
});
document.querySelector('#go-back')?.addEventListener('click', () => window.history.back());
const system = document.querySelector('#system');
if (system) {
  const updateGroups = () => { document.querySelector('#groups').disabled = system.value !== 'group_double'; };
  system.addEventListener('change', updateGroups);
  updateGroups();
}
