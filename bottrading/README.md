# Bot de trading en modo Paper (MEXC · Ollama local)

Proyecto final de *Deep Learning para Finanzas*. Un agente que **observa** velas diarias cerradas de MEXC, **pronostica** el rendimiento del siguiente día con tu modelo, **consulta un LLM local (Ollama)** para proponer una acción, **aplica controles de riesgo deterministas**, **simula** la ejecución (dinero virtual) y **registra** todo en SQLite. Una interfaz React + TypeScript muestra el estado y permite iniciar, pausar y detener.

> **Solo simulación.** Se consumen únicamente datos públicos de MEXC (no requieren clave) y las órdenes existen solo en el simulador propio. MEXC no tiene sandbox; **no hay ninguna ruta de órdenes reales** (hay una prueba automática que lo verifica). Finalidad académica, no es una recomendación de inversión.

## Arquitectura

```
React + TS (Vite)  →  FastAPI (X-API-Key)  →  Modelo + scaler (models/<SIMBOLO>/)
                                           →  Ollama local (propone JSON validado)
                                           →  Riesgo (reglas fijas en config/risk.yaml)
                                           →  Paper (saldo, costos, precisión, deslizamiento)
                                           →  SQLite (velas procesadas, decisiones, órdenes, patrimonio, eventos)
        ↑ velas cerradas y precio de MEXC (API pública, con reintentos y espera creciente)
```

Activos: BTCUSDT, ETHUSDT, SOLUSDT, ADAUSDT. El capital inicial (50,000 USDT) se reparte en 4 bolsas independientes de 12,500; hay además un límite de exposición total.

## Estructura

```
backend/app/     agent.py (ciclo) · mexc.py · features.py · model.py · llm.py · risk.py · paper.py · db.py · main.py (API) · backtest.py
backend/tests/   43 pruebas (los 9 escenarios de error, riesgo, simulador, API, alineación con el notebook)
frontend/        React + TypeScript + Vite
config/risk.yaml  límites de riesgo, costos, deslizamiento y reglas por par (el LLM no puede cambiarlos)
models/          <SIMBOLO>/{scaler.pkl, features.json, model.pkl | model.keras}
notebooks/       avance1_mexc.ipynb (datos, particiones, baselines)
scripts/         export_baseline.py · compare_strategies.py
```

## Puesta en marcha

### 1. Del notebook al bot (contrato del modelo)
1. Ejecuta `notebooks/avance1_mexc.ipynb` en Colab. Genera, por símbolo, `models/<SIMBOLO>/scaler.pkl` y `features.json`, y `data/processed/<SIMBOLO>_ventanas_20.csv`.
2. Copia esas carpetas a este repo (`models/` y `data/processed/`).
3. Genera el modelo de referencia (regresión lineal, ajustada solo con TR):
   ```bash
   pip install -r backend/requirements.txt
   python scripts/export_baseline.py
   ```
4. Cuando termines el MLP/LSTM del Avance 2 en Colab (T4), guarda `model.keras` en `models/<SIMBOLO>/` y en `features.json` pon `"model_type": "mlp"` (o `"lstm"`) y `"model_version"`. Instala `keras`/`tensorflow` (comentados en `requirements.txt`).
5. Ajusta `delta` de cada par en `config/risk.yaml` con el valor elegido **en VA** (hoy hay un valor de ejemplo, 0.001).

**Importante:** `features.json` define qué variables se calculan (`r_t-k`, `dist_sma20`, `vol20`). Si en el Avance 5 agregas indicadores, actualiza ese contrato y reentrena: el backend se niega a arrancar si el escalador y el contrato no coinciden.

### 2. Ollama local
```bash
ollama pull llama3.1:8b        # o el modelo que prefieras (OLLAMA_MODEL en .env)
ollama serve                   # escucha en 127.0.0.1:11434; no lo expongas a la red
```
Ollama no trae autenticación: por eso el acceso desde la UI pasa por este backend, que exige `X-API-Key` (incluido `/api/llm/chat`).

### 3. Backend
```bash
cp .env.example .env           # edita API_KEY (mín. 16 caracteres; genera una con secrets.token_urlsafe(32))
cd backend
uvicorn app.main:create_app --factory --port 8000
```
Documentación interactiva: `http://127.0.0.1:8000/docs`. Sin `API_KEY` válida el backend no arranca.

### 4. Interfaz
```bash
cd frontend && npm install && npm run dev      # http://localhost:5173
```
Escribe la API key en la pantalla de entrada (queda solo en memoria; nunca en variables `VITE_*`).

## Cómo opera
- **En vivo:** al pulsar *Iniciar*, cada `POLL_SECONDS` revisa si hay una **vela diaria nueva y cerrada** por símbolo (la vela del día en curso se descarta). Hay **una decisión por vela**; la frecuencia diaria del modelo no se acelera.
- **Ejecución:** la señal emitida al cierre de *t* se ejecuta al precio de mercado actual (≈ apertura de *t+1*) más el deslizamiento supuesto. En la repetición histórica se usa la apertura real de *t+1*.
- **Repetición (replay):** *Repetir últimos 20 días* corre el mismo ciclo sobre velas históricas, en un modo separado (`mode='replay'`) y marcado como tal; sirve para demostrar ≥20 ciclos sin esperar 20 días.
- **Detener ≠ liquidar:** *Detener* bloquea órdenes nuevas y deja la posición visible. *Liquidar posiciones* es una acción explícita y aparte.
- **Límites** (en `config/risk.yaml`): exposición total máxima, pérdida máxima por vela diaria en un símbolo (al alcanzarla se bloquean las órdenes hasta reanudar manualmente), antigüedad máxima del dato, tamaño mínimo y paso de cantidad por par.
- **Supuestos documentados:** costo 0.0 % (MEXC spot, según el protocolo) y deslizamiento de 5 pb por ejecución. `qty_step` y `min_notional` son valores de ejemplo: **verifícalos** en la documentación de MEXC.

## Los 9 errores exigidos y dónde se demuestran
Todos con mensaje útil, registro en la tabla `events` y sin operaciones indebidas (`cd backend && pytest -v`):

| # | Escenario | Prueba |
|---|---|---|
| 1 | API sin respuesta / límite (429) | `test_agent::test_01…`, `test_mexc` (reintentos 1 s, 2 s, luego pausa) |
| 2 | Dato atrasado | `test_agent::test_02…` |
| 3 | Ollama detenido | `test_agent::test_03…` (pausa) |
| 4 | JSON inválido del LLM | `test_agent::test_04…` (se rechaza; no se opera) |
| 5 | Clave incorrecta/ausente | `test_api::test_05…` (todas las rutas, 401; la clave no se registra) |
| 6 | Saldo insuficiente | `test_agent::test_06…` |
| 7 | Evento duplicado | `test_agent::test_07…` (clave única por símbolo+vela) |
| 8 | Reinicio del backend | `test_agent::test_08…` (recupera efectivo/posición; queda en pausa; sin duplicados) |
| 9 | Botón de detención | `test_agent::test_09…` |

## Comparación final (mismo periodo y costos)
```bash
cd backend
python ../scripts/compare_strategies.py --steps 30          # modelo sin LLM · agente con LLM · comprar y mantener
python ../scripts/compare_strategies.py --steps 30 --no-llm
```
Reporta rendimiento neto, drawdown máximo, Win Rate (no definido si no hay operaciones cerradas), operaciones, costos, latencia del LLM y acciones rechazadas. Las posiciones se liquidan al último cierre con costo de venta en las tres estrategias. Evalúa si el LLM aporta valor; no se espera que mejore.

## Seguridad
- Secretos solo en `.env` del servidor (ignorado por git); `.env.example` sin valores.
- Todas las rutas `/api/*` (salvo `/api/health`) exigen `X-API-Key` (comparación en tiempo constante). Cambiar configuración, iniciar, detener y el proxy del LLM están protegidos.
- El LLM recibe datos estructurados, sin claves ni herramientas de escritura; su salida se valida con esquema (acción, símbolo permitido, fecha de vela) y **siempre** pasa por el riesgo. Los datos externos se tratan como datos, no como instrucciones.
- Si usas una clave de MEXC solo para lectura, sin permisos de trading ni retiro (los datos públicos no la necesitan).

## Limitaciones conocidas (pendientes de tu validación)
- La integración **real** con MEXC y Ollama no se pudo probar desde el entorno donde se generó el código; las pruebas usan dobles. Haz una primera corrida real y revisa el formato de velas contra la documentación vigente de MEXC.
- Hasta que entrenes el MLP/LSTM, el modelo del bot es la **regresión lineal de referencia**. La carga de `model.keras` está implementada pero no probada con una red real.
- El LLM **no usa llamadas a herramientas (function calling)**: recibe un resumen estructurado y de solo lectura. Si tu rúbrica exige herramientas explícitas, es la ampliación natural.
- La interfaz compila con TypeScript estricto, pero no se revisó visualmente en un navegador.
- El replay y la comparación dependen de que MEXC entregue velas diarias completas; si hay un hueco, se detienen con un mensaje (no se rellena).

## Subir a GitHub
```bash
git init && git add . && git status        # verifica que NO aparezca .env ni *.db
git commit -m "Bot de trading Paper: backend, riesgo, simulador, UI y pruebas"
git branch -M main
git remote add origin <URL-de-tu-repositorio>
git push -u origin main
```
