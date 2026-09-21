import test from 'node:test';
import assert from 'node:assert/strict';
import { groupFaceTracks } from '../src/lib/face-track-groups.ts';

const scope = 'a'.repeat(32);
const capture = (id, changes = {}) => ({
  id, camera_id: 'camera-a', mode: 'face_capture', subject_key: 'track-1-1',
  metadata: { track_scope: scope }, ...changes,
});

test('continuous captures stay together, preserving newest-first order', () => {
  const rows = [capture('newest'), capture('other-track', { subject_key: 'track-1-2' }), capture('older')];
  assert.deepEqual(groupFaceTracks(rows).map(items => items.map(item => item.id)), [['newest', 'older'], ['other-track']]);
});

test('reused track numbers from another tracking scope never merge', () => {
  assert.equal(groupFaceTracks([capture('one'), capture('two', { metadata: { track_scope: 'b'.repeat(32) } })]).length, 2);
});

test('camera identity is part of face grouping', () => {
  assert.equal(groupFaceTracks([capture('one'), capture('two', { camera_id: 'camera-b' })]).length, 2);
});

test('legacy or invalid continuity markers keep each capture separate', () => {
  for (const metadata of [undefined, {}, { track_scope: '' }, { track_scope: 'unknown' }, { track_scope: null }]) {
    assert.equal(groupFaceTracks([capture('one', { metadata }), capture('two', { metadata })]).length, 2);
  }
});

test('plate observations never join face groups', () => {
  assert.deepEqual(groupFaceTracks([capture('plate', { mode: 'lpr' })]), []);
});
