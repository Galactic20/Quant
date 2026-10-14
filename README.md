# Quant Bot

Bot de inversión (operado manualmente en eToro). Corre en GitHub
Actions y avisa por Telegram.

**Estrategia actual (v3.0):** cartera modelo **50% SPY + 50% estrategia B**
(momentum mensual entre SPY, QQQ, EFA, GLD e IEF), con aportes cuando los haya. El
último día hábil de cada mes el bot envía las órdenes de rebalanceo; los
viernes, un resumen frente a SPY. El escáner de swing trading anterior sigue
disponible con `ESTRATEGIA_BOT=quant`.

**Filtro de protección (P1):** si incluso el mejor de los 5 ETFs tiene
momentum negativo, la mitad B va a **SGOV** (letras del Tesoro de 0-3 meses).
En el backtest 2005-2026 subió el CAGR de 11.7% a 12.0% y redujo la pérdida de
2022 de -22.3% a -17.1%; actúa solo en ~3-6% de los meses.

**Estado actual:** la cartera ETF se inicia con **$316.15** (efectivo real en
eToro, `cartera.CONFIG["CAPITAL_INICIAL"]`), sin aporte mensual fijo. Cuando
deposites más dinero: Actions → *Bot de Inversión* → Run workflow, modo
`cierre` y el monto en **aporte**; el bot lo registra y envía las órdenes
para invertirlo. Para **retirar**, igual pero con el monto en **retiro**: el
bot indica qué vender manteniendo el 50/50 y descuenta el retiro sin contarlo
como pérdida. Registra cada depósito o retiro una sola vez.

**Satélite fundamental (solo virtual, aprendizaje):** cartera virtual aparte ($1,000, 5 acciones de EE. UU.
y de otros mercados vía ADR) elegida con un filtro de calidad + valoración +
crecimiento. Cada compra trae stop loss y take profit según la volatilidad. Revisión trimestral,
reporte mensual del filtro y resumen semanal frente a SPY. Se desactiva con
`SATELITE=0`.

## Archivos

| Archivo | Para qué |
|---|---|
| `quant_core.py` | Estrategia Quant anterior (swing): parámetros, indicadores, señales. |
| `bot_telegram.py` | Bot diario. Modo cartera: actúa en el CIERRE (3:55 NY). |
| `cartera.py` | Cartera modelo 50% SPY + 50% B: aportes, rebalanceo y mensajes. Estado en `datos/cartera_modelo.json`. |
| `estrategias.py` | Backtest de SPY, A (tendencia), B (momentum) y la mezcla 50/50. |
| `calendario.py` | Feriados y días hábiles de NYSE. |
| `fundamental.py` | Filtro fundamental (datos de yfinance): filtros mínimos y puntaje. `python fundamental.py` muestra el ranking actual. |
| `backtest_fundamental.py` | Backtest del satélite con datos históricos de SimFin (punto en el tiempo). Actions → Backtest → estudio `fundamental_historico`; requiere el secreto `SIMFIN_API_KEY`. |
| `satelite.py` | Cartera satélite: compras iniciales, revisión trimestral, reportes. Estado en `datos/satelite_modelo.json`. |
| `backtest.py` | Backtest día a día con las mismas reglas del bot, comparación con SPY y walk-forward. |
| `notebooks/ultimate_quant_v3.ipynb` | Laboratorio de Colab. Descarga el código de este repo (no tiene copia propia). |
| `posiciones.json` | Posiciones abiertas que vigila el bot. Formato simple `"PLD": 2` o detallado con `precio_entrada`, `stop_loss`, `take_profit`. |
| `datos/operaciones_reales.json` | Operaciones cerradas en eToro (`origen`: `bot` o `pre_sistema`). |

## Uso

- **Bot:** automático. Para probarlo: Actions → *Bot de Inversión - Cartera ETF + Satélite* → *Run workflow*.
- **Backtest:** Actions → *Backtest* → *Run workflow*. El resumen aparece en la página de la ejecución; los CSV se descargan como artefacto.
- **Colab:** abre `notebooks/ultimate_quant_v3.ipynb` en Colab (Archivo → Abrir notebook → GitHub → `Galactic20/Quant`).
- **Pruebas:** `python -m pytest -q`
