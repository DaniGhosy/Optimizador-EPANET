"""Ciclo de algoritmo genético (deap): cromosoma = índice de diámetro por tubería.

Soporta evaluación paralela (via `map_fn`, p.ej. multiprocessing.Pool.map) y
checkpoint/resume periódico para poder pausar y retomar corridas largas.
"""

import os
import pickle
import random

from deap import base, creator, tools

if not hasattr(creator, "FitnessMin"):
    creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
if not hasattr(creator, "Individual"):
    creator.create("Individual", list, fitness=creator.FitnessMin)


def _build_toolbox(n_genes, n_diametros, tam_torneo, bandas=None):
    """`bandas`, si se da, es una lista de (idx_min, idx_max) por gen — acota
    la inicialización y mutación de cada tubería a un rango de catálogo
    físicamente plausible (calculado a partir de un caudal aproximado, ver
    main.py:_calcular_bandas_diametro) en vez del catálogo completo. Reduce el
    espacio de búsqueda real sin descartar el óptimo: el cálculo deja margen
    generoso. Sin `bandas`, cada gen usa el catálogo completo (0..n_diametros-1),
    el comportamiento original."""
    toolbox = base.Toolbox()
    rangos = bandas if bandas is not None else [(0, n_diametros - 1)] * n_genes

    def _individuo():
        return creator.Individual(random.randint(lo, hi) for lo, hi in rangos)

    def _mutar(individual, indpb):
        for i, (lo, hi) in enumerate(rangos):
            if random.random() < indpb:
                individual[i] = random.randint(lo, hi)
        return (individual,)

    toolbox.register("individual", _individuo)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)
    # Cruce uniforme en vez de dos-puntos: el orden de los genes (tuberías) no
    # tiene significado espacial, así que cortar en dos puntos y mezclar
    # bloques contiguos no respeta ninguna estructura real del problema — es
    # el caso típico donde el cruce uniforme converge mejor que n-puntos.
    toolbox.register("mate", tools.cxUniform, indpb=0.5)
    toolbox.register("mutate", _mutar, indpb=0.02)
    toolbox.register("select", tools.selTournament, tournsize=tam_torneo)
    return toolbox


def _evaluar_lote(evaluate_fn, map_fn, individuos):
    planos = [list(ind) for ind in individuos]
    resultados = list(map_fn(evaluate_fn, planos))
    for ind, (genes, fitness) in zip(individuos, resultados):
        # evaluate_fn puede devolver genes reparados distintos a los que se
        # mandaron a evaluar (ver core/evaluator.py) — se escriben de vuelta
        # en el individuo (Lamarckiano) para que el genotipo quede consistente
        # con el fitness que se le asigna, y el cruce/mutación de la próxima
        # generación parta de la versión ya reparada.
        if genes != list(ind):
            ind[:] = genes
        ind.fitness.values = (fitness,)


def _guardar_checkpoint(path, generacion, pop, hof):
    estado = {
        "generacion": generacion,
        "poblacion": [(list(ind), ind.fitness.values[0]) for ind in pop],
        "hof_individuo": list(hof[0]),
        "hof_fitness": hof[0].fitness.values[0],
        "random_state": random.getstate(),
    }
    with open(path, "wb") as f:
        pickle.dump(estado, f)


def _cargar_checkpoint(path):
    with open(path, "rb") as f:
        return pickle.load(f)


def _tasa_mutacion(gen, generaciones, prob_inicial, prob_min):
    """Interpolación lineal de prob_inicial (gen 1) a prob_min (última
    generación) — muta más al principio para explorar, menos al final para
    afinar en vez de seguir revolviendo una población ya casi convergida."""
    if generaciones <= 1:
        return prob_inicial
    fraccion = (gen - 1) / (generaciones - 1)
    return prob_inicial + (prob_min - prob_inicial) * fraccion


def run_ga(
    evaluate_fn,
    n_genes,
    n_diametros,
    poblacion=30,
    generaciones=30,
    prob_cruce=0.7,
    prob_mutacion=0.2,
    tam_torneo=3,
    semilla=None,
    on_generation=None,
    map_fn=None,
    checkpoint_path=None,
    checkpoint_cada=5,
    individuos_semilla=None,
    mutacion_adaptativa=False,
    prob_mutacion_min=None,
    bandas_diametro=None,
    reinicio_activo=False,
    reinicio_paciencia=15,
    reinicio_fraccion=0.15,
):
    if map_fn is None:
        map_fn = map

    toolbox = _build_toolbox(n_genes, n_diametros, tam_torneo, bandas=bandas_diametro)
    hof = tools.HallOfFame(1)

    gen_inicial = 1
    if checkpoint_path is not None and os.path.exists(checkpoint_path):
        estado = _cargar_checkpoint(checkpoint_path)
        random.setstate(estado["random_state"])
        pop = []
        for genes, fitness in estado["poblacion"]:
            ind = creator.Individual(genes)
            ind.fitness.values = (fitness,)
            pop.append(ind)
        hof_ind = creator.Individual(estado["hof_individuo"])
        hof_ind.fitness.values = (estado["hof_fitness"],)
        hof.update([hof_ind])
        gen_inicial = estado["generacion"] + 1
        print(f"  [checkpoint] retomando desde generación {gen_inicial} ({checkpoint_path})")
    else:
        if semilla is not None:
            random.seed(semilla)
        pop = toolbox.population(n=poblacion)
        # "Warm start": si se da un diseño de partida (p.ej. el actual, ya
        # razonable), se inyecta en la población inicial en vez de dejar que
        # el GA tenga que redescubrirlo desde cero por búsqueda aleatoria —
        # con 300+ genes eso puede no converger ni en cientos de generaciones.
        for i, genes in enumerate(individuos_semilla or []):
            if i < len(pop):
                pop[i] = creator.Individual(genes)
        _evaluar_lote(evaluate_fn, map_fn, pop)
        hof.update(pop)

    mejor_historico = hof[0].fitness.values[0]
    gens_sin_mejora = 0

    for gen in range(gen_inicial, generaciones + 1):
        elite = toolbox.clone(hof[0])

        prob_mutacion_gen = (
            _tasa_mutacion(gen, generaciones, prob_mutacion, prob_mutacion_min)
            if mutacion_adaptativa and prob_mutacion_min is not None
            else prob_mutacion
        )

        descendencia = toolbox.select(pop, len(pop) - 1)
        descendencia = [toolbox.clone(ind) for ind in descendencia]

        for hijo1, hijo2 in zip(descendencia[::2], descendencia[1::2]):
            if random.random() < prob_cruce:
                toolbox.mate(hijo1, hijo2)
                del hijo1.fitness.values
                del hijo2.fitness.values

        for mutante in descendencia:
            if random.random() < prob_mutacion_gen:
                toolbox.mutate(mutante)
                del mutante.fitness.values

        invalidos = [ind for ind in descendencia if not ind.fitness.valid]
        _evaluar_lote(evaluate_fn, map_fn, invalidos)

        pop = descendencia + [elite]
        hof.update(pop)

        mejor_actual = hof[0].fitness.values[0]
        if mejor_actual < mejor_historico - 1e-9:
            mejor_historico = mejor_actual
            gens_sin_mejora = 0
        else:
            gens_sin_mejora += 1

        # Reinicio por estancamiento: si el mejor fitness no mejora en varias
        # generaciones seguidas, la población probablemente ya perdió
        # diversidad (convergió a un óptimo local) y las generaciones que
        # quedan se estarían gastando en poco más que ruido. Se reemplazan
        # los peores individuos por otros frescos (aleatorios dentro de las
        # mismas bandas de catálogo) para darle al GA una vía de escape, en
        # vez de dejarlo estancado el resto de la corrida.
        if (
            reinicio_activo
            and gens_sin_mejora >= reinicio_paciencia
            and gen < generaciones
        ):
            n_frescos = max(1, round(len(pop) * reinicio_fraccion))
            frescos = [toolbox.individual() for _ in range(n_frescos)]
            _evaluar_lote(evaluate_fn, map_fn, frescos)
            pop.sort(key=lambda ind: ind.fitness.values[0])
            pop[-n_frescos:] = frescos
            gens_sin_mejora = 0

        if on_generation is not None:
            fitnesses = [ind.fitness.values[0] for ind in pop]
            # `on_generation` puede devolver False para pedir que se corte acá
            # (ver overnight_search.py: poda temprana de semillas que van muy
            # por detrás de las anteriores). Cualquier otro valor de retorno
            # (None incluido) significa "seguir" — no rompe a nadie que ya
            # use este callback solo para loguear.
            continuar = on_generation(gen, min(fitnesses), sum(fitnesses) / len(fitnesses))
            if continuar is False:
                break

        if checkpoint_path is not None and (
            gen % checkpoint_cada == 0 or gen == generaciones
        ):
            _guardar_checkpoint(checkpoint_path, gen, pop, hof)

    return hof[0]
