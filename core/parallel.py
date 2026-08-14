"""Funciones a nivel de módulo para evaluar individuos en procesos worker de
multiprocessing.Pool. Cada worker carga su propia copia de la red UNA vez
(en el initializer) en vez de recibir el modelo wntr por cada evaluación
(evita repickle costoso y problemas de serialización de objetos wntr)."""

from core.constraints import construir_restricciones
from core.evaluator import Evaluator
from core.network import configurar_directorio_temporal, get_pipe_names, load_network_para_optimizacion

_worker_evaluator = None


def init_worker(inp_path, config, run_tmp_dir):
    global _worker_evaluator
    configurar_directorio_temporal(run_tmp_dir)
    wn_base = load_network_para_optimizacion(inp_path, config)
    pipe_names = get_pipe_names(wn_base)
    catalogo = config["catalogo_diametros"]
    restricciones = construir_restricciones(config)
    ga_cfg = config.get("ga", {})
    reparar = ga_cfg.get("reparacion_activa", True)
    max_intentos = ga_cfg.get("reparacion_max_intentos", 3)
    _worker_evaluator = Evaluator(
        wn_base, pipe_names, catalogo, restricciones,
        reparar=reparar, reparacion_max_intentos=max_intentos,
    )


def evaluate_worker(indices):
    return _worker_evaluator.evaluate(indices)
