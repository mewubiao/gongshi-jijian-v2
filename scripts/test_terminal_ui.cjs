/* Browser-independent checks: real QR pixels, camera lifecycle and idle privacy. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const asset = file => fs.readFileSync(path.join(root, "terminal/static/terminal", file), "utf8");
const jsQR = require(path.join(root, "terminal/static/terminal/vendor/jsQR.js"));
const fixture = JSON.parse(fs.readFileSync(0, "utf8"));
const frame = {width: fixture.width, height: fixture.height, data: new Uint8ClampedArray(fixture.data)};
assert.equal(jsQR(frame.data, frame.width, frame.height).data, fixture.expected);

async function cameraCase({denied = false, late = false} = {}) {
  let submitted = 0, stopped = 0, release;
  const callbacks = {}, windowCallbacks = {};
  const stream = {getTracks: () => [{stop: () => stopped++}]};
  const button = key => ({disabled: false, hidden: false, addEventListener: (_, fn) => callbacks[key] = fn});
  const start = button("start"), stop = button("stop");
  const form = {elements: {method: {value: "qr", addEventListener() {}}, credential: {value: ""}}, requestSubmit: () => submitted++};
  const video = {readyState: 2, videoWidth: frame.width, videoHeight: frame.height, play: () => Promise.resolve()};
  const message = {textContent: ""};
  const elements = {"identify-form": form, camera: video, "camera-message": message, "camera-start": start, "camera-stop": stop};
  const sandbox = {
    window: {jsQR, addEventListener: (name, fn) => windowCallbacks[name] = fn},
    document: {hidden: false, getElementById: id => elements[id], createElement: () => ({getContext: () => ({drawImage() {}, getImageData: () => frame})}), addEventListener() {}},
    navigator: {mediaDevices: {getUserMedia: () => denied ? Promise.reject({name: "NotAllowedError"}) : late ? new Promise(resolve => release = resolve) : Promise.resolve(stream)}},
    setTimeout() {}, clearTimeout() {},
  };
  vm.runInNewContext(asset("scanner.js"), sandbox);
  const pending = callbacks.start();
  if (late) { windowCallbacks.pagehide(); release(stream); }
  await pending;
  if (denied) { assert.equal(submitted, 0); assert.match(message.textContent, /未获得/); }
  else if (late) { assert.equal(submitted, 0); assert.equal(stopped, 1); }
  else { assert.equal(submitted, 1); assert.equal(form.elements.credential.value, fixture.expected); assert.equal(stopped, 1); }
}

function idleCase() {
  let now = 0, wiped = false, destination = "";
  const events = {}, windowEvents = {}, intervals = [];
  const screen = {dataset: {sequence: "7"}, style: {}, replaceChildren: () => wiped = true, querySelectorAll: () => []};
  const sandbox = {
    Date: {now: () => now}, performance: {now: () => now},
    document: {hidden: false, getElementById: id => id === "terminal-private" ? screen : {textContent: ""}, querySelector: selector => selector.includes("csrf") ? {value: "test-csrf"} : null, addEventListener: (name, fn) => events[name] = fn},
    window: {location: {replace: value => destination = value}, addEventListener: (name, fn) => windowEvents[name] = fn},
    navigator: {onLine: true}, fetch: async () => ({status: 200, ok: true, json: async () => ({active: true, sequence: 7})}),
    setInterval: fn => intervals.push(fn), setTimeout() {}, clearTimeout() {}, encodeURIComponent,
  };
  vm.runInNewContext(asset("idle.js"), sandbox);
  now = 14000; events.keydown({isTrusted: true});
  now = 16000; intervals[0](); assert.equal(wiped, false);
  now = 29001; intervals[0](); assert.equal(wiped, true); assert.match(destination, /locked=1&sequence=7/);
  wiped = false; windowEvents.pagehide(); assert.equal(wiped, true);
}

function dispatchCase() {
  let change;
  const kind = {value: "single", addEventListener: (_, fn) => change = fn};
  const field = {}, help = {}, label = {}, input = {checked: true};
  const elements = {"dispatch-form": {elements: {kind}}, "id_additional_members": {closest: () => field, querySelectorAll: () => [input]}, "id_assignee_helptext": help};
  vm.runInNewContext(fs.readFileSync(path.join(root, "dispatch/static/dispatch/form.js"), "utf8"), {
    document: {getElementById: id => elements[id], querySelector: () => label},
  });
  assert.equal(field.hidden, true); assert.equal(input.disabled, true);
  kind.value = "group"; change();
  assert.equal(field.hidden, false); assert.equal(input.disabled, false);
  assert.equal(input.checked, true); assert.match(label.textContent, /小组长/);
}

(async () => {
  await cameraCase(); await cameraCase({denied: true}); await cameraCase({late: true}); idleCase(); dispatchCase();
  process.stdout.write("PASS: QR decoding, camera permission error, late stream cleanup, 15-second idle lock and back-navigation cleanup.\n");
})().catch(error => { process.stderr.write(error.stack + "\n"); process.exitCode = 1; });
