"""App interactiva (Flask) para el optimizador de diámetros EPANET.

Lanza main.py/overnight_search.py como subproceso en vez de importarlos
directo: multiprocessing.Pool dentro del propio proceso del servidor web es
frágil en Windows (spawn re-importa el módulo "principal", que bajo Flask no
es un script normal con `if __name__ == "__main__":` limpio).

Estado en memoria (ESTADO, dict global) en vez de sesiones — es una app local
de un solo usuario, no pensada para múltiples usuarios concurrentes.
"""

import json
import os
import subprocess
import sys
import tempfile

import yaml
from flask import Flask, abort, jsonify, render_template, request, send_file

from core.constraints import construir_restricciones
from core.io_utils import cargar_inp_robusto
from core.network import apply_diameters, load_network_para_optimizacion, run_simulation
from io_epanet.excel_writer import escribir_reporte_excel
from io_epanet.inp_writer import escribir_inp_optimizado

DIR_PROYECTO = os.path.dirname(os.path.abspath(__file__))
CONFIG_DEFAULT_PATH = os.path.join(DIR_PROYECTO, "config.yaml")
SALIDA_NOCTURNA = os.path.join(DIR_PROYECTO, "overnight_resultados")

app = Flask(__name__)
# Sin esto, Jinja cachea las plantillas en memoria cuando debug=False y no
# recoge cambios en templates/index.html sin reiniciar el proceso.
app.config["TEMPLATES_AUTO_RELOAD"] = True


def _config_default():
    with open(CONFIG_DEFAULT_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


ESTADO = {
    "inp_path": None,
    "inp_nombre": None,
    "config": _config_default(),
    "resultado": None,
    "log": "",
}

_default_inp = os.path.join(DIR_PROYECTO, "networks", "Net1.inp")
if os.path.exists(_default_inp):
    ESTADO["inp_path"] = _default_inp
    ESTADO["inp_nombre"] = os.path.basename(_default_inp)


# ------------------------------------------------------------- utilidades --

def _wn_y_simulacion(resultado, config):
    wn_base = load_network_para_optimizacion(resultado["inp_path"], config)
    wn_final = apply_diameters(wn_base, resultado["pipe_names"], resultado["diametros_mm"])
    res_sim = run_simulation(wn_final)
    restricciones = construir_restricciones(config)
    return wn_base, wn_final, res_sim, restricciones


def _datos_mapa(wn, resultados, restricciones):
    pipe_names = wn.pipe_name_list
    velocidad_r = next((r for r in restricciones if type(r).__name__ == "VelocidadConstraint"), None)
    hl_r = next((r for r in restricciones if type(r).__name__ == "PerdidaUnitariaConstraint"), None)

    v = resultados.link["velocity"][pipe_names].iloc[0].abs()
    hl = resultados.link["headloss"][pipe_names].iloc[0].abs() * 1000.0

    pipes = []
    conteos = {"ok": 0, "vel": 0, "hl": 0}
    for name in pipe_names:
        pipe = wn.get_link(name)
        x0, y0 = pipe.start_node.coordinates
        x1, y1 = pipe.end_node.coordinates

        estado = "ok"
        es_excepcion = False
        if velocidad_r is not None:
            es_excepcion = name in velocidad_r.excepciones_vmax
            vmax_ef = velocidad_r.excepciones_vmax.get(name, velocidad_r.vmax)
            if not (velocidad_r.vmin <= v[name] <= vmax_ef):
                estado = "vel"
        if estado == "ok" and hl_r is not None and hl[name] > hl_r.hlmax:
            estado = "hl"
        conteos[estado] += 1

        pipes.append({
            "id": name, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "estado": estado, "excepcion": es_excepcion,
            "diametro": round(pipe.diameter * 1000, 1),
            "longitud": round(pipe.length, 1),
            "velocidad": round(float(v[name]), 4),
            "perdida": round(float(hl[name]), 3),
        })

    presiones = resultados.node["pressure"]
    nodes = []
    for name in wn.junction_name_list:
        node = wn.get_node(name)
        x, y = node.coordinates
        demanda_lps = sum(d.base_value for d in node.demand_timeseries_list) * 1000.0
        nodes.append({
            "id": name, "x": x, "y": y,
            "cota": round(node.elevation, 2),
            "demanda": round(demanda_lps, 4),
            "presion": round(float(presiones[name].iloc[0]), 3),
        })

    tanques = []
    for name in wn.tank_name_list:
        tank = wn.get_node(name)
        x, y = tank.coordinates
        tanques.append({
            "id": name, "x": x, "y": y,
            "solera": round(tank.elevation, 2),
            "inicial": round(tank.init_level, 2),
            "min": round(tank.min_level, 2),
            "max": round(tank.max_level, 2),
        })

    return {"pipes": pipes, "nodes": nodes, "tank": tanques, "conteos": conteos}


def _filas_tabla(wn, resultados, restricciones):
    pipe_names = wn.pipe_name_list
    velocidad_r = next((r for r in restricciones if type(r).__name__ == "VelocidadConstraint"), None)
    hl_r = next((r for r in restricciones if type(r).__name__ == "PerdidaUnitariaConstraint"), None)

    v = resultados.link["velocity"][pipe_names].iloc[0].abs()
    hl = resultados.link["headloss"][pipe_names].iloc[0].abs() * 1000.0

    filas = []
    for name in pipe_names:
        pipe = wn.get_link(name)
        cumple = True
        if velocidad_r is not None:
            vmax_ef = velocidad_r.excepciones_vmax.get(name, velocidad_r.vmax)
            cumple = cumple and (velocidad_r.vmin <= v[name] <= vmax_ef)
        if hl_r is not None:
            cumple = cumple and (hl[name] <= hl_r.hlmax)
        filas.append({
            "tuberia": name,
            "diametro": round(pipe.diameter * 1000, 1),
            "longitud": round(pipe.length, 1),
            "velocidad": round(float(v[name]), 4),
            "perdida": round(float(hl[name]), 3),
            "cumple": bool(cumple),
        })
    return filas


def _resultado_por_fuente(fuente):
    if fuente == "nocturna":
        ruta = os.path.join(SALIDA_NOCTURNA, "mejor.json")
        if not os.path.exists(ruta):
            return None
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    return ESTADO["resultado"]


# ------------------------------------------------------------------ vistas --

@app.route("/")
def index():
    return render_template("index.html")


# --------------------------------------------------------------- API: red --

def _tuberias_conectadas_a_tanque(wn):
    """IDs de tuberías con un extremo en un tanque — típicamente la línea de
    conducción/aducción principal, candidata natural a excepción de velocidad
    máxima (tolera más que el resto de la red). Reservorios (fuente de agua
    externa, cabeza fija) no cuentan — la idea es específicamente el tanque de
    almacenamiento/depósito."""
    tanques = set(wn.tank_name_list)
    if not tanques:
        return []
    return [
        name for name in wn.pipe_name_list
        if wn.get_link(name).start_node_name in tanques or wn.get_link(name).end_node_name in tanques
    ]


@app.route("/api/red/subir", methods=["POST"])
def red_subir():
    archivo = request.files.get("archivo")
    if archivo is None or not archivo.filename:
        return jsonify({"ok": False, "error": "No se recibió archivo"}), 400

    fd, tmp_subida = tempfile.mkstemp(suffix=f"_{archivo.filename}")
    os.close(fd)
    archivo.save(tmp_subida)

    try:
        ruta = cargar_inp_robusto(tmp_subida)
        wn = load_network_para_optimizacion(ruta, ESTADO["config"])
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    ESTADO["inp_path"] = ruta
    ESTADO["inp_nombre"] = archivo.filename
    return jsonify({
        "ok": True,
        "nombre": archivo.filename,
        "tuberias": wn.num_pipes,
        "nodos": wn.num_nodes,
        "unidades": wn.options.hydraulic.inpfile_units,
        "formula": wn.options.hydraulic.headloss,
        "tuberias_tanque": _tuberias_conectadas_a_tanque(wn),
    })


@app.route("/api/red/estado")
def red_estado():
    if not ESTADO["inp_path"]:
        return jsonify({"ok": False})
    try:
        wn = load_network_para_optimizacion(ESTADO["inp_path"], ESTADO["config"])
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({
        "ok": True,
        "nombre": ESTADO["inp_nombre"] or os.path.basename(ESTADO["inp_path"]),
        "tuberias": wn.num_pipes,
        "nodos": wn.num_nodes,
        "unidades": wn.options.hydraulic.inpfile_units,
        "formula": wn.options.hydraulic.headloss,
    })


def _wn_y_simulacion_actual():
    """Simula la red tal como está cargada, con sus diámetros actuales (sin
    optimizar) — para el diagnóstico de "estado actual" en la pestaña Red, así
    se puede ver de entrada cómo cumple la red antes de correr nada."""
    if not ESTADO["inp_path"]:
        return None, None, None
    wn = load_network_para_optimizacion(ESTADO["inp_path"], ESTADO["config"])
    resultados = run_simulation(wn)
    restricciones = construir_restricciones(ESTADO["config"])
    return wn, resultados, restricciones


@app.route("/api/red/diagnostico")
def red_diagnostico():
    wn, resultados, restricciones = _wn_y_simulacion_actual()
    if wn is None:
        return jsonify({"ok": False, "error": "No hay red cargada"}), 400
    if resultados is None:
        return jsonify({"ok": False, "error": "La simulación no convergió con los diámetros actuales"})
    penalizaciones = {type(r).__name__: float(r.evaluar(wn, resultados)) for r in restricciones}
    return jsonify({"ok": True, "fitness": float(sum(penalizaciones.values())), "penalizaciones": penalizaciones})


@app.route("/api/red/mapa")
def red_mapa():
    wn, resultados, restricciones = _wn_y_simulacion_actual()
    if wn is None or resultados is None:
        return jsonify({"ok": False})
    return jsonify({"ok": True, **_datos_mapa(wn, resultados, restricciones)})


@app.route("/api/red/tabla")
def red_tabla():
    wn, resultados, restricciones = _wn_y_simulacion_actual()
    if wn is None or resultados is None:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "filas": _filas_tabla(wn, resultados, restricciones)})


@app.route("/api/red/descarga/xlsx")
def red_descarga_xlsx():
    wn, resultados, restricciones = _wn_y_simulacion_actual()
    if wn is None or resultados is None:
        abort(500)
    fitness = float(sum(r.evaluar(wn, resultados) for r in restricciones))
    ruta_out = os.path.join(tempfile.mkdtemp(prefix="epanet_opt_out_"), "reporte_estado_actual.xlsx")
    escribir_reporte_excel(wn, resultados, restricciones, ruta_out, fitness, ESTADO["config"]["catalogo_diametros"])
    return send_file(ruta_out, as_attachment=True, download_name="reporte_estado_actual.xlsx")


# --------------------------------------------------------- API: parámetros --

@app.route("/api/parametros", methods=["GET"])
def parametros_get():
    return jsonify(ESTADO["config"])


def _fusionar_config(base, nuevo):
    """Combina `nuevo` sobre `base`, recursivo en los dicts anidados. Así
    guardar parámetros desde la UI no borra claves que el frontend todavía no
    conoce (p.ej. ga.reparacion_activa, ga.poda_temprana) — sin esto, cada
    "Guardar parámetros" reemplazaba la config entera y las perdía."""
    resultado = dict(base)
    for clave, valor in nuevo.items():
        if isinstance(valor, dict) and isinstance(resultado.get(clave), dict):
            resultado[clave] = _fusionar_config(resultado[clave], valor)
        else:
            resultado[clave] = valor
    return resultado


@app.route("/api/parametros", methods=["POST"])
def parametros_post():
    nuevo = request.get_json()
    nuevo["red"] = {"inp_path": ESTADO["inp_path"]}
    ESTADO["config"] = _fusionar_config(ESTADO["config"], nuevo)
    return jsonify({"ok": True})


@app.route("/api/parametros/tuberias")
def parametros_tuberias():
    if not ESTADO["inp_path"]:
        return jsonify([])
    try:
        wn = load_network_para_optimizacion(ESTADO["inp_path"], ESTADO["config"])
        return jsonify(list(wn.pipe_name_list))
    except Exception:
        return jsonify([])


# ------------------------------------------------------------ API: ejecutar --

@app.route("/api/ejecutar", methods=["POST"])
def ejecutar():
    if not ESTADO["inp_path"]:
        return jsonify({"ok": False, "error": "No hay red cargada"}), 400

    config_corrida = dict(ESTADO["config"])
    config_corrida["red"] = {"inp_path": ESTADO["inp_path"]}

    dir_tmp = tempfile.mkdtemp(prefix="epanet_opt_run_")
    ruta_config = os.path.join(dir_tmp, "config.yaml")
    ruta_json = os.path.join(dir_tmp, "resultado.json")
    ruta_checkpoint = os.path.join(dir_tmp, "checkpoint.pkl")

    with open(ruta_config, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_corrida, f, allow_unicode=True)

    proceso = subprocess.run(
        [
            sys.executable, os.path.join(DIR_PROYECTO, "main.py"),
            "--config", ruta_config,
            "--resultado-json", ruta_json,
            "--checkpoint", ruta_checkpoint,
        ],
        capture_output=True, text=True, cwd=DIR_PROYECTO,
    )
    log = (proceso.stdout or "") + "\n" + (proceso.stderr or "")
    ESTADO["log"] = log

    if not os.path.exists(ruta_json):
        return jsonify({"ok": False, "error": "La corrida no generó resultados.", "log": log}), 500

    with open(ruta_json, "r", encoding="utf-8") as f:
        resultado = json.load(f)
    ESTADO["resultado"] = resultado
    return jsonify({"ok": True, "resultado": resultado, "log": log})


# ------------------------------------------------------------ API: resultado --

@app.route("/api/resultado/info")
def resultado_info():
    resultado = _resultado_por_fuente(request.args.get("fuente", "normal"))
    if resultado is None:
        return jsonify({"ok": False}), 404
    return jsonify({
        "ok": True,
        "fitness_final": resultado["fitness_final"],
        "penalizaciones": resultado["penalizaciones"],
        "historia": resultado.get("historia", []),
    })


@app.route("/api/resultado/mapa")
def resultado_mapa():
    resultado = _resultado_por_fuente(request.args.get("fuente", "normal"))
    if resultado is None:
        return jsonify({"ok": False}), 404
    _, wn_final, res_sim, restricciones = _wn_y_simulacion(resultado, ESTADO["config"])
    if res_sim is None:
        return jsonify({"ok": False, "error": "La simulación final no convergió"}), 500
    return jsonify({"ok": True, **_datos_mapa(wn_final, res_sim, restricciones)})


@app.route("/api/resultado/tabla")
def resultado_tabla():
    resultado = _resultado_por_fuente(request.args.get("fuente", "normal"))
    if resultado is None:
        return jsonify({"ok": False}), 404
    _, wn_final, res_sim, restricciones = _wn_y_simulacion(resultado, ESTADO["config"])
    if res_sim is None:
        return jsonify({"ok": False, "error": "La simulación final no convergió"}), 500
    return jsonify({"ok": True, "filas": _filas_tabla(wn_final, res_sim, restricciones)})


@app.route("/api/resultado/descarga/inp")
def descarga_inp():
    resultado = _resultado_por_fuente(request.args.get("fuente", "normal"))
    if resultado is None:
        abort(404)
    wn_base = load_network_para_optimizacion(resultado["inp_path"], ESTADO["config"])
    ruta_out = os.path.join(tempfile.mkdtemp(prefix="epanet_opt_out_"), "red_optimizada.inp")
    escribir_inp_optimizado(wn_base, resultado["pipe_names"], resultado["diametros_mm"], ruta_out)
    return send_file(ruta_out, as_attachment=True, download_name="red_optimizada.inp")


@app.route("/api/resultado/descarga/xlsx")
def descarga_xlsx():
    resultado = _resultado_por_fuente(request.args.get("fuente", "normal"))
    if resultado is None:
        abort(404)
    _, wn_final, res_sim, restricciones = _wn_y_simulacion(resultado, ESTADO["config"])
    if res_sim is None:
        abort(500)
    ruta_out = os.path.join(tempfile.mkdtemp(prefix="epanet_opt_out_"), "reporte_resultados.xlsx")
    escribir_reporte_excel(
        wn_final, res_sim, restricciones, ruta_out,
        resultado["fitness_final"], ESTADO["config"]["catalogo_diametros"],
    )
    return send_file(ruta_out, as_attachment=True, download_name="reporte_resultados.xlsx")


# ------------------------------------------------------------ API: nocturna --

@app.route("/api/nocturna/iniciar", methods=["POST"])
def nocturna_iniciar():
    if not ESTADO["inp_path"]:
        return jsonify({"ok": False, "error": "No hay red cargada"}), 400

    datos = request.get_json() or {}
    horas = float(datos.get("horas") or 8)
    n_semillas = datos.get("n_semillas")

    os.makedirs(SALIDA_NOCTURNA, exist_ok=True)
    for nombre in ("estado.json", "bitacora_semillas.jsonl"):
        ruta = os.path.join(SALIDA_NOCTURNA, nombre)
        if os.path.exists(ruta):
            os.remove(ruta)

    config_corrida = dict(ESTADO["config"])
    config_corrida["red"] = {"inp_path": ESTADO["inp_path"]}
    ruta_config = os.path.join(SALIDA_NOCTURNA, "config_usada.yaml")
    with open(ruta_config, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_corrida, f, allow_unicode=True)

    ruta_parar = os.path.join(SALIDA_NOCTURNA, "PARAR")
    if os.path.exists(ruta_parar):
        os.remove(ruta_parar)

    comando = [
        sys.executable, os.path.join(DIR_PROYECTO, "overnight_search.py"),
        "--config", ruta_config,
        "--horas", str(horas),
        "--salida-dir", SALIDA_NOCTURNA,
        "--detener-si-existe", ruta_parar,
    ]
    if n_semillas:
        comando += ["--n-semillas", str(int(n_semillas))]

    log_file = open(os.path.join(SALIDA_NOCTURNA, "log.txt"), "w", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen(comando, cwd=DIR_PROYECTO, stdout=log_file, stderr=subprocess.STDOUT, creationflags=flags)
    return jsonify({"ok": True})


@app.route("/api/nocturna/detener", methods=["POST"])
def nocturna_detener():
    os.makedirs(SALIDA_NOCTURNA, exist_ok=True)
    open(os.path.join(SALIDA_NOCTURNA, "PARAR"), "w").close()
    return jsonify({"ok": True})


@app.route("/api/nocturna/estado")
def nocturna_estado():
    ruta_estado = os.path.join(SALIDA_NOCTURNA, "estado.json")
    if not os.path.exists(ruta_estado):
        return jsonify({"ok": False})

    with open(ruta_estado, "r", encoding="utf-8") as f:
        estado = json.load(f)

    semillas = []
    ruta_bitacora = os.path.join(SALIDA_NOCTURNA, "bitacora_semillas.jsonl")
    if os.path.exists(ruta_bitacora):
        with open(ruta_bitacora, "r", encoding="utf-8") as f:
            for linea in f:
                if linea.strip():
                    semillas.append(json.loads(linea))

    tiene_mejor = os.path.exists(os.path.join(SALIDA_NOCTURNA, "mejor.json"))
    return jsonify({"ok": True, "estado": estado, "semillas": semillas, "tiene_mejor": tiene_mejor})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
