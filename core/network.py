"""Carga de red, aplicación de diámetros candidatos y simulación EPANET."""

import copy
import os
import tempfile

import wntr

# EpanetSimulator escribe temp.inp/.rpt/.bin/.hyd con un prefijo fijo por
# defecto. Sin un prefijo único por proceso, evaluaciones concurrentes
# (multiprocessing) se pisan los archivos entre sí y corrompen resultados
# en silencio (sin excepción). Un prefijo por PID en el directorio temporal
# del sistema evita la colisión y no ensucia el proyecto.
_FILE_PREFIX = os.path.join(tempfile.gettempdir(), f"epanet_optimizer_{os.getpid()}")


def load_network(inp_path):
    wn = wntr.network.WaterNetworkModel(inp_path)
    # Diseño de diámetros = problema de una condición de demanda (peak/diseño),
    # no de comportamiento extendido en el tiempo.
    wn.options.time.duration = 0
    return wn


def get_pipe_names(wn):
    return list(wn.pipe_name_list)


def aplicar_caudal_extra(wn, presupuesto_lps):
    """Reparte un caudal de diseño adicional (margen de crecimiento/seguridad)
    entre los nodos, proporcional a su demanda base actual. Sin esto, las
    tuberías secundarias de cierre de anillo llevan un caudal tan bajo en el
    balance hidráulico que ningún diámetro del catálogo alcanza la velocidad
    mínima ahí — el diámetro no puede forzar más flujo por un tramo cuando
    existe un camino paralelo de menor resistencia; hace falta más caudal real
    circulando por la red."""
    if presupuesto_lps <= 0:
        return

    junctions = wn.junction_name_list
    demandas_base = {
        name: sum(d.base_value for d in wn.get_node(name).demand_timeseries_list)
        for name in junctions
    }
    total_base = sum(demandas_base.values())
    if total_base <= 0:
        return

    presupuesto_m3s = presupuesto_lps / 1000.0
    for name in junctions:
        proporcion = demandas_base[name] / total_base
        extra_m3s = presupuesto_m3s * proporcion
        if extra_m3s > 0:
            wn.get_node(name).add_demand(extra_m3s, None, category="extra_diseno")


def resetear_demanda_base(wn, valor_lps):
    """Fija la demanda base de TODOS los nodos al mismo valor de diseño,
    descartando cualquier margen/extra que ya viniera incluido en el .inp
    para nodos específicos (p.ej. un caudal extra aplicado manualmente en
    una iteración anterior del diseño). Deja una base pareja para simular
    desde cero, antes de — opcionalmente — repartir un caudal_extra nuevo
    y controlado con aplicar_caudal_extra()."""
    if valor_lps <= 0:
        return
    valor_m3s = valor_lps / 1000.0
    for name in wn.junction_name_list:
        node = wn.get_node(name)
        node.demand_timeseries_list.clear()
        node.add_demand(valor_m3s, None, category="base_diseno")


def diametros_actuales_a_indices_catalogo(wn, pipe_names, catalogo_diametros):
    """Diámetros ya presentes en la red, cada uno mapeado al valor más
    cercano del catálogo. Sirve para "sembrar" el GA con el diseño actual
    en vez de arrancar de una población 100% aleatoria."""
    indices = []
    for name in pipe_names:
        d_mm = wn.get_link(name).diameter * 1000.0
        idx = min(
            range(len(catalogo_diametros)),
            key=lambda i: abs(catalogo_diametros[i] - d_mm),
        )
        indices.append(idx)
    return indices


def load_network_para_optimizacion(inp_path, config):
    """load_network + resetear_demanda_base + aplicar_caudal_extra según
    config, en un solo paso para no repetir esta lógica en cada punto de
    entrada (main.py serial y cada worker de multiprocessing). El reseteo
    de demanda base corre primero — si está activo, deja a todos los nodos
    en el mismo valor de diseño antes de que el caudal extra (si también
    está activo) reparta su presupuesto sobre esa base ya pareja."""
    wn = load_network(inp_path)

    demanda_base_cfg = config.get("demanda_base", {})
    if demanda_base_cfg.get("resetear", False):
        resetear_demanda_base(wn, demanda_base_cfg.get("valor_lps", 0))

    extra_cfg = config.get("extra_caudal", {})
    if extra_cfg.get("activo", False):
        aplicar_caudal_extra(wn, extra_cfg.get("presupuesto_lps", 0))

    return wn


def apply_diameters(wn_base, pipe_names, diametros_mm):
    if len(pipe_names) != len(diametros_mm):
        raise ValueError("pipe_names y diametros_mm deben tener la misma longitud")

    wn = copy.deepcopy(wn_base)
    for name, diametro_mm in zip(pipe_names, diametros_mm):
        wn.get_link(name).diameter = diametro_mm / 1000.0
    return wn


def run_simulation(wn):
    try:
        sim = wntr.sim.EpanetSimulator(wn)
        resultados = sim.run_sim(file_prefix=_FILE_PREFIX)
    except Exception:
        return None

    # Ante fallas de convergencia severas, EPANET puede devolver resultados
    # sin filas (0 pasos de tiempo) en vez de que wntr lance una excepción.
    if resultados.node["pressure"].empty or resultados.link["velocity"].empty:
        return None

    return resultados
