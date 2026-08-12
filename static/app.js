const SVG_NS = "http://www.w3.org/2000/svg";
let tuberiasDisponibles = [];

// ============================================================ Navegación =
document.querySelectorAll(".tab-btn").forEach(function (btn) {
  btn.addEventListener("click", function () {
    document.querySelectorAll(".tab-btn").forEach(function (b) { b.classList.remove("active"); });
    document.querySelectorAll(".panel").forEach(function (p) { p.classList.remove("active"); });
    btn.classList.add("active");
    document.getElementById("panel-" + btn.dataset.target).classList.add("active");
  });
});

// ================================================== Switches desplegables =
document.querySelectorAll(".switch-row[data-toggles]").forEach(function (row) {
  row.addEventListener("click", function () {
    const sw = row.querySelector(".switch");
    const body = document.getElementById(row.dataset.toggles);
    const on = !sw.classList.contains("on");
    sw.classList.toggle("on", on);
    if (body) body.hidden = !on;
    const hint = row.querySelector(".hint");
    if (hint) {
      if (row.dataset.toggles === "body-velocidad") hint.textContent = on ? "Activa — se evalúa en cada tubería" : "Desactivada — no se evalúa";
      if (row.dataset.toggles === "body-perdida") hint.textContent = on ? "Activa — se evalúa en cada tubería" : "Desactivada — no se evalúa";
      if (row.dataset.toggles === "body-presion") hint.textContent = on ? "Activa — se evalúa en cada nodo" : "Desactivada — no se evalúa";
      if (row.dataset.toggles === "body-demanda-base") hint.textContent = on ? "Activo — todos los nodos se fijan a este valor antes de simular" : "Desactivado — usa las demandas tal cual vienen en el .inp";
      if (row.dataset.toggles === "body-extra") hint.textContent = on ? "Activo" : "Desactivado";
      if (row.dataset.toggles === "body-tabla-normal") hint.textContent = on ? "Desplegada" : "Oculta";
      if (row.dataset.toggles === "body-tabla-nocturna") hint.textContent = on ? "Desplegada" : "Oculta";
    }
  });
});

function setSwitch(switchId, bodyId, on, hintId, hintTexto) {
  document.getElementById(switchId).classList.toggle("on", on);
  if (bodyId) {
    const body = document.getElementById(bodyId);
    if (body) body.hidden = !on;
  }
  if (hintId) document.getElementById(hintId).textContent = hintTexto;
}

// Sembrar (sin cuerpo desplegable, solo on/off)
document.getElementById("switch-sembrar").addEventListener("click", function () {
  this.classList.toggle("on");
});

// Sliders de probabilidad
document.getElementById("input-prob-cruce").addEventListener("input", function () {
  document.getElementById("val-prob-cruce").textContent = Number(this.value).toFixed(2);
});
document.getElementById("input-prob-mutacion").addEventListener("input", function () {
  document.getElementById("val-prob-mutacion").textContent = Number(this.value).toFixed(2);
});

// ==================================================== Modo normal/nocturna =
function setMode(mode) {
  document.getElementById("switch-mode-normal").classList.toggle("on", mode === "normal");
  document.getElementById("switch-mode-nocturna").classList.toggle("on", mode === "nocturna");
  document.getElementById("mode-card-normal").classList.toggle("active", mode === "normal");
  document.getElementById("mode-card-nocturna").classList.toggle("active", mode === "nocturna");
  document.getElementById("mode-card-nocturna").classList.toggle("night-mode", mode === "nocturna");
  document.getElementById("mode-normal").classList.toggle("active", mode === "normal");
  document.getElementById("mode-nocturna").classList.toggle("active", mode === "nocturna");
}
document.getElementById("mode-card-normal").addEventListener("click", function () { setMode("normal"); });
document.getElementById("mode-card-nocturna").addEventListener("click", function () { setMode("nocturna"); });

// ============================================================== Tooltip =
const tooltip = document.getElementById("map-tooltip");
const ttId = document.getElementById("tt-id");
const ttRows = document.getElementById("tt-rows");

function fila(label, valor) {
  return '<div class="tt-row"><span>' + label + "</span><span>" + valor + "</span></div>";
}

function mostrarTooltip(e, titulo, filas) {
  tooltip.hidden = false;
  tooltip.style.left = e.clientX + 16 + "px";
  tooltip.style.top = e.clientY + 16 + "px";
  ttId.textContent = titulo;
  ttRows.innerHTML = filas.join("");
}

document.addEventListener("mousemove", function (e) {
  const t = e.target;
  const pipe = t.closest ? t.closest(".pipe-line") : null;
  const node = t.closest ? t.closest(".node-dot") : null;
  const tank = t.closest ? t.closest(".tank-shape") : null;

  if (pipe) {
    const d = pipe.dataset;
    mostrarTooltip(e, "Tubería " + d.id, [
      fila("Diámetro", d.diam + " mm"),
      fila("Longitud", d.len + " m"),
      fila("Velocidad", d.vel + " m/s"),
      fila("Pérd. unitaria", d.hl + " m/km"),
    ]);
  } else if (node) {
    const n = node.dataset;
    mostrarTooltip(e, "Nodo " + n.id, [
      fila("Cota", n.cota + " m"),
      fila("Demanda base", n.demanda + " L/s"),
      fila("Presión", n.presion + " m"),
    ]);
  } else if (tank) {
    const k = tank.dataset;
    mostrarTooltip(e, "Tanque " + k.id, [
      fila("Cota de solera", k.solera + " m"),
      fila("Nivel inicial", k.inicial + " m"),
      fila("Nivel mínimo", k.min + " m"),
      fila("Nivel máximo", k.max + " m"),
    ]);
  } else {
    tooltip.hidden = true;
  }
});
document.addEventListener("mouseleave", function () { tooltip.hidden = true; });

// ===================================================== Mapa dinámico (SVG) =
function colorParaEstado(estado, excepcion) {
  if (excepcion && estado === "ok") return "var(--brass)";
  if (estado === "vel") return "var(--critical)";
  if (estado === "hl") return "var(--warn)";
  return "var(--ok)";
}

function construirMapaSVG(svgEl, leyendaEl, datos) {
  while (svgEl.firstChild) svgEl.removeChild(svgEl.firstChild);
  leyendaEl.innerHTML = "";

  const xs = [], ys = [];
  datos.pipes.forEach(function (p) { xs.push(p.x0, p.x1); ys.push(p.y0, p.y1); });
  datos.nodes.forEach(function (n) { xs.push(n.x); ys.push(n.y); });
  datos.tank.forEach(function (t) { xs.push(t.x); ys.push(t.y); });
  if (!xs.length) return;

  const minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
  const minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
  const w = Math.max(maxX - minX, 1), h = Math.max(maxY - minY, 1);
  const pad = Math.max(w, h) * 0.08;
  svgEl.setAttribute("viewBox", (minX - pad) + " " + (minY - pad) + " " + (w + pad * 2) + " " + (h + pad * 2));
  const escala = Math.max(w, h) / 640;

  datos.pipes.forEach(function (p) {
    const linea = document.createElementNS(SVG_NS, "line");
    linea.setAttribute("class", "pipe-line");
    linea.setAttribute("x1", p.x0); linea.setAttribute("y1", p.y0);
    linea.setAttribute("x2", p.x1); linea.setAttribute("y2", p.y1);
    linea.setAttribute("stroke", colorParaEstado(p.estado, p.excepcion));
    linea.setAttribute("stroke-width", (p.excepcion ? 3.2 : 1.6) * escala);
    linea.setAttribute("fill", "none");
    linea.dataset.id = p.id;
    linea.dataset.diam = p.diametro;
    linea.dataset.len = p.longitud;
    linea.dataset.vel = p.velocidad;
    linea.dataset.hl = p.perdida;
    svgEl.appendChild(linea);
  });

  datos.nodes.forEach(function (n) {
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("class", "node-dot");
    c.setAttribute("cx", n.x); c.setAttribute("cy", n.y);
    c.setAttribute("r", 2.6 * escala);
    c.setAttribute("fill", "var(--surface)");
    c.setAttribute("stroke", "var(--ink-faint)");
    c.setAttribute("stroke-width", 0.9 * escala);
    c.dataset.id = n.id; c.dataset.cota = n.cota; c.dataset.demanda = n.demanda; c.dataset.presion = n.presion;
    svgEl.appendChild(c);
  });

  datos.tank.forEach(function (t) {
    const r = document.createElementNS(SVG_NS, "rect");
    r.setAttribute("class", "tank-shape");
    const lado = 12 * escala;
    r.setAttribute("x", t.x - lado); r.setAttribute("y", t.y - lado * 0.8);
    r.setAttribute("width", lado * 2); r.setAttribute("height", lado * 1.6);
    r.setAttribute("rx", 2);
    r.setAttribute("fill", "none"); r.setAttribute("stroke", "var(--brass)"); r.setAttribute("stroke-width", 1.4 * escala);
    r.dataset.id = t.id; r.dataset.solera = t.solera; r.dataset.inicial = t.inicial; r.dataset.min = t.min; r.dataset.max = t.max;
    svgEl.appendChild(r);
  });

  const items = [];
  if (datos.conteos.ok) items.push(["var(--ok)", "Cumple (" + datos.conteos.ok + ")"]);
  if (datos.conteos.vel) items.push(["var(--critical)", "Viola velocidad (" + datos.conteos.vel + ")"]);
  if (datos.conteos.hl) items.push(["var(--warn)", "Viola pérdida unitaria (" + datos.conteos.hl + ")"]);
  if (datos.pipes.some(function (p) { return p.excepcion; })) items.push(["var(--brass)", "Línea de conducción (excepción)"]);
  items.forEach(function (par) {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = '<span class="swatch" style="background:' + par[0] + '"></span>' + par[1];
    leyendaEl.appendChild(div);
  });
}

function construirTabla(tbody, filas) {
  tbody.innerHTML = "";
  filas.forEach(function (f) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td class="mono">' + f.tuberia + "</td>" +
      '<td class="num">' + f.diametro + "</td>" +
      '<td class="num">' + f.longitud + "</td>" +
      '<td class="num">' + f.velocidad + "</td>" +
      '<td class="num">' + f.perdida + "</td>" +
      "<td>" + (f.cumple ? '<span class="pill ok">Cumple</span>' : '<span class="pill critical">No cumple</span>') + "</td>";
    tbody.appendChild(tr);
  });
}

function construirViolaciones(container, pipes) {
  container.innerHTML = "";
  const malas = pipes.filter(function (p) { return p.estado !== "ok"; });
  if (!malas.length) {
    container.innerHTML = '<div class="texto-tenue">Ninguna — todas las tuberías cumplen.</div>';
    return;
  }
  malas.forEach(function (p) {
    const div = document.createElement("div");
    div.className = "violation-row";
    const valor = p.estado === "vel" ? p.velocidad + " m/s" : p.perdida + " m/km";
    div.innerHTML = '<span class="id">Tubería ' + p.id + "</span><span class=\"val\">" + valor + "</span>";
    container.appendChild(div);
  });
}

function construirConvergencia(svgEl, historia) {
  svgEl.innerHTML = "";
  if (!historia || !historia.length) return;
  const W = 300, H = 110;
  const minFit = Math.min.apply(null, historia.map(function (h) { return h.fitness_min; }));
  const maxFit = Math.max.apply(null, historia.map(function (h) { return h.fitness_prom; }));
  const rango = Math.max(maxFit - minFit, 1e-6);
  const n = historia.length;
  function pt(i, val) {
    const x = n > 1 ? (i / (n - 1)) * W : 0;
    const y = H - ((val - minFit) / rango) * H;
    return x.toFixed(1) + "," + y.toFixed(1);
  }
  const ptsMin = historia.map(function (h, i) { return pt(i, h.fitness_min); }).join(" ");
  const ptsProm = historia.map(function (h, i) { return pt(i, h.fitness_prom); }).join(" ");
  svgEl.innerHTML =
    '<polyline fill="none" stroke="var(--ink-faint)" stroke-width="1.4" points="' + ptsProm + '"/>' +
    '<polyline fill="none" stroke="var(--accent)" stroke-width="1.8" points="' + ptsMin + '"/>';
}

// ============================================================ Red (subir) =
const dropzone = document.getElementById("dropzone");
const inputInp = document.getElementById("input-inp");
dropzone.addEventListener("click", function () { inputInp.click(); });
dropzone.addEventListener("dragover", function (e) { e.preventDefault(); });
dropzone.addEventListener("drop", function (e) {
  e.preventDefault();
  if (e.dataTransfer.files.length) subirRed(e.dataTransfer.files[0]);
});
inputInp.addEventListener("change", function () {
  if (inputInp.files.length) subirRed(inputInp.files[0]);
});

async function subirRed(archivo) {
  const fd = new FormData();
  fd.append("archivo", archivo);
  const msg = document.getElementById("red-mensaje");
  msg.textContent = "Subiendo...";
  const resp = await fetch("/api/red/subir", { method: "POST", body: fd });
  const datos = await resp.json();
  if (datos.ok) {
    msg.textContent = "Red cargada: " + datos.nombre;
    actualizarInfoRed(datos);
    tuberiasDisponibles = await fetch("/api/parametros/tuberias").then(function (r) { return r.json(); });
  } else {
    msg.textContent = "Error: " + datos.error;
  }
}

function actualizarInfoRed(datos) {
  document.getElementById("red-tuberias").textContent = datos.tuberias;
  document.getElementById("red-nodos").textContent = datos.nodos;
  document.getElementById("red-unidades").textContent = datos.unidades;
  document.getElementById("red-formula").textContent = datos.formula;
  document.getElementById("rail-red-estado").innerHTML =
    'Red cargada<br><span class="mono" style="color:var(--ink-dim)">' + datos.nombre + "</span> · " + datos.tuberias + " tuberías";
  document.getElementById("resultados-sub").textContent = datos.tuberias + " tuberías · red cargada";
}

async function cargarEstadoRedInicial() {
  const datos = await fetch("/api/red/estado").then(function (r) { return r.json(); });
  if (datos.ok) actualizarInfoRed(datos);
}

// ====================================================== Parámetros (form) =
function crearFilaExcepcion(tuberiaId, vmax) {
  const div = document.createElement("div");
  div.className = "exc-row";
  const opciones = tuberiasDisponibles.map(function (id) {
    return '<option value="' + id + '"' + (id === tuberiaId ? " selected" : "") + ">Tubería " + id + "</option>";
  }).join("");
  div.innerHTML =
    '<select class="exc-select">' + opciones + "</select>" +
    '<input type="text" class="exc-vmax" value="' + vmax + '">' +
    '<span class="exc-unit">m/s</span>' +
    '<button class="exc-remove" title="Quitar excepción">×</button>';
  return div;
}

document.getElementById("exc-add").addEventListener("click", function () {
  const primero = tuberiasDisponibles[0] || "";
  document.getElementById("exc-list").appendChild(crearFilaExcepcion(primero, "3.00"));
});
document.getElementById("exc-list").addEventListener("click", function (e) {
  if (e.target.classList.contains("exc-remove")) {
    e.target.closest(".exc-row").remove();
  }
});

function renderCatalogoChips() {
  const valores = document.getElementById("input-catalogo").value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
  document.getElementById("catalogo-chips").innerHTML = valores.map(function (v) {
    return '<span class="diam-chip">' + v + "</span>";
  }).join("");
}
document.getElementById("input-catalogo").addEventListener("input", renderCatalogoChips);

async function cargarParametros() {
  const [config, tuberias] = await Promise.all([
    fetch("/api/parametros").then(function (r) { return r.json(); }),
    fetch("/api/parametros/tuberias").then(function (r) { return r.json(); }),
  ]);
  tuberiasDisponibles = tuberias;

  document.getElementById("input-catalogo").value = config.catalogo_diametros.join(", ");
  renderCatalogoChips();

  const vel = config.restricciones.velocidad;
  setSwitch("switch-velocidad", "body-velocidad", vel.activo, "hint-velocidad",
    vel.activo ? "Activa — se evalúa en cada tubería" : "Desactivada — no se evalúa");
  document.getElementById("input-vmin").value = vel.vmin;
  document.getElementById("input-vmax").value = vel.vmax;
  document.getElementById("input-peso-v").value = vel.peso_penalizacion;

  const excList = document.getElementById("exc-list");
  excList.innerHTML = "";
  Object.entries(vel.excepciones_vmax || {}).forEach(function ([id, vmax]) {
    excList.appendChild(crearFilaExcepcion(id, vmax));
  });

  const perdida = config.restricciones.perdida_unitaria;
  setSwitch("switch-perdida", "body-perdida", perdida.activo, "hint-perdida",
    perdida.activo ? "Activa — se evalúa en cada tubería" : "Desactivada — no se evalúa");
  document.getElementById("input-hlmax").value = perdida.hlmax;
  document.getElementById("input-peso-hl").value = perdida.peso_penalizacion;

  const presion = config.restricciones.presion_minima;
  setSwitch("switch-presion", "body-presion", presion.activo, "hint-presion",
    presion.activo ? "Activa — se evalúa en cada nodo" : "Desactivada — no se evalúa");
  document.getElementById("input-presion-valor").value = presion.valor;
  document.getElementById("input-peso-presion").value = presion.peso_penalizacion;

  const demandaBase = config.demanda_base || {};
  setSwitch("switch-demanda-base", "body-demanda-base", !!demandaBase.resetear, "hint-demanda-base",
    demandaBase.resetear ? "Activo — todos los nodos se fijan a este valor antes de simular" : "Desactivado — usa las demandas tal cual vienen en el .inp");
  document.getElementById("input-demanda-base-valor").value = demandaBase.valor_lps || 2.366;

  const extra = config.extra_caudal || {};
  setSwitch("switch-extra", "body-extra", !!extra.activo, "hint-extra", extra.activo ? "Activo" : "Desactivado");
  document.getElementById("input-presupuesto").value = extra.presupuesto_lps || 0;

  const ga = config.ga;
  document.getElementById("input-poblacion").value = ga.poblacion;
  document.getElementById("input-generaciones").value = ga.generaciones;
  document.getElementById("input-prob-cruce").value = ga.prob_cruce;
  document.getElementById("val-prob-cruce").textContent = Number(ga.prob_cruce).toFixed(2);
  document.getElementById("input-prob-mutacion").value = ga.prob_mutacion;
  document.getElementById("val-prob-mutacion").textContent = Number(ga.prob_mutacion).toFixed(2);
  document.getElementById("input-tam-torneo").value = ga.tam_torneo;
  document.getElementById("input-semilla").value = ga.semilla;
  document.getElementById("input-procesos").value = ga.procesos;
  document.getElementById("switch-sembrar").classList.toggle("on", !!ga.sembrar_diseno_actual);
}

document.getElementById("btn-guardar-parametros").addEventListener("click", async function () {
  const excepciones = {};
  document.querySelectorAll("#exc-list .exc-row").forEach(function (row) {
    const id = row.querySelector(".exc-select").value;
    const vmax = parseFloat(row.querySelector(".exc-vmax").value);
    if (id && !isNaN(vmax)) excepciones[id] = vmax;
  });

  const config = {
    demanda_base: {
      resetear: document.getElementById("switch-demanda-base").classList.contains("on"),
      valor_lps: parseFloat(document.getElementById("input-demanda-base-valor").value) || 0,
    },
    extra_caudal: {
      activo: document.getElementById("switch-extra").classList.contains("on"),
      presupuesto_lps: parseFloat(document.getElementById("input-presupuesto").value) || 0,
    },
    restricciones: {
      velocidad: {
        activo: document.getElementById("switch-velocidad").classList.contains("on"),
        vmin: parseFloat(document.getElementById("input-vmin").value),
        vmax: parseFloat(document.getElementById("input-vmax").value),
        peso_penalizacion: parseFloat(document.getElementById("input-peso-v").value),
        excepciones_vmax: excepciones,
      },
      perdida_unitaria: {
        activo: document.getElementById("switch-perdida").classList.contains("on"),
        hlmax: parseFloat(document.getElementById("input-hlmax").value),
        peso_penalizacion: parseFloat(document.getElementById("input-peso-hl").value),
      },
      presion_minima: {
        activo: document.getElementById("switch-presion").classList.contains("on"),
        valor: parseFloat(document.getElementById("input-presion-valor").value),
        peso_penalizacion: parseFloat(document.getElementById("input-peso-presion").value),
      },
      costo: { activo: false },
    },
    catalogo_diametros: document.getElementById("input-catalogo").value
      .split(",").map(function (s) { return parseInt(s.trim(), 10); }).filter(function (n) { return !isNaN(n); }),
    ga: {
      sembrar_diseno_actual: document.getElementById("switch-sembrar").classList.contains("on"),
      poblacion: parseInt(document.getElementById("input-poblacion").value, 10),
      generaciones: parseInt(document.getElementById("input-generaciones").value, 10),
      prob_cruce: parseFloat(document.getElementById("input-prob-cruce").value),
      prob_mutacion: parseFloat(document.getElementById("input-prob-mutacion").value),
      tam_torneo: parseInt(document.getElementById("input-tam-torneo").value, 10),
      semilla: parseInt(document.getElementById("input-semilla").value, 10),
      procesos: parseInt(document.getElementById("input-procesos").value, 10),
      checkpoint_path: "checkpoint.pkl",
      checkpoint_cada: 5,
    },
  };

  const resp = await fetch("/api/parametros", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config),
  });
  const status = document.getElementById("parametros-status");
  status.classList.remove("oculto");
  status.textContent = resp.ok ? "Parámetros guardados." : "Error al guardar parámetros.";
});

// ===================================================== Resultado (normal) =
async function mostrarResultadoCompleto(sufijo) {
  const prefijo = sufijo === "normal" ? "rn" : "rc";
  const [info, mapa, tabla] = await Promise.all([
    fetch("/api/resultado/info?fuente=" + sufijo).then(function (r) { return r.json(); }),
    fetch("/api/resultado/mapa?fuente=" + sufijo).then(function (r) { return r.json(); }),
    fetch("/api/resultado/tabla?fuente=" + sufijo).then(function (r) { return r.json(); }),
  ]);
  if (!info.ok || !mapa.ok || !tabla.ok) return;

  document.getElementById(prefijo + "-fitness").textContent = info.fitness_final.toFixed(4);
  const pen = info.penalizaciones || {};
  document.getElementById(prefijo + "-velocidad").textContent = (pen.VelocidadConstraint || 0).toFixed(4);
  document.getElementById(prefijo + "-perdida").textContent = (pen.PerdidaUnitariaConstraint || 0).toFixed(4);

  const total = mapa.conteos.ok + mapa.conteos.vel + mapa.conteos.hl;
  document.getElementById(prefijo + "-conformes").textContent = mapa.conteos.ok + " / " + total;

  construirMapaSVG(document.getElementById("svg-" + sufijo), document.getElementById("leyenda-" + sufijo), mapa);
  construirViolaciones(document.getElementById("viol-" + sufijo), mapa.pipes);
  construirTabla(document.getElementById("tabla-" + sufijo + "-body"), tabla.filas);
  if (sufijo === "normal") construirConvergencia(document.getElementById("conv-normal"), info.historia);

  document.getElementById("dl-inp-" + sufijo).href = "/api/resultado/descarga/inp?fuente=" + sufijo;
  document.getElementById("dl-xlsx-" + sufijo).href = "/api/resultado/descarga/xlsx?fuente=" + sufijo;

  const wrapId = sufijo === "normal" ? "resultado-normal-wrap" : "mejor-nocturna-wrap";
  document.getElementById(wrapId).classList.remove("oculto");
}

document.getElementById("btn-correr").addEventListener("click", async function () {
  const boton = document.getElementById("btn-correr");
  const chip = document.getElementById("chip-normal");
  const chipTexto = document.getElementById("chip-normal-texto");
  boton.disabled = true;
  chip.classList.remove("oculto");
  chipTexto.textContent = "Optimizando... esto puede tardar desde segundos hasta varios minutos";

  const resp = await fetch("/api/ejecutar", { method: "POST" });
  const datos = await resp.json();
  boton.disabled = false;

  const bitacoraWrap = document.getElementById("bitacora-normal-wrap");
  document.getElementById("bitacora-normal").textContent = datos.log || "";
  bitacoraWrap.classList.remove("oculto");

  if (!datos.ok) {
    chipTexto.textContent = "Error: " + datos.error;
    return;
  }
  chipTexto.textContent = "Última corrida convergió";
  await mostrarResultadoCompleto("normal");
});

// ========================================================= Modo nocturna =
document.getElementById("btn-iniciar-nocturna").addEventListener("click", async function () {
  const horas = parseFloat(document.getElementById("input-horas").value) || 8;
  const nSemillasRaw = document.getElementById("input-nsemillas").value.trim();
  const payload = { horas: horas };
  if (nSemillasRaw) payload.n_semillas = parseInt(nSemillasRaw, 10);
  const resp = await fetch("/api/nocturna/iniciar", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const datos = await resp.json();
  if (datos.ok) setTimeout(actualizarEstadoNocturna, 1500);
});

document.getElementById("btn-detener-nocturna").addEventListener("click", async function () {
  await fetch("/api/nocturna/detener", { method: "POST" });
});

document.getElementById("btn-actualizar-nocturna").addEventListener("click", actualizarEstadoNocturna);

async function actualizarEstadoNocturna() {
  const datos = await fetch("/api/nocturna/estado").then(function (r) { return r.json(); });
  const sinDatos = document.getElementById("nocturna-sin-datos");
  const wrap = document.getElementById("nocturna-wrap");

  if (!datos.ok) {
    sinDatos.classList.remove("oculto");
    wrap.classList.add("oculto");
    return;
  }
  sinDatos.classList.add("oculto");
  wrap.classList.remove("oculto");

  const est = datos.estado;
  document.getElementById("nocturna-estado").textContent = est.corriendo ? "🟢 Corriendo" : "⚪ Detenida";
  document.getElementById("nocturna-dot").style.background = est.corriendo ? "var(--ok)" : "var(--ink-faint)";
  document.getElementById("nocturna-intentos").textContent = est.intentos;
  const minutos = (est.actualizado - est.inicio) / 60;
  document.getElementById("nocturna-tiempo").textContent = minutos.toFixed(1) + " min";
  document.getElementById("nocturna-mejor-fitness").textContent = est.mejor_fitness != null ? est.mejor_fitness.toFixed(4) : "—";

  if (datos.semillas && datos.semillas.length) {
    document.getElementById("seed-card").classList.remove("oculto");
    document.getElementById("seed-titulo").textContent = "Fitness por semilla — mejor: semilla " + est.mejor_semilla;
    const maxFit = Math.max.apply(null, datos.semillas.map(function (s) { return s.fitness_final; }));
    const cont = document.getElementById("seed-list");
    cont.innerHTML = "";
    datos.semillas.forEach(function (s, i) {
      const esMejor = s.semilla === est.mejor_semilla;
      const div = document.createElement("div");
      div.className = "seed-row" + (esMejor ? " best" : "");
      const pct = maxFit > 0 ? Math.max((s.fitness_final / maxFit) * 100, 2) : 2;
      div.innerHTML =
        '<span class="idx num">' + String(i + 1).padStart(2, "0") + "</span>" +
        '<span class="seed num">' + s.semilla + "</span>" +
        '<div class="bar-track"><div class="bar" style="width:' + pct + '%"></div></div>' +
        '<span class="fit num">' + s.fitness_final.toFixed(2) + "</span>";
      cont.appendChild(div);
    });
  }

  if (datos.tiene_mejor) {
    document.getElementById("mejor-nocturna-titulo").textContent = "Resultado de la mejor semilla (" + est.mejor_semilla + ")";
    await mostrarResultadoCompleto("nocturna");
  }
}

// ================================================================= Init =
cargarEstadoRedInicial();
cargarParametros();
actualizarEstadoNocturna();
