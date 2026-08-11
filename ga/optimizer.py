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


def _build_toolbox(n_genes, n_diametros, tam_torneo):
    toolbox = base.Toolbox()
    toolbox.register("attr_indice", random.randint, 0, n_diametros - 1)
    toolbox.register(
        "individual",
        tools.initRepeat,
        creator.Individual,
        toolbox.attr_indice,
        n=n_genes,
    )
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)
    toolbox.register("mate", tools.cxTwoPoint)
    toolbox.register(
        "mutate", tools.mutUniformInt, low=0, up=n_diametros - 1, indpb=0.02
    )
    toolbox.register("select", tools.selTournament, tournsize=tam_torneo)
    return toolbox


def _evaluar_lote(evaluate_fn, map_fn, individuos):
    planos = [list(ind) for ind in individuos]
    resultados = list(map_fn(evaluate_fn, planos))
    for ind, valor in zip(individuos, resultados):
        ind.fitness.values = (valor,)


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
):
    if map_fn is None:
        map_fn = map

    toolbox = _build_toolbox(n_genes, n_diametros, tam_torneo)
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

    for gen in range(gen_inicial, generaciones + 1):
        elite = toolbox.clone(hof[0])

        descendencia = toolbox.select(pop, len(pop) - 1)
        descendencia = [toolbox.clone(ind) for ind in descendencia]

        for hijo1, hijo2 in zip(descendencia[::2], descendencia[1::2]):
            if random.random() < prob_cruce:
                toolbox.mate(hijo1, hijo2)
                del hijo1.fitness.values
                del hijo2.fitness.values

        for mutante in descendencia:
            if random.random() < prob_mutacion:
                toolbox.mutate(mutante)
                del mutante.fitness.values

        invalidos = [ind for ind in descendencia if not ind.fitness.valid]
        _evaluar_lote(evaluate_fn, map_fn, invalidos)

        pop = descendencia + [elite]
        hof.update(pop)

        if on_generation is not None:
            fitnesses = [ind.fitness.values[0] for ind in pop]
            on_generation(gen, min(fitnesses), sum(fitnesses) / len(fitnesses))

        if checkpoint_path is not None and (
            gen % checkpoint_cada == 0 or gen == generaciones
        ):
            _guardar_checkpoint(checkpoint_path, gen, pop, hof)

    return hof[0]
