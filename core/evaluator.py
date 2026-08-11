"""Función de fitness: aplica diámetros candidatos, simula y suma penalizaciones."""

from core.network import apply_diameters, run_simulation

PENALIZACION_SIMULACION_FALLIDA = 1e7


class Evaluator:
    def __init__(self, wn_base, pipe_names, catalogo_diametros, restricciones):
        self.wn_base = wn_base
        self.pipe_names = pipe_names
        self.catalogo_diametros = catalogo_diametros
        self.restricciones = restricciones

    def evaluate(self, indices_diametro):
        diametros_mm = [self.catalogo_diametros[i] for i in indices_diametro]
        wn = apply_diameters(self.wn_base, self.pipe_names, diametros_mm)
        resultados = run_simulation(wn)

        if resultados is None:
            return PENALIZACION_SIMULACION_FALLIDA

        try:
            return sum(r.evaluar(wn, resultados) for r in self.restricciones)
        except Exception:
            return PENALIZACION_SIMULACION_FALLIDA
