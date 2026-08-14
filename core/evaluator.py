"""Función de fitness: aplica diámetros candidatos, simula y suma penalizaciones.

Dos mejoras sobre la evaluación pura:

- Caché por combinación exacta de diámetros: cuando la población converge
  (común pasada la mitad de las generaciones), muchos individuos son iguales
  o comparten genes tras cruce/mutación sin cambios efectivos — cachear evita
  volver a correr EPANET para algo ya simulado por este worker.
- Reparación Lamarckiana opcional, iterativa: si alguna tubería queda con
  velocidad o pérdida unitaria fuera de rango, se le sube o baja un escalón de
  catálogo en la dirección que corrige la violación (más diámetro si sobra
  velocidad o pérdida, menos si falta velocidad) y se simula de nuevo. Como
  corregir una tubería cambia el reparto de caudal en la red, eso puede dejar
  a OTRA tubería violando algo que antes estaba bien — por eso se repite hasta
  un tope de intentos, no solo una vez, mientras cada ronda siga mejorando. Se
  devuelve el mejor punto encontrado en la cadena — genes incluidos — en vez
  de dejar que el GA tenga que "descubrir por accidente" con cruce/mutación la
  combinación válida que ya se puede construir directo a partir de la física
  del problema."""

from core.network import apply_diameters, run_simulation

PENALIZACION_SIMULACION_FALLIDA = 1e7


class Evaluator:
    def __init__(self, wn_base, pipe_names, catalogo_diametros, restricciones, reparar=True, reparacion_max_intentos=3):
        self.wn_base = wn_base
        self.pipe_names = pipe_names
        self.catalogo_diametros = catalogo_diametros
        self.restricciones = restricciones
        self.reparar = reparar
        self.reparacion_max_intentos = reparacion_max_intentos
        self._velocidad_r = next((r for r in restricciones if type(r).__name__ == "VelocidadConstraint"), None)
        self._hl_r = next((r for r in restricciones if type(r).__name__ == "PerdidaUnitariaConstraint"), None)
        self._cache = {}

    def evaluate(self, indices_diametro):
        """Devuelve (genes, fitness). `genes` puede diferir de `indices_diametro`
        de entrada si la reparación encontró un diseño mejor — quien llama debe
        escribirlo de vuelta en el individuo (ver ga/optimizer.py:_evaluar_lote)."""
        clave = tuple(indices_diametro)
        if clave in self._cache:
            return self._cache[clave]

        genes, fitness = self._evaluar_con_reparacion(list(clave))
        resultado = (genes, fitness)
        self._cache[clave] = resultado
        return resultado

    def _simular(self, indices):
        diametros_mm = [self.catalogo_diametros[i] for i in indices]
        wn = apply_diameters(self.wn_base, self.pipe_names, diametros_mm)
        resultados = run_simulation(wn)

        if resultados is None:
            return None, PENALIZACION_SIMULACION_FALLIDA

        try:
            fitness = sum(r.evaluar(wn, resultados) for r in self.restricciones)
        except Exception:
            return None, PENALIZACION_SIMULACION_FALLIDA

        return resultados, fitness

    def _proponer_reparacion(self, indices, resultados):
        """Un escalón de catálogo por tubería, en la dirección que corrige la
        violación detectada en `resultados`. None si ninguna tubería cambia."""
        n_catalogo = len(self.catalogo_diametros)
        velocidades = (
            resultados.link["velocity"][self.pipe_names].abs()
            if self._velocidad_r is not None else None
        )
        hl = (
            resultados.link["headloss"][self.pipe_names].abs() * 1000.0
            if self._hl_r is not None else None
        )

        reparados = list(indices)
        cambio = False
        for i, nombre in enumerate(self.pipe_names):
            paso = 0
            if hl is not None and hl.iloc[0][nombre] > self._hl_r.hlmax:
                paso = 1  # más pérdida de la permitida -> más diámetro
            if velocidades is not None:
                v = velocidades.iloc[0][nombre]
                vmax_ef = self._velocidad_r.excepciones_vmax.get(nombre, self._velocidad_r.vmax)
                if v > vmax_ef:
                    paso = 1  # va muy rápido -> más diámetro
                elif v < self._velocidad_r.vmin and paso == 0:
                    paso = -1  # va muy lento -> menos diámetro
            if paso != 0:
                nuevo = min(max(reparados[i] + paso, 0), n_catalogo - 1)
                if nuevo != reparados[i]:
                    reparados[i] = nuevo
                    cambio = True

        return reparados if cambio else None

    def _evaluar_con_reparacion(self, indices):
        resultados, fitness = self._simular(indices)
        mejor_indices, mejor_fitness = indices, fitness

        if not self.reparar or resultados is None or fitness == 0:
            return mejor_indices, mejor_fitness

        actuales, resultados_actuales, fitness_actual = indices, resultados, fitness
        for _ in range(self.reparacion_max_intentos):
            propuesta = self._proponer_reparacion(actuales, resultados_actuales)
            if propuesta is None:
                break

            resultados_prop, fitness_prop = self._simular(propuesta)
            if fitness_prop >= fitness_actual:
                break  # esta ronda ya no mejora, no seguir insistiendo

            if fitness_prop < mejor_fitness:
                mejor_indices, mejor_fitness = propuesta, fitness_prop
            if fitness_prop == 0 or resultados_prop is None:
                break

            actuales, resultados_actuales, fitness_actual = propuesta, resultados_prop, fitness_prop

        return mejor_indices, mejor_fitness
