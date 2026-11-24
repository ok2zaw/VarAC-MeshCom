// SVG construction test with a small DOM test double; no browser dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
class Element {
  constructor(name) { this.name = name; this.children = []; this.attrs = {}; this.dataset = {}; this.style = {}; this.events = {}; }
  append(...items) { this.children.push(...items); }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  addEventListener(name, callback) { this.events[name] = callback; }
  replaceChildren() { this.children = []; }
  getBoundingClientRect() { return {left: 0, top: 0, width: 1000, height: 280}; }
  querySelectorAll(selector) {
    return this.children.flatMap(c => c instanceof Element ? [c, ...c.querySelectorAll(selector)] : [])
      .filter(e => e.className === selector.slice(1));
  }
}
const point = (t, rssi, boot = "a") => ({t, rssi, snr: -10, boot, seq: t, first_t: t, last_t: t});
const points = [point(100, -110), point(200, -100), point(1500, -90), point(1510, -95, "b")];
const elements = Object.fromEntries(["neighbor-data", "neighbor-legend", "neighbor-readout", "neighbor-rssi", "neighbor-snr"].map(id => [id, new Element(id)]));
const secondPoints = points.map(p => ({...p, rssi: p.rssi - 15}));
elements["neighbor-data"].textContent = JSON.stringify({history: {
  since: 0, until: 2000, selected: ["OK2RPT-9", "OK2RPT-8"], series: [
    {station: "OK2RPT-9", points, trend: points},
    {station: "OK2RPT-8", points: secondPoints, trend: secondPoints}]
}, gap_seconds: 300, utc: true});
const root = new Element("html");
const context = vm.createContext({
  document: {documentElement: root, getElementById: id => elements[id],
    createElementNS: (_, name) => new Element(name), createElement: name => new Element(name), createTextNode: text => ({text})},
  window: {addEventListener() {}}, MutationObserver: class { observe() {} },
  Intl, setTimeout, clearTimeout
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/neighbors.js"), "utf8"), context);
const paths = id => elements[id].children.filter(c => c.name === "path");
assert.equal(paths("neighbor-rssi").length, 4); // Two differently colored stations.
assert.equal(paths("neighbor-snr").length, 4);
const strokes = paths("neighbor-rssi").map(p => p.attrs.stroke);
assert.notEqual(strokes[0], strokes[2]);
assert.equal(strokes[0], paths("neighbor-snr")[0].attrs.stroke);
assert.equal(strokes[2], paths("neighbor-snr")[2].attrs.stroke);
const swatches = elements["neighbor-legend"].querySelectorAll(".neighbor-swatch");
assert.equal(swatches[0].style.backgroundColor, strokes[0]);
assert.equal(swatches[1].style.backgroundColor, strokes[2]);
for (const id of ["neighbor-rssi", "neighbor-snr"]) {
  for (const p of paths(id)) assert.ok(!/NaN|Infinity/.test(p.attrs.d));
}
const svg = elements["neighbor-rssi"];
const x = 62 + 200 / 2000 * (982 - 62);
const y = 235 - (-100 + 129) / 43 * (235 - 24); // bounds -129..-86
svg.onpointermove({clientX: x, clientY: y});
assert.match(elements["neighbor-readout"].textContent, /OK2RPT-9.*RSSI -100 dBm.*SNR -10 dB/);
const checkbox = elements["neighbor-legend"].children[0].children[0];
checkbox.checked = false;
checkbox.events.change();
assert.equal(paths("neighbor-rssi").length, 2);
assert.equal(paths("neighbor-snr").length, 2);
assert.equal(paths("neighbor-rssi")[0].attrs.stroke, strokes[2]); // Hiding a station never reassigns colors.
console.log("Neighbor SVG rendering tests passed.");
