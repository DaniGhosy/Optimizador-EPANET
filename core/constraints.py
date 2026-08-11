"""Restricciones activables/parametrizables. Cada una expone la misma interfaz:
`activo`, `peso_penalizacion` y `evaluar(wn, resultados) -> float`, con
penalización proporcional a la magnitud de la violación (no binaria)."""

import pandas as pd


class VelocidadConstraint:
    def __init__(self, vmin, vmax, peso_penalizacion, activo=True, excepciones_vmax=None):
        self.activo = activo
        self.vmin = vmin
        self.vmax = vmax
        self.peso_penalizacion = peso_penalizacion
        # Tuberías con un límite de velocidad propio (p.ej. la línea de
        # aducción principal, que puede ir más rápido que el resto de la red).
        self.excepciones_vmax = excepciones_vmax or {}

    def evaluar(self, wn, resultados):
        velocidades = resultados.link["velocity"][wn.pipe_name_list].iloc[0].abs()
        vmax_por_tuberia = pd.Series(self.vmax, index=wn.pipe_name_list)
        for nombre, valor in self.excepciones_vmax.items():
            if nombre in vmax_por_tuberia.index:
                vmax_por_tuberia[nombre] = valor
        exceso = (velocidades - vmax_por_tuberia).clip(lower=0.0)
        defecto = (self.vmin - velocidades).clip(lower=0.0)
        return self.peso_penalizacion * (exceso.sum() + defecto.sum())


class PerdidaUnitariaConstraint:
    def __init__(self, hlmax, peso_penalizacion, activo=True):
        self.activo = activo
        self.hlmax = hlmax
        self.peso_penalizacion = peso_penalizacion

    def evaluar(self, wn, resultados):
        # wntr informa 'headloss' ya como gradiente unitario (m de pérdida por
        # m de tubería), no como pérdida total del tramo. Se multiplica por
        # 1000 para pasar a m/km, la unidad convencional de hlmax.
        hl_unitaria = resultados.link["headloss"][wn.pipe_name_list].iloc[0].abs() * 1000.0
        exceso = (hl_unitaria - self.hlmax).clip(lower=0.0)
        return self.peso_penalizacion * exceso.sum()


class PresionMinimaConstraint:
    def __init__(self, valor, peso_penalizacion, activo=True):
        self.activo = activo
        self.valor = valor
        self.peso_penalizacion = peso_penalizacion

    def evaluar(self, wn, resultados):
        presiones = resultados.node["pressure"][wn.junction_name_list].iloc[0]
        defecto = (self.valor - presiones).clip(lower=0.0)
        return self.peso_penalizacion * defecto.sum()


def construir_restricciones(config):
    restricciones_cfg = config["restricciones"]
    restricciones = []

    vel_cfg = restricciones_cfg["velocidad"]
    restricciones.append(
        VelocidadConstraint(
            vmin=vel_cfg["vmin"],
            vmax=vel_cfg["vmax"],
            peso_penalizacion=vel_cfg["peso_penalizacion"],
            activo=vel_cfg["activo"],
            excepciones_vmax=vel_cfg.get("excepciones_vmax"),
        )
    )

    hl_cfg = restricciones_cfg["perdida_unitaria"]
    restricciones.append(
        PerdidaUnitariaConstraint(
            hlmax=hl_cfg["hlmax"],
            peso_penalizacion=hl_cfg["peso_penalizacion"],
            activo=hl_cfg["activo"],
        )
    )

    presion_cfg = restricciones_cfg["presion_minima"]
    restricciones.append(
        PresionMinimaConstraint(
            valor=presion_cfg["valor"],
            peso_penalizacion=presion_cfg["peso_penalizacion"],
            activo=presion_cfg["activo"],
        )
    )

    return [r for r in restricciones if r.activo]
