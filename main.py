"""CLI: carga config.yaml, corre el GA sobre la red configurada e imprime resultados."""

import argparse
import json
import multiprocessing
import sys

import yaml

if sys.stdout.encoding is not None and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from core.constraints import construir_restricciones
from core.evaluator import Evaluator
from core.network import (
    apply_diameters,
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


def correr_optimizacion(config, procesos=None, checkpoint_path=None, imprimir=True):
    """Corre el motor completo (carga red, arma restricciones, corre el GA) y
    devuelve un dict serializable con el resultado. La usan tanto `main()`
    (CLI/app) como `overnight_search.py` (búsqueda multi-semilla), para no
    duplicar esta lógica en cada punto de entrada."""

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
    )

    if ga_cfg.get("sembrar_diseno_actual", True):
        semilla_actual = diametros_actuales_a_indices_catalogo(wn_base, pipe_names, catalogo)
        ga_kwargs["individuos_semilla"] = [semilla_actual]
        log("Sembrando población inicial con el diseño actual (diámetros del .inp)")

    log("\nCorriendo GA...")
    if procesos > 1:
        with multiprocessing.Pool(
            processes=procesos,
            initializer=init_worker,
            initargs=(config["red"]["inp_path"], config),
        ) as pool:
            mejor = run_ga(evaluate_fn=evaluate_worker, map_fn=pool.map, **ga_kwargs)
    else:
        evaluator = Evaluator(wn_base, pipe_names, catalogo, restricciones)
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
