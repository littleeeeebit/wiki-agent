// Fixed host driver: scenarios never choose a running app, CDP endpoint or device.
import assert from 'node:assert/strict';
import {spawn, execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFileSync, writeFileSync, mkdirSync, readdirSync, openSync, closeSync} from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';

const request = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const {root, artifact_dir: artifact, target, settings, head} = request;
const approval = settings.applications[target.application];
const require = createRequire(path.join(root, 'package.json'));
const {chromium} = require('playwright');
const hash = data => createHash('sha256').update(data).digest('hex');
const save = (name, data) => writeFileSync(path.join(artifact, name), JSON.stringify(data));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async (fn, timeout = 30000) => {
  const end = Date.now() + timeout;
  let last;
  do {try {const result = await fn(); if (result) return result;} catch (error) {last = error;}
    await sleep(100);
  } while (Date.now() < end);
  throw new Error(`Native readiness timed out: ${last?.message ?? 'not ready'}`);
};
const freePort = () => new Promise((resolve, reject) => {
  const server = net.createServer(); server.on('error', reject);
  server.listen(0, '127.0.0.1', () => {const port = server.address().port; server.close(() => resolve(port));});
});
const run = (file, args, options = {}) => execFileSync(file, args, {encoding: 'utf8', windowsHide: true, timeout: 30000,
  stdio: ['ignore', 'pipe', 'pipe'], ...options});
const owned = file => {
  const resolved = path.resolve(file);
  assert.ok(resolved.startsWith(path.resolve(artifact) + path.sep), 'Scenario data must be attempt-owned');
  return resolved;
};
const profile = path.join(artifact, 'profile'); mkdirSync(profile);
const sessions = [], actions = [], artifacts = [];
const lifecycle = [];
let child, emulator, browser, context, page, serial, forward, data, debugPort, measuredHead, logcat, androidPid, installed = false;
const childAlive = proc => proc && Number.isInteger(proc.pid) && proc.exitCode === null && proc.signalCode === null;
function diagnose(event) {
  const state = {event, time: Date.now(), emulator: emulator && {pid: emulator.pid, code: emulator.exitCode, signal: emulator.signalCode}};
  if (serial) {
    try {state.transport = adb('get-state').trim(); state.app_pid = shell('pidof', target.package).trim();}
    catch (error) {state.error = error.message;}
  }
  lifecycle.push(state); save('lifecycle.json', lifecycle);
}
function logged(file, args, name, options = {}) {
  const descriptor = openSync(path.join(artifact, name), 'a');
  try {return spawn(file, args, {windowsHide: true, ...options, stdio: ['ignore', descriptor, descriptor]});}
  finally {closeSync(descriptor);}
}
const stop = async proc => {
  if (!childAlive(proc)) return;
  const exited = new Promise(resolve => proc.once('exit', resolve)); proc.kill();
  await Promise.race([exited, sleep(5000).then(() => {if (childAlive(proc)) throw new Error('Owned process did not stop');})]);
};
const adb = (...args) => run(approval.adb.path, ['-s', serial, ...args]);
const shell = (...args) => adb('shell', ...args.map(arg => `'${String(arg).replaceAll("'", "'\\''")}'`));
const addArtifact = name => artifacts.push({path: name, sha256: hash(readFileSync(path.join(artifact, name)))});
function emulatorListener() {
  assert.ok(childAlive(emulator), 'Owned emulator exited');
  const file = path.join(path.dirname(fileURLToPath(import.meta.url)), 'native_window.py');
  return JSON.parse(run(request.python, [file, 'listener', serial.slice('emulator-'.length), String(emulator.pid)])).pid;
}
function androidFocus() {
  const lines = shell('dumpsys', 'window').split('\n').filter(line => /mCurrentFocus|mFocusedApp/.test(line));
  save('android-focus.json', {lines});
  return lines.some(line => /mCurrentFocus/.test(line) && line.includes(`${target.package}/`));
}
async function androidTargets(url) {
  emulatorListener();
  assert.equal(Number(shell('pidof', target.package).trim()), androidPid, 'Owned Android application process changed');
  return until(async () => {
    const response = await fetch(`http://127.0.0.1:${forward}/json/list`);
    const targets = await response.json();
    save('debug-targets.json', targets.map(({id, type, url}) => ({id, type, url})));
    return targets.some(target => target.type === 'page' && target.url && target.url !== 'about:blank' && (!url || target.url === url));
  });
}

async function android() {
  for (let port = 5554; port < 5586; port += 2) {
    const devices = run(approval.adb.path, ['devices']);
    if (devices.includes(`emulator-${port}`)) continue;
    serial = `emulator-${port}`;
    // All writable emulator images belong to this attempt; the AVD is only a template.
    const images = path.join(profile, 'emulator'); mkdirSync(images);
    emulator = logged(approval.emulator.path, ['-avd', approval.avd, '-port', String(port), '-datadir', images,
      '-wipe-data', '-no-snapshot', '-no-window', '-no-audio', '-no-boot-anim'], 'emulator.log');
    emulator.on('error', () => {});
    emulator.on('exit', () => diagnose('emulator-exit'));
    await until(() => {
      assert.ok(childAlive(emulator), 'Owned emulator exited');
      return shell('getprop', 'sys.boot_completed').trim() === '1';
    }, 180000);
    emulatorListener();
    shell('input', 'keyevent', 'KEYCODE_WAKEUP');
    shell('wm', 'dismiss-keyguard');
    shell('settings', 'put', 'system', 'screen_off_timeout', '1800000');
    return;
  }
  throw new Error('No unused emulator console port');
}

async function launch() {
  assert.equal(hash(readFileSync(approval.binary.path)), approval.binary.sha256, 'Approved native binary changed');
  if (target.platform === 'windows') {
    const webview = path.join(profile, 'webview');
    debugPort = await freePort();
    child = spawn(approval.binary.path, [], {cwd: root, stdio: 'ignore', windowsHide: true,
      env: {...process.env, WIKI_NATIVE_PROFILE: data, WEBVIEW2_USER_DATA_FOLDER: webview,
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-address=127.0.0.1 --remote-debugging-port=${debugPort}`}});
    child.on('error', () => {});
    await until(() => {
      assert.ok(childAlive(child), 'Owned application exited');
      return inspectWindows();
    });
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, {noDefaults: true});
  } else {
    if (!emulator) await android();
    if (!installed) {
      adb('install', '-t', approval.binary.path);
      shell('pm', 'grant', target.package, 'android.permission.POST_NOTIFICATIONS');
      const remote = shell('pm', 'path', target.package).trim().replace(/^package:/, '');
      const pulled = path.join(profile, 'installed.apk'); adb('pull', remote, pulled);
      assert.equal(hash(readFileSync(pulled)), approval.binary.sha256, 'Installed APK differs from approval');
      for (const name of readdirSync(data)) {
        assert.match(name, /^[A-Za-z0-9_.-]+$/);
        const remoteFile = `/data/local/tmp/wiki-${name}`;
        adb('push', path.join(data, name), remoteFile);
        shell('run-as', target.package, 'cp', '-R', remoteFile, name);
        shell('rm', '-r', remoteFile);
      }
      installed = true;
    }
    shell('am', 'start', '-n', `${target.package}/${target.activity}`);
    await until(androidFocus);
    const pid = await until(() => Number(shell('pidof', target.package).trim()));
    androidPid = pid;
    logcat = logged(approval.adb.path, ['-s', serial, 'logcat', '--pid', String(pid)], `android-app-${sessions.length}.log`);
    forward = await freePort();
    await until(() => shell('cat', '/proc/net/unix').includes(`@webview_devtools_remote_${pid}`));
    adb('forward', `tcp:${forward}`, `localabstract:webview_devtools_remote_${pid}`);
    await androidTargets();
    browser = await until(async () => chromium.connectOverCDP(`http://127.0.0.1:${forward}`, {noDefaults: true}));
  }
  context = browser.contexts()[0];
  browser.on('disconnected', () => diagnose('browser-disconnected'));
  page = await until(async () => {
    const pages = browser.contexts().flatMap(current => current.pages());
    save('targets.json', pages.map(candidate => ({url: candidate.url()})));
    for (const candidate of pages) {
      const revision = await candidate.evaluate(() => ({screen: document.querySelector('meta[name="wiki-build-head"]')?.content,
        core: window.__WIKI_NATIVE_BUILD_HEAD__, profile: window.__WIKI_NATIVE_PROFILE__}));
      save('build-observation.json', revision);
      const expectedProfile = hash(Buffer.from(target.platform === 'windows' ? data : `/data/user/0/${target.package}`));
      if (revision.screen === head && revision.core === head && revision.profile === expectedProfile) {
        measuredHead = revision.core; context = candidate.context(); return candidate;
      }
    }
  });
  page.on('close', () => diagnose('page-closed'));
  await checkpoint('launch');
}

function inspectWindows() {
  const file = path.join(path.dirname(fileURLToPath(import.meta.url)), 'native_window.py');
  return JSON.parse(run(request.python, [file, String(child.pid), approval.binary.path, approval.binary.sha256, request.desktop, String(debugPort)], {stdio: ['ignore', 'pipe', 'pipe']}));
}

async function checkpoint(label) {
  let observed;
  if (target.platform === 'windows') {
    assert.ok(childAlive(child), 'Owned application exited');
    const native = inspectWindows();
    observed = {pid: native.pid, listener_pid: native.listener_pid, ...native.windows[0]};
  } else {
    const listener = emulatorListener();
    const pid = Number(shell('pidof', target.package).trim()); assert.ok(pid > 0);
    assert.ok(androidFocus(), 'Installed app lost native focus');
    observed = {pid, package: target.package, activity: target.activity, emulator_pid: emulator.pid, listener_pid: listener,
      fingerprint: hash(shell('getprop', 'ro.build.fingerprint'))};
    const screen = `android-${sessions.length}.png`;
    writeFileSync(path.join(artifact, screen), execFileSync(approval.adb.path, ['-s', serial, 'exec-out', 'screencap', '-p'], {windowsHide: true, timeout: 30000}));
    addArtifact(screen);
  }
  sessions.push({...observed, label, runtime: browser.version(), build_head: measuredHead, binary_sha256: approval.binary.sha256});
  save('sessions.json', sessions);
}

const session = {
  root, artifact, platform: target.platform, until,
  get page() {return page;}, get context() {return context;},
  async start(directory) {assert.ok(!data); data = owned(directory); assert.equal(data, path.join(artifact, 'data')); await launch();},
  async restart() {
    await browser.close(); browser = null;
    await stop(logcat); logcat = null;
    if (target.platform === 'windows') await stop(child);
    else {shell('am', 'force-stop', target.package); adb('forward', '--remove', `tcp:${forward}`); forward = null;}
    await launch();
  },
  async pageFor(url) {
    if (target.platform === 'windows') return until(() => context.pages().find(page => page.url() === url));
    await androidTargets(url);
    let selected = browser.contexts().flatMap(context => context.pages()).find(page => !page.isClosed() && page.url() === url);
    if (!selected) {
      await browser.close();
      browser = await chromium.connectOverCDP(`http://127.0.0.1:${forward}`, {noDefaults: true});
      browser.on('disconnected', () => diagnose('browser-disconnected'));
      selected = await until(() => browser.contexts().flatMap(context => context.pages()).find(page => page.url() === url));
    }
    context = selected.context(); page = selected;
    return selected;
  },
  async observe(id, actual, currentPage = page) {
    const registered = target.actions.find(action => action.assertion === id);
    assert.ok(registered && !actions.some(action => action.assertion === id), 'Unknown or duplicate native assertion');
    await checkpoint(id);
    const screenshot = `webview-${actions.length}.png`;
    await currentPage.screenshot({path: path.join(artifact, screenshot)}); addArtifact(screenshot);
    actions.push({...registered, actual: String(actual)}); save('observations.json', actions);
    assert.equal(String(actual), registered.expected);
  },
  record(name, data) {
    assert.match(name, /^[a-z0-9-]+\.json$/);
    assert.ok(!artifacts.some(row => row.path === name));
    save(name, data); addArtifact(name);
  },
};

let result;
try {
  const scenario = await import(pathToFileURL(path.join(root, target.scenario)).href);
  await scenario.default(session);
  assert.equal(actions.length, target.actions.length, 'Scenario omitted a registered assertion');
  addArtifact('sessions.json'); addArtifact('observations.json');
  addArtifact('build-observation.json'); addArtifact('targets.json');
  if (target.platform === 'android') addArtifact('android-focus.json');
  result = {...Object.fromEntries(['head', 'flow', 'environment_id', 'test_scope'].map(key => [key, request[key]])),
    version: 2, observations: actions.map(action => ({id: action.assertion, expected: action.expected, actual: action.actual, pass: action.actual === action.expected})),
    requests: [], actions: [], native: {application: target.application, host: target.platform, driver: 'owned-webview-v1',
      build_head: measuredHead, build_inputs: {}, binary_sha256: approval.binary.sha256, profile: approval.profile,
      revisions: request.revisions, desktop: request.desktop, sessions, actions, artifacts}};
} catch (error) {
  if (target.platform === 'android' && emulator && serial) {
    try {writeFileSync(path.join(artifact, 'failure.png'), execFileSync(approval.adb.path,
      ['-s', serial, 'exec-out', 'screencap', '-p'], {windowsHide: true, timeout: 10000}));} catch {}
  }
  result = {blocked: {prerequisite: 'native', reason: error.message}};
  console.error(error.stack);
  process.exitCode = 1;
} finally {
  try {
    if (browser) await browser.close();
    await stop(logcat);
    if (target.platform === 'windows') await stop(child);
    if (emulator) {try {adb('emu', 'kill');} finally {await stop(emulator);}}
  } catch (error) {result = {blocked: {prerequisite: 'native', reason: `Native cleanup failed: ${error.message}`}}; process.exitCode = 1;}
  save('result.json', result);
}
