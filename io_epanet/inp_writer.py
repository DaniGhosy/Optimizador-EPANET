"""Exportar la red optimizada como archivo .inp de EPANET."""

import wntr

from core.network import apply_diameters


def escribir_inp_optimizado(wn_base, pipe_names, diametros_mm, ruta_salida):
    wn_final = apply_diameters(wn_base, pipe_names, diametros_mm)
    wntr.network.write_inpfile(
        wn_final, ruta_salida, units=wn_base.options.hydraulic.inpfile_units
    )
    return ruta_salida
