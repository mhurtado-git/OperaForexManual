# Plataforma de operación manual en Forex (MT5 + Claude)

Fase 1: conexión con MetaTrader 5, perfiles **demo** y **real**, y salvaguardas.
Fase 2: estrategias parametrizables, alertas de largo/corto con stop loss, take profit y lote, y backtest.
Fase 3: avisos por Telegram con botones para registrar si tomaste o descartaste cada señal.
No envía órdenes: **tú decides y ejecutas en MetaTrader 5**.

> Proyecto técnico, no es asesoría financiera. Operar en Forex implica riesgo de perder el capital.

## Requisitos (Windows)

- Windows 10 u 11 (o un VPS con Windows). El paquete `MetaTrader5` de Python solo funciona en Windows.
- Python 3.11 o superior desde python.org. En Windows se usa el lanzador `py` (el comando `python`
  suele ser un acceso directo de la Microsoft Store que no hace nada).
- Git y VS Code (recomendados).
- MetaTrader 5 instalado desde tu broker, con una **cuenta demo** creada.

## Puesta en marcha

En PowerShell, dentro de la carpeta del proyecto:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]

copy .env.demo.example .env.demo
notepad .env.demo
```

Completa en `.env.demo` el login, la contraseña y el servidor de tu cuenta **demo** (los ves en MT5, en
*Archivo → Abrir una cuenta* o en el correo de tu broker). Deja MetaTrader 5 abierto y luego:

```powershell
py -m forexplat check --profile demo
```

Si todo va bien verás la cuenta (marcada como demo), el spread del símbolo y las últimas velas cerradas.

Pruebas del proyecto (no necesitan MT5):

```powershell
pytest
```

## Señales, riesgo y backtest (fase 2)

```powershell
py -m forexplat signals  --profile demo                       # alerta sobre la última vela cerrada
py -m forexplat backtest --profile demo                       # prueba la estrategia con 5000 velas de MT5
py -m forexplat backtest --profile demo --strategy fib_pullback --bars 20000
py -m forexplat backtest --profile demo --csv velas.csv       # sin MT5, con velas de un CSV
```

`signals` imprime, si hay señal, la dirección (largo o corto), la entrada, el stop loss, el take profit, la
relación beneficio/riesgo, el **lote sugerido** y los motivos. Si no hay señal, muestra el estado de cada regla.
Una señal que incumple el spread máximo, la relación beneficio/riesgo mínima, la distancia mínima del broker o el
margen libre se descarta y se explica por qué. Ejecútalo cuando cierre una vela (en H1, al inicio de cada hora).

### Todo es parametrizable: `config/strategies.yaml`

Cada estrategia combina **un disparador** (un evento: cruce, ruptura, giro del RSI) con **reglas de confirmación**
(por defecto, todas deben cumplirse; `min_confirmations` lo relaja) y define cómo se calculan el stop loss y el
take profit. Las tres estrategias incluidas usan tres indicadores cada una:

| Estrategia | Indicadores | Stop loss | Take profit |
| --- | --- | --- | --- |
| `ema_rsi_atr` (inicial) | cruce EMA 20/50, RSI 14, filtro ATR | 1,5 x ATR(14) | 1:2 |
| `fib_pullback` | tendencia EMA 50/200, retroceso Fibonacci, giro del RSI | swing de 20 velas + 0,5 ATR | 1:2 |
| `bb_breakout_macd` | ruptura Bollinger, histograma MACD, estocástico | 2 x ATR(14) | 1:2 |

Reglas disponibles (parámetros y valores por defecto, en el encabezado del propio archivo): `ma_cross`, `rsi`,
`macd`, `bollinger`, `stochastic`, `fibonacci` y `atr_filter`. Stop loss: `atr`, `fixed_pips` o `swing`, con
límites `min_pips`/`max_pips`. Take profit: `rr`, `fixed_pips` o `atr`. Una clave mal escrita o un valor fuera de
rango detiene el programa con un mensaje claro, en vez de ignorarse.

Los perfiles eligen qué estrategias usan (`strategies:`) y su riesgo (`risk:`): en demo, 1 % por operación;
el lote se calcula con el valor del tick del símbolo y siempre se redondea **hacia abajo**. Si ni el lote mínimo cabe
en el riesgo permitido, la señal se descarta.

### Cómo se simula el backtest (conservador)

- La señal nace al cierre de una vela y la entrada es en la **apertura de la siguiente**.
- Las velas de MT5 son de precio bid: las compras entran en el ask (apertura + spread) y los stops de las ventas
  se tocan en el ask.
- Si una vela toca a la vez el stop loss y el take profit, se asume que tocó **primero el stop loss**.
- Los huecos de precio se respetan (la salida puede ser peor que -1 R). Una operación abierta a la vez.
- Los resultados se miden en R (1 R = distancia al stop loss): tasa de aciertos, expectativa, factor de
  beneficio, drawdown y mayor racha perdedora. Se guardan en `reports/` (no se versiona).

Un buen backtest no garantiza resultados futuros, y ajustar parámetros hasta que el histórico se vea bien suele
fallar en vivo. Con menos de 30 operaciones el resultado no es fiable. Valida siempre en demo.

### Limitaciones conocidas

- `max_daily_loss` está en la configuración pero **todavía no se aplica**: llegará con el envío de órdenes.
- Las llamadas nuevas a MT5 (precio actual y margen requerido) se probaron solo con un cliente simulado;
  confírmalas en tu cuenta demo con `signals`.

## Alertas por Telegram (fase 3)

1. En Telegram, con `@BotFather`, envía `/newbot` y copia el token. **No lo compartas ni lo subas a Git.**
2. Agrega a `.env.demo` la línea `TELEGRAM_BOT_TOKEN=...` (hay un ejemplo en `.env.demo.example`).
3. Abre tu bot en Telegram y envíale `/start`.
4. `py -m forexplat telegram-setup --profile demo` muestra tu `chat_id`; agrégalo a `.env.demo` como `TELEGRAM_CHAT_ID=...`.
5. `py -m forexplat telegram-test --profile demo` envía un mensaje de prueba.
6. `py -m forexplat watch --profile demo` deja el programa vigilando (Ctrl+C para detener). Cada revisión evalúa la
   última vela cerrada y, si hay una señal nueva que pasa el gestor de riesgo, te envía la alerta con entrada, SL, TP
   y lote. Una misma vela no se avisa dos veces, ni siquiera tras reiniciar (`state/`).

Los botones **La tomé** y **Descartar** solo registran tu decisión en `journal/decisiones_<perfil>.csv`; sirven para
comparar después lo que hiciste con lo que dijo la estrategia. **No se envía ninguna orden a MetaTrader 5**: la
ejecutas tú. Solo responde a tu `chat_id`; las pulsaciones de otros usuarios se ignoran.

Para que `watch` funcione, MetaTrader 5 debe estar abierto y el PC encendido. Si se cierra el programa, no hay avisos.
`py -m forexplat watch --once` hace una sola revisión (útil para probar).

### Revisión de resultados: `review`

```powershell
py -m forexplat review --profile demo
```

Lista las alertas enviadas, qué decidiste (tomada, descartada o sin decisión) y cómo terminó cada una con las
velas posteriores (TP, SL o abierta), con el resultado en R y un resumen por tipo de decisión. Sirve para ver si
descartar ayudó o estorbó y si el demo se parece al backtest (+0,1 a +0,2 R por operación). El resultado es
**teórico**: usa los niveles del aviso, no las órdenes reales de tu cuenta, y con menos de 30 operaciones cerradas
no permite juzgar la estrategia.

## Interfaz gráfica (entregas 1 y 2)

```powershell
pip install -e .[ui]
py -m forexplat ui --profile demo
```

Se abre en el navegador (`http://127.0.0.1:8501`), solo accesible desde tu propio PC. Deja la ventana de PowerShell
abierta mientras la uses; Ctrl+C la cierra. Con MetaTrader 5 abierto, tiene siete pestañas:

- **Inicio:** estado de MetaTrader 5 (cuenta, saldo), de Telegram y de la vigilancia. En el perfil real aparece un
  aviso rojo permanente. Pulsa «Actualizar estado» para consultar.
- **Señales:** «Revisar ahora» muestra la alerta (dirección, entrada, SL, TP, lote, motivos) o el estado de cada regla.
- **Backtest:** estrategia, par, temporalidad, velas, spread y saldo, con velas de MetaTrader 5 o de un CSV. Por
  defecto separa el 70 % más antiguo (ajuste) del 30 % más reciente (validación) y muestra ambos resultados por
  separado: solo cuenta lo que se sostiene en la validación. Avisa si hay menos de 30 operaciones y si llevas
  muchas pruebas sobre los mismos datos.
- **Vigilancia:** inicia y detiene `watch` como proceso aparte, que sigue vivo aunque cierres la pestaña (pero no si
  apagas el PC), con su registro en `logs/watch_<perfil>.log`. Desde la interfaz solo se inicia en demo.

- **Diario:** tabla de alertas con tu decisión (tomada / descartada), una nota libre, el resultado teórico
  («Calcular resultados» usa velas de MetaTrader 5) y el resumen por decisión. «Buscar en el historial de
  MetaTrader 5» rellena precio, lote, resultado y ticket de la operación que abriste tras la alerta (mismo par y
  dirección, hasta 7 días después); revísalos antes de guardar. Es una búsqueda aproximada: si no acierta, escribe
  los datos a mano. La decisión se guarda en `journal/decisiones_<perfil>.csv`; si ya la tomaste en la
  interfaz, el botón de Telegram avisa de que ya está registrada (y al revés).
- **Estrategias:** formularios para indicadores, periodos, niveles, stop loss, take profit y confirmaciones
  mínimas, validados con las mismas reglas que la consola. Antes de guardar se copia la versión anterior en
  `config/backups/`. Se puede duplicar una estrategia. Si la usa el perfil real hay que escribir `REAL`.
  Al guardar se conserva el encabezado de `strategies.yaml`, pero los comentarios internos de cada estrategia se
  sustituyen por su descripción.
- **Perfil y riesgo:** pares, temporalidad, riesgo por operación, spread máximo, relación mínima beneficio/riesgo,
  estrategias activas y backtest. Se rechaza una combinación que descartaría todas las señales de una estrategia.
  El perfil real también exige escribir `REAL`.

Los cambios de estrategia o perfil no afectan a una vigilancia ya iniciada: deténla y vuelve a iniciarla.

Para el perfil real: `py -m forexplat ui --profile real --i-understand-real-account` (pide escribir `REAL`).
Los secretos (`.env.*`) nunca se muestran. La interfaz no envía órdenes a MetaTrader 5 (solo lee cuenta, velas y
historial). La consola sigue funcionando igual.

## Filtro de sesión (Nueva York) y velas M5

MetaTrader 5 entrega las velas en la hora del **servidor** del broker, no en UTC. Para filtrar por sesión hay
que decirle al programa cómo convertirla. Es un paso obligatorio: sin él, `session_filter` se niega a funcionar
(en vez de filtrar a la hora equivocada sin avisar).

1. Con MT5 abierto: `py -m forexplat check-time`. Mide el desfase con el último precio (solo fiable con el
   mercado abierto) y lo comprueba con el **cierre semanal**: el mercado cierra cada viernes a las 17:00 de
   Nueva York todo el año, así que, si la conversión es correcta, la última vela de cada semana termina a esa
   hora. Usa ~1 año de velas H1 para cubrir los dos cambios de horario de EE. UU. y sugiere la línea a copiar.
2. Agrégala a `config/demo.yaml` (y a `real.yaml`). Hay tres formas, exactamente una:

   ```yaml
   broker_time: {ny_offset_hours: 7}      # servidor = hora de Nueva York + 7 h (GMT+2 invierno / GMT+3 verano)
   # broker_time: {utc_offset_hours: 3}   # servidor fijo en UTC+3
   # broker_time: {timezone: Europe/Athens}  # servidor que sigue una zona con su horario de verano
   ```
3. Vuelve a ejecutar `check-time`: debe decir «bien configurada». En Windows, si falla una zona horaria:
   `py -m pip install tzdata` (ya es dependencia al reinstalar con `pip install -e .`).

Con `broker_time`, las velas descargadas (y las que guarda `backtest --save-candles` o `optimize`) quedan en
**UTC real**. Los CSV guardados antes de configurarlo están en hora del servidor: vuelve a descargarlos.

La regla `session_filter` (en `strategies.yaml`, también editable en la pestaña Estrategias) es una
**compuerta**: se exige siempre, aunque `min_confirmations` sea menor, y no cuenta como confirmación.

```yaml
- {type: session_filter, name: sesion_ny, timezone: America/New_York, start: "08:00", end: "17:00",
   days: [0, 1, 2, 3, 4], no_new_minutes: 30}
```

- La hora que cuenta es la de la **entrada** (el cierre de la vela de la señal), en la zona indicada y con su
  cambio de horario de verano. Con los valores de arriba, entradas desde las 08:00 hasta antes de las 16:30.
- Las operaciones abiertas siguen hasta su SL/TP aunque termine la sesión.
- En Bogotá la sesión de 08:00-17:00 de Nueva York son 07:00-16:00 mientras EE. UU. tiene horario de verano
  y 08:00-17:00 el resto del año.
- Una estrategia puede limitarse a ciertos pares con `symbols: [EURUSD]`; el resto de pares la ignoran.

### Buscar estrategias M5: `optimize`

```powershell
py -m forexplat optimize --symbol EURUSD --symbol GBPUSD --symbol USDJPY      # solo informe
py -m forexplat optimize --write                                               # además guarda las ganadoras
py -m forexplat optimize --use-saved --symbol EURUSD                           # reutiliza data/velas_<par>_M5.csv
```

Descarga las velas M5 de cada par (hasta `--bars`, por defecto 100000; MT5 puede dar menos, el informe dice
cuántos días son), mide el spread (más 0.3 pips de holgura) y descarta los pares donde supera el 15 % del
ATR medio. Prueba tres familias (ruptura de Bollinger + MACD, giro del RSI a favor de la tendencia, cruce de
EMAs + RSI), cada una con una rejilla pequeña de parámetros y las cuatro variantes de sesión (sin filtro,
08:00-17:00, 08:00-12:00 y 09:30-16:00 de Nueva York). Tarda unos minutos por par.

Cómo evita engañarse:

- Cada combinación se simula una vez; el 70 % antiguo es el **ajuste** y el 30 % reciente la **validación**.
- La elección se hace solo con el ajuste y premia las zonas estables (la nota promedia cada combinación con
  sus vecinas de un paso). La validación solo confirma o rechaza a las finalistas (una por par y familia).
- Criterios fijos: operaciones mínimas (100 en el ajuste, 30 en la validación), expectativa mayor que +0.10 R,
  factor de beneficio mayor que 1.2, positiva en 2 de 3 ventanas consecutivas de la validación, positiva con el
  spread multiplicado por 1.5, estable ante cambios de un paso y **estadísticamente significativa** (t mínimo con
  corrección de Bonferroni por el número de finalistas). Esta última existe porque, con ruido puro, sin ella
  alrededor de una de cada seis finalistas cumplía el resto de criterios por pura suerte.
- Cuenta todas las combinaciones probadas y lo escribe en el informe.

Entrega `reports/informe_m5.md` (con señales por hora de Bogotá, resultado con y sin filtro de sesión y
riesgos) y `reports/optimize_m5_finalistas.csv`. Si ninguna candidata cumple, lo dice y no escribe nada.
Con `--write` guarda las ganadoras (con copia de seguridad) en `strategies.yaml` y muestra qué poner en
`demo.yaml` (`timeframe: M5`, los pares y los nombres de las estrategias). Ni `optimize` ni nada de esto envía
órdenes. Los resultados históricos no garantizan resultados futuros.

## Perfiles demo y real

| | Demo | Real |
| --- | --- | --- |
| Parámetros | `config/demo.yaml` | `config/real.yaml` |
| Credenciales | `.env.demo` | `.env.real` |
| Comando | `py -m forexplat check --profile demo` | `py -m forexplat check --profile real --i-understand-real-account` |

Salvaguardas ya incluidas:

- El perfil real exige una bandera explícita y que escribas `REAL` para continuar.
- Al conectar, se lee el tipo de cuenta que informa MT5 y el programa se detiene si no coincide con el perfil
  (por ejemplo, perfil demo conectado a una cuenta real).
- Cada perfil lee solo su propio `.env.<perfil>`; las credenciales nunca se mezclan ni van al repositorio.
- Las contraseñas no aparecen al imprimir la configuración.

Los valores de riesgo de `real.yaml` son un ejemplo: defínelos tú antes de usar dinero real.

## Estructura

```
config/            demo.yaml, real.yaml (perfiles) y strategies.yaml (estrategias)
src/forexplat/
  config.py        carga y validación de perfiles
  safety.py        comprobación de tipo de cuenta
  mt5_client.py    conector con MT5 (cuenta, símbolos, velas, precio, margen, historial)
  indicators.py    SMA, EMA, RSI, ATR, MACD, Bollinger, estocástico, Fibonacci
  rules.py         reglas combinables (cruce, RSI, MACD, ...)
  strategy.py      disparador + confirmaciones desde strategies.yaml
  risk.py          stop loss, take profit y tamaño del lote
  backtest.py      simulador y métricas
  scanner.py       evalúa la última vela y arma el plan de operación
  alerts.py        texto de las alertas y del informe
  telegram.py      cliente de la API de Telegram
  watch.py         vigilancia, avisos sin repetir y diario de decisiones
  review.py        revisión de las alertas enviadas
  timeutil.py      hora del servidor del broker y zonas horarias
  timecheck.py     comando `check-time`
  optimize.py      búsqueda de estrategias con validación fuera de muestra
  journal.py       diario de decisiones (CSV compartido, escritura atómica)
  history.py       empareja una alerta con la operación real del historial de MT5
  safeio.py        escritura atómica y bloqueo de archivos compartidos
  ui/              interfaz gráfica (Streamlit): app.py dibuja; services.py, configio.py, forms.py y watchproc.py tienen la lógica
  data.py          velas en CSV
  cli.py           comandos `check`, `signals`, `backtest`, `telegram-setup`, `telegram-test`, `watch`, `review`, `check-time`, `optimize` y `ui`
tests/             pruebas (no necesitan MT5)
```

## Trabajo con Git

El proyecto ya es un repositorio con la rama `main` y un primer commit. Identifícate una sola vez:

```powershell
git config user.name "Tu nombre"
git config user.email "tu@correo.com"
```

Para conectarlo a GitHub (u otro servidor), crea allí un repositorio **privado y vacío** y luego:

```powershell
git remote add origin https://github.com/TU-USUARIO/forex-platform.git
git push -u origin main
```

Reglas del flujo:

- `main` siempre funciona y pasa las pruebas. El trabajo va en ramas: `feat/senales`, `fix/spread`, `docs/readme`.
- Una rama por fase o tarea; se fusiona a `main` solo con `pytest` en verde.
- Mensajes de commit al estilo Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Cada fase cerrada se marca con una etiqueta: `git tag -a fase-2 -m "Señales y riesgo"`.
- Las credenciales (`.env.demo`, `.env.real`) nunca se versionan: están en `.gitignore` y el hook
  `.githooks/pre-commit` bloquea cualquier commit que las incluya.
- Si una contraseña llegó a un commit, cámbiala en el broker: borrar el commit no basta.

Si clonas el repositorio en otra máquina, activa el hook una vez: `git config core.hooksPath .githooks`.

## Fases siguientes

1. **Conexión y datos (demo):** hecha.
2. **Señales y riesgo:** motor de señales y gestor de riesgo (SL, TP y lote), con backtest: esta entrega.
3. **Alertas:** Telegram con botones para registrar tu decisión (hecha). Envío de la orden con SL y TP tras la confirmación: pendiente.
4. **Capa Claude:** validación y explicación de cada setup con salida JSON.
5. **Prueba en demo** durante al menos un mes.
6. **Real con riesgo mínimo.**
