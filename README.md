# 💧 Optimizador de Diámetros EPANET — GALINDO

Optimizador de diámetros de tuberías para redes de distribución de agua. En vez
de ajustar tubería por tubería a mano (o con un script greedy que se estanca en
óptimos locales porque no ve el acoplamiento hidráulico de la red completa),
este proyecto trata el diseño como un **problema de optimización combinatoria
sobre toda la red**: un algoritmo genético (GA) propone combinaciones completas
de diámetros, cada una se evalúa con una simulación EPANET real (vía
[`wntr`](https://github.com/USEPA/WNTR)), y el GA evoluciona la población hacia
diseños que cumplen las restricciones de velocidad y pérdida de carga.

Incluye una app web para cargar tu red, ajustar parámetros, correr la
optimización, visualizar el resultado sobre el mapa de la red y exportar a
`.inp`/`.xlsx`.

## Funcionalidades

- **Motor de optimización**: algoritmo genético (`deap`) sobre un catálogo de
  diámetros comerciales, con simulación hidráulica real en cada evaluación
  (no un modelo aproximado).
- **Restricciones configurables** (activables/desactivables y con peso
  propio): velocidad mínima/máxima (con excepciones por tubería, ej. una línea
  de conducción), pérdida de carga unitaria máxima, presión mínima.
- **Paralelización** con `multiprocessing` sobre varios núcleos.
- **Checkpoint/resume**: pausa y retoma corridas largas sin perder progreso.
- **"Warm start"**: siembra la población inicial con el diseño actual en vez
  de arrancar 100% al azar — clave para converger en redes grandes (cientos de
  tuberías).
- **Búsqueda AFK (multi-semilla)**: corre la optimización con muchas semillas
  distintas sin supervisión, en segundo plano (pensado para dejar corriendo
  toda la noche o mientras haces otra cosa) y se queda con la mejor solución
  encontrada.
- **App web**: subir/exportar `.inp`, panel de parámetros, mapa de la red
  interactivo (tooltips por tubería, nodo y tanque), curva de convergencia, y
  reporte `.xlsx`.

## Instalación

Requiere Python 3.10+.

```bash
git clone <url-de-este-repo>
cd epanet_optimizer
pip install -r requirements.txt
```

## Cómo entrar

```bash
cd epanet_optimizer
python server.py
```

La terminal va a mostrar algo como `Running on http://127.0.0.1:5000` —
mientras esa terminal siga abierta, abre esa dirección en tu navegador:

**http://localhost:5000**

Para apagarla, `Ctrl+C` en esa misma terminal.

## Cómo se usa la app

La app tiene tres secciones en la barra lateral:

1. **Red** — sube tu archivo `.inp` (arrastrándolo o haciendo clic). La
   codificación (utf-8 o cp1252, típico de exportaciones de EPANET en
   Windows) se detecta sola. Al cargar muestra tuberías, nodos, unidades y
   fórmula de pérdida de la red.
2. **Parámetros** — catálogo de diámetros comerciales, y cada restricción
   (velocidad, pérdida unitaria, presión mínima, caudal extra) con su propio
   interruptor: al activarla se despliegan sus valores (mínimos/máximos,
   peso de penalización); al desactivarla, el GA ni la evalúa. La excepción
   de velocidad máxima por tubería (para una línea de conducción, por
   ejemplo) se agrega/quita con el botón "+ Agregar excepción". Al final,
   los parámetros del algoritmo genético (población, generaciones, semilla,
   núcleos, etc.). "Guardar parámetros" aplica los cambios para la próxima
   corrida.
3. **Ejecutar / Resultados** — elige entre:
   - **Búsqueda normal**: una corrida con la semilla configurada. Al
     terminar muestra fitness, el mapa de la red (pasa el mouse sobre una
     tubería, un nodo o el tanque para ver sus datos), la curva de
     convergencia, la tabla de tuberías (con su propio interruptor para
     mostrarla/ocultarla) y los botones de descarga.
   - **Búsqueda AFK**: define horas máximo (y opcionalmente un tope de
     semillas), dale "Iniciar en segundo plano" — corre como proceso aparte,
     puedes cerrar el navegador y sigue trabajando. Arranca sin datos hasta
     que corras al menos una: "Actualizar estado" muestra cuántas semillas
     lleva, el ranking de fitness por semilla, y — apenas encuentra una
     solución — el resultado completo (mapa, tabla, descargas) de la mejor
     semilla hasta el momento.

## Tu propia red

El repo trae solo `networks/Net1.inp` (una red genérica de 12 tuberías,
incluida con `wntr`, para probar que todo funciona — por eso en "Red" verás
unidades GPM y fórmula H-W por defecto, son las de esa red de ejemplo). El
`.gitignore` excluye cualquier otra cosa en `networks/` a propósito — así
nadie sube por accidente los datos de una red real/de cliente a un repo
público. Por la misma razón, `overnight_resultados/` (lo que genera la
búsqueda AFK) tampoco se versiona — cada quien empieza con esa sección vacía
hasta que corre su propia búsqueda.

Para usar tu propia red: súbela desde la sección "Red" de la app (queda
cargada para esa sesión), o colócala en `networks/` y actualiza
`red.inp_path` en `config.yaml` para usarla también por CLI. Revisa también
`catalogo_diametros` y las `excepciones_vmax` (id de tubería) en
"Parámetros" — vienen pensadas para la red de ejemplo, no para la tuya.

## Uso por línea de comandos

```bash
python main.py --config config.yaml
```

Opciones útiles: `--inp <ruta>` (red a optimizar), `--procesos N` (núcleos a
usar), `--checkpoint <ruta>` (para pausar/retomar), `--resultado-json <ruta>`
(escribe un resumen del resultado en JSON).

### Búsqueda AFK (multi-semilla) por CLI

```bash
python overnight_search.py --config config.yaml --horas 8
```

Prueba semillas al azar una tras otra; cada vez que una mejora el resultado
anterior, guarda de inmediato `overnight_resultados/mejor.inp`,
`mejor.xlsx` y `mejor.json`. Se puede interrumpir en cualquier momento
(`Ctrl+C`) sin perder lo ya encontrado.

## Estructura del proyecto

```
epanet_optimizer/
├── server.py                # App web (Flask) — python server.py
├── templates/
│   └── index.html           # Página de la app
├── static/
│   ├── style.css             # Estilos
│   └── app.js                 # Interactividad (fetch a la API)
├── main.py                   # CLI del motor de optimización
├── overnight_search.py       # Búsqueda AFK (multi-semilla) desatendida
├── config.yaml                # Parámetros: red, restricciones, catálogo, GA
├── core/
│   ├── network.py            # Cargar red, aplicar diámetros, simular
│   ├── constraints.py         # Restricciones activables/parametrizables
│   ├── evaluator.py           # Función de fitness
│   ├── parallel.py            # Workers para multiprocessing
│   └── io_utils.py            # Carga robusta de .inp (utf-8/cp1252)
├── ga/
│   └── optimizer.py           # Ciclo del algoritmo genético (deap)
├── io_epanet/
│   ├── inp_writer.py          # Exportar .inp optimizado
│   └── excel_writer.py        # Exportar reporte .xlsx
└── networks/                   # Redes .inp de ejemplo/trabajo
```

## Configuración

Todo se controla desde `config.yaml` (o desde la sección "Parámetros" de la
app, que lo genera por corrida):

```yaml
red:
  inp_path: networks/mi_red.inp

restricciones:
  velocidad:
    activo: true
    vmin: 0.6
    vmax: 3.0
    peso_penalizacion: 10
    excepciones_vmax:
      "161": 5.0        # ej. línea de conducción con límite propio
  perdida_unitaria:
    activo: true
    hlmax: 60
    peso_penalizacion: 5
  presion_minima:
    activo: false
    valor: 15
    peso_penalizacion: 15

catalogo_diametros: [50, 60, 75, 100, 125, 150, 175, 200, 225, 250, 300, 350, 400]

ga:
  poblacion: 150
  generaciones: 300
  prob_cruce: 0.7
  prob_mutacion: 0.2
  tam_torneo: 3
  semilla: 42
  procesos: 14
  sembrar_diseno_actual: true
```

Cada restricción se puede activar/desactivar y pesar independientemente — si
una no importa para una corrida, se apaga y el GA ni la considera.

## Cómo funciona (resumen)

1. Se carga la red `.inp` con `wntr`.
2. El GA representa cada diseño candidato como un vector de índices al
   catálogo de diámetros (un índice por tubería).
3. Cada candidato se evalúa aplicando esos diámetros a una copia de la red y
   corriendo una simulación hidráulica EPANET real; el fitness es la suma de
   penalizaciones (proporcionales a la magnitud de la violación, no binarias)
   de las restricciones activas.
4. Selección por torneo + cruce de dos puntos + mutación uniforme de baja
   probabilidad + elitismo, generación tras generación.
5. El mejor diseño encontrado se exporta a `.inp` (compatible con EPANET) y a
   un reporte `.xlsx`.

## Licencia

MIT — ver [LICENSE](LICENSE).
