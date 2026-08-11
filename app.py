"""App interactiva (Streamlit) para el optimizador de diámetros EPANET.

Corre el GA como subproceso (`python main.py ...`) en vez de llamarlo
directamente: multiprocessing.Pool dentro del propio proceso de Streamlit es
frágil en Windows (spawn re-importa el módulo "principal", que bajo Streamlit
no es un script normal). Lanzarlo como subproceso reusa main.py tal cual, sin
tocar su lógica, y aísla el Pool en un proceso `python main.py` genuino.
"""

import json
import os
import subprocess
import sys
import tempfile

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from core.constraints import construir_restricciones
from core.io_utils import cargar_inp_robusto
from core.network import apply_diameters, load_network_para_optimizacion, run_simulation
from io_epanet.excel_writer import escribir_reporte_excel
from io_epanet.inp_writer import escribir_inp_optimizado

DIR_PROYECTO = os.path.dirname(os.path.abspath(__file__))
CONFIG_DEFAULT_PATH = os.path.join(DIR_PROYECTO, "config.yaml")
YOUTUBE_URL = "https://www.youtube.com/@Galindo.IngCivil"

st.set_page_config(page_title="Optimizador EPANET", page_icon="💧", layout="wide")

st.markdown(
    """
    <style>
    .main .block-container { padding-top: 1.5rem; max-width: 1200px; }
    div[data-testid="stMetricValue"] { font-size: 1.7rem; }
    div[data-testid="stMetric"] {
        background: rgba(15, 127, 140, 0.08);
        border: 1px solid rgba(15, 127, 140, 0.25);
        border-radius: 10px;
        padding: 0.6rem 0.9rem 0.3rem 0.9rem;
    }
    h1, h2, h3 { letter-spacing: -0.01em; }

    /* Ocultar el "chrome" propio de Streamlit (menú hamburguesa, botón
    Deploy, badge "Made with Streamlit", aviso de skills) — es nuestra app. */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header { visibility: hidden; }
    [data-testid="stToolbar"] { visibility: hidden; }
    [data-testid="stDeployButton"] { display: none; }
    [data-testid="stStatusWidget"] { visibility: hidden; }
    [data-testid="stSkillsNudge"] { display: none; }
    [data-testid="stSkillsNudgeAnchor"] { display: none; }
    /* El botón para abrir/cerrar la barra lateral vive dentro de <header>,
    que acabamos de ocultar arriba — hay que devolverle la visibilidad
    explícitamente o no hay forma de reabrirla una vez cerrada. */
    [data-testid="stExpandSidebarButton"] { visibility: visible !important; }
    [data-testid="stSidebarCollapseButton"] { visibility: visible !important; }

    /* Marca GALINDO + insignia EPANET en la barra lateral */
    .brand-wordmark {
        font-size: 1.35rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        color: #0f7f8c;
        margin-bottom: 2px;
    }
    .epanet-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 9px 4px 7px;
        border: 1px solid rgba(127, 149, 161, 0.35);
        border-radius: 999px;
        background: rgba(127, 149, 161, 0.10);
        color: #7f95a1;
        font-size: 11px;
        margin-bottom: 14px;
    }
    .yt-sidebar-link {
        display: flex;
        align-items: center;
        gap: 7px;
        color: #7f95a1;
        text-decoration: none;
        font-size: 12px;
        margin-top: 14px;
    }
    .yt-sidebar-link:hover { color: #4c6473; }
    </style>
    """,
    unsafe_allow_html=True,
)


def cargar_config_default():
    with open(CONFIG_DEFAULT_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if "config" not in st.session_state:
    st.session_state.config = cargar_config_default()
if "inp_path" not in st.session_state:
    ruta_default = os.path.join(DIR_PROYECTO, "networks", "INP.inp")
    st.session_state.inp_path = ruta_default if os.path.exists(ruta_default) else None
if "resultado" not in st.session_state:
    st.session_state.resultado = None
if "log" not in st.session_state:
    st.session_state.log = ""


def pipe_names_disponibles():
    if not st.session_state.inp_path:
        return []
    try:
        wn = load_network_para_optimizacion(st.session_state.inp_path, st.session_state.config)
        return list(wn.pipe_name_list)
    except Exception:
        return []


def construir_mapa_red(wn, resultados, restricciones):
    pipe_names = wn.pipe_name_list
    velocidad_r = next((r for r in restricciones if type(r).__name__ == "VelocidadConstraint"), None)
    hl_r = next((r for r in restricciones if type(r).__name__ == "PerdidaUnitariaConstraint"), None)

    v = resultados.link["velocity"][pipe_names].iloc[0].abs()
    hl = resultados.link["headloss"][pipe_names].iloc[0].abs() * 1000.0

    grupos = {
        "Cumple": {"x": [], "y": [], "hover": [], "color": "#2ecc71"},
        "Viola velocidad": {"x": [], "y": [], "hover": [], "color": "#e74c3c"},
        "Viola pérdida unitaria": {"x": [], "y": [], "hover": [], "color": "#f39c12"},
    }

    for name in pipe_names:
        pipe = wn.get_link(name)
        x0, y0 = pipe.start_node.coordinates
        x1, y1 = pipe.end_node.coordinates

        estado = "Cumple"
        if velocidad_r is not None:
            vmax_ef = velocidad_r.excepciones_vmax.get(name, velocidad_r.vmax)
            if not (velocidad_r.vmin <= v[name] <= vmax_ef):
                estado = "Viola velocidad"
        if estado == "Cumple" and hl_r is not None and hl[name] > hl_r.hlmax:
            estado = "Viola pérdida unitaria"

        texto = f"Tubería {name}<br>Diámetro: {pipe.diameter * 1000:.0f} mm<br>Velocidad: {v[name]:.3f} m/s<br>Pérdida unitaria: {hl[name]:.2f} m/km"
        grupos[estado]["x"] += [x0, x1, None]
        grupos[estado]["y"] += [y0, y1, None]
        grupos[estado]["hover"] += [texto, texto, ""]

    fig = go.Figure()
    for estado, datos in grupos.items():
        if datos["x"]:
            fig.add_trace(
                go.Scatter(
                    x=datos["x"],
                    y=datos["y"],
                    mode="lines",
                    line=dict(color=datos["color"], width=2.5),
                    name=f"{estado} ({sum(1 for h in datos['hover'] if h) // 2})",
                    hoverinfo="text",
                    text=datos["hover"],
                )
            )

    # Nodos: cota, demanda base y presión al pasar el mouse
    presiones = resultados.node["pressure"]
    nx, ny, ntext = [], [], []
    for name in wn.junction_name_list:
        node = wn.get_node(name)
        x, y = node.coordinates
        demanda_lps = sum(d.base_value for d in node.demand_timeseries_list) * 1000.0
        presion = float(presiones[name].iloc[0])
        nx.append(x)
        ny.append(y)
        ntext.append(
            f"Nodo {name}<br>Cota: {node.elevation:.1f} m<br>"
            f"Demanda base: {demanda_lps:.3f} L/s<br>Presión: {presion:.2f} m"
        )
    if nx:
        fig.add_trace(
            go.Scatter(
                x=nx, y=ny, mode="markers",
                marker=dict(size=6, color="#ffffff", line=dict(width=1.2, color="#7f95a1")),
                name="Nodos", hoverinfo="text", text=ntext,
            )
        )

    # Tanque(s): cota de solera, nivel inicial, mínimo y máximo
    tx, ty, ttext = [], [], []
    for name in wn.tank_name_list:
        tank = wn.get_node(name)
        x, y = tank.coordinates
        tx.append(x)
        ty.append(y)
        ttext.append(
            f"Tanque {name}<br>Cota de solera: {tank.elevation:.1f} m<br>"
            f"Nivel inicial: {tank.init_level:.1f} m<br>Nivel mínimo: {tank.min_level:.1f} m<br>"
            f"Nivel máximo: {tank.max_level:.1f} m"
        )
    if tx:
        fig.add_trace(
            go.Scatter(
                x=tx, y=ty, mode="markers",
                marker=dict(size=15, symbol="square", color="#b3792f"),
                name="Tanque", hoverinfo="text", text=ttext,
            )
        )

    fig.update_layout(
        showlegend=True,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1),
        height=650,
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def construir_tabla_cumplimiento(wn, resultados, restricciones):
    pipe_names = wn.pipe_name_list
    velocidad_r = next((r for r in restricciones if type(r).__name__ == "VelocidadConstraint"), None)
    hl_r = next((r for r in restricciones if type(r).__name__ == "PerdidaUnitariaConstraint"), None)

    v = resultados.link["velocity"][pipe_names].iloc[0].abs()
    hl = resultados.link["headloss"][pipe_names].iloc[0].abs() * 1000.0

    filas = []
    for name in pipe_names:
        pipe = wn.get_link(name)
        cumple = True
        if velocidad_r is not None:
            vmax_ef = velocidad_r.excepciones_vmax.get(name, velocidad_r.vmax)
            cumple = cumple and (velocidad_r.vmin <= v[name] <= vmax_ef)
        if hl_r is not None:
            cumple = cumple and (hl[name] <= hl_r.hlmax)
        filas.append(
            {
                "Tubería": name,
                "Diámetro (mm)": round(pipe.diameter * 1000, 1),
                "Velocidad (m/s)": round(float(v[name]), 4),
                "Pérdida unitaria (m/km)": round(float(hl[name]), 3),
                "Cumple": cumple,
            }
        )
    return pd.DataFrame(filas)


def _resaltar_no_cumple(fila):
    color = "background-color: #ffc7ce" if not fila["Cumple"] else ""
    return [color] * len(fila)


def mostrar_resultado_completo(resultado, config, key_sufijo=""):
    """Métricas + mapa + convergencia + tabla + descargas para un resultado ya
    corrido (dict con el mismo formato que escribe correr_optimizacion). La
    usan tanto el modo normal como, para la mejor semilla, el modo nocturno."""
    col_metricas = st.columns(1 + len(resultado["penalizaciones"]))
    col_metricas[0].metric("Fitness final", f"{resultado['fitness_final']:.4f}")
    for col, (nombre, valor) in zip(col_metricas[1:], resultado["penalizaciones"].items()):
        col.metric(nombre, f"{valor:.4f}")

    wn_base = load_network_para_optimizacion(resultado["inp_path"], config)
    wn_final = apply_diameters(wn_base, resultado["pipe_names"], resultado["diametros_mm"])
    res_sim = run_simulation(wn_final)
    restricciones = construir_restricciones(config)

    if res_sim is None:
        st.error("La simulación final no convergió, no se puede visualizar.")
        return

    st.subheader("Mapa de la red")
    st.plotly_chart(
        construir_mapa_red(wn_final, res_sim, restricciones),
        use_container_width=True,
        key=f"mapa_{key_sufijo}",
    )

    df_hist = pd.DataFrame(resultado.get("historia", []))
    if not df_hist.empty:
        st.subheader("Curva de convergencia")
        fig_conv = go.Figure()
        fig_conv.add_trace(go.Scatter(x=df_hist["gen"], y=df_hist["fitness_min"], name="fitness mínimo"))
        fig_conv.add_trace(go.Scatter(x=df_hist["gen"], y=df_hist["fitness_prom"], name="fitness promedio"))
        fig_conv.update_layout(xaxis_title="Generación", yaxis_title="Fitness", height=350)
        st.plotly_chart(fig_conv, use_container_width=True, key=f"conv_{key_sufijo}")

    st.subheader("Tabla de tuberías")
    df_tabla = construir_tabla_cumplimiento(wn_final, res_sim, restricciones)
    st.dataframe(
        df_tabla.style.apply(_resaltar_no_cumple, axis=1),
        use_container_width=True,
        height=400,
        key=f"tabla_{key_sufijo}",
    )

    st.subheader("Descargas")
    col1, col2 = st.columns(2)
    with col1:
        ruta_inp_out = os.path.join(tempfile.mkdtemp(prefix="epanet_opt_out_"), "red_optimizada.inp")
        escribir_inp_optimizado(wn_base, resultado["pipe_names"], resultado["diametros_mm"], ruta_inp_out)
        with open(ruta_inp_out, "rb") as f:
            st.download_button(
                "⬇️ Descargar .inp optimizado", data=f.read(), file_name="red_optimizada.inp",
                key=f"dl_inp_{key_sufijo}",
            )
    with col2:
        ruta_xlsx_out = os.path.join(tempfile.mkdtemp(prefix="epanet_opt_out_"), "reporte_resultados.xlsx")
        escribir_reporte_excel(
            wn_final, res_sim, restricciones, ruta_xlsx_out,
            resultado["fitness_final"], config["catalogo_diametros"],
        )
        with open(ruta_xlsx_out, "rb") as f:
            st.download_button(
                "⬇️ Descargar reporte .xlsx", data=f.read(), file_name="reporte_resultados.xlsx",
                key=f"dl_xlsx_{key_sufijo}",
            )


# ---------------------------------------------------------------- Sidebar --

with st.sidebar:
    st.markdown('<div class="brand-wordmark">GALINDO</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="epanet-badge">⏣ Optimizador para EPANET 2.2</div>',
        unsafe_allow_html=True,
    )

    seccion = st.radio(
        "Navegación",
        ["📂 Red", "⚙️ Parámetros", "▶️ Ejecutar / Resultados"],
        label_visibility="collapsed",
    )

    st.divider()
    if st.session_state.inp_path:
        st.caption(f"Red cargada: `{os.path.basename(st.session_state.inp_path)}`")
    else:
        st.caption("Ninguna red cargada todavía.")

    st.markdown(
        f'<a class="yt-sidebar-link" href="{YOUTUBE_URL}" target="_blank" rel="noopener">▶ @Galindo.IngCivil</a>',
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------ Red ---

if seccion == "📂 Red":
    st.header("Red EPANET")
    st.caption("Sube un archivo .inp (se detecta automáticamente si viene en utf-8 o cp1252).")

    archivo = st.file_uploader("Archivo .inp", type=["inp"])
    if archivo is not None:
        fd, tmp_subida = tempfile.mkstemp(suffix=f"_{archivo.name}")
        with os.fdopen(fd, "wb") as f:
            f.write(archivo.getvalue())
        try:
            st.session_state.inp_path = cargar_inp_robusto(tmp_subida)
            st.success(f"Red cargada: {archivo.name}")
        except Exception as e:
            st.error(f"No se pudo leer el archivo: {e}")

    if st.session_state.inp_path:
        try:
            wn_preview = load_network_para_optimizacion(st.session_state.inp_path, st.session_state.config)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Tuberías", wn_preview.num_pipes)
            c2.metric("Nodos", wn_preview.num_nodes)
            c3.metric("Unidades", wn_preview.options.hydraulic.inpfile_units)
            c4.metric("Fórmula", wn_preview.options.hydraulic.headloss)
        except Exception as e:
            st.error(f"No se pudo cargar la red: {e}")
    else:
        st.info("Sube un archivo .inp para empezar.")


# ------------------------------------------------------------ Parámetros --

elif seccion == "⚙️ Parámetros":
    st.header("Parámetros")
    cfg = st.session_state.config

    st.subheader("Catálogo de diámetros comerciales")
    catalogo_str = st.text_input(
        "Diámetros en mm, separados por coma",
        value=", ".join(str(d) for d in cfg["catalogo_diametros"]),
    )

    st.subheader("Restricción de velocidad")
    velocidad_activa = st.checkbox(
        "Activar", value=cfg["restricciones"]["velocidad"].get("activo", True), key="velocidad_activa"
    )
    vmin = cfg["restricciones"]["velocidad"]["vmin"]
    vmax = cfg["restricciones"]["velocidad"]["vmax"]
    peso_v = cfg["restricciones"]["velocidad"]["peso_penalizacion"]
    excepciones_vmax = cfg["restricciones"]["velocidad"].get("excepciones_vmax", {}) or {}
    if velocidad_activa:
        col1, col2, col3 = st.columns(3)
        vmin = col1.number_input("Velocidad mínima (m/s)", value=float(vmin), step=0.01, format="%.2f")
        vmax = col2.number_input("Velocidad máxima (m/s)", value=float(vmax), step=0.1, format="%.2f")
        peso_v = col3.number_input("Peso de penalización (velocidad)", value=float(peso_v))

        st.caption("Excepciones de velocidad máxima por tubería — ej. la línea de conducción, que puede ir más rápido que el resto de la red")
        pipes_disponibles = pipe_names_disponibles()
        df_excepciones = pd.DataFrame(
            [{"Tubería": k, "vmax (m/s)": v} for k, v in excepciones_vmax.items()]
        ) if excepciones_vmax else pd.DataFrame(columns=["Tubería", "vmax (m/s)"])
        columnas_config = {
            "vmax (m/s)": st.column_config.NumberColumn("vmax (m/s)", min_value=0.0, step=0.1, format="%.2f"),
        }
        if pipes_disponibles:
            columnas_config["Tubería"] = st.column_config.SelectboxColumn("Tubería", options=pipes_disponibles)
        df_excepciones_editado = st.data_editor(
            df_excepciones, num_rows="dynamic", key="excepciones_editor", column_config=columnas_config
        )
        excepciones_vmax = {
            str(row["Tubería"]): float(row["vmax (m/s)"])
            for _, row in df_excepciones_editado.iterrows()
            if str(row["Tubería"]).strip() and pd.notna(row["vmax (m/s)"])
        }
    else:
        st.caption("Desactivada — el GA no la considera.")

    st.subheader("Restricción de pérdida unitaria")
    perdida_activa = st.checkbox(
        "Activar", value=cfg["restricciones"]["perdida_unitaria"].get("activo", True), key="perdida_activa"
    )
    hlmax = cfg["restricciones"]["perdida_unitaria"]["hlmax"]
    peso_hl = cfg["restricciones"]["perdida_unitaria"]["peso_penalizacion"]
    if perdida_activa:
        col1, col2 = st.columns(2)
        hlmax = col1.number_input("Pérdida unitaria máxima (m/km)", value=float(hlmax))
        peso_hl = col2.number_input("Peso de penalización (pérdida unitaria)", value=float(peso_hl))
    else:
        st.caption("Desactivada — el GA no la considera.")

    st.subheader("Restricción de presión mínima")
    presion_activa = st.checkbox("Activar", value=cfg["restricciones"]["presion_minima"]["activo"], key="presion_activa")
    presion_valor = cfg["restricciones"]["presion_minima"]["valor"]
    peso_presion = cfg["restricciones"]["presion_minima"]["peso_penalizacion"]
    if presion_activa:
        col1, col2 = st.columns(2)
        presion_valor = col1.number_input("Presión mínima (m)", value=float(presion_valor))
        peso_presion = col2.number_input("Peso de penalización (presión)", value=float(peso_presion))
    else:
        st.caption("Desactivada — el GA no la considera.")

    st.subheader("Caudal extra de diseño")
    extra_activo = st.checkbox("Activar", value=cfg.get("extra_caudal", {}).get("activo", False), key="extra_activo")
    presupuesto_extra = cfg.get("extra_caudal", {}).get("presupuesto_lps", 0)
    if extra_activo:
        presupuesto_extra = st.number_input("Presupuesto (L/s)", value=float(presupuesto_extra))
    else:
        st.caption("Desactivado.")

    st.subheader("Parámetros del algoritmo genético")
    col1, col2, col3 = st.columns(3)
    poblacion = col1.number_input("Población", value=int(cfg["ga"]["poblacion"]), min_value=2, step=10)
    generaciones = col2.number_input("Generaciones", value=int(cfg["ga"]["generaciones"]), min_value=1, step=10)
    procesos = col3.number_input("Procesos (núcleos)", value=int(cfg["ga"].get("procesos", 1)), min_value=1)

    col1, col2, col3 = st.columns(3)
    prob_cruce = col1.slider("Prob. cruce", 0.0, 1.0, float(cfg["ga"]["prob_cruce"]))
    prob_mutacion = col2.slider("Prob. mutación", 0.0, 1.0, float(cfg["ga"]["prob_mutacion"]))
    tam_torneo = col3.number_input("Tamaño de torneo", value=int(cfg["ga"]["tam_torneo"]), min_value=2)

    col1, col2 = st.columns(2)
    semilla = col1.number_input("Semilla aleatoria", value=int(cfg["ga"].get("semilla") or 42))
    sembrar_actual = col2.checkbox("Sembrar población inicial con el diseño actual", value=cfg["ga"].get("sembrar_diseno_actual", True))

    if st.button("💾 Guardar parámetros", type="primary"):
        try:
            catalogo = [int(x.strip()) for x in catalogo_str.split(",") if x.strip()]
        except ValueError:
            st.error("El catálogo debe ser una lista de números separados por coma.")
            catalogo = cfg["catalogo_diametros"]

        st.session_state.config = {
            "red": cfg["red"],
            "extra_caudal": {"activo": extra_activo, "presupuesto_lps": presupuesto_extra},
            "restricciones": {
                "velocidad": {
                    "activo": velocidad_activa,
                    "vmin": vmin,
                    "vmax": vmax,
                    "peso_penalizacion": peso_v,
                    "excepciones_vmax": excepciones_vmax,
                },
                "perdida_unitaria": {"activo": perdida_activa, "hlmax": hlmax, "peso_penalizacion": peso_hl},
                "presion_minima": {"activo": presion_activa, "valor": presion_valor, "peso_penalizacion": peso_presion},
                "costo": cfg["restricciones"].get("costo", {"activo": False}),
            },
            "catalogo_diametros": catalogo,
            "ga": {
                "sembrar_diseno_actual": sembrar_actual,
                "poblacion": int(poblacion),
                "generaciones": int(generaciones),
                "prob_cruce": prob_cruce,
                "prob_mutacion": prob_mutacion,
                "tam_torneo": int(tam_torneo),
                "semilla": int(semilla),
                "procesos": int(procesos),
                "checkpoint_path": cfg["ga"].get("checkpoint_path", "checkpoint.pkl"),
                "checkpoint_cada": cfg["ga"].get("checkpoint_cada", 5),
            },
        }
        st.success("Parámetros guardados.")


# ------------------------------------------------------- Ejecutar/Resultados

else:
    st.header("Ejecutar / Resultados")

    if not st.session_state.inp_path:
        st.warning("Sube primero una red en la pestaña 'Red'.")
    else:
        modo = st.radio(
            "Modo", ["Búsqueda normal", "Búsqueda nocturna"], horizontal=True, label_visibility="collapsed"
        )

        # ---------------------------------------------------- Modo normal --
        if modo == "Búsqueda normal":
            if st.button("▶️ Correr optimización", type="primary"):
                with st.spinner("Optimizando... puede tardar desde segundos hasta varios minutos"):
                    config_corrida = dict(st.session_state.config)
                    config_corrida["red"] = {"inp_path": st.session_state.inp_path}

                    dir_tmp = tempfile.mkdtemp(prefix="epanet_opt_run_")
                    ruta_config = os.path.join(dir_tmp, "config.yaml")
                    ruta_json = os.path.join(dir_tmp, "resultado.json")
                    ruta_checkpoint = os.path.join(dir_tmp, "checkpoint.pkl")

                    with open(ruta_config, "w", encoding="utf-8") as f:
                        yaml.safe_dump(config_corrida, f, allow_unicode=True)

                    proceso = subprocess.run(
                        [
                            sys.executable,
                            os.path.join(DIR_PROYECTO, "main.py"),
                            "--config", ruta_config,
                            "--resultado-json", ruta_json,
                            "--checkpoint", ruta_checkpoint,
                        ],
                        capture_output=True,
                        text=True,
                        cwd=DIR_PROYECTO,
                    )
                    st.session_state.log = (proceso.stdout or "") + "\n" + (proceso.stderr or "")

                    if os.path.exists(ruta_json):
                        with open(ruta_json, "r", encoding="utf-8") as f:
                            st.session_state.resultado = json.load(f)
                    else:
                        st.session_state.resultado = None
                        st.error("La corrida no generó resultados. Revisa la bitácora abajo.")

            if st.session_state.log:
                with st.expander("Bitácora de la corrida"):
                    st.code(st.session_state.log)

            if st.session_state.resultado:
                st.divider()
                st.subheader("Resultado")
                mostrar_resultado_completo(st.session_state.resultado, st.session_state.config, key_sufijo="normal")

        # -------------------------------------------------- Modo nocturna --
        else:
            st.caption(
                "Corre la optimización muchas veces con semillas distintas, en segundo plano, y se "
                "queda con la mejor. Corre como proceso aparte — puedes cambiar de sección o cerrar "
                "el navegador, sigue trabajando; vuelve cuando quieras y pulsa 'Actualizar estado'."
            )

            salida_dir_nocturna = os.path.join(DIR_PROYECTO, "overnight_resultados")

            col1, col2 = st.columns(2)
            horas = col1.number_input("Horas máximo", value=8.0, min_value=0.1, step=0.5)
            n_semillas_input = col2.text_input("Máximo de semillas (opcional, vacío = sin límite)", value="")

            col_a, col_b, col_c = st.columns(3)

            if col_a.button("🌙 Iniciar en segundo plano", type="primary"):
                os.makedirs(salida_dir_nocturna, exist_ok=True)
                for nombre in ("estado.json", "bitacora_semillas.jsonl"):
                    ruta = os.path.join(salida_dir_nocturna, nombre)
                    if os.path.exists(ruta):
                        os.remove(ruta)

                config_corrida = dict(st.session_state.config)
                config_corrida["red"] = {"inp_path": st.session_state.inp_path}
                ruta_config = os.path.join(salida_dir_nocturna, "config_usada.yaml")
                with open(ruta_config, "w", encoding="utf-8") as f:
                    yaml.safe_dump(config_corrida, f, allow_unicode=True)

                ruta_parar = os.path.join(salida_dir_nocturna, "PARAR")
                if os.path.exists(ruta_parar):
                    os.remove(ruta_parar)

                comando = [
                    sys.executable, os.path.join(DIR_PROYECTO, "overnight_search.py"),
                    "--config", ruta_config,
                    "--horas", str(horas),
                    "--salida-dir", salida_dir_nocturna,
                    "--detener-si-existe", ruta_parar,
                ]
                if n_semillas_input.strip():
                    comando += ["--n-semillas", n_semillas_input.strip()]

                log_file = open(os.path.join(salida_dir_nocturna, "log.txt"), "w", encoding="utf-8")
                flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                subprocess.Popen(comando, cwd=DIR_PROYECTO, stdout=log_file, stderr=subprocess.STDOUT, creationflags=flags)
                st.success("Búsqueda iniciada en segundo plano. Puedes cambiar de sección o cerrar el navegador.")

            if col_b.button("⏹ Detener"):
                os.makedirs(salida_dir_nocturna, exist_ok=True)
                open(os.path.join(salida_dir_nocturna, "PARAR"), "w").close()
                st.info("Se pidió detener — se frenará después de terminar la semilla en curso.")

            if col_c.button("🔄 Actualizar estado"):
                st.rerun()

            ruta_estado = os.path.join(salida_dir_nocturna, "estado.json")
            if os.path.exists(ruta_estado):
                with open(ruta_estado, "r", encoding="utf-8") as f:
                    estado = json.load(f)

                st.divider()
                transcurrido_min = (estado["actualizado"] - estado["inicio"]) / 60
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Estado", "🟢 Corriendo" if estado["corriendo"] else "⚪ Detenida")
                c2.metric("Semillas probadas", estado["intentos"])
                c3.metric("Tiempo transcurrido", f"{transcurrido_min:.1f} min")
                c4.metric("Mejor fitness", f"{estado['mejor_fitness']:.4f}" if estado["mejor_fitness"] is not None else "—")

                ruta_bitacora = os.path.join(salida_dir_nocturna, "bitacora_semillas.jsonl")
                if os.path.exists(ruta_bitacora):
                    filas = []
                    with open(ruta_bitacora, "r", encoding="utf-8") as f:
                        for linea in f:
                            if linea.strip():
                                filas.append(json.loads(linea))
                    if filas:
                        df_semillas = pd.DataFrame(filas)
                        df_semillas["intento"] = range(1, len(df_semillas) + 1)

                        fig = go.Figure()
                        fig.add_trace(
                            go.Scatter(x=df_semillas["intento"], y=df_semillas["fitness_final"], mode="markers+lines", name="fitness por semilla")
                        )
                        idx_mejor = df_semillas["fitness_final"].idxmin()
                        fig.add_trace(
                            go.Scatter(
                                x=[df_semillas["intento"][idx_mejor]],
                                y=[df_semillas["fitness_final"][idx_mejor]],
                                mode="markers",
                                marker=dict(size=14, color="#2ecc71", symbol="star"),
                                name="mejor",
                            )
                        )
                        fig.update_layout(xaxis_title="Semilla # (orden de intento)", yaxis_title="Fitness final", height=350)
                        st.plotly_chart(fig, use_container_width=True)
                        st.dataframe(df_semillas[["semilla", "fitness_final"]], use_container_width=True, height=200)

                ruta_mejor_json = os.path.join(salida_dir_nocturna, "mejor.json")
                if os.path.exists(ruta_mejor_json):
                    st.subheader(f"Resultado de la mejor semilla ({estado.get('mejor_semilla', '—')})")
                    with open(ruta_mejor_json, "r", encoding="utf-8") as f:
                        mejor_resultado = json.load(f)
                    mostrar_resultado_completo(mejor_resultado, st.session_state.config, key_sufijo="nocturna")
            else:
                st.info("Todavía no se ha iniciado ninguna búsqueda nocturna en esta carpeta.")
