---
name: ukraine-territory-trend
description: Update and present the DeepState-derived time series of Ukrainian territory under Russian control, including geodesic area calculation, delta caching, monthly aggregation, and the mobile chart in this repository.
---

# Ukraine Territory Trend

Use `scripts/territory_chart.py` as the deterministic implementation.

- Preserve the full calculated history in `data/territory.csv`; do not trim it to the chart window.
- On an empty cache, bootstrap from a single archive of the upstream `data/` directory. Do not use the upstream unified gzip as the historical authority because it has sometimes contained only a rolling subset. Later runs fetch individual daily GeoJSON files and recheck the most recent seven days for retrospective corrections.
- Treat 2024-01-01 as the requested lower bound, while clearly reporting that the current source begins on 2024-07-08.
- Calculate polygon and multipolygon areas geodesically on WGS-84. Label the result as territory under Russian control according to DeepState, not as an independently verified measurement.
- Aggregate the chart by calendar month using the last available observation in each month and display the latest 12 months.
- Run `python -m unittest discover -s tests -v` after changes. Run the script with `--skip-fetch` when only the renderer needs testing against an existing cache.
