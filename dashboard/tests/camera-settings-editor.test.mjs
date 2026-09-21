import test from 'node:test';
import assert from 'node:assert/strict';
import { CameraSettingsEditor } from '../src/lib/camera-settings-editor.ts';

function pending() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('late camera response cannot overwrite the selected camera or be saved under its ID', async () => {
  const first = pending();
  const calls = [];
  const editor = new CameraSettingsEditor((path, init) => {
    calls.push({ path, init });
    if (init.method === 'PUT') return Promise.resolve({ settings: JSON.parse(init.body) });
    return path.includes('/first/') ? first.promise : Promise.resolve({ threshold: 80 });
  });
  const old = editor.select('first');
  await editor.select('second');
  assert.equal(calls[0].init.signal.aborted, true);
  first.resolve({ threshold: 20 });
  await old;
  assert.equal(editor.getSnapshot().cameraId, 'second');
  assert.equal(editor.getSnapshot().settings.threshold, 80);
  editor.edit(value => ({ ...value, threshold: 90 }));
  await editor.save();
  assert.equal(calls.at(-1).path, '/cameras/second/mode-settings');
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), { threshold: 90 });
});

test('late load errors do not replace current camera status', async () => {
  const old = pending();
  const editor = new CameraSettingsEditor(path => path.includes('/old/') ? old.promise : Promise.resolve({ value: 1 }));
  const request = editor.select('old');
  await editor.select('new');
  old.reject(new Error('Old camera unavailable'));
  await request;
  assert.equal(editor.getSnapshot().error, '');
  assert.equal(editor.getSnapshot().loading, false);
});

test('failed loads have a retry path and cannot save previous camera settings', async () => {
  let fail = false;
  let puts = 0;
  const editor = new CameraSettingsEditor(async (path, init) => {
    if (init.method === 'PUT') puts += 1;
    if (fail) throw Error('Camera offline');
    return { value: 1 };
  });
  await editor.select('first');
  fail = true;
  await editor.select('second');
  assert.equal(editor.getSnapshot().settings, null);
  assert.equal(editor.getSnapshot().loading, false);
  await editor.save();
  assert.equal(puts, 0);
  fail = false;
  await editor.select('second');
  assert.equal(editor.getSnapshot().error, '');
  assert.deepEqual(editor.getSnapshot().settings, { value: 1 });
});

test('dirty drafts can revert to the camera saved values', async () => {
  const editor = new CameraSettingsEditor(async () => ({ nested: { value: 72 } }));
  await editor.select('camera');
  editor.edit(value => { value.nested.value = 30; return value; });
  assert.equal(editor.getSnapshot().dirty, true);
  assert.equal(editor.getSnapshot().saved.nested.value, 72);
  editor.revert();
  assert.equal(editor.getSnapshot().settings.nested.value, 72);
  assert.equal(editor.getSnapshot().dirty, false);
});

test('saving locks edits, switching, and duplicate submissions until response arrives', async () => {
  const saved = pending();
  const calls = [];
  const editor = new CameraSettingsEditor(async (path, init) => {
    calls.push(path);
    return init.method === 'PUT' ? saved.promise : { value: 1 };
  });
  await editor.select('camera');
  editor.edit(() => ({ value: 2 }));
  const save = editor.save();
  editor.edit(() => ({ value: 3 }));
  editor.revert();
  await editor.select('another');
  await editor.save();
  assert.equal(calls.length, 2);
  assert.equal(editor.getSnapshot().settings.value, 2);
  saved.resolve({ settings: { value: 2 } });
  await save;
  assert.equal(editor.getSnapshot().dirty, false);
  assert.equal(editor.getSnapshot().busy, false);
});

test('failed save keeps the draft available for retry', async () => {
  let fail = true;
  const editor = new CameraSettingsEditor(async (path, init) => {
    if (init.method !== 'PUT') return { value: 1 };
    if (fail) throw Error('Save failed');
    return { settings: JSON.parse(init.body) };
  });
  await editor.select('camera');
  editor.edit(() => ({ value: 2 }));
  await editor.save();
  assert.equal(editor.getSnapshot().dirty, true);
  assert.equal(editor.getSnapshot().busy, false);
  assert.equal(editor.getSnapshot().saved.value, 1);
  fail = false;
  await editor.save();
  assert.equal(editor.getSnapshot().saved.value, 2);
  assert.equal(editor.getSnapshot().error, '');
});

test('unmount cancellation suppresses pending load responses', async () => {
  const response = pending();
  const editor = new CameraSettingsEditor(() => response.promise);
  const load = editor.select('camera');
  editor.cancel();
  const state = editor.getSnapshot();
  response.resolve({ value: 3 });
  await load;
  assert.equal(editor.getSnapshot(), state);
});
