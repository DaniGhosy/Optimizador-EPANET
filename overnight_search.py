"""Corre el GA con muchas semillas distintas, sin supervisión, y se queda con
la mejor solución encontrada. Pensado para dejar corriendo toda la noche.

Cada semilla es una corrida independiente y completa (misma red, mismos
parámetros de config.yaml, sin reusar checkpoint entre semillas). Después de
cada semilla se compara su fitness contra la mejor hasta el momento; si la
mejora, se guarda de inmediato el .inp y .xlsx correspondientes — así, si se
corta a medianoche, ya queda listo lo mejor encontrado hasta ese punto, no
hay que esperar a que termine todo.

Uso:
    python overnight_search.py --config config.yaml --horas 8
    python overnight_search.py --config config.yaml --n-semillas 30
"""

import argparse
import copy
import json
import os
import random
import sys
import time

import yaml

if sys.stdout.encoding is not None and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from core.constraints import construir_restricciones
from core.network import apply_diameters, load_network_para_optimizacion, run_simulation
from io_epanet.excel_writer import escribir_reporte_excel
from io_epanet.inp_writer import escribir_inp_optimizado
from main import cargar_config, correr_optimizacion


def parse_args():
    parser = argparse.ArgumentParser(description="Búsqueda multi-semilla desatendida")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--horas", type=float, default=8.0, help="Tiempo máximo total, en horas")
    parser.add_argument(
        "--n-semillas",
        type=int,
        default=None,
        help="Si se da, para también al llegar a esta cantidad de semillas (lo que ocurra primero)",
    )
    parser.add_argument("--salida-dir", default="overnight_resultados")
    parser.add_argument(
        "--detener-si-existe",
        default=None,
        help="Si se da una ruta y ese archivo aparece, se detiene con gracia después de la semilla en curso",
    )
    return parser.parse_args()


def _escribir_estado(salida_dir, corriendo, intentos, inicio, mejor):
    estado = {
        "corriendo": corriendo,
        "intentos": intentos,
        "inicio": inicio,
        "actualizado": time.time(),
        "mejor_fitness": mejor["fitness_final"] if mejor else None,
        "mejor_semilla": mejor.get("semilla") if mejor else None,
    }
    with open(os.path.join(salida_dir, "estado.json"), "w", encoding="utf-8") as f:
        json.dump(estado, f)


def guardar_mejor(config, resultado, salida_dir):
    wn_base = load_network_para_optimizacion(config["red"]["inp_path"], config)
    wn_final = apply_diameters(wn_base, resultado["pipe_names"], resultado["diametros_mm"])
    resultados_sim = run_simulation(wn_final)
    restricciones = construir_restricciones(config)

    escribir_inp_optimizado(
        wn_base, resultado["pipe_names"], resultado["diametros_mm"],
        os.path.join(salida_dir, "mejor.inp"),
    )
    if resultados_sim is not None:
        escribir_reporte_excel(
            wn_final, resultados_sim, restricciones,
            os.path.join(salida_dir, "mejor.xlsx"),
            resultado["fitness_final"], config["catalogo_diametros"],
        )
    with open(os.path.join(salida_dir, "mejor.json"), "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)


def main():
    args = parse_args()
    config_base = cargar_config(args.config)
    os.makedirs(args.salida_dir, exist_ok=True)
    ruta_bitacora = os.path.join(args.salida_dir, "bitacora_semillas.jsonl")

    mejor = None
    inicio = time.time()
    limite_seg = args.horas * 3600
    intentos = 0

    print(f"Búsqueda multi-semilla — límite: {args.horas} h" + (f", máx {args.n_semillas} semillas" if args.n_semillas else ""))
    print(f"Resultados en: {os.path.abspath(args.salida_dir)}\n")
    _escribir_estado(args.salida_dir, True, intentos, inicio, mejor)

    while True:
        transcurrido = time.time() - inicio
        if transcurrido >= limite_seg:
            print(f"\nSe alcanzó el límite de {args.horas} h. Deteniendo.")
            break
        if args.n_semillas is not None and intentos >= args.n_semillas:
            print(f"\nSe alcanzó el máximo de {args.n_semillas} semillas. Deteniendo.")
            break
        if args.detener_si_existe is not None and os.path.exists(args.detener_si_existe):
            print("\nSe pidió detener la búsqueda. Deteniendo.")
            os.remove(args.detener_si_existe)
            break

        semilla = random.randint(1, 10_000_000)
        config = copy.deepcopy(config_base)
        config["ga"]["semilla"] = semilla
        config["ga"]["checkpoint_path"] = None  # cada semilla es una corrida propia, sin resume

        print(f"=== Semilla {semilla}  (intento {intentos + 1}, {transcurrido / 60:.1f} min transcurridos) ===")
        try:
            resultado = correr_optimizacion(config, imprimir=False)
        except Exception as e:
            print(f"  Falló esta semilla ({e}), sigue con la próxima.")
            intentos += 1
            continue

        fitness = resultado["fitness_final"]
        print(f"  fitness final: {fitness:.4f}")

        with open(ruta_bitacora, "a", encoding="utf-8") as f:
            f.write(json.dumps({"semilla": semilla, "fitness_final": fitness, "penalizaciones": resultado["penalizaciones"]}) + "\n")

        if mejor is None or fitness < mejor["fitness_final"]:
            resultado["semilla"] = semilla
            mejor = resultado
            print(f"  >>> Nueva mejor solución encontrada (fitness={fitness:.4f}), guardando...")
            guardar_mejor(config, mejor, args.salida_dir)

        intentos += 1
        _escribir_estado(args.salida_dir, True, intentos, inicio, mejor)

    print(f"\nTerminado: {intentos} semillas probadas en {(time.time() - inicio) / 60:.1f} min.")
    if mejor:
        print(f"Mejor semilla: {mejor['semilla']}  —  fitness: {mejor['fitness_final']:.4f}")
        print(f"Resultado final en: {os.path.abspath(args.salida_dir)}/mejor.inp, mejor.xlsx, mejor.json")
    else:
        print("Ninguna semilla terminó con éxito.")
    _escribir_estado(args.salida_dir, False, intentos, inicio, mejor)


if __name__ == "__main__":
    main()
