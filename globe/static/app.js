const state = {
  meta: null,
  metric: "total_population",
  year: 2024,
  geoid: null,
  booted: false,
  projection: "globe",
  map: null,
  layer: null,
};

const $ = (id) => document.getElementById(id);

const PROMPTS = [
  "What is the median household income in Miami-Dade County in 2024?",
  "Which counties grew fastest from 2019 to 2024?",
  "How does Orange County compare with Florida on median age in 2024?",
  "Is the income estimate in Liberty County reliable?",
];

function motionOk() {
  return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

async function boot() {
  state.meta = await fetch("/api/meta").then((res) => res.json());
  renderChrome();
  const [counties, land] = await Promise.all([
    fetch("/data/fl_counties.geojson").then((res) => res.json()),
    fetch("/data/ne_110m_land.geojson").then((res) => res.json()),
  ]);
  initMap(counties, land);
}

function renderChrome() {
  const meta = state.meta;
  $("source-line").textContent = `${meta.source.program}. ${meta.source.access} Boundaries: ${meta.source.boundaries}. ${meta.source.endorsement}`;
  $("llm-banner").textContent = meta.llm.reason;
  const layers = $("layers");
  layers.innerHTML = "";
  for (const metric of meta.metrics.filter((item) => item.layer)) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = metric.label;
    button.setAttribute("aria-pressed", String(metric.name === state.metric));
    button.addEventListener("click", () => {
      state.metric = metric.name;
      for (const other of layers.querySelectorAll("button")) other.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-pressed", "true");
      refreshLayer();
    });
    layers.append(button);
  }
  const list = $("county-list");
  const counties = [...meta.counties].sort((a, b) => a.name.localeCompare(b.name));
  for (const county of counties) {
    const button = document.createElement("button");
    button.type = "button";
    button.role = "option";
    button.dataset.geoid = county.geoid;
    button.dataset.name = county.name.toLowerCase();
    button.textContent = county.name;
    button.addEventListener("click", () => selectCounty(county.geoid, true));
    list.append(button);
  }
  for (const id of ["compare-a", "compare-b"]) {
    const select = $(id);
    select.innerHTML = '<option value="">Choose a county</option>';
    for (const county of counties) {
      const option = document.createElement("option");
      option.value = county.geoid;
      option.textContent = county.name;
      select.append(option);
    }
  }
  const prompts = $("prompts");
  for (const text of PROMPTS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", () => {
      $("question").value = text;
      ask(text);
    });
    prompts.append(button);
  }
}

function initMap(counties, land) {
  const map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {},
      layers: [{ id: "background", type: "background", paint: { "background-color": "#07111d" } }],
    },
    center: [-83.4, 27.9],
    zoom: 5.35,
    minZoom: 1.4,
    maxZoom: 9,
    attributionControl: false,
    projection: { type: "globe" },
  });
  state.map = map;
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.AttributionControl({
    compact: true,
    customAttribution: "© GeoKnight GIS · U.S. Census Bureau boundaries · Natural Earth",
  }), "bottom-right");
  map.on("load", () => {
    if (typeof map.setProjection === "function") map.setProjection({ type: "globe" });
    if (typeof map.setFog === "function") {
      map.setFog({
        color: "rgb(16, 42, 68)",
        "high-color": "rgb(8, 22, 40)",
        "horizon-blend": 0.12,
        "space-color": "rgb(4, 8, 14)",
        "star-intensity": 0.28,
      });
    }
    map.addSource("land", { type: "geojson", data: land });
    map.addSource("counties", { type: "geojson", data: counties });
    map.addLayer({ id: "land", type: "fill", source: "land", paint: { "fill-color": "#123049", "fill-opacity": 1 } });
    map.addLayer({ id: "land-line", type: "line", source: "land", paint: { "line-color": "#1d4c6c", "line-width": 0.7 } });
    map.addLayer({ id: "counties", type: "fill", source: "counties", paint: { "fill-color": "#314255", "fill-opacity": 0.94 } });
    map.addLayer({ id: "counties-line", type: "line", source: "counties", paint: { "line-color": "#f6e7b8", "line-width": 1.1 } });
    map.addLayer({
      id: "counties-active",
      type: "line",
      source: "counties",
      paint: { "line-color": "#fff6d8", "line-width": 2.2 },
      filter: ["==", ["get", "geoid"], ""],
    });
    frameFlorida(false, true);
    map.on("mousemove", "counties", (event) => {
      const feature = event.features && event.features[0];
      if (!feature) return;
      const row = (state.layer?.features || []).find((item) => item.geoid === feature.properties.geoid);
      const hover = $("hover");
      hover.hidden = false;
      hover.style.left = `${Math.min(event.point.x + 12, map.getContainer().clientWidth - 180)}px`;
      hover.style.top = `${event.point.y + 12}px`;
      hover.textContent = row ? `${row.name}: ${row.display}` : feature.properties.name;
    });
    map.on("mouseleave", "counties", () => { $("hover").hidden = true; });
    map.on("click", "counties", (event) => {
      const feature = event.features && event.features[0];
      if (feature) selectCounty(feature.properties.geoid, true);
    });
    refreshLayer();
  });
}

function frameFlorida(animate, close) {
  const box = state.meta.bbox;
  const center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2];
  const zoom = state.projection === "globe" ? (close ? 5.35 : 2.15) : (close ? 6.15 : 5.5);
  const view = { center, zoom };
  if (animate && motionOk()) state.map.easeTo({ ...view, duration: 600 });
  else state.map.jumpTo(view);
}

function setProjection(next) {
  state.projection = next;
  state.map.setProjection({ type: next === "globe" ? "globe" : "mercator" });
  $("view-globe").setAttribute("aria-pressed", String(next === "globe"));
  $("view-flat").setAttribute("aria-pressed", String(next === "flat"));
  frameFlorida(true);
}

async function refreshLayer() {
  const metric = state.meta.metrics.find((item) => item.name === state.metric);
  const growth = Boolean(metric && metric.growth);
  $("year-2019").disabled = growth;
  $("year-2024").disabled = growth;
  const query = new URLSearchParams({ metric: state.metric });
  if (!growth) query.set("year", String(state.year));
  const layer = await fetch(`/api/layer?${query}`).then((res) => res.json());
  state.layer = layer;
  $("period-line").textContent = `${layer.label} · ${layer.period}`;
  $("layer-note").textContent = growth
    ? "This layer uses the non-overlapping 2019 to 2024 window. Income change is nominal, not adjusted for inflation."
    : "Gold is the higher end of the published values, not a score. Counties without an estimate stay gray.";
  paint(layer);
  renderLegend(layer);
  if (!state.booted) {
    const top = layer.features.reduce((best, row) => (row.value != null && (best == null || row.value > best.value) ? row : best), null);
    if (top) await selectCounty(top.geoid, false);
    state.booted = true;
    const shot = new URLSearchParams(location.search).get("shot");
    if (shot === "ask") {
      showTab("ask");
      const question = "What is the median household income in Miami-Dade County in 2024?";
      $("question").value = question;
      await ask(question);
    } else if (shot === "flat") {
      setProjection("flat");
    } else if (shot === "close") {
      frameFlorida(false, true);
    } else if (shot === "compare") {
      showTab("compare");
      const miami = state.meta.counties.find((county) => county.name.startsWith("Miami-Dade"));
      const orange = state.meta.counties.find((county) => county.name.startsWith("Orange"));
      if (miami && orange) {
        state.geoid = miami.geoid;
        highlight(miami.geoid);
        $("compare-a").value = miami.geoid;
        $("compare-b").value = orange.geoid;
        await loadCompare();
      }
    }
    document.body.dataset.map = "ready";
  } else if (state.geoid) {
    highlight(state.geoid);
    loadDetail();
  }
}

function paint(layer) {
  if (!state.map.getLayer("counties")) return;
  const expression = ["match", ["get", "geoid"]];
  for (const feature of layer.features) expression.push(feature.geoid, feature.color);
  expression.push("#314255");
  state.map.setPaintProperty("counties", "fill-color", expression);
}

function renderLegend(layer) {
  const legend = $("legend");
  legend.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = layer.label;
  legend.append(title);
  const period = document.createElement("div");
  period.className = "note";
  period.textContent = layer.period;
  legend.append(period);
  for (const item of layer.legend) {
    const row = document.createElement("div");
    row.className = "swatch";
    const swatch = document.createElement("i");
    swatch.style.background = item.color;
    row.append(swatch, document.createTextNode(`${item.min_display} to ${item.max_display} · ${item.count}`));
    legend.append(row);
  }
  if (layer.missing_count) {
    const row = document.createElement("div");
    row.className = "swatch";
    const swatch = document.createElement("i");
    swatch.style.background = layer.no_data_color;
    row.append(swatch, document.createTextNode(`No published estimate · ${layer.missing_count}`));
    legend.append(row);
  }
  const state = document.createElement("p");
  state.className = "note";
  state.textContent = `Florida: ${layer.state.display}${layer.state.moe_display ? ` (${layer.state.moe_display})` : ""}`;
  legend.append(state);
}

function highlight(geoid) {
  if (state.map && state.map.getLayer("counties-active")) {
    state.map.setFilter("counties-active", ["==", ["get", "geoid"], geoid || ""]);
  }
  for (const button of $("county-list").querySelectorAll("button")) {
    button.setAttribute("aria-selected", String(button.dataset.geoid === geoid));
  }
}

async function selectCounty(geoid, fly) {
  state.geoid = geoid;
  $("compare-a").value = geoid;
  highlight(geoid);
  showTab("detail");
  if (fly && state.map) {
    const features = state.map.querySourceFeatures("counties", { filter: ["==", ["get", "geoid"], geoid] });
    if (features[0]) {
      const center = centerOf(features[0]);
      if (center) {
        state.map.easeTo({ center, zoom: Math.max(state.map.getZoom(), 5.1), duration: motionOk() ? 500 : 0 });
      }
    }
  }
  await loadDetail();
  if ($("compare-b").value) loadCompare();
}

function centerOf(feature) {
  const coords = [];
  const walk = (node) => {
    if (typeof node[0] === "number") coords.push(node);
    else node.forEach(walk);
  };
  walk(feature.geometry.coordinates);
  if (!coords.length) return null;
  const lon = coords.reduce((sum, pair) => sum + pair[0], 0) / coords.length;
  const lat = coords.reduce((sum, pair) => sum + pair[1], 0) / coords.length;
  return [lon, lat];
}

async function loadDetail() {
  if (!state.geoid) return;
  const profile = await fetch(`/api/county?geoid=${state.geoid}&year=${state.year}`).then((res) => res.json());
  $("detail-empty").hidden = true;
  const body = $("detail-body");
  body.hidden = false;
  body.innerHTML = "";
  const title = document.createElement("h2");
  title.textContent = profile.name;
  body.append(title);
  const period = document.createElement("p");
  period.className = "note";
  period.textContent = `ACS 5-year ${profile.year} (${profile.period.replace("-", "–")}). GEOID ${profile.geoid}.`;
  body.append(period);
  const stock = profile.metrics.filter((item) => !item.period.includes(" to "));
  const growth = profile.metrics.filter((item) => item.period.includes(" to "));
  body.append(metricGroup("This vintage", stock, profile.state.metrics));
  body.append(metricGroup("Change from 2015–2019 to 2020–2024", growth, profile.state.metrics));
}

function metricGroup(heading, rows, stateRows) {
  const wrap = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = heading;
  wrap.append(title);
  for (const row of rows) {
    const card = document.createElement("div");
    card.className = "metric";
    const name = document.createElement("div");
    name.textContent = row.label;
    const value = document.createElement("strong");
    value.textContent = row.display;
    if (row.moe_display) value.textContent += ` (${row.moe_display})`;
    if (row.low_reliability) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "Use with caution";
      value.append(badge);
    }
    const note = document.createElement("p");
    note.className = "note";
    const state = stateRows.find((item) => item.metric === row.metric);
    note.textContent = `${row.uncertainty} Florida: ${state ? state.display : "not available"}.`;
    card.append(name, value, note);
    wrap.append(card);
  }
  return wrap;
}

async function loadCompare() {
  const a = $("compare-a").value;
  const b = $("compare-b").value;
  const host = $("compare-body");
  host.innerHTML = "";
  if (!a || !b || a === b) {
    host.textContent = "Choose two different counties. The active layer supplies the metric.";
    return;
  }
  const query = new URLSearchParams({ a, b, metric: state.metric, year: String(state.year) });
  const payload = await fetch(`/api/compare?${query}`).then((res) => res.json());
  const answer = document.createElement("p");
  answer.className = "answer";
  answer.textContent = payload.answer || payload.error;
  host.append(answer);
  const rows = payload.evidence && payload.evidence.readings;
  if (rows) host.append(evidenceTable(rows));
}

function evidenceTable(rows) {
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const label of ["Place", "Estimate", "90% margin", "Note"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    head.append(cell);
  }
  table.append(head);
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const text of [row.name || "", row.display, row.moe_display || "not published", row.uncertainty || ""]) {
      const cell = document.createElement("td");
      cell.textContent = text;
      tr.append(cell);
    }
    table.append(tr);
  }
  return table;
}

async function ask(question) {
  const host = $("ask-body");
  host.innerHTML = "";
  const response = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      year: state.year,
      geoid: state.geoid,
      compare_geoid: $("compare-b").value || null,
    }),
  });
  const payload = await response.json();
  const answer = document.createElement("p");
  answer.className = "answer";
  answer.textContent = payload.answer || payload.error;
  host.append(answer);
  if (payload.llm) {
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = payload.llm.contract_verified
      ? payload.llm.reason
      : `${payload.llm.reason} The language-model contract is not verified, so the paragraph above is the governed text.`;
    host.append(note);
  }
  if (payload.model_text) {
    const model = document.createElement("p");
    model.className = "answer";
    model.textContent = `Endpoint wording (not verified as the LLM Lab): ${payload.model_text}`;
    host.append(model);
  }
  const rows = payload.evidence && payload.evidence.rows;
  if (rows && rows.length) host.append(evidenceTable(rows));
}

function showTab(name) {
  for (const tab of ["detail", "compare", "ask"]) {
    $(`pane-${tab}`).hidden = tab !== name;
    $(`tab-${tab}`).setAttribute("aria-selected", String(tab === name));
  }
}

$("year-2019").addEventListener("click", () => setYear(2019));
$("year-2024").addEventListener("click", () => setYear(2024));
function setYear(year) {
  state.year = year;
  $("year-2019").setAttribute("aria-pressed", String(year === 2019));
  $("year-2024").setAttribute("aria-pressed", String(year === 2024));
  refreshLayer();
}
$("view-globe").addEventListener("click", () => setProjection("globe"));
$("view-flat").addEventListener("click", () => setProjection("flat"));
$("view-florida").addEventListener("click", () => frameFlorida(true, true));
$("county-search").addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();
  for (const button of $("county-list").querySelectorAll("button")) {
    button.hidden = query && !button.dataset.name.includes(query);
  }
});
for (const tab of ["detail", "compare", "ask"]) {
  $(`tab-${tab}`).addEventListener("click", () => showTab(tab));
}
$("compare-a").addEventListener("change", loadCompare);
$("compare-b").addEventListener("change", loadCompare);
$("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $("question").value.trim();
  if (question) ask(question);
});

boot().catch((error) => {
  $("period-line").textContent = "The globe data did not load.";
  console.error(error);
});
