const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Workflow = require('../static/workflow.js');
const script = fs.readFileSync(path.join(__dirname, '../static/chat.js'), 'utf8');

function functionSource(name) {
  const start = script.indexOf(`function ${name}(`);
  assert.ok(start >= 0);
  return script.slice(start, script.indexOf('\n}\n', start) + 2);
}

test('separate source contributions survive merging and retry updates one contribution', () => {
  const manual = {product_id: 101, quantity: 7};
  const a = {product_id: 101, quantity: 2, source_id: 'a'};
  const b = {product_id: 101, quantity: 3, source_id: 'b'};
  const merged = Workflow.merge([manual], [a, b]);
  assert.equal(merged.reduce((sum, item) => sum + item.quantity, 0), 12);
  assert.deepEqual(Workflow.merge(merged, [a, b]), merged);
  assert.equal(Workflow.merge(merged, [{...a, quantity: 4}]).reduce((sum, item) => sum + item.quantity, 0), 14);
});

test('bulk ready selection excludes fulfilled rows and ambiguous units', () => {
  const product = {unit: 'шт', purchase_options: {can_add: true, remaining: '10'}};
  assert.equal(Workflow.eligible({source_unit: 'шт.'}, product, 2), true);
  for (const row of [{source_unit: 'м'}, {source_unit: ''}, {source_unit: 'шт', completion: 'added'}, {source_unit: 'шт', completion: 'excluded'}]) {
    assert.equal(Boolean(Workflow.eligible(row, product, 2)), false);
  }
  assert.equal(Workflow.eligible({source_unit: 'шт'}, product, 11), false);
});

test('expiry restores a visible renewal action while retaining selection', () => {
  const button = {hidden: true};
  const inputs = [{product_id: 101, quantity: 2}];
  const context = {proposal: {id: 'old'}, currentState: {proposal: {id: 'old'}, selection_inputs: inputs},
    selectionDock: {querySelector: () => button}, renderSelection() {}, showStatus() {}};
  vm.runInNewContext(functionSource('expireProposal') + '; expireProposal();', context);
  assert.equal(context.proposal, null);
  assert.equal(context.currentState.proposal, null);
  assert.equal(button.hidden, false);
  assert.equal(button.textContent, 'Обновить предложение');
  assert.deepEqual(context.currentState.selection_inputs, inputs);
});

test('reconnected request polls until complete and clears only its submitted files', async () => {
  let nextPoll, rendered, busy, restored = false;
  const submitted = {name: 'old.csv'}, nextFile = {name: 'next.csv'};
  const responses = [{busy: true, request_status: {id: 'r', stage: 'reading'}},
    {busy: false, request_status: {id: 'r', stage: 'complete'}, messages: [{content: 'done'}]}];
  const context = {recoveredRequest: 'r', submittedDraft: {id: 'r', files: ['old.csv']}, files: [submitted, nextFile],
    localSelectionInputs: [{product_id: 1}], document: {getElementById: () => ({hidden: false})},
    fetch: async () => ({ok: true, json: async () => responses.shift()}),
    setTimeout: callback => {nextPoll = callback;}, showStatus() {}, saveDrafts() {}, renderFiles() {},
    restoreSubmitted() {restored = true;}, renderState(data) {rendered = data;}, setBusy(value) {busy = value;}};
  vm.runInNewContext('async ' + functionSource('pollRequest'), context);
  await context.pollRequest();
  assert.equal(context.recoveredRequest, 'r');
  assert.equal(rendered, undefined);
  await nextPoll();
  assert.equal(context.recoveredRequest, null);
  assert.equal(context.submittedDraft, null);
  assert.equal(busy, false);
  assert.equal(restored, false);
  assert.equal(rendered.messages[0].content, 'done');
  assert.deepEqual(context.files, [nextFile]);
  assert.equal(context.localSelectionInputs.length, 1);
});
