/* Florida Census Globe. Standard catalog map shell. Figures come from catalog.json. */
const ROOT = new URL("../", document.currentScript.src).href;
const $ = (id) => document.getElementById(id);
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg";
const RAMP = ["#1f4f78", "#4e7ea3", "#c4a15a", "#e4c56a", "#ffe7a3"];
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
function acsUnit(metric) {
  return { total_population: "people", median_household_income: "USD", median_age: "years", hispanic_population: "people", hispanic_share: "ratio" }[metric];
}
function reading(place, source, year, topicId) {
  if (!source.loaded) return null;
  if (source.id === "pep-v2025-july1") return { value: place.pep_july1[String(year)], moe: null, unit: "people" };
  if (source.id === "pep-v2025-april1-base") return { value: place.pep_april1_2020_base, moe: null, unit: "people" };
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
      },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": "#050d1c" } },
        { id: "bm", type: "raster", source: "bm", paint: { "raster-brightness-max": 0.6, "raster-saturation": -0.3 } },
        { id: "fill", type: "fill", source: "counties", paint: { "fill-color": "#314255", "fill-opacity": 0.58 } },
        { id: "ex", type: "fill-extrusion", source: "counties", paint: { "fill-extrusion-color": "#314255", "fill-extrusion-height": 0, "fill-extrusion-opacity": 0.86 } },
        { id: "line", type: "line", source: "counties", paint: { "line-color": "#f6e7b8", "line-width": 0.8 } },
      ],
    },
  });
}

let map;
function frameFlorida() {
  const panel = $("panel").getBoundingClientRect();
  const narrow = innerWidth <= 760;
  const bottom = narrow ? Math.round(panel.height) + 16 : 28;
  const pitch = $("extrude").checked && state.globe ? 55 : 0;
  map.fitBounds([[-87.95, 24.2], [-79.9, 31.15]], {
    padding: { top: narrow ? 128 : 64, right: narrow ? 14 : 28, bottom, left: narrow ? 12 : 20 },
    duration: 0,
    maxZoom: 6.4,
    pitch,
  });
  map.setPitch(pitch);
  if (!state.globe) map.setProjection({ type: "mercator" });
}

function paint() {
  const source = sourceById(state.sourceId);
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
  state.catalog.counties.forEach((county) => {
    const value = values[county.geoid];
    if (value == null) {
      color.push(county.geoid, "#314255");
      height.push(county.geoid, 0);
    } else {
      const t = (value - lo) / span;
      const idx = Math.min(RAMP.length - 1, Math.floor(t * (RAMP.length - 1)));
      color.push(county.geoid, RAMP[idx]);
      height.push(county.geoid, $("extrude").checked ? Math.round(12000 + t * 130000) : 0);
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
  const county = state.catalog.counties.find((item) => item.geoid === state.geoid) || state.catalog.counties[0];
  state.geoid = county.geoid;
  const item = reading(county, source, state.year);
  const stateItem = reading(state.catalog.state, source, state.year);
  $("place-name").textContent = county.name;
  const when = source.periods
    ? `period ${source.periods[state.year]}`
    : (String(source.reference_date).includes(String(state.year)) ? source.reference_date : `${source.reference_date} ${state.year}`);
  $("place-meta").textContent = `${source.label}. ${when}.`;
  const moe = item.moe == null ? "not published" : "±" + fmt(item.unit, item.moe);
  $("place-kv").replaceChildren();
  [["Estimate", fmt(item.unit, item.value)], ["90% margin", moe], ["Florida", fmt(stateItem.unit, stateItem.value)]].forEach(([label, value]) => {
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

function fillSelectors() {
  const topic = $("topic");
  topic.innerHTML = "";
  state.catalog.topics.forEach((item) => topic.add(new Option(item.label, item.id)));
  topic.value = state.topic;
  const chosen = topicById(state.topic);
  const source = $("source");
  source.innerHTML = "";
  chosen.sources.forEach((id) => {
    const item = sourceById(id);
    const option = new Option(item.loaded ? item.short : item.short + " (not loaded)", item.id);
    option.disabled = !item.loaded;
    source.add(option);
  });
  if (!sourceById(state.sourceId).loaded || !chosen.sources.includes(state.sourceId)) {
    state.sourceId = chosen.sources.find((id) => sourceById(id).loaded);
  }
  source.value = state.sourceId;
  const spec = sourceById(state.sourceId);
  const year = $("year");
  year.innerHTML = "";
  spec.years.forEach((value) => {
    const label = spec.periods ? `${value} (${spec.periods[value]})` : String(value);
    year.add(new Option(label, String(value)));
  });
  if (!spec.years.map(String).includes(String(state.year))) state.year = spec.years[spec.years.length - 1];
  year.value = String(state.year);
  $("badge").textContent = spec.short;
  $("source-note").textContent = spec.note;
  const link = spec.links.file || spec.links.documentation || spec.links.product;
  $("source-link").replaceChildren();
  if (link) {
    const anchor = document.createElement("a");
    anchor.href = link;
    anchor.rel = "noreferrer";
    anchor.textContent = link;
    $("source-link").append("Official source: ", anchor);
  }
  if (spec.retrieval && spec.retrieval.sha256) {
    $("source-link").append(document.createTextNode(` SHA-256 ${spec.retrieval.sha256}.`));
  }
  const blocked = state.catalog.sources.filter((item) => chosen.sources.includes(item.id) && !item.loaded);
  $("unavailable").hidden = blocked.length === 0;
  $("unavailable").textContent = blocked.map((item) => item.reason).join(" ");
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
  let topic = state.topic;
  if (q.includes("income")) topic = "income";
  else if (q.includes("age")) topic = "age";
  else if (q.includes("hispanic") && q.includes("share")) topic = "hispanic_share";
  else if (q.includes("hispanic")) topic = "hispanic_population";
  else if (q.includes("population") || q.includes("people")) topic = "population";
  const specTopic = topicById(topic);
  const source = specTopic.sources.includes(state.sourceId) && sourceById(state.sourceId).loaded
    ? sourceById(state.sourceId)
    : sourceById(specTopic.sources.find((id) => sourceById(id).loaded));
  const named = state.catalog.counties.find((county) => q.includes(county.name.toLowerCase().replace(" county", "")));
  const wantsState = /\bflorida\b/.test(q) && !named;
  const place = named || (wantsState ? state.catalog.state : state.catalog.counties.find((county) => county.geoid === state.geoid) || state.catalog.state);
  const yearMatch = text.match(/\b(2019|2020|2021|2022|2023|2024|2025)\b/);
  const year = yearMatch && source.years.map(String).includes(yearMatch[1]) ? Number(yearMatch[1]) : state.year;
  if (!source.years.map(String).includes(String(year))) {
    return `${source.label} is not loaded for ${yearMatch ? yearMatch[1] : "that year"}. Loaded years: ${source.years.join(", ")}.`;
  }
  const item = reading(place.geoid ? place : state.catalog.state, source, year, topic);
  const who = place.name || "Florida";
  return `${who}, ${specTopic.label}, ${source.label}, ${source.reference_date} ${year}: ${fmt(item.unit, item.value)}. ${item.moe == null ? "No 90% margin of error is published for this selection." : "90% margin of error " + fmt(item.unit, item.moe) + "."} ${source.note}`;
}

async function boot() {
  state.catalog = await fetch(ROOT + "data/catalog.json").then((res) => res.json());
  const counties = await fetch(ROOT + "data/fl_counties.geojson").then((res) => res.json());
  $("credit").textContent = "Sources: U.S. Census Bureau Population Estimates Program Vintage 2025 county totals (July 1, 2020–2025, and the April 1, 2020 estimates base in that file); American Community Survey 5-year 2019 and 2024. Imagery: NASA GIBS Blue Marble. Not endorsed or certified by the Census Bureau or NASA. Decennial counts are not loaded in this build.";
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
      const county = state.catalog.counties.find((item) => item.geoid === feature.properties.geoid);
      const item = reading(county, sourceById(state.sourceId), state.year);
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
  if (map && map.isStyleLoaded()) paint();
}
$("topic").onchange = () => { state.topic = $("topic").value; refresh(); };
$("source").onchange = () => { state.sourceId = $("source").value; refresh(); };
$("year").onchange = () => { state.year = Number($("year").value); refresh(); };
$("compare").onchange = showPlace;
$("extrude").onchange = () => { if (map) { paint(); map.setPitch($("extrude").checked && state.globe ? 55 : 0); } };
$("frame").onclick = () => frameFlorida();
$("view-globe").onclick = () => { state.globe = true; map.setProjection({ type: "globe" }); $("view-globe").classList.add("on"); $("view-flat").classList.remove("on"); frameFlorida(); };
$("view-flat").onclick = () => { state.globe = false; map.setProjection({ type: "mercator" }); map.setPitch(0); $("view-flat").classList.add("on"); $("view-globe").classList.remove("on"); frameFlorida(); };
$("grip").onclick = () => { $("panel").classList.toggle("min"); if (map) frameFlorida(); };
$("ask").onclick = () => { $("answer").textContent = ask($("question").value); };
window.addEventListener("resize", () => { if (map && document.body.dataset.map === "ready") frameFlorida(); });
boot();
