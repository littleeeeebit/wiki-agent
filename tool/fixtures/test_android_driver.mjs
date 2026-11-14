// Offline control-flow fixture: real collector source, simulated Android and WebView adapters.
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {EventEmitter} from 'node:events';
import {SourceTextModule, SyntheticModule} from 'node:vm';
import {pathToFileURL} from 'node:url';

const [driver, python, root, mode] = process.argv.slice(2);
const artifact = path.join(root, 'evidence');
const data = path.join(artifact, 'data');
fs.mkdirSync(data, {recursive: true});
const trace = [];
const hash = value => createHash('sha256').update(value).digest('hex');
const binary = path.join(root, 'fixture.apk'); fs.writeFileSync(binary, 'offline fixture');
const head = 'a'.repeat(40), application = 'dev.example.app';
const request = {root, artifact_dir: artifact, head, python, desktop: 'fixture',
  flow: 'startup', environment_id: 'offline', test_scope: 'simulated adapters', revisions: {},
  target: {application: 'fixture', platform: 'android', package: application, activity: '.MainActivity', scenario: 'scenario.mjs',
    actions: [{assertion: 'saved', expected: 'Saved'}]},
  settings: {applications: {fixture: {binary: {path: binary, sha256: hash(fs.readFileSync(binary))},
    adb: {path: 'fixture-adb'}, emulator: {path: 'fixture-emulator'}, avd: 'fixture', profile: 'disposable'}}}};
const file = path.join(root, 'request.json'); fs.writeFileSync(file, JSON.stringify(request));
let focus = 'ready', pid = 1000, startingReads = 0;
const children = [];
function spawn(command) {
  const child = new EventEmitter();
  Object.assign(child, {pid: ++pid, exitCode: null, signalCode: null,
    kill() {this.exitCode = 0; this.emit('exit', 0);}});
  children.push(child); trace.push(['spawn', command]); return child;
}
function execute(command, args) {
  if (command === python) {
    if (args[0].endsWith('native_window.py')) return JSON.stringify({pid: 1001});
    assert.equal(args[0], driver.replace(/\.mjs$/, '.py'));
    assert.equal(args[1], 'android-wait');
    trace.push(['recognize']);
    return execFileSync(python, args, {encoding: 'utf8'});
  }
  assert.equal(command, 'fixture-adb');
  if (args[0] === 'devices') return 'List of devices attached\n';
  const cmd = args.slice(2).map(value => String(value).replace(/^'|'$/g, ''));
  if (cmd[0] === 'install') return 'Success';
  if (cmd[0] === 'pull') {fs.copyFileSync(binary, cmd[2]); return '';}
  if (cmd[0] === 'exec-out') return Buffer.from('offline screenshot placeholder');
  if (cmd[0] === 'get-state') return 'device';
  if (cmd[0] !== 'shell') return '';
  const words = cmd.slice(1);
  if (words[0] === 'getprop') return words[1] === 'sys.boot_completed' ? '1' : 'fixture';
  if (words[0] === 'pm' && words[1] === 'path') return 'package:/data/app/fixture.apk';
  if (words[0] === 'am' && words[1] === 'start') {
    focus = mode === 'ready' || mode === 'late_system_ui' ? 'ready' :
      mode === 'recurrent' ? 'system_ui' : mode;
    trace.push(['activity-start']); return '';
  }
  if (words[0] === 'dumpsys') {
    if (focus === 'starting' && ++startingReads >= 2) focus = 'ready';
    trace.push(['focus', focus]);
    return focus === 'ready' ? `mCurrentFocus=Window{1 u0 ${application}/.MainActivity}\n` :
      `mCurrentFocus=Window{1 u0 Application Not Responding: ${focus === 'system_ui' ? 'com.android.systemui' : application}}\n`;
  }
  if (words[0] === 'pidof') {trace.push(['pid', focus]); assert.equal(focus, 'ready'); return '3001';}
  if (words[0] === 'cat') return words[1] === '/proc/net/unix' ? '@webview_devtools_remote_3001' :
    '<hierarchy><node resource-id="android:id/aerr_wait" package="android" enabled="true" clickable="true" bounds="[60,1090][860,1220]" /></hierarchy>';
  if (words[0] === 'input' && words[1] === 'tap') {
    assert.equal(focus, 'system_ui'); assert.deepEqual(words.slice(2), ['460', '1155']);
    trace.push(['tap']); focus = mode === 'recurrent' ? 'system_ui' : 'ready';
  }
  return '';
}
const page = {url: () => 'http://fixture.invalid/', on() {}, context: () => context,
  evaluate: async () => ({core: head, screen: head, profile: hash(`/data/user/0/${application}`)}),
  screenshot: async ({path: file}) => fs.writeFileSync(file, 'offline screenshot placeholder')};
const context = {pages: () => [page]};
const browser = {contexts: () => [context], on() {}, version: () => 'simulated WebView', close: async () => {}};
const scenario = async session => {
  await session.start(data);
  if (mode === 'late_system_ui') focus = 'system_ui';
  await session.observe('saved', 'Saved');
};
let clock = 0;
const modules = {
  'node:assert/strict': {default: assert}, 'node:fs': fs, 'node:path': {default: path},
  'node:crypto': {createHash}, 'node:child_process': {spawn, execFileSync: execute},
  'node:net': {default: {createServer() {return {on() {}, listen(_port, _host, done) {done();},
    address: () => ({port: 5554}), close(done) {done();}};}}},
  'node:module': {createRequire: () => () => ({chromium: {connectOverCDP: async () => {
    trace.push(['debugger']); assert.equal(focus, 'ready'); return browser;}}})},
  'node:url': await import('node:url'),
  './native_cleanup.mjs': await import(pathToFileURL(path.join(path.dirname(driver), 'native_cleanup.mjs')).href),
};
const source = new SourceTextModule(fs.readFileSync(driver, 'utf8'), {
  identifier: pathToFileURL(driver).href,
  initializeImportMeta(meta) {meta.url = pathToFileURL(driver).href;},
  importModuleDynamically: async () => {
    const module = new SyntheticModule(['default'], function() {this.setExport('default', scenario);});
    await module.link(() => {}); await module.evaluate(); return module;
  },
});
const prior = process.argv;
process.argv = ['node', driver, file];
const originalNow = Date.now, originalFetch = globalThis.fetch, originalTimeout = globalThis.setTimeout;
Date.now = () => {clock += 1000; return clock;};
globalThis.fetch = async () => ({json: async () => [{type: 'page', url: page.url()}]});
globalThis.setTimeout = (fn, milliseconds) => originalTimeout(fn, milliseconds === 100 ? 0 : milliseconds);
try {
  await source.link(async name => {
    assert.ok(name in modules, `Unexpected import ${name}`);
    const exports = modules[name];
    return new SyntheticModule(Object.keys(exports), function() {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    });
  });
  await source.evaluate();
} finally {
  Date.now = originalNow; globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout;
  process.argv = prior;
  fs.writeFileSync(path.join(root, 'trace.json'), JSON.stringify(trace));
  assert.ok(children.every(child => child.exitCode === 0), 'Every simulated child must be cleaned');
}
