// Run with: node tests/neighbors_chart.test.js
const assert = require("node:assert/strict");
const {segments, colorAssignments, color, dash, bounds, nearest} = require("../static/neighbors.js");
const a = {t: 100, first_t: 90, last_t: 110, boot: "a"};
const b = {t: 200, first_t: 190, last_t: 210, boot: "a"};
const c = {t: 1000, first_t: 990, last_t: 1010, boot: "a"};
const d = {t: 1015, first_t: 1015, last_t: 1015, boot: "b"};
assert.deepEqual(segments([a, b, c, d], 120), [[a, b], [c], [d]]);
assert.deepEqual(segments([], 120), []);
assert.equal(nearest([a, b, c], 220), b);
assert.equal(nearest([a, b, c], -1), a);
assert.equal(nearest([a, b, c], 5000), c);
assert.equal(nearest([], 50), null);
assert.deepEqual(bounds([-100, -100]), [-105, -95]);
assert.ok(bounds([-120, -90])[0] < -120);
assert.ok(bounds([-120, -90])[1] > -90);
assert.ok(bounds([-15, -8])[1] < 0);  // Negative SNR does not acquire an artificial zero baseline.
const stations = Array.from({length: 20}, (_, i) => `OK2RPT-${i}`);
const assignments = colorAssignments(stations);
const reversed = colorAssignments([...stations].reverse());
for (const dark of [false, true]) {
  assert.equal(new Set(stations.map(s => color(s, dark, assignments))).size, 20);
  for (const station of stations) assert.equal(color(station, dark, assignments), color(station, dark, reversed));
}
assert.notEqual(color(stations[0], false, assignments), color(stations[0], true, assignments));
assert.equal(stations.filter(s => dash(s, assignments)).length, 10);
console.log("Neighbor chart helper tests passed.");
