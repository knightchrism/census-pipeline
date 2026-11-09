/* Florida Census Globe. Standard catalog map shell. Figures come from catalog.json. */
const ROOT = new URL("../", document.currentScript.src).href;
const $ = (id) => document.getElementById(id);
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg";
const RAMP = ["#1f4f78", "#4e7ea3", "#c4a15a", "#e4c56a", "#ffe7a3"];
const HIDDEN_TOPICS = new Set(["hispanic_population", "hispanic_share"]);
const HIDDEN_SOURCES = new Set(["historical-statewide"]);
const state = { catalog: null, topic: "population", sourceId: "pep-v2025-july1", year: 2025, geoid: null, globe: true };

function fmt(unit, value) {
  if (value == null) return "not available";
  const n = Number(value);
  if (unit === "people") return Math.round(n).toLocaleString("en-US");
  if (unit === "USD") return "$" + Math.round(n).toLocaleString("en-US");
  if (unit === "years") return n.toFixed(1);
  if (unit === "ratio") return (n * 100).toFixed(2) + "%";
  return String(value);
}
function sourceById(id) { return state.catalog.sources.find((item) => item.id === id); }
function topicById(id) { return state.catalog.topics.find((item) => item.id === id); }
function shownTopics() { return state.catalog.topics.filter((item) => !HIDDEN_TOPICS.has(item.id)); }
function shownSourceIds(topic) { return topic.sources.filter((id) => !HIDDEN_SOURCES.has(id) && !HIDDEN_TOPICS.has(id)); }
function acsUnit(metric) {
  return { total_population: "people", median_household_income: "USD", median_age: "years", hispanic_population: "people", hispanic_share: "ratio" }[metric];
}
function reading(place, source, year, topicId) {
  if (!source.loaded) return null;
  if (source.id === "pep-v2025-july1") return { value: place.pep_july1[String(year)], moe: null, unit: "people" };
  if (source.id === "pep-v2025-april1-base") return { value: place.pep_april1_2020_base, moe: null, unit: "people" };
  if (source.id === "decennial-april1") return { value: place.decennial[String(year)], moe: null, unit: "people" };
  if (source.id === "historical-statewide") {
    if (place.geoid) return null;
    const obs = source.observations.find((row) => row.year === Number(year));
    return obs ? { value: obs.population, moe: null, unit: "people", status: obs.status, reference_date: obs.reference_date } : null;
  }
  const metric = topicById(topicId || state.topic).acs_metric;
  const item = place.acs5[String(year)][metric];
  return { value: item.value, moe: item.moe, unit: acsUnit(metric), uncertainty: item.uncertainty, low_reliability: item.low_reliability };
}
function placeByGeoid(geoid) {
  return geoid === "state" ? state.catalog.state : state.catalog.counties.find((item) => item.geoid === geoid);
}

function mkMap() {
  return new maplibregl.Map({
    container: "map",
    attributionControl: false,
    maxZoom: 15,
    center: [-83.7, 27.6],
    zoom: innerWidth < 760 ? 4.6 : 5.2,
    pitch: 55,
    canvasContextAttributes: { preserveDrawingBuffer: true },
    style: {
      version: 8,
      projection: { type: "globe" },
      sky: { "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 6, 0.5] },
      sources: {
        bm: { type: "raster", tileSize: 256, maxzoom: 8, tiles: [GIBS] },
        counties: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
        stateoutline: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": "#050d1c" } },
        { id: "bm", type: "raster", source: "bm", paint: { "raster-brightness-max": 0.6, "raster-saturation": -0.3 } },
        { id: "fill", type: "fill", source: "counties", paint: { "fill-color": "#314255", "fill-opacity": 0.58 } },
        { id: "ex", type: "fill-extrusion", source: "counties", paint: { "fill-extrusion-color": "#314255", "fill-extrusion-height": 0, "fill-extrusion-opacity": 0.86 } },
        { id: "line", type: "line", source: "counties", paint: { "line-color": "#f6e7b8", "line-width": 0.8 } },
        { id: "state-line", type: "line", source: "stateoutline", paint: { "line-color": "#f6e7b8", "line-width": 1.6, "line-opacity": 0 } },
      ],
    },
  });
}

let map;
const FLORIDA_BOUNDS = [[-87.63494, 24.5213], [-80.03136, 31.00089]];

function collectSamples(geojson) {
  const out = [];
  const walk = (coords, bucket) => {
    if (typeof coords[0] === "number") bucket.push(coords);
    else coords.forEach((part) => walk(part, bucket));
  };
  geojson.features.forEach((feature) => {
    const bucket = [];
    walk(feature.geometry.coordinates, bucket);
    if (!bucket.length) return;
    let west = bucket[0], east = bucket[0], south = bucket[0], north = bucket[0];
    bucket.forEach((coord, index) => {
      if (coord[0] < west[0]) west = coord;
      if (coord[0] > east[0]) east = coord;
      if (coord[1] < south[1]) south = coord;
      if (coord[1] > north[1]) north = coord;
      if (index % 10 === 0) out.push({ lng: coord[0], lat: coord[1], geoid: feature.properties.geoid });
    });
    [west, east, south, north].forEach((coord) => out.push({ lng: coord[0], lat: coord[1], geoid: feature.properties.geoid }));
  });
  return out;
}

function projectColumn(lng, lat, meters) {
  const ground = map.project([lng, lat]);
  const inner = map.transform && map.transform._mercatorTransform;
  if (!meters || !inner || !inner.coordinatePoint || !inner._pixelMatrix3D || !maplibregl.MercatorCoordinate) return ground;
  const coord = maplibregl.MercatorCoordinate.fromLngLat([lng, lat]);
  const base = inner.coordinatePoint(coord, 0, inner._pixelMatrix3D);
  const top = inner.coordinatePoint(coord, meters, inner._pixelMatrix3D);
  if (!base || !top || !isFinite(top.x) || !isFinite(top.y)) return ground;
  return { x: ground.x + (top.x - base.x), y: ground.y + (top.y - base.y) };
}

function safeRect() {
  const mapRect = $("map").getBoundingClientRect();
  const place = (el) => {
    const rect = el.getBoundingClientRect();
    return {
      left: rect.left - mapRect.left,
      top: rect.top - mapRect.top,
      right: rect.right - mapRect.left,
      bottom: rect.bottom - mapRect.top,
    };
  };
  const views = place(document.querySelector(".views"));
  const legend = place($("legend"));
  const panel = place($("panel"));
  const narrow = mapRect.width <= 760;
  let top = Math.max(8, views.bottom + 8);
  let bottom = mapRect.height - 36;
  if (narrow) {
    top = Math.max(top, legend.bottom + 8);
    const sheetTop = panel.top;
    if (sheetTop < mapRect.height - 4) bottom = Math.min(bottom, sheetTop - 10);
  }
  return { top, left: 22, right: mapRect.width - 28, bottom: Math.max(top + 80, bottom), width: mapRect.width, height: mapRect.height };
}

function frameFlorida() {
  if (!map || !state.samples) return;
  const pitch = $("extrude").checked && state.globe ? 55 : 0;
  map.setBearing(0);
  map.setProjection({ type: state.globe ? "globe" : "mercator" });
  const first = safeRect();
  map.fitBounds(FLORIDA_BOUNDS, {
    padding: {
      top: first.top,
      left: first.left,
      right: Math.max(12, first.width - first.right),
      bottom: Math.max(12, first.height - first.bottom),
    },
    pitch,
    duration: 0,
    maxZoom: 6.6,
  });
  map.jumpTo({ pitch, bearing: 0 });
  let fit = null;
  for (let step = 0; step < 14; step += 1) {
    const safe = safeRect();
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    state.samples.forEach((sample) => {
      const meters = $("extrude").checked ? (state.columnMeters[sample.geoid] || 0) : 0;
      const point = projectColumn(sample.lng, sample.lat, meters);
      minX = Math.min(minX, point.x);
      maxX = Math.max(maxX, point.x);
      minY = Math.min(minY, point.y);
      maxY = Math.max(maxY, point.y);
    });
    const overflowLeft = safe.left - minX;
    const overflowRight = maxX - safe.right;
    const overflowTop = safe.top - minY;
    const overflowBottom = maxY - safe.bottom;
    fit = { minX, minY, maxX, maxY, safe, zoom: map.getZoom(), overflowLeft, overflowRight, overflowTop, overflowBottom };
    if (overflowLeft <= 1 && overflowRight <= 1 && overflowTop <= 1 && overflowBottom <= 1) break;
    const roomX = Math.max(40, safe.right - safe.left);
    const roomY = Math.max(40, safe.bottom - safe.top);
    const spanX = maxX - minX;
    const spanY = maxY - minY;
    let zoom = map.getZoom();
    if (spanX > roomX || spanY > roomY) zoom = Math.max(3.5, zoom - Math.log2(Math.max(spanX / roomX, spanY / roomY)));
    const centerPx = map.project(map.getCenter());
    const shiftX = Math.max(overflowRight, 0) - Math.max(overflowLeft, 0);
    const shiftY = Math.max(overflowBottom, 0) - Math.max(overflowTop, 0);
    const next = map.unproject([centerPx.x + shiftX, centerPx.y + shiftY]);
    map.jumpTo({ center: next, zoom, pitch, bearing: 0 });
  }
  window.__fit = fit;
}

function statewideSelected() {
  const source = sourceById(state.sourceId);
  return !source || source.county_data === false;
}

function renderTimeline(source) {
  const box = $("timeline");
  const note = $("history-note");
  if (!source || source.county_data !== false) {
    box.hidden = true;
    note.hidden = true;
    box.replaceChildren();
    return;
  }
  box.hidden = false;
  note.hidden = false;
  box.replaceChildren();
  source.observations.forEach((obs) => {
    const tick = document.createElement("div");
    tick.className = "tick" + (obs.year === Number(state.year) ? " on" : "");
    const mark = document.createElement("b");
    tick.append(mark, document.createTextNode(String(obs.year)));
    tick.title = `${obs.status}, ${obs.reference_date}: ${fmt("people", obs.population)}`;
    box.append(tick);
    if (source.statehood && obs.year === source.statehood.before) {
      const event = document.createElement("div");
      event.className = "tick event";
      const diamond = document.createElement("b");
      event.append(diamond, document.createTextNode("1845"));
      event.title = "March 3, 1845: Florida became the 27th state. Not a population count.";
      box.append(event);
    }
  });
  note.textContent = "Each mark is one census. Statehood, March 3, 1845, sits between 1840 and 1850 and has no population. Years between censuses are not estimated.";
}

function paint() {
  const source = sourceById(state.sourceId);
  if (statewideSelected()) {
    state.columnMeters = {};
    map.setLayoutProperty("fill", "visibility", "none");
    map.setLayoutProperty("line", "visibility", "none");
    map.setLayoutProperty("ex", "visibility", "none");
    map.setPaintProperty("fill", "fill-opacity", 0);
    map.setPaintProperty("line", "line-opacity", 0);
    map.setPaintProperty("ex", "fill-extrusion-opacity", 0);
    map.setPaintProperty("ex", "fill-extrusion-height", 0);
    map.setLayoutProperty("state-line", "visibility", "visible");
    map.setPaintProperty("state-line", "line-opacity", 1);
    map.setPaintProperty("state-line", "line-width", 1.15);
    map.setPaintProperty("state-line", "line-color", "#f6e7b8");
    const legend = $("legend");
    legend.replaceChildren();
    const title = document.createElement("b");
    title.textContent = "Statewide count";
    const meta = document.createElement("div");
    meta.className = "small";
    const obs = source.observations.find((row) => row.year === Number(state.year));
    meta.textContent = obs ? `${obs.status} · ${obs.reference_date}` : "";
    const body = document.createElement("p");
    body.className = "small";
    body.textContent = "Present-day state outline only. Not a historical boundary and not a population layer.";
    legend.append(title, meta, body);
    return;
  }
  map.setLayoutProperty("fill", "visibility", "visible");
  map.setLayoutProperty("line", "visibility", "visible");
  map.setLayoutProperty("ex", "visibility", "visible");
  map.setPaintProperty("fill", "fill-opacity", 0.58);
  map.setPaintProperty("line", "line-opacity", 1);
  map.setPaintProperty("ex", "fill-extrusion-opacity", 0.86);
  map.setLayoutProperty("state-line", "visibility", "none");
  map.setPaintProperty("state-line", "line-opacity", 0);
  const values = {};
  state.catalog.counties.forEach((county) => {
    const item = reading(county, source, state.year);
    values[county.geoid] = item ? item.value : null;
  });
  const present = Object.values(values).filter((value) => value != null);
  const lo = present.length ? Math.min(...present) : 0;
  const hi = present.length ? Math.max(...present) : 1;
  const span = hi - lo || 1;
  const color = ["match", ["get", "geoid"]];
  const height = ["match", ["get", "geoid"]];
  state.columnMeters = {};
  state.catalog.counties.forEach((county) => {
    const value = values[county.geoid];
    if (value == null) {
      color.push(county.geoid, "#314255");
      height.push(county.geoid, 0);
    } else {
      const t = (value - lo) / span;
      const idx = Math.min(RAMP.length - 1, Math.floor(t * (RAMP.length - 1)));
      const meters = Math.round(12000 + t * 130000);
      state.columnMeters[county.geoid] = meters;
      color.push(county.geoid, RAMP[idx]);
      height.push(county.geoid, $("extrude").checked ? meters : 0);
    }
  });
  color.push("#314255");
  height.push(0);
  map.setPaintProperty("fill", "fill-color", color);
  map.setPaintProperty("ex", "fill-extrusion-color", color);
  map.setPaintProperty("ex", "fill-extrusion-height", height);
  const unit = reading(state.catalog.state, source, state.year).unit;
  const legend = $("legend");
  legend.replaceChildren();
  const title = document.createElement("b");
  title.textContent = topicById(state.topic).label;
  const meta = document.createElement("div");
  meta.className = "small";
  meta.textContent = source.periods
    ? `${source.short} · ${source.periods[state.year]}`
    : `${source.short} · ${source.reference_date}${String(source.reference_date).includes(String(state.year)) ? "" : " " + state.year}`;
  legend.append(title, meta);
  RAMP.forEach((swatch, index) => {
    const row = document.createElement("div");
    row.className = "swatch";
    const chip = document.createElement("i");
    chip.style.background = swatch;
    row.append(chip, document.createTextNode(fmt(unit, lo + span * index / (RAMP.length - 1))));
    legend.append(row);
  });
  const note = document.createElement("p");
  note.className = "small";
  note.textContent = state.catalog.map.extrusion;
  legend.append(note);
}

function showPlace() {
  const source = sourceById(state.sourceId);
  if (statewideSelected()) {
    const obs = source.observations.find((row) => row.year === Number(state.year));
    $("place-name").textContent = "Florida";
    $("place-meta").textContent = obs
      ? `${source.label}. ${obs.status === "territory" ? "Territory" : "State"} census, ${obs.reference_date}.`
      : source.label;
    $("place-kv").replaceChildren();
    [["Count", obs ? fmt("people", obs.population) : "not available"], ["Margin of error", "none (census count)"], ["Counties", "not in this series"]].forEach(([label, value]) => {
      const span = document.createElement("span");
      span.textContent = label;
      const strong = document.createElement("b");
      strong.textContent = value;
      $("place-kv").append(span, strong);
    });
    $("compare").disabled = true;
    $("compare-out").textContent = "County comparisons are not available. This series is statewide only.";
    return;
  }
  $("compare").disabled = false;
  const county = state.catalog.counties.find((item) => item.geoid === state.geoid) || state.catalog.counties[0];
  state.geoid = county.geoid;
  const item = reading(county, source, state.year);
  const stateItem = reading(state.catalog.state, source, state.year);
  $("place-name").textContent = county.name;
  const when = source.periods
    ? `period ${source.periods[state.year]}`
    : (String(source.reference_date).includes(String(state.year)) ? source.reference_date : `${source.reference_date} ${state.year}`);
  $("place-meta").textContent = `${source.label}. ${when}.`;
  const count = source.kind === "count";
  const moe = count ? "none (census count)" : (item.moe == null ? "not published" : "±" + fmt(item.unit, item.moe));
  $("place-kv").replaceChildren();
  [[count ? "Count" : "Estimate", fmt(item.unit, item.value)], [count ? "Margin of error" : "90% margin", moe], ["Florida", fmt(stateItem.unit, stateItem.value)]].forEach(([label, value]) => {
    const span = document.createElement("span");
    span.textContent = label;
    const strong = document.createElement("b");
    strong.textContent = value;
    $("place-kv").append(span, strong);
  });
  const other = $("compare").value;
  const out = $("compare-out");
  if (!other || other === county.geoid) {
    out.textContent = "Choose another county in this same source and year.";
    return;
  }
  const otherPlace = placeByGeoid(other);
  const otherItem = reading(otherPlace, source, state.year);
  if (item.value == null || otherItem.value == null) {
    out.textContent = "One estimate is not available. It is not treated as zero.";
    return;
  }
  const delta = item.value - otherItem.value;
  const moeNote = source.moe
    ? " Both 90% margins are on the county card. This difference is not a significance test, and overlapping ACS windows are not treated as independent."
    : " This source does not publish an ACS-style margin of error.";
  out.textContent = `${county.name} minus ${otherPlace.name}: ${fmt(item.unit, delta)}. Same source, same year, same metric.${moeNote}`;
}

function citationFor(source) {
  const pack = source.citations && source.citations[String(state.year)];
  if (!pack) return null;
  if (pack.by_metric) {
    const metric = topicById(state.topic).acs_metric;
    return pack.by_metric[metric] || null;
  }
  return pack;
}

function citeRow(label, value, href) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  if (value == null || value === "") {
    dd.className = "missing";
    dd.textContent = "Missing";
  } else if (href && /^https:\/\//.test(href)) {
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.rel = "noreferrer";
    anchor.textContent = value;
    dd.append(anchor);
  } else {
    dd.textContent = String(value);
  }
  return [dt, dd];
}

function renderCitation(source) {
  const box = $("citation");
  const validation = $("validation");
  const caveat = $("enum-caveat");
  box.replaceChildren();
  const cite = citationFor(source);
  if (!cite) {
    box.append(...citeRow("Official citation", null));
    validation.textContent = "";
  } else if (cite.traceable === false) {
    box.append(...citeRow("Official citation", null));
    box.append(...citeRow("Document", cite.document));
    box.append(...citeRow("Reference date", cite.reference_date));
    validation.textContent = cite.validation || "This observation is not tied to an official document in this build. Do not treat it as verified.";
  } else {
    box.append(...citeRow("Document", cite.document));
    box.append(...citeRow("Dataset", cite.dataset));
    if (cite.links && cite.links.length) {
      cite.links.forEach((link) => box.append(...citeRow("Official citation", link.label, link.url)));
    } else {
      box.append(...citeRow("Official citation", null));
    }
    box.append(...citeRow("Reference date", cite.reference_date));
    box.append(...citeRow("Period", cite.period));
    box.append(...citeRow("Revision vintage", cite.vintage));
    box.append(...citeRow("Variable", cite.variable));
    box.append(...citeRow("Column", cite.column));
    box.append(...citeRow("Table", cite.table));
    box.append(...citeRow("PDF page", cite.pdf_page));
    box.append(...citeRow("Retrieval", cite.retrieval));
    validation.textContent = cite.validation || "";
  }
  if (source.caveat) {
    caveat.hidden = false;
    caveat.textContent = "Enumeration caveat: " + source.caveat;
  } else {
    caveat.hidden = true;
    caveat.textContent = "";
  }
}

function fillSelectors() {
  const topic = $("topic");
  topic.innerHTML = "";
  const topics = shownTopics();
  if (!topics.some((item) => item.id === state.topic)) state.topic = topics[0].id;
  topics.forEach((item) => topic.add(new Option(item.label, item.id)));
  topic.value = state.topic;
  const chosen = topicById(state.topic);
  const source = $("source");
  source.innerHTML = "";
  shownSourceIds(chosen).forEach((id) => {
    const item = sourceById(id);
    const option = new Option(item.loaded ? item.short : item.short + " (not in this build)", item.id);
    option.disabled = !item.loaded;
    source.add(option);
  });
  if (!sourceById(state.sourceId).loaded || !shownSourceIds(chosen).includes(state.sourceId)) {
    state.sourceId = shownSourceIds(chosen).find((id) => sourceById(id).loaded);
  }
  source.value = state.sourceId;
  const spec = sourceById(state.sourceId);
  const year = $("year");
  year.innerHTML = "";
  spec.years.forEach((value) => {
    let label = spec.periods ? `${value} (${spec.periods[value]})` : String(value);
    if (spec.observations) {
      const obs = spec.observations.find((row) => row.year === value);
      if (obs) label = `${value} · ${obs.status}`;
    }
    year.add(new Option(label, String(value)));
  });
  if (!spec.years.map(String).includes(String(state.year))) state.year = spec.years[spec.years.length - 1];
  year.value = String(state.year);
  $("badge").textContent = spec.short;
  $("source-note").textContent = spec.note;
  renderCitation(spec);
  const blocked = state.catalog.sources.filter((item) => shownSourceIds(chosen).includes(item.id) && !item.loaded);
  const unavailable = $("unavailable");
  unavailable.hidden = blocked.length === 0;
  unavailable.textContent = blocked.length
    ? blocked.map((item) => `${item.short} is in this list and cannot be selected yet. This review build does not include those counts.`).join(" ")
    : "";
  if (spec.county_data === false) {
    if (!$("extrude").disabled) state.extrudeBeforeHistory = $("extrude").checked;
    $("extrude").checked = false;
    $("extrude").disabled = true;
  } else if ($("extrude").disabled) {
    $("extrude").disabled = false;
    $("extrude").checked = state.extrudeBeforeHistory !== false;
  }
  renderTimeline(spec);
  const compare = $("compare");
  const current = compare.value;
  compare.innerHTML = '<option value="">Another county</option>';
  state.catalog.counties.forEach((county) => compare.add(new Option(county.name, county.geoid)));
  compare.value = current;
}

function ask(question) {
  const text = question.trim();
  if (!text) return "Enter a question.";
  if (text.length > 480) return "Question is limited to 480 characters.";
  if (/\b(select|insert|update|delete|drop|alter)\b/i.test(text)) return "This panel does not run SQL.";
  const q = text.toLowerCase();
  if (/\b(hispanic|latino|latina|latinx|race|ethnicity|racial)\b/.test(q)) {
    return "Race and ethnicity topics are turned off in this view. Population, income, and age are still available.";
  }
  let topic = state.topic;
  if (HIDDEN_TOPICS.has(topic)) topic = "population";
  if (q.includes("income")) topic = "income";
  else if (q.includes("age")) topic = "age";
  else if (q.includes("population") || q.includes("people")) topic = "population";
  const specTopic = topicById(topic);
  const allowedSources = shownSourceIds(specTopic);
  let source = allowedSources.includes(state.sourceId) && sourceById(state.sourceId).loaded
    ? sourceById(state.sourceId)
    : sourceById(allowedSources.find((id) => sourceById(id).loaded));
  const yearMatch = text.match(/\b(18[3-9]0|19[0-9]0|2000|2010|2019|202[0-5])\b/);
  if (yearMatch && topic === "population" && !source.years.map(String).includes(yearMatch[1])) {
    const decennial = sourceById("decennial-april1");
    if (decennial && decennial.years.map(String).includes(yearMatch[1])) source = decennial;
    else if ((sourceById("historical-statewide") || { years: [] }).years.map(String).includes(yearMatch[1])) {
      return "The statewide 1830–2020 census view is turned off. County population, income, age, and the 2010 and 2020 county counts are still available.";
    }
  }
  const named = state.catalog.counties.find((county) => q.includes(county.name.toLowerCase().replace(" county", "")));
  const wantsState = /\bflorida\b/.test(q) && !named;
  const place = named || (wantsState ? state.catalog.state : state.catalog.counties.find((county) => county.geoid === state.geoid) || state.catalog.state);
  const year = yearMatch && source.years.map(String).includes(yearMatch[1]) ? Number(yearMatch[1]) : state.year;
  if (!source.years.map(String).includes(String(year))) {
    return `${source.label} is not loaded for ${yearMatch ? yearMatch[1] : "that year"}. Loaded years: ${source.years.join(", ")}.`;
  }
  const item = reading(place.geoid ? place : state.catalog.state, source, year, topic);
  const who = place.name || "Florida";
  const figure = source.kind === "count"
    ? "This is a census count, not a PEP or ACS estimate."
    : (item.moe == null ? "No 90% margin of error is published for this selection." : "90% margin of error " + fmt(item.unit, item.moe) + ".");
  return `${who}, ${specTopic.label}, ${source.label}, ${source.reference_date} ${year}: ${fmt(item.unit, item.value)}. ${figure} ${source.note}`;
}

async function loadJson(url) {
  let response;
  try {
    response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
  } catch (error) {
    throw new Error(url + " did not respond. " + (error && error.message ? error.message : "network error"));
  }
  const type = response.headers.get("content-type") || "Missing";
  const text = await response.text();
  const sample = text.replace(/\s+/g, " ").slice(0, 180);
  const where = "HTTP " + response.status + ". Final URL " + response.url + ". Content-Type " + type + ". Body: " + sample;
  if (!response.ok) throw new Error(url + " returned " + where);
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(url + " was not JSON. " + where);
  }
}

function startupFailed(error) {
  const message = error && error.message ? error.message : String(error);
  console.error(message);
  document.body.dataset.map = "failed";
  $("badge").textContent = "Startup failed";
  const box = $("startup");
  box.hidden = false;
  box.textContent = message;
  const note = $("source-note");
  if (note) note.textContent = message;
}

async function boot() {
  let catalog, counties;
  try {
    catalog = await loadJson(ROOT + "data/catalog.json");
    counties = await loadJson(ROOT + "data/fl_counties.geojson");
  } catch (error) {
    startupFailed(error);
    return;
  }
  state.catalog = catalog;
  $("credit").textContent = "Sources in this review build: U.S. Census Bureau Population Estimates Vintage 2025 (July 1, 2020–2025, and the separate April 1, 2020 estimates base), county decennial counts for April 1, 2010 and 2020, statewide decennial counts for 1830–2020 (Forstall 1996 and the 1910–2020 apportionment table), and American Community Survey 5-year 2019 and 2024. Imagery: NASA GIBS Blue Marble. Not endorsed or certified by the Census Bureau or NASA.";
  state.samples = collectSamples(counties);
  state.columnMeters = {};
  fillSelectors();
  if (innerWidth <= 760) $("panel").classList.add("min");
  map = window.__map = mkMap();
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "bottom-right");
  map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "Imagery: NASA GIBS · Data: U.S. Census Bureau" }));
  map.on("load", () => {
    map.getSource("counties").setData(counties);
    paint();
    frameFlorida();
    const pick = (event) => {
      if (statewideSelected()) {
        showPlace();
        return;
      }
      const feature = event.features && event.features[0];
      if (!feature) return;
      state.geoid = feature.properties.geoid;
      showPlace();
    };
    map.on("click", "fill", pick);
    map.on("click", "ex", pick);
    const hover = (event) => {
      const feature = event.features && event.features[0];
      if (!feature) return;
      const sourceNow = sourceById(state.sourceId);
      if (sourceNow.county_data === false) {
        const tip = $("tip");
        tip.hidden = false;
        tip.textContent = "County figures are not in this statewide series.";
        tip.style.left = Math.min(event.originalEvent.clientX + 12, innerWidth - 220) + "px";
        tip.style.top = (event.originalEvent.clientY + 12) + "px";
        return;
      }
      const county = state.catalog.counties.find((item) => item.geoid === feature.properties.geoid);
      const item = reading(county, sourceNow, state.year);
      const tip = $("tip");
      tip.hidden = false;
      tip.replaceChildren();
      const name = document.createElement("b");
      name.textContent = county.name;
      tip.append(name, document.createElement("br"), document.createTextNode(fmt(item.unit, item.value)));
      tip.style.left = Math.min(event.originalEvent.clientX + 12, innerWidth - 220) + "px";
      tip.style.top = (event.originalEvent.clientY + 12) + "px";
    };
    map.on("mousemove", "fill", hover);
    map.on("mousemove", "ex", hover);
    map.on("mouseleave", "fill", () => { $("tip").hidden = true; });
    map.on("mouseleave", "ex", () => { $("tip").hidden = true; });
    const top = state.catalog.counties.reduce((best, county) => county.pep_july1["2025"] > best.pep_july1["2025"] ? county : best);
    state.geoid = top.geoid;
    showPlace();
    map.once("idle", () => { document.body.dataset.map = "ready"; });
  });
}

function refresh() {
  fillSelectors();
  showPlace();
  if (map && map.getLayer("fill")) {
    paint();
    frameFlorida();
  }
}
$("topic").onchange = () => { state.topic = $("topic").value; refresh(); };
$("source").onchange = () => { state.sourceId = $("source").value; refresh(); };
$("year").onchange = () => { state.year = Number($("year").value); refresh(); };
$("compare").onchange = showPlace;
$("extrude").onchange = () => { if (map) { paint(); frameFlorida(); } };
$("view-globe").onclick = () => { state.globe = true; map.setProjection({ type: "globe" }); $("view-globe").classList.add("on"); $("view-flat").classList.remove("on"); frameFlorida(); };
$("view-flat").onclick = () => { state.globe = false; map.setProjection({ type: "mercator" }); map.setPitch(0); $("view-flat").classList.add("on"); $("view-globe").classList.remove("on"); frameFlorida(); };
$("grip").onclick = () => { $("panel").classList.toggle("min"); if (map) frameFlorida(); };
$("ask").onclick = () => { $("answer").textContent = ask($("question").value); };
window.addEventListener("resize", () => { if (map && document.body.dataset.map === "ready") frameFlorida(); });
boot();
