const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function browser() {
  const handlers = {};
  const timers = [];
  const requests = [];
  const status = {};
  const panel = {
    dataset: {changesUrl: '/immer-pong/changes', version: 'old'},
    querySelectorAll: () => [],
    replaceWith(updated) { this.replacement = updated; },
  };
  const document = {
    hidden: false,
    activeElement: {matches: () => false},
    querySelectorAll: () => [],
    querySelector: selector => selector === '#refresh-status' ? status
      : selector === '.panel[data-changes-url]' ? panel : null,
    addEventListener: (name, callback) => { handlers[name] = callback; },
  };
  const window = {
    addEventListener() {},
    setTimeout: (callback, delay) => timers.push({callback, delay}),
    location: {href: 'http://localhost/stats?player=alice'},
    scrollTo() {},
  };
  const updated = {...panel, dataset: {...panel.dataset, version: 'new'}};
  const context = vm.createContext({document, window, AbortSignal,
    DOMParser: class { parseFromString() { return {querySelector: () => updated, title: 'Stats'}; } },
    fetch: async url => {
      requests.push(url);
      return url === panel.dataset.changesUrl
        ? {ok: true, json: async () => ({version: 'old'})}
        : {ok: true, text: async () => '<html>'};
    },
  });
  vm.runInContext(readFileSync('static/app.js', 'utf8'), context);
  return {context, document, handlers, timers, requests, status, panel,
    poll: () => timers.shift().callback()};
}

test('unchanged polling is lightweight and hidden tabs do not request data', async () => {
  const b = browser();
  assert.equal(b.timers[0].delay, 500);
  await b.poll();
  assert.deepEqual(b.requests, ['/immer-pong/changes']);
  assert.equal(b.panel.replacement, undefined);
  b.document.hidden = true;
  await b.poll();
  assert.equal(b.requests.length, 1);
});

test('changed data updates content using the current filters', async () => {
  const b = browser();
  b.context.fetch = async url => {
    b.requests.push(url);
    return {ok: true, json: async () => ({version: 'new'}), text: async () => '<html>'};
  };
  await b.poll();
  assert.equal(b.requests[1], 'http://localhost/stats?player=alice');
  assert.equal(b.panel.replacement.dataset.version, 'new');
});

test('edits during a pending request prevent replacement; submissions stop polling', async () => {
  const b = browser();
  let resolve;
  b.context.fetch = () => new Promise(done => { resolve = done; });
  const polling = b.poll();
  assert.equal(b.timers.length, 0, 'no second poll while the first is running');
  b.handlers.input({target: {closest: () => ({})}});
  resolve({ok: true, json: async () => ({version: 'new'})});
  await polling;
  assert.equal(b.panel.replacement, undefined);
  assert.equal(b.status.textContent, '');
  b.handlers.submit({defaultPrevented: false});
  b.context.fetch = () => assert.fail('request during submission');
  await b.poll();
});

test('network failures back off and recover', async () => {
  const b = browser();
  const fetch = b.context.fetch;
  b.context.fetch = async () => { throw new Error('offline'); };
  await b.poll();
  assert.equal(b.timers[0].delay, 1000);
  assert.equal(b.status.textContent, 'Cannot access shared data · retrying');
  await b.poll();
  assert.equal(b.timers[0].delay, 2000);
  b.context.fetch = fetch;
  await b.poll();
  assert.equal(b.timers[0].delay, 500);
  assert.equal(b.status.textContent, '');
});

test('edits while updated HTML is loading are preserved', async () => {
  const b = browser();
  let resolve;
  b.context.fetch = async url => url === b.panel.dataset.changesUrl
    ? {ok: true, json: async () => ({version: 'new'})}
    : {ok: true, text: () => new Promise(done => { resolve = done; })};
  const polling = b.poll();
  // Allow the check and HTML request to reach the pending response body.
  while (!resolve) await Promise.resolve();
  b.handlers.input({target: {closest: () => ({})}});
  resolve('<html>');
  await polling;
  assert.equal(b.panel.replacement, undefined);
});
