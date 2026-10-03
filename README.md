# Quant Bot

Bot de señales para acciones de EE. UU. (operado manualmente en eToro). Corre en
GitHub Actions y avisa por Telegram.

## Archivos

| Archivo | Para qué |
|---|---|
| `quant_core.py` | Estrategia: parámetros, indicadores, régimen, señales, tamaño de posición. Única fuente de verdad. |
| `bot_telegram.py` | Bot diario: APERTURA (8:45 NY) vigila posiciones; CIERRE (3:55 NY) busca compras. |
| `backtest.py` | Backtest día a día con las mismas reglas del bot, comparación con SPY y walk-forward. |
| `notebooks/ultimate_quant_v3.ipynb` | Laboratorio de Colab. Descarga el código de este repo (no tiene copia propia). |
| `posiciones.json` | Posiciones abiertas que vigila el bot. Formato simple `"PLD": 2` o detallado con `precio_entrada`, `stop_loss`, `take_profit`. |
| `datos/operaciones_reales.json` | Operaciones cerradas en eToro (`origen`: `bot` o `pre_sistema`). |

## Uso

- **Bot:** automático. Para probarlo: Actions → *Quant Bot Diario* → *Run workflow*.
- **Backtest:** Actions → *Backtest* → *Run workflow*. El resumen aparece en la página de la ejecución; los CSV se descargan como artefacto.
- **Colab:** abre `notebooks/ultimate_quant_v3.ipynb` en Colab (Archivo → Abrir notebook → GitHub → `Galactic20/Quant`).
- **Pruebas:** `python -m pytest -q`
