// Shared by the live page (dashboard.html) and the recorded-data page (history.html).
// Expects the page to define $ and cellbox.
const esc = s => String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function renderDecoded(d) {
  const pol = d.polled || [];
  $("valbox").style.display = pol.length ? "" : "none";
  const order = ["Vehicle", "Battery", "Charging", "Drive", "Climate", "Chassis", "Body"];
  const groups = {};
  // the cell and module voltage summaries are shown in the cell table below, not as cards
  // also dropped as duplicates: the VIN (in the Car card) and the health block's second capacity and raw SOH
  const inTable = v => /^(Cell|Module) voltage (min|max|average|spread)$/.test(v.name)
    || ["VIN", "Pack capacity (health block)", "State of health (raw)"].includes(v.name);
  pol.filter(v => !inTable(v)).forEach(v => (groups[v.group] = groups[v.group] || []).push(v));
  // first in the Battery group: state of health, state of charge, Hx
  const top = ["State of health", "State of charge", "Hx"];
  (groups.Battery || []).sort((a, b) => (top.indexOf(a.name) + 1 || 99) - (top.indexOf(b.name) + 1 || 99));
  const fmt = v => typeof v === "number" ? v.toLocaleString(undefined, {maximumFractionDigits: 2}) : v;
  $("vals").innerHTML = Object.keys(groups).sort((a, b) => order.indexOf(a) - order.indexOf(b)).map(g =>
    `<h2>${esc(g === "Battery" ? "HV Battery" : g)}</h2><div class="grid" style="margin-bottom:16px">` +
    groups[g].map(v => `<div class="card" title="updated ${v.age} s ago"><div class="sub">${esc(v.name)}</div>
      <div class="big" style="font-size:20px">${esc(fmt(v.value))} <span class="sub">${esc(v.unit)}</span></div></div>`).join("") +
    `</div>` + (g === "Battery" ? `<div id="cellslot"></div>` : "")).join("");
  // the cell voltage table goes right under the Battery cards
  ($("cellslot") || $("valbox")).appendChild(cellbox);
  const cv = (d.cells || []).filter(c => c > 0 && c < 6000);
  cellbox.style.display = cv.length ? "" : "none";
  if (cv.length) {
    const avg = cv.reduce((a, b) => a + b, 0) / cv.length;
    const spread = Math.max(...cv) - Math.min(...cv);
    const range = `${Math.min(...cv)}–${Math.max(...cv)} mV, average ${Math.round(avg)} mV`;
    // Limits: spread up to 30 mV is good (the Autel report's voltage delta threshold), over 100 mV is
    // bad (forum rule of thumb), in between is a warning. Judged at any pack current.
    const lvl = (v, good, bad) => v <= good ? "ok" : v <= bad ? "warn" : "bad";
    const verdict = {ok: "Good", warn: "Warning", bad: "Bad"}[lvl(spread, 30, 100)];
    const vk = lvl(spread, 30, 100);
    const note = "limits: good up to 30 mV, warning up to 100 mV, bad above";
    // 24 modules of 4 consecutive cells, as in the Autel battery report of this car.
    const rows = [];
    for (let m = 0; m < 24 && m * 4 < d.cells.length; m++) {
      const cells = d.cells.slice(m * 4, m * 4 + 4);
      const sum = cells.reduce((a, b) => a + b, 0) / 1000;
      const tiles = cells.map((c, i) => {
        // within 10 mV of the average green, further below blue, further above red
        const dv = c - Math.round(avg);
        const bg = Math.abs(dv) <= 10 ? "hsl(140 45% 28%)" : dv < 0 ? "hsl(215 60% 38%)" : "hsl(0 60% 38%)";
        return `<td class="m" style="padding:3px 6px"><div title="cell ${m * 4 + i + 1}: ${dv >= 0 ? "+" : ""}${dv} mV from average"
          style="background:${bg};color:#fff;border-radius:5px;padding:4px 0;text-align:center">${(c / 1000).toFixed(3)} V</div></td>`;
      }).join("");
      // module name: orange when a cell is 10-30 mV from the average, red beyond 30 mV
      const worst = Math.max(...cells.map(c => Math.abs(c - Math.round(avg))));
      const mc = worst > 30 ? "hsl(0 80% 60%)" : worst > 10 ? "hsl(30 90% 55%)" : "";
      rows.push(`<tr><td${mc ? ` style="color:${mc};font-weight:600"` : ""}>M${m + 1} <span class="sub">(#${m * 4 + 1}–#${m * 4 + cells.length})</span></td>
        <td class="m"><b>${sum.toFixed(2)} V</b></td>${tiles}</tr>`);
    }
    $("cells").innerHTML = `<div class="card ${vk}" style="margin-bottom:8px"><span class="dot"></span><b>Cell spread ${spread} mV: ${verdict}</b>
      <div class="sub">${range}</div><div class="sub">* ${note}</div></div>
      <div class="card"><div class="scroll" style="max-height:none"><table><thead><tr>
      <th>Module</th><th>Voltage</th><th>Cell 1</th><th>Cell 2</th><th>Cell 3</th><th>Cell 4</th></tr></thead>
      <tbody>${rows.join("")}</tbody></table></div></div>`;
    const tm = t => new Date(t * 1000).toLocaleTimeString([], {hour12: false});
    const col = v => v < 0 ? "hsl(215 70% 65%)" : "hsl(0 70% 65%)";
    const hist = (d.history || []).map(e => `<tr><td><b>M${e.module}</b></td><td>#${e.cell}</td>
      <td class="m">${(e.lo / 1000).toFixed(3)} V</td><td class="m">${(e.hi / 1000).toFixed(3)} V</td>
      <td class="m" style="color:${col(e.peak)}"><b>${e.peak > 0 ? "+" : ""}${e.peak} mV</b></td>
      <td class="m">${tm(e.first)}</td></tr>`).join("");
    $("cells").innerHTML += `<div class="card" style="margin-top:8px"><div class="scroll" style="max-height:none"><table><thead><tr>
      <th colspan="6">Cells that went out of balance (more than 30 mV from the average)</th></tr><tr>
      <th>Module</th><th>Cell</th><th>Lowest</th><th>Highest</th><th>Worst vs average</th><th>First out</th></tr></thead>
      <tbody>${hist || `<tr><td colspan="6" class="empty">None in this session.</td></tr>`}</tbody></table></div></div>`;
  }
  const sg = d.signals || [];
  $("sigsum").textContent = !d.have_dbc ? "(signal files missing: run server/fetch_dbc.sh)"
    : `${sg.length} signals · set ${d.dbc}`;
  if (!$("sigbox").open) return;
  const f = $("sigflt").value.trim().toLowerCase();
  const rows = sg.filter(x => !f || (x.msg + " " + x.sig + " " + x.ch).toLowerCase().includes(f)).slice(0, 600);
  $("sigs").innerHTML = rows.length ? rows.map(x => `<tr><td>${esc(x.ch)}</td><td class="m">${esc(x.id)}</td>
    <td>${esc(x.sig)}</td><td class="m">${esc(x.text ?? x.value)}${x.text != null ? ` <span class="sub">(${esc(x.value)})</span>` : ""}</td>
    <td>${esc(x.unit)}</td></tr>`).join("") : `<tr><td colspan="5" class="empty">No decoded signals.</td></tr>`;
}
