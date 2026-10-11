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

## Interfaz gráfica (entrega 1)

```powershell
pip install -e .[ui]
py -m forexplat ui --profile demo
```

Se abre en el navegador (`http://127.0.0.1:8501`), solo accesible desde tu propio PC. Deja la ventana de PowerShell
abierta mientras la uses; Ctrl+C la cierra. Con MetaTrader 5 abierto, tiene cuatro pestañas:

- **Inicio:** estado de MetaTrader 5 (cuenta, saldo), de Telegram y de la vigilancia. En el perfil real aparece un
  aviso rojo permanente. Pulsa «Actualizar estado» para consultar.
- **Señales:** «Revisar ahora» muestra la alerta (dirección, entrada, SL, TP, lote, motivos) o el estado de cada regla.
- **Backtest:** estrategia, par, temporalidad, velas, spread y saldo, con velas de MetaTrader 5 o de un CSV. Por
  defecto separa el 70 % más antiguo (ajuste) del 30 % más reciente (validación) y muestra ambos resultados por
  separado: solo cuenta lo que se sostiene en la validación. Avisa si hay menos de 30 operaciones y si llevas
  muchas pruebas sobre los mismos datos.
- **Vigilancia:** inicia y detiene `watch` como proceso aparte, que sigue vivo aunque cierres la pestaña (pero no si
  apagas el PC), con su registro en `logs/watch_<perfil>.log`. Desde la interfaz solo se inicia en demo.

Para el perfil real: `py -m forexplat ui --profile real --i-understand-real-account` (pide escribir `REAL`).
Los secretos (`.env.*`) nunca se muestran. La interfaz no envía órdenes ni cambia estrategias: la edición de
estrategias y perfil y el diario llegan en la entrega 2. La consola sigue funcionando igual.

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
  mt5_client.py    conector con MT5 (cuenta, símbolos, velas, precio, margen)
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
  safeio.py        escritura atómica y bloqueo de archivos compartidos
  ui/              interfaz gráfica (Streamlit): app.py dibuja; services.py y watchproc.py tienen la lógica
  data.py          velas en CSV
  cli.py           comandos `check`, `signals`, `backtest`, `telegram-setup`, `telegram-test`, `watch`, `review` y `ui`
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
