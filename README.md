# Ukraine territory trend

Mobile-friendly monthly statistics of Ukrainian territory under Russian control according to DeepState-derived geometry.

## What it does

- bootstraps all available history from one archive request, starting at the requested lower bound `2024-01-01` (the source currently begins on `2024-07-08`);
- stores the compact calculated series permanently in `data/territory.csv`;
- downloads only new daily GeoJSON files after bootstrap and rechecks the latest seven days;
- calculates geodesic WGS-84 area in square kilometres;
- renders an interactive, dependency-free `index.html` containing the latest 12 monthly observations.

The monthly value is the **last available daily observation in that calendar month**, not an average.

## Run locally

```bash
python -m pip install -r requirements.txt
python scripts/territory_chart.py
python -m unittest discover -s tests -v
```

## Data source and interpretation

Geometry comes from [`cyterat/deepstate-map-data`](https://github.com/cyterat/deepstate-map-data), which mirrors DeepState's occupied-territory multipolygon. The figures are derived estimates and should be labelled “territory under Russian control according to DeepState.”

The repository requests history from 2024-01-01, but the current mirror has no observations before 2024-07-08. Those dates are not interpolated.

## GitHub Pages

The daily workflow updates the cache and chart, then deploys the repository root to GitHub Pages. In repository settings, select **GitHub Actions** as the Pages source if GitHub does not do so automatically.
