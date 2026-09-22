'use strict';

function initializePanel() {
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
document.querySelector('#go-back')?.addEventListener('click', () => window.history.back());
const matchForm = document.querySelector('[data-match-form]');
if (matchForm) {
  const finalResult = matchForm.querySelector('#final-result-toggle');
  const action = matchForm.querySelector('input[name="action"]');
  const targetField = matchForm.querySelector('[data-live-target]');
  const targetInput = targetField.querySelector('input');
  const finalScoreFields = [...matchForm.querySelectorAll('[data-final-score]')];
  const submit = matchForm.querySelector('[data-match-submit]');

  const updateMatchMode = () => {
    const isFinal = finalResult.checked;
    action.value = isFinal ? 'register' : 'create_live';
    targetField.hidden = isFinal;
    targetInput.disabled = isFinal;
    finalScoreFields.forEach(field => {
      field.hidden = !isFinal;
      field.querySelector('input').disabled = !isFinal;
    });
    submit.textContent = isFinal ? 'Register match' : 'Create match';
  };

  finalResult.addEventListener('change', updateMatchMode);
  updateMatchMode();
}
const system = document.querySelector('#system');
if (system) {
  const form = system.form;
  const groups = document.querySelector('#groups');
  const targetStage = document.querySelector('#target-stage');
  const alternate = document.querySelector('#alternate-target-field');
  const bo3Stage = document.querySelector('#bo3-stage');
  const errors = document.querySelector('#tournament-errors');
  const updateTournament = () => {
    const grouped = system.value === 'group_double';
    groups.disabled = !grouped;
    document.querySelector('#groups-field').hidden = !grouped;
    const roundRobin = system.value === 'round_robin';
    document.querySelector('#target-stage-field').hidden = roundRobin;
    targetStage.disabled = roundRobin;
    for (const option of bo3Stage.options) {
      const finalsOnly = !['none', 'all'].includes(option.value);
      option.hidden = roundRobin && finalsOnly;
      option.disabled = roundRobin && finalsOnly;
    }
    if (roundRobin && !['none', 'all'].includes(bo3Stage.value)) bo3Stage.value = 'none';
    alternate.hidden = roundRobin || targetStage.value === 'all';
    alternate.querySelector('input').disabled = alternate.hidden;
    const count = form.querySelectorAll('[name="participants"]:checked').length;
    const messages = [];
    if (count < 2) messages.push('Select at least two players.');
    if (system.value === 'single' && count % 2) messages.push('Single-elimination requires an even number of players.');
    if (grouped && count < 4) messages.push('Group-stage double elimination requires at least four players.');
    if (grouped && (!Number.isInteger(Number(groups.value)) || Number(groups.value) < 1 || Number(groups.value) > Math.floor(count / 2))) messages.push('Each group must have at least two players.');
    form.querySelectorAll('[name="target"], [name="alternate_target"]').forEach(input => {
      if (!input.disabled && (!Number.isInteger(Number(input.value)) || Number(input.value) < 2)) messages.push('Target points must be whole numbers of at least 2.');
    });
    errors.textContent = messages.join(' ');
    form.querySelector('button').disabled = messages.length > 0;
  };
  form.addEventListener('input', updateTournament);
  form.addEventListener('change', updateTournament);
  updateTournament();
}

document.querySelectorAll('[data-stats-form] select').forEach(select => {
  select.addEventListener('change', () => select.form.requestSubmit());
});

const classification = document.querySelector('#classification');
if (classification) {
  const badgeOptions = document.querySelector('#tournament-badge-options');
  const updateBadgeOptions = () => {
    const isMajor = classification.value === 'major';
    badgeOptions.hidden = !isMajor;
    badgeOptions.disabled = !isMajor;
  };
  classification.addEventListener('change', updateBadgeOptions);
  updateBadgeOptions();
}

}
initializePanel();

// Back/forward navigation can restore disabled controls from the browser cache.
window.addEventListener('pageshow', event => {
  if (event.persisted) window.location.reload();
});

const refreshStatus = document.querySelector('#refresh-status');
let panel = document.querySelector('.panel[data-changes-url]');
let dirty = false;
let submitting = false;
let pointerDown = false;
document.addEventListener('input', event => {
  if (event.target.closest('form')) dirty = true;
});
document.addEventListener('change', event => {
  if (event.target.closest('form')) dirty = true;
});
document.addEventListener('submit', event => {
  if (!event.defaultPrevented) submitting = true;
});
document.addEventListener('pointerdown', () => { pointerDown = true; });
document.addEventListener('pointerup', () => { pointerDown = false; });
document.addEventListener('pointercancel', () => { pointerDown = false; });
window.addEventListener('blur', () => { pointerDown = false; });

function canUpdate() {
  return !document.hidden && !dirty && !submitting && !pointerDown
    && !document.activeElement?.matches('input, select, textarea, button');
}

let pollDelay = 500;
async function pollChanges() {
  try {
    if (document.hidden || submitting) return;
    const response = await fetch(panel.dataset.changesUrl, {
      cache: 'no-store', signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error('Change check failed');
    const {version} = await response.json();
    refreshStatus.textContent = '';
    if (version !== panel.dataset.version) {
      if (!canUpdate()) {
        return;
      }
      const page = await fetch(window.location.href, {
        cache: 'no-store', signal: AbortSignal.timeout(10000),
      });
      // A deleted match/tournament has a useful error page to display, too.
      if (!page.ok && page.status !== 404) throw new Error('Page update failed');
      const html = new DOMParser().parseFromString(await page.text(), 'text/html');
      const updated = html.querySelector('.panel');
      if (!updated) throw new Error('Missing page content');
      // Editing or submitting may have started while the request was in flight.
      if (!canUpdate()) return;
      const openDetails = [...panel.querySelectorAll('details')].map(detail => detail.open);
      updated.querySelectorAll('details').forEach((detail, index) => {
        if (index < openDetails.length) detail.open = openDetails[index];
      });
      const {scrollX, scrollY} = window;
      panel.replaceWith(updated);
      panel = updated;
      document.title = html.title;
      initializePanel();
      window.scrollTo(scrollX, scrollY);
      if (!panel.dataset.changesUrl) {
        return;
      }
    }
    pollDelay = 500;
  } catch (error) {
    pollDelay = Math.min(pollDelay * 2, 30000);
    refreshStatus.textContent = 'Cannot access shared data · retrying';
  } finally {
    // Schedule after completion: requests never overlap, even on a slow NAS.
    if (panel?.dataset.changesUrl) window.setTimeout(pollChanges, pollDelay);
  }
}
if (panel) window.setTimeout(pollChanges, pollDelay);
