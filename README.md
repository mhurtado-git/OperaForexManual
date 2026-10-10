# Plataforma de operación manual en Forex (MT5 + Claude)

Fase 1: conexión con MetaTrader 5, perfiles **demo** y **real**, y salvaguardas.
Todavía no genera alertas ni envía órdenes. Esas piezas llegan en las fases siguientes.

> Proyecto técnico, no es asesoría financiera. Operar en Forex implica riesgo de perder el capital.

## Requisitos (Windows)

- Windows 10 u 11 (o un VPS con Windows). El paquete `MetaTrader5` de Python solo funciona en Windows.
- Python 3.11 o 3.12 desde python.org (marca "Add python.exe to PATH" al instalar).
- Git y VS Code (recomendados).
- MetaTrader 5 instalado desde tu broker, con una **cuenta demo** creada.

## Puesta en marcha

En PowerShell, dentro de la carpeta del proyecto:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]

copy .env.demo.example .env.demo
notepad .env.demo
```

Completa en `.env.demo` el login, la contraseña y el servidor de tu cuenta **demo** (los ves en MT5, en
*Archivo → Abrir una cuenta* o en el correo de tu broker). Deja MetaTrader 5 abierto y luego:

```powershell
python -m forexplat check --profile demo
```

Si todo va bien verás la cuenta (marcada como demo), el spread del símbolo y las últimas velas cerradas.

Pruebas del proyecto (no necesitan MT5):

```powershell
pytest
```

## Perfiles demo y real

| | Demo | Real |
| --- | --- | --- |
| Parámetros | `config/demo.yaml` | `config/real.yaml` |
| Credenciales | `.env.demo` | `.env.real` |
| Comando | `python -m forexplat check --profile demo` | `python -m forexplat check --profile real --i-understand-real-account` |

Salvaguardas ya incluidas:

- El perfil real exige una bandera explícita y que escribas `REAL` para continuar.
- Al conectar, se lee el tipo de cuenta que informa MT5 y el programa se detiene si no coincide con el perfil
  (por ejemplo, perfil demo conectado a una cuenta real).
- Cada perfil lee solo su propio `.env.<perfil>`; las credenciales nunca se mezclan ni van al repositorio.
- Las contraseñas no aparecen al imprimir la configuración.

Los valores de riesgo de `real.yaml` son un ejemplo: defínelos tú antes de usar dinero real.

## Estructura

```
config/            demo.yaml, real.yaml (parámetros de cada perfil)
src/forexplat/
  config.py        carga y validación de perfiles
  safety.py        comprobación de tipo de cuenta
  mt5_client.py    conector con MT5 (cuenta, símbolos, velas)
  cli.py           comando `check`
tests/             pruebas de configuración y salvaguardas
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

1. **Conexión y datos (demo):** esta entrega.
2. **Señales y riesgo:** motor de señales y gestor de riesgo (SL, TP y lote), con backtest.
3. **Alertas:** Telegram con botones para confirmar o descartar; envío de la orden con SL y TP tras la confirmación.
4. **Capa Claude:** validación y explicación de cada setup con salida JSON.
5. **Prueba en demo** durante al menos un mes.
6. **Real con riesgo mínimo.**
