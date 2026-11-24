/* Dependency-free SVG signal charts. The pure helpers are also tested in Node. */
(() => {
  "use strict";
  const segments = (points, gapSeconds) => {
    const lines = [];
    let previous = null;
    for (const point of points) {
      if (!previous || point.boot !== previous.boot || point.first_t - previous.last_t > gapSeconds) lines.push([]);
      lines[lines.length - 1].push(point);
      previous = point;
    }
    return lines;
  };
  // Hashing similar callsigns produced almost identical hues, especially SSIDs.
  // Allocate a distinct categorical color to every displayed station instead.
  const palette = {
    light: ["#0072b2", "#d55e00", "#009e73", "#8e44ad", "#c62828",
      "#00838f", "#c2185b", "#8d6e00", "#455a64", "#6d4c41",
      "#3949ab", "#e65100", "#558b2f", "#6a1b9a", "#ad1457",
      "#0277bd", "#00695c", "#9e9d24", "#5d4037", "#546e7a"],
    dark: ["#56b4e9", "#ff9a55", "#5ed6a8", "#c79bf2", "#ff7474",
      "#64d8df", "#f58ac3", "#eed25a", "#b0c4de", "#d7aa88",
      "#9baaff", "#ffc078", "#b6dd72", "#e1a6ff", "#ffb0cd",
      "#8cd8ff", "#8be0cb", "#d9e68a", "#eac2a8", "#c1d0dc"]
  };
  const colorAssignments = stations => new Map(
    [...new Set(stations)].sort().map((station, index) => [station, index])
  );
  const color = (station, dark, assignments) => {
    const index = assignments.get(station) ?? 0;
    return palette[dark ? "dark" : "light"][index % palette.light.length];
  };
  const dash = (station, assignments) => assignments.get(station) >= 10 ? "8 4" : "";
  const bounds = (values) => {
    if (!values.length) return [-1, 1];
    let low = Infinity, high = -Infinity;
    for (const v of values) { low = Math.min(low, v); high = Math.max(high, v); }
    const padding = Math.max(1, (high - low) * 0.1);
    if (low === high) return [low - 5, high + 5];
    return [Math.floor(low - padding), Math.ceil(high + padding)];
  };
  const nearest = (points, timestamp) => {
    if (!points.length) return null;
    let lo = 0, hi = points.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (points[mid].t < timestamp) lo = mid + 1; else hi = mid;
    }
    if (lo === 0) return points[0];
    if (lo === points.length) return points[lo - 1];
    return timestamp - points[lo - 1].t <= points[lo].t - timestamp ? points[lo - 1] : points[lo];
  };
  if (typeof module !== "undefined" && module.exports) module.exports = {segments, colorAssignments, color, dash, bounds, nearest};
  if (typeof document === "undefined") return;

  const dataElement = document.getElementById("neighbor-data");
  if (!dataElement) return;
  const config = JSON.parse(dataElement.textContent);
  const history = config.history;
  const assignments = colorAssignments(history.series.map(s => s.station));
  const enabled = new Set(history.selected);
  const legend = document.getElementById("neighbor-legend");
  const readout = document.getElementById("neighbor-readout");
  const NS = "http://www.w3.org/2000/svg";
  const element = (name, attrs = {}, text = null) => {
    const e = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) e.setAttribute(key, value);
    if (text !== null) e.textContent = text;
    return e;
  };
  const dateOptions = history.until - history.since > 86400
    ? {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"}
    : {hour: "2-digit", minute: "2-digit"};
  if (config.utc) dateOptions.timeZone = "UTC";
  const ticks = new Intl.DateTimeFormat("cs-CZ", dateOptions);
  const detailed = new Intl.DateTimeFormat("cs-CZ", {...dateOptions, day: "2-digit", month: "2-digit", second: "2-digit"});
  const isDark = () => document.documentElement.dataset.theme === "dark";

  for (const series of history.series) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox"; checkbox.checked = true;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) enabled.add(series.station); else enabled.delete(series.station);
      draw();
    });
    const swatch = document.createElement("span");
    swatch.className = "neighbor-swatch";
    swatch.dataset.station = series.station;
    label.append(checkbox, swatch, document.createTextNode(series.station));
    legend.append(label);
  }

  function drawChart(metric, unit) {
    const svg = document.getElementById("neighbor-" + metric);
    svg.replaceChildren();
    const width = Math.max(360, Math.min(1100, svg.getBoundingClientRect().width));
    const height = 280, left = 62, right = width - 18, top = 24, bottom = 235;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const series = history.series.filter(s => enabled.has(s.station));
    const values = series.flatMap(s => s.points.map(p => p[metric]));
    const [low, high] = bounds(values);
    const x = t => left + (t - history.since) / (history.until - history.since) * (right - left);
    const y = value => bottom - (value - low) / (high - low) * (bottom - top);
    svg.append(element("text", {x: 4, y: 14}, unit));
    for (let i = 0; i <= 5; ++i) {
      const value = low + (high - low) * i / 5;
      svg.append(element("line", {x1: left, x2: right, y1: y(value), y2: y(value), class: "signal-grid"}));
      svg.append(element("text", {x: left - 8, y: y(value) + 4, "text-anchor": "end"}, String(Number(value.toFixed(1)))));
    }
    const tickCount = width < 600 ? 2 : 4;
    for (let i = 0; i <= tickCount; ++i) {
      const t = history.since + (history.until - history.since) * i / tickCount;
      svg.append(element("text", {x: x(t), y: bottom + 25, "text-anchor": i === 0 ? "start" : i === tickCount ? "end" : "middle"}, ticks.format(new Date(t * 1000))));
    }
    if (!values.length) {
      svg.append(element("text", {x: (left + right) / 2, y: (top + bottom) / 2, "text-anchor": "middle"}, "Vyberte sousedy s dostupnými měřeními."));
    }
    for (const s of series) {
      const stroke = color(s.station, isDark(), assignments);
      const dots = s.points.map(p => `M${x(p.t).toFixed(2)},${y(p[metric]).toFixed(2)}h0.1`).join(" ");
      svg.append(element("path", {d: dots, stroke, "stroke-width": 4, "stroke-linecap": "round", fill: "none", opacity: 0.8}));
      for (const line of segments(s.trend, config.gap_seconds)) {
        if (line.length < 2) continue;
        const path = line.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(2)},${y(p[metric]).toFixed(2)}`).join(" ");
        svg.append(element("path", {d: path, stroke, "stroke-width": 2.5, "stroke-dasharray": dash(s.station, assignments), fill: "none"}));
      }
    }
    const cursor = element("line", {y1: top, y2: bottom, class: "signal-cursor", visibility: "hidden"});
    const marker = element("circle", {r: 5, fill: "none", "stroke-width": 2, visibility: "hidden"});
    svg.append(cursor, marker);
    svg.onpointermove = (event) => {
      const rect = svg.getBoundingClientRect();
      const px = (event.clientX - rect.left) * width / rect.width;
      const py = (event.clientY - rect.top) * height / rect.height;
      if (px < left || px > right) return;
      const timestamp = history.since + (px - left) / (right - left) * (history.until - history.since);
      let closest = null, distance = Infinity;
      for (const s of series) {
        const p = nearest(s.points, timestamp);
        if (!p) continue;
        const d = Math.hypot(x(p.t) - px, y(p[metric]) - py);
        if (d < distance) { closest = {p, station: s.station}; distance = d; }
      }
      if (!closest || distance > 45) {
        cursor.setAttribute("visibility", "hidden"); marker.setAttribute("visibility", "hidden");
        readout.textContent = "V tomto místě grafu není blízké měření.";
        return;
      }
      const {p, station} = closest;
      cursor.setAttribute("x1", x(p.t)); cursor.setAttribute("x2", x(p.t)); cursor.setAttribute("visibility", "visible");
      marker.setAttribute("cx", x(p.t)); marker.setAttribute("cy", y(p[metric])); marker.setAttribute("stroke", color(station, isDark(), assignments)); marker.setAttribute("visibility", "visible");
      readout.textContent = `${station} · ${detailed.format(new Date(p.t * 1000))}${config.utc ? " UTC" : ""} · RSSI ${p.rssi} dBm · SNR ${p.snr} dB · pořadí ${p.seq}`;
    };
    svg.onpointerleave = () => {
      cursor.setAttribute("visibility", "hidden"); marker.setAttribute("visibility", "hidden");
    };
  }
  function draw() {
    for (const s of legend.querySelectorAll(".neighbor-swatch")) {
      s.style.backgroundColor = color(s.dataset.station, isDark(), assignments);
      s.style.borderStyle = dash(s.dataset.station, assignments) ? "dashed" : "solid";
    }
    drawChart("rssi", "RSSI [dBm]"); drawChart("snr", "SNR [dB]");
  }
  new MutationObserver(draw).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(draw, 100); });
  draw();
})();
