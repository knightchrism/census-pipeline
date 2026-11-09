const params = new URLSearchParams(location.search);
document.body.classList.toggle("jazz", params.get("variant") === "jazz");

function ortho(lon, lat, lon0, lat0) {
  const λ = lon * Math.PI / 180;
  const φ = lat * Math.PI / 180;
  const λ0 = lon0 * Math.PI / 180;
  const φ0 = lat0 * Math.PI / 180;
  const front = Math.sin(φ0) * Math.sin(φ) + Math.cos(φ0) * Math.cos(φ) * Math.cos(λ - λ0);
  return {
    x: Math.cos(φ) * Math.sin(λ - λ0),
    y: Math.cos(φ0) * Math.sin(φ) - Math.sin(φ0) * Math.cos(φ) * Math.cos(λ - λ0),
    front: front >= 0,
  };
}

function boundary(a, b, lon0, lat0) {
  let lo = a.front ? a : b;
  let hi = a.front ? b : a;
  for (let i = 0; i < 12; i += 1) {
    const lon = (lo.lon + hi.lon) / 2;
    const lat = (lo.lat + hi.lat) / 2;
    const mid = { lon, lat, ...ortho(lon, lat, lon0, lat0) };
    if (mid.front) lo = mid;
    else hi = mid;
  }
  return lo;
}

function ringsOf(geometry) {
  if (geometry.type === "Polygon") return geometry.coordinates;
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat();
  return [];
}

function projectRing(ring, lon0, lat0) {
  const points = ring.map(([lon, lat]) => ({ lon, lat, ...ortho(lon, lat, lon0, lat0) }));
  const parts = [];
  let current = [];
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    const jump = Math.abs(a.lon - b.lon) > 180;
    if (a.front) current.push(a);
    if (!jump && a.front !== b.front) {
      current.push(boundary(a, b, lon0, lat0));
      if (!b.front && current.length > 2) {
        parts.push(current);
        current = [];
      }
    } else if (jump && current.length > 2) {
      parts.push(current);
      current = [];
    }
  }
  if (current.length > 2) parts.push(current);
  return parts;
}

function eachCoord(feature, fn) {
  const walk = (node) => {
    if (typeof node[0] === "number") fn(node[0], node[1]);
    else node.forEach(walk);
  };
  walk(feature.geometry.coordinates);
}

function fitFlorida(features, width, height, pad) {
  const lon0 = -83.8;
  const lat0 = 27.7;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const feature of features) {
    eachCoord(feature, (lon, lat) => {
      const point = ortho(lon, lat, lon0, lat0);
      if (!point.front) return;
      minX = Math.min(minX, point.x);
      maxX = Math.max(maxX, point.x);
      minY = Math.min(minY, point.y);
      maxY = Math.max(maxY, point.y);
    });
  }
  const spanX = Math.max(maxX - minX, 0.001);
  const spanY = Math.max(maxY - minY, 0.001);
  const radius = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY);
  const midX = (minX + maxX) / 2;
  const midY = (minY + maxY) / 2;
  return {
    lon0,
    lat0,
    radius,
    cx: width / 2 - midX * radius,
    cy: height / 2 + midY * radius,
  };
}

function pathFor(feature, lon0, lat0, cx, cy, radius) {
  const parts = ringsOf(feature.geometry).flatMap((ring) => projectRing(ring, lon0, lat0));
  return parts.map((part) => part.map((point, index) => {
    const x = cx + point.x * radius;
    const y = cy - point.y * radius;
    return `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ") + "Z").join(" ");
}

function people(value) {
  return Math.round(value).toLocaleString("en-US");
}

async function draw() {
  const [florida, counties, land, layer] = await Promise.all([
    fetch("/data/florida.json").then((res) => res.json()),
    fetch("/data/fl_counties.geojson").then((res) => res.json()),
    fetch("/data/ne_110m_land.geojson").then((res) => res.json()),
    fetch("/api/layer?metric=total_population&year=2024").then((res) => res.json()),
  ]);
  const svg = document.getElementById("scene");
  const width = 860;
  const height = 640;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const jazz = document.body.classList.contains("jazz");
  const view = fitFlorida(counties.features, width, height, jazz ? 28 : 36);
  const { lon0, lat0, cx, cy, radius } = view;
  const colors = Object.fromEntries(layer.features.map((row) => [row.geoid, row.color]));
  const ocean = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  ocean.setAttribute("width", width);
  ocean.setAttribute("height", height);
  ocean.setAttribute("fill", "#0c3558");
  svg.append(ocean);
  for (const feature of land.features) {
    const d = pathFor(feature, lon0, lat0, cx, cy, radius);
    if (!d) continue;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "#16324c");
    svg.append(path);
  }
  for (const feature of counties.features) {
    const d = pathFor(feature, lon0, lat0, cx, cy, radius);
    if (!d) continue;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", colors[feature.properties.geoid] || "#e4c56a");
    path.setAttribute("stroke", "#102033");
    path.setAttribute("stroke-width", "1.7");
    svg.append(path);
  }
  if (jazz) {
    const frame = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    frame.setAttribute("x", "12");
    frame.setAttribute("y", "12");
    frame.setAttribute("width", String(width - 24));
    frame.setAttribute("height", String(height - 24));
    frame.setAttribute("rx", "22");
    frame.setAttribute("fill", "none");
    frame.setAttribute("stroke", "#e4c56a");
    frame.setAttribute("stroke-width", "3");
    svg.append(frame);
  }

  const y2024 = florida.state.estimates["2024"];
  const growth = florida.state.growth["2019-2024"].population_growth.value;
  document.getElementById("chips").innerHTML = [
    `ACS 5-year ${y2024.period.replace("-", "–")}`,
    "ACS 5-year 2015–2019",
    "67 counties",
  ].map((text) => `<span>${text}</span>`).join("");
  const sign = growth > 0 ? "+" : "";
  document.getElementById("stats").innerHTML = [
    ["Florida population, 2020–2024", people(y2024.total_population.value)],
    ["Median age, 2020–2024", `${Number(y2024.median_age.value).toFixed(1)} years`],
    ["Population change, 2015–2019 to 2020–2024", `${sign}${(growth * 100).toFixed(2)}%`],
  ].map(([label, value]) => `<div><strong>${value}</strong><span>${label}</span></div>`).join("");
  document.getElementById("fine").textContent = florida.source.endorsement;
  document.body.dataset.ready = "1";
}

draw().catch((error) => {
  document.getElementById("deck").textContent = "Card data did not load.";
  console.error(error);
});
