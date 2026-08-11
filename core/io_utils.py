"""Carga robusta de archivos .inp de EPANET con codificación desconocida."""

import os
import tempfile


def cargar_inp_robusto(path_entrada):
    """Devuelve la ruta a una copia del .inp normalizada a utf-8.

    Los .inp exportados desde EPANET en Windows suelen venir en cp1252
    (acentos/ñ), no utf-8, lo que hace fallar a wntr directamente. Se intenta
    utf-8 primero y solo se reintenta con cp1252 si falla."""
    with open(path_entrada, "rb") as f:
        data = f.read()

    try:
        texto = data.decode("utf-8")
    except UnicodeDecodeError:
        texto = data.decode("cp1252")

    nombre_base = os.path.basename(path_entrada)
    fd, ruta_normalizada = tempfile.mkstemp(prefix="inp_utf8_", suffix=f"_{nombre_base}")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(texto)

    return ruta_normalizada
