import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import test from 'node:test';
import {childAlive, stopOwned, cleanupOwned} from './native_cleanup.mjs';

test('already exited emulator needs no console connection and preserves the original failure', async () => {
  const emulator = spawn(process.execPath, ['-e', 'process.exit(9)'], {stdio: 'ignore', windowsHide: true});
  await once(emulator, 'exit');
  const failure = {blocked: {prerequisite: 'native', reason: 'Emulator crashed during playback'}};
  assert.equal(await cleanupOwned(failure, [['emulator', () => stopOwned(emulator)]]), failure);
  assert.equal(emulator.exitCode, 9);
});

test('stopping an owned process leaves another process alive', async () => {
  const make = () => spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio: 'ignore', windowsHide: true});
  const owned = make(), unrelated = make();
  try {
    await stopOwned(owned);
    assert.ok(!childAlive(owned));
    assert.ok(childAlive(unrelated));
  } finally {await stopOwned(owned); await stopOwned(unrelated);}
});

test('one cleanup failure does not skip other resources or replace the primary failure', async () => {
  const visited = [], primary = {blocked: {prerequisite: 'native', reason: 'Original emulator crash'}};
  const result = await cleanupOwned(primary, [
    ['browser', () => {visited.push('browser'); throw new Error('Disconnected');}],
    ['logcat', () => {visited.push('logcat');}], ['emulator', () => {visited.push('emulator');}],
  ]);
  assert.deepEqual(visited, ['browser', 'logcat', 'emulator']);
  assert.match(result.blocked.reason, /^Original emulator crash; Native cleanup failed: browser: Disconnected$/);
  assert.equal(primary.blocked.reason, 'Original emulator crash');
});

test('cleanup failure cannot turn a successful scenario into passing evidence', async () => {
  const result = await cleanupOwned({observations: [{pass: true}]}, [['emulator', () => {throw new Error('Still alive');}]]);
  assert.equal(result.blocked.prerequisite, 'native');
  assert.match(result.blocked.reason, /emulator: Still alive/);
});

test('a hung resource is bounded and later cleanup still runs', async () => {
  let reached = false;
  const result = await cleanupOwned({blocked: {reason: 'Original failure'}}, [
    ['browser', () => new Promise(() => {})], ['emulator', () => {reached = true;}],
  ]);
  assert.ok(reached);
  assert.match(result.blocked.reason, /^Original failure; Native cleanup failed: browser: Cleanup exceeded five seconds$/);
});
