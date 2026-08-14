"""CLI: carga config.yaml, corre el GA sobre la red configurada e imprime resultados."""

import argparse
import json
import math
import multiprocessing
import shutil
import sys
import tempfile

import yaml

if sys.stdout.encoding is not None and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from core.constraints import construir_restricciones
from core.evaluator import Evaluator
from core.network import (
    apply_diameters,
    configurar_directorio_temporal,
    diametros_actuales_a_indices_catalogo,
    get_pipe_names,
    load_network_para_optimizacion,
    run_simulation,
)
from core.parallel import evaluate_worker, init_worker
from ga.optimizer import run_ga


def parse_args():
    parser = argparse.ArgumentParser(description="Optimizador GA de diámetros EPANET")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--inp", default=None, help="Ruta a un .inp que reemplaza red.inp_path del config"
    )
    parser.add_argument(
        "--procesos",
        type=int,
        default=None,
        help="Núcleos a usar (default: ga.procesos del config). 1 = sin paralelizar",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Ruta de checkpoint que reemplaza ga.checkpoint_path del config",
    )
    parser.add_argument(
        "--resultado-json",
        default=None,
        help="Si se da, escribe ahí un resumen del resultado en JSON (para la app)",
    )
    return parser.parse_args()


def cargar_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resumir_violaciones(wn, resultados, restricciones):
    resumen = []
    for r in restricciones:
        penalizacion = r.evaluar(wn, resultados)
        resumen.append((type(r).__name__, penalizacion))
    return resumen


def _calcular_bandas_diametro(wn_base, pipe_names, catalogo, restricciones, semilla_actual, margen_pasos=2):
    """Para cada tubería, acota el catálogo a un rango físicamente plausible
    en vez del catálogo completo — reduce el espacio de búsqueda real del GA.

    Corre UNA simulación con el diámetro más grande del catálogo en toda la
    red (minimiza velocidad/pérdida, así que siempre es la que más chance
    tiene de converger) para estimar el caudal real por tubería, y de ahí
    calcula qué diámetros mantendrían la velocidad dentro de [vmin, vmax] a
    ese caudal. El caudal real puede variar algo según los diámetros elegidos
    (redes en anillo redistribuyen flujo), así que se deja un margen de
    `margen_pasos` escalones de catálogo de cada lado — y la banda de cada
    tubería siempre incluye su índice en el diseño actual, para no invalidar
    el sembrado (warm start). Si no hay restricción de velocidad activa, la
    simulación no converge, o el cálculo da un rango vacío para una tubería
    puntual, esa tubería no se acota (catálogo completo, comportamiento
    original) — nunca se descarta el óptimo real por un error de estimación."""
    velocidad_r = next((r for r in restricciones if type(r).__name__ == "VelocidadConstraint"), None)
    n_catalogo = len(catalogo)
    sin_acotar = (0, n_catalogo - 1)
    if velocidad_r is None or velocidad_r.vmin <= 0:
        return None

    wn_neutro = apply_diameters(wn_base, pipe_names, [max(catalogo)] * len(pipe_names))
    resultados = run_simulation(wn_neutro)
    if resultados is None:
        return None

    caudales = resultados.link["flowrate"][pipe_names].abs().iloc[0]
    bandas = []
    for i, nombre in enumerate(pipe_names):
        q = caudales[nombre]
        if q <= 0:
            bandas.append(sin_acotar)
            continue

        vmax_ef = velocidad_r.excepciones_vmax.get(nombre, velocidad_r.vmax)
        diam_min_mm = math.sqrt(4 * q / (math.pi * vmax_ef)) * 1000.0
        diam_max_mm = math.sqrt(4 * q / (math.pi * velocidad_r.vmin)) * 1000.0

        idx_min = next((j for j, d in enumerate(catalogo) if d >= diam_min_mm), n_catalogo - 1)
        idx_max = next((j for j in range(n_catalogo - 1, -1, -1) if catalogo[j] <= diam_max_mm), 0)

        idx_min = max(0, idx_min - margen_pasos)
        idx_max = min(n_catalogo - 1, idx_max + margen_pasos)
        # siempre incluye el diámetro actual, para que el sembrado del diseño
        # existente nunca quede fuera de la banda que le toca a esa tubería
        idx_min = min(idx_min, semilla_actual[i])
        idx_max = max(idx_max, semilla_actual[i])

        bandas.append((idx_min, idx_max) if idx_min <= idx_max else sin_acotar)

    return bandas


def correr_optimizacion(config, procesos=None, checkpoint_path=None, imprimir=True, debe_continuar=None):
    """Corre el motor completo (carga red, arma restricciones, corre el GA) y
    devuelve un dict serializable con el resultado. La usan tanto `main()`
    (CLI/app) como `overnight_search.py` (búsqueda multi-semilla), para no
    duplicar esta lógica en cada punto de entrada.

    `debe_continuar(gen, fitness_min) -> bool`, si se da, se consulta después
    de cada generación; si devuelve False el GA corta ahí (poda temprana de
    semillas que van muy por detrás — ver overnight_search.py)."""

    def log(*a, **k):
        if imprimir:
            print(*a, **k)

    wn_base = load_network_para_optimizacion(config["red"]["inp_path"], config)
    pipe_names = get_pipe_names(wn_base)
    catalogo = config["catalogo_diametros"]
    restricciones = construir_restricciones(config)

    ga_cfg = config["ga"]
    procesos = procesos if procesos is not None else ga_cfg.get("procesos", 1)
    checkpoint_path = checkpoint_path if checkpoint_path is not None else ga_cfg.get("checkpoint_path")

    extra_cfg = config.get("extra_caudal", {})
    log(f"Red: {config['red']['inp_path']} ({len(pipe_names)} tuberías)")
    log(f"Catálogo de diámetros (mm): {catalogo}")
    log(f"Restricciones activas: {[type(r).__name__ for r in restricciones]}")
    if extra_cfg.get("activo", False):
        log(f"Caudal extra de diseño: {extra_cfg.get('presupuesto_lps', 0)} L/s (repartido proporcional a demanda base)")
    log(f"Semilla: {ga_cfg.get('semilla')}  Procesos: {procesos}")

    historia = []

    def on_generation(gen, fitness_min, fitness_prom):
        historia.append({"gen": gen, "fitness_min": fitness_min, "fitness_prom": fitness_prom})
        log(f"  gen {gen:3d}  fitness_min={fitness_min:12.4f}  fitness_prom={fitness_prom:12.4f}")
        if debe_continuar is not None and not debe_continuar(gen, fitness_min):
            log(f"  Podada: va muy por detrás de semillas anteriores en este punto, se corta acá.")
            return False

    mutacion_cfg = ga_cfg.get("mutacion_adaptativa", {})
    reinicio_cfg = ga_cfg.get("reinicio_diversidad", {})

    ga_kwargs = dict(
        n_genes=len(pipe_names),
        n_diametros=len(catalogo),
        poblacion=ga_cfg["poblacion"],
        generaciones=ga_cfg["generaciones"],
        prob_cruce=ga_cfg["prob_cruce"],
        prob_mutacion=ga_cfg["prob_mutacion"],
        tam_torneo=ga_cfg["tam_torneo"],
        semilla=ga_cfg.get("semilla"),
        on_generation=on_generation,
        checkpoint_path=checkpoint_path,
        checkpoint_cada=ga_cfg.get("checkpoint_cada", 5),
        mutacion_adaptativa=mutacion_cfg.get("activo", False),
        prob_mutacion_min=mutacion_cfg.get("prob_min"),
        reinicio_activo=reinicio_cfg.get("activo", False),
        reinicio_paciencia=reinicio_cfg.get("paciencia", 15),
        reinicio_fraccion=reinicio_cfg.get("fraccion", 0.15),
    )

    # Diámetros actuales del .inp mapeados al catálogo — hace falta siempre
    # que se calculen bandas (para no dejar el diseño actual fuera de su
    # propia banda), no solo cuando se usa como semilla.
    semilla_actual = diametros_actuales_a_indices_catalogo(wn_base, pipe_names, catalogo)

    if ga_cfg.get("restringir_banda_diametros", True):
        bandas = _calcular_bandas_diametro(wn_base, pipe_names, catalogo, restricciones, semilla_actual)
        if bandas is not None:
            ga_kwargs["bandas_diametro"] = bandas
            log("Acotando cada tubería a un rango de catálogo físicamente plausible (a partir de un caudal estimado)")

    if ga_cfg.get("sembrar_diseno_actual", True):
        individuos_semilla = [semilla_actual]

        # Un segundo punto de partida: el diseño actual ya pasado por
        # reparación — más cerca de factible desde la generación 1, en vez de
        # dejar que cruce/mutación lo descubran solos.
        reparar = ga_cfg.get("reparacion_activa", True)
        if reparar:
            max_intentos = ga_cfg.get("reparacion_max_intentos", 3)
            evaluador_semilla = Evaluator(
                wn_base, pipe_names, catalogo, restricciones,
                reparar=True, reparacion_max_intentos=max_intentos,
            )
            genes_reparados, _ = evaluador_semilla.evaluate(semilla_actual)
            if genes_reparados != semilla_actual:
                individuos_semilla.append(genes_reparados)

        # Un tercer punto de partida con margen extra (un escalón de catálogo
        # más grande en cada tubería) — diversifica el material genético
        # inicial con otra alternativa razonable, no solo el diseño actual.
        semilla_margen = [min(idx + 1, len(catalogo) - 1) for idx in semilla_actual]
        if semilla_margen != semilla_actual:
            individuos_semilla.append(semilla_margen)

        ga_kwargs["individuos_semilla"] = individuos_semilla
        log(f"Sembrando población inicial con {len(individuos_semilla)} diseño(s) de partida (diseño actual y variantes)")

    log("\nCorriendo GA...")
    # Carpeta temporal propia de esta corrida para los .inp/.rpt/.bin que
    # escribe cada simulación EPANET (uno por proceso, ver core/network.py).
    # Se borra entera al final: cada llamada a correr_optimizacion() crea un
    # multiprocessing.Pool nuevo con PIDs nuevos, así que sin esto cada
    # semilla de una búsqueda AFK deja basura huérfana en el temp del
    # sistema para siempre — con miles de semillas por hora, eso se come
    # el disco.
    run_tmp_dir = tempfile.mkdtemp(prefix="epanet_optimizer_run_")
    try:
        configurar_directorio_temporal(run_tmp_dir)
        if procesos > 1:
            with multiprocessing.Pool(
                processes=procesos,
                initializer=init_worker,
                initargs=(config["red"]["inp_path"], config, run_tmp_dir),
            ) as pool:
                mejor = run_ga(evaluate_fn=evaluate_worker, map_fn=pool.map, **ga_kwargs)
        else:
            reparar = ga_cfg.get("reparacion_activa", True)
            max_intentos = ga_cfg.get("reparacion_max_intentos", 3)
            evaluator = Evaluator(
                wn_base, pipe_names, catalogo, restricciones,
                reparar=reparar, reparacion_max_intentos=max_intentos,
            )
            mejor = run_ga(evaluate_fn=evaluator.evaluate, **ga_kwargs)

        diametros_mm = [catalogo[i] for i in mejor]
        fitness_final = mejor.fitness.values[0]

        log(f"\nMejor fitness encontrado: {fitness_final:.4f}")
        log("\nDiámetros por tubería:")
        for name, diametro in zip(pipe_names, diametros_mm):
            log(f"  {name:>6}  {diametro:6.1f} mm")

        wn_final = apply_diameters(wn_base, pipe_names, diametros_mm)
        resultados_final = run_simulation(wn_final)

        penalizaciones = {}
        if resultados_final is not None:
            log("\nPenalización por restricción (solución final):")
            for nombre, penalizacion in resumir_violaciones(wn_final, resultados_final, restricciones):
                log(f"  {nombre:<28} {penalizacion:10.4f}")
                penalizaciones[nombre] = float(penalizacion)
        else:
            log("\nLa simulación final no convergió.")
    finally:
        shutil.rmtree(run_tmp_dir, ignore_errors=True)

    return {
        "inp_path": config["red"]["inp_path"],
        "pipe_names": pipe_names,
        "diametros_mm": [float(d) for d in diametros_mm],
        "fitness_final": float(fitness_final),
        "penalizaciones": penalizaciones,
        "historia": [
            {"gen": h["gen"], "fitness_min": float(h["fitness_min"]), "fitness_prom": float(h["fitness_prom"])}
            for h in historia
        ],
        "convergio": resultados_final is not None,
    }


def main():
    args = parse_args()
    config = cargar_config(args.config)
    if args.inp is not None:
        config["red"]["inp_path"] = args.inp

    resultado = correr_optimizacion(config, procesos=args.procesos, checkpoint_path=args.checkpoint)

    if args.resultado_json is not None:
        with open(args.resultado_json, "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
