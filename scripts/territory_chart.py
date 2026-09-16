#!/usr/bin/env python3
"""Maintain a compact DeepState area history and render a mobile chart."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
from bisect import bisect_right
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from pyproj import Geod


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "territory.csv"
SUMMARY_PATH = ROOT / "data" / "summary.json"
HTML_PATH = ROOT / "index.html"
START_DATE = date(2024, 1, 1)
SOURCE_FIRST_DATE = date(2024, 7, 8)
REFRESH_DAYS = 7
ARCHIVE_URL = "https://codeload.github.com/cyterat/deepstate-map-data/tar.gz/refs/heads/main"
DAILY_URL = (
    "https://raw.githubusercontent.com/cyterat/deepstate-map-data/main/data/"
    "deepstatemap_data_{stamp}.geojson"
)
USER_AGENT = "ukraine-territory-trend/1.0"
GEOD = Geod(ellps="WGS84")


def request_bytes(url: str, timeout: int = 90) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def ring_area_m2(ring: list[list[float]]) -> float:
    if len(ring) < 3:
        return 0.0
    longitudes = [point[0] for point in ring]
    latitudes = [point[1] for point in ring]
    area, _ = GEOD.polygon_area_perimeter(longitudes, latitudes)
    return abs(area)


def polygon_area_m2(rings: list[list[list[float]]]) -> float:
    if not rings:
        return 0.0
    return max(0.0, ring_area_m2(rings[0]) - sum(ring_area_m2(r) for r in rings[1:]))


def geometry_area_km2(geometry: dict[str, Any]) -> float:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "Polygon":
        area = polygon_area_m2(coordinates)
    elif kind == "MultiPolygon":
        area = sum(polygon_area_m2(polygon) for polygon in coordinates)
    else:
        raise ValueError(f"Unsupported geometry type: {kind!r}")
    return area / 1_000_000.0


def feature_collection_area_km2(document: dict[str, Any]) -> float:
    if document.get("type") == "FeatureCollection":
        geometries = [feature.get("geometry") for feature in document.get("features", [])]
    elif document.get("type") == "Feature":
        geometries = [document.get("geometry")]
    else:
        geometries = [document]
    return sum(geometry_area_km2(g) for g in geometries if g)


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def load_history(path: Path = CSV_PATH) -> dict[date, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[date, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not row.get("date"):
                continue
            day = parse_iso_date(row["date"])
            rows[day] = {
                "occupied_km2": float(row["occupied_km2"]),
                "source": row.get("source", "deepstate"),
            }
    return rows


def write_history(rows: dict[date, dict[str, Any]], path: Path = CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="territory-", suffix=".csv", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "occupied_km2", "source"])
            for day in sorted(rows):
                writer.writerow([day.isoformat(), f"{rows[day]['occupied_km2']:.3f}", rows[day]["source"]])
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bootstrap(rows: dict[date, dict[str, Any]]) -> int:
    """Load every dated file from one repository archive request.

    The upstream unified gzip has at times contained only a rolling subset even
    though the data directory retained the complete history, so the directory
    files are authoritative for bootstrap.
    """
    payload = request_bytes(ARCHIVE_URL, timeout=180)
    added = 0
    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive_file:
        archive_file.write(payload)
        archive_file.flush()
        with tarfile.open(archive_file.name, mode="r:gz") as archive:
            for member in archive.getmembers():
                match = re.search(r"/data/deepstatemap_data_(\d{8})\.geojson$", member.name)
                if not match or not member.isfile():
                    continue
                day = datetime.strptime(match.group(1), "%Y%m%d").date()
                if day < START_DATE:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                document = json.load(extracted)
                rows[day] = {
                    "occupied_km2": feature_collection_area_km2(document),
                    "source": "deepstate-archive",
                }
                added += 1
    if not added:
        raise RuntimeError("Repository archive contained no usable dated files")
    return added


def daterange(first: date, last: date) -> Iterable[date]:
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


def refresh_daily(rows: dict[date, dict[str, Any]], today: date) -> tuple[int, int]:
    newest = max(rows)
    first = max(START_DATE, newest - timedelta(days=REFRESH_DAYS - 1))
    updated = 0
    missing = 0
    for day in daterange(first, today):
        try:
            payload = request_bytes(DAILY_URL.format(stamp=day.strftime("%Y%m%d")), timeout=30)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                missing += 1
                continue
            raise
        document = json.loads(payload)
        rows[day] = {
            "occupied_km2": feature_collection_area_km2(document),
            "source": "deepstate-daily",
        }
        updated += 1
    return updated, missing


def month_end_rows(rows: dict[date, dict[str, Any]]) -> list[dict[str, Any]]:
    by_month: OrderedDict[str, date] = OrderedDict()
    for day in sorted(rows):
        by_month[day.strftime("%Y-%m")] = day
    result: list[dict[str, Any]] = []
    previous_value: float | None = None
    for month, day in by_month.items():
        value = rows[day]["occupied_km2"]
        result.append(
            {
                "month": month,
                "date": day.isoformat(),
                "occupied_km2": round(value, 3),
                "monthly_change_km2": None if previous_value is None else round(value - previous_value, 3),
            }
        )
        previous_value = value
    return result


def value_at_or_before(rows: dict[date, dict[str, Any]], target: date) -> float | None:
    days = sorted(rows)
    position = bisect_right(days, target) - 1
    return None if position < 0 else rows[days[position]]["occupied_km2"]


def build_summary(rows: dict[date, dict[str, Any]], generated_at: datetime) -> dict[str, Any]:
    if not rows:
        raise RuntimeError("No cached observations are available")
    newest = max(rows)
    latest = rows[newest]["occupied_km2"]
    value_30 = value_at_or_before(rows, newest - timedelta(days=30))
    value_365 = value_at_or_before(rows, newest - timedelta(days=365))
    all_months = month_end_rows(rows)
    return {
        "generated_at": generated_at.isoformat(),
        "requested_start_date": START_DATE.isoformat(),
        "source_first_date": SOURCE_FIRST_DATE.isoformat(),
        "first_observation": min(rows).isoformat(),
        "latest_date": newest.isoformat(),
        "latest_occupied_km2": round(latest, 3),
        "change_30_days_km2": None if value_30 is None else round(latest - value_30, 3),
        "change_12_months_km2": None if value_365 is None else round(latest - value_365, 3),
        "monthly": all_months[-12:],
        "method": "WGS84 geodesic area; last available observation in each calendar month",
        "source": "cyterat/deepstate-map-data (derived from DeepState)",
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="theme-color" content="#111827">
  <title>Динаміка окупованої території України</title>
  <style>
    :root{color-scheme:dark;--bg:#0b1020;--panel:#141b2d;--muted:#94a3b8;--text:#f8fafc;--blue:#60a5fa;--red:#fb7185;--green:#34d399;--grid:#263248}
    *{box-sizing:border-box}body{margin:0;background:linear-gradient(160deg,#0b1020,#101827);color:var(--text);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
    main{max-width:1040px;margin:auto;padding:24px 14px 48px}h1{font-size:clamp(1.55rem,5vw,2.6rem);line-height:1.08;margin:8px 0}p{color:var(--muted);line-height:1.5}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:24px 0}.card,.chart-card{background:rgba(20,27,45,.92);border:1px solid #27334a;border-radius:16px;box-shadow:0 18px 45px #0004}.card{padding:15px}.label{font-size:.78rem;color:var(--muted)}.value{font-size:clamp(1.25rem,4vw,1.9rem);font-weight:750;margin-top:5px}.chart-card{padding:14px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}button{border:1px solid #334155;background:#111827;color:var(--muted);padding:9px 12px;border-radius:999px;font-weight:650}button.active{color:#08111f;background:var(--blue);border-color:var(--blue)}#chart{width:100%;min-height:390px;position:relative}svg{display:block;width:100%;height:auto;overflow:visible}.tip{display:none;position:absolute;z-index:2;pointer-events:none;background:#050914eF;border:1px solid #475569;border-radius:10px;padding:9px 11px;font-size:.82rem;box-shadow:0 8px 28px #0008}.notes{font-size:.86rem;margin-top:18px}.notes a{color:#93c5fd}.positive{color:var(--red)}.negative{color:var(--green)}
    @media(max-width:640px){main{padding-top:16px}.cards{grid-template-columns:1fr}.card{display:flex;justify-content:space-between;align-items:center}.value{font-size:1.25rem}.chart-card{padding:10px}#chart{min-height:340px}}
  </style>
</head>
<body><main>
  <p>DeepState · місячні дані</p>
  <h1>Територія України під російським контролем</h1>
  <p>Останні 12 місяців. Для кожного місяця використано останнє доступне спостереження.</p>
  <section class="cards">
    <div class="card"><div class="label">Останнє значення<br>__LATEST_DATE__</div><div class="value">__LATEST__ км²</div></div>
    <div class="card"><div class="label">Зміна за 30 днів</div><div class="value __C30_CLASS__">__CHANGE_30__ км²</div></div>
    <div class="card"><div class="label">Зміна за 12 місяців</div><div class="value __C365_CLASS__">__CHANGE_365__ км²</div></div>
  </section>
  <section class="chart-card">
    <div class="toolbar"><button class="active" data-mode="total">Загальна площа</button><button data-mode="change">Зміна за місяць</button></div>
    <div id="chart"><div class="tip" id="tip"></div></div>
  </section>
  <p class="notes">Джерело: <a href="https://github.com/cyterat/deepstate-map-data">cyterat/deepstate-map-data</a>, похідні дані DeepState. Площа розрахована геодезично на еліпсоїді WGS‑84. Історія джерела починається 08.07.2024; запитані раніші дати наразі відсутні. Оновлено: __GENERATED__.</p>
</main>
<script>
const rows=__MONTHLY_DATA__;
const chart=document.getElementById('chart'),tip=document.getElementById('tip');
const monthFmt=new Intl.DateTimeFormat('uk-UA',{month:'short',year:'2-digit',timeZone:'UTC'});
const dateFmt=new Intl.DateTimeFormat('uk-UA',{day:'2-digit',month:'long',year:'numeric',timeZone:'UTC'});
const num=new Intl.NumberFormat('uk-UA',{maximumFractionDigits:0});
let mode='total';
function render(){
  chart.querySelector('svg')?.remove();
  const W=900,H=410,L=76,R=24,T=25,B=72,plotW=W-L-R,plotH=H-T-B;
  const values=rows.map(r=>mode==='total'?r.occupied_km2:(r.monthly_change_km2??0));
  let min=Math.min(...values),max=Math.max(...values); if(mode==='total'){const pad=Math.max((max-min)*.12,40);min-=pad;max+=pad}else{const edge=Math.max(Math.abs(min),Math.abs(max),1)*1.15;min=-edge;max=edge}
  const x=i=>L+(rows.length===1?plotW/2:i*plotW/(rows.length-1)); const y=v=>T+(max-v)*plotH/(max-min);
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('role','img');
  for(let i=0;i<5;i++){const v=min+(max-min)*i/4,yy=y(v),line=document.createElementNS(ns,'line');line.setAttribute('x1',L);line.setAttribute('x2',W-R);line.setAttribute('y1',yy);line.setAttribute('y2',yy);line.setAttribute('stroke','#263248');svg.append(line);const label=document.createElementNS(ns,'text');label.setAttribute('x',L-10);label.setAttribute('y',yy+4);label.setAttribute('text-anchor','end');label.setAttribute('fill','#94a3b8');label.setAttribute('font-size','12');label.textContent=num.format(v);svg.append(label)}
  if(mode==='total'){const path=document.createElementNS(ns,'path');path.setAttribute('d',values.map((v,i)=>`${i?'L':'M'}${x(i)},${y(v)}`).join(' '));path.setAttribute('fill','none');path.setAttribute('stroke','#60a5fa');path.setAttribute('stroke-width','4');path.setAttribute('stroke-linejoin','round');svg.append(path)}
  rows.forEach((r,i)=>{const xx=x(i),v=values[i];if(mode==='change'){const zero=y(0),bar=document.createElementNS(ns,'rect'),bw=Math.max(10,plotW/rows.length*.55);bar.setAttribute('x',xx-bw/2);bar.setAttribute('y',Math.min(y(v),zero));bar.setAttribute('width',bw);bar.setAttribute('height',Math.max(2,Math.abs(zero-y(v))));bar.setAttribute('rx','4');bar.setAttribute('fill',v>=0?'#fb7185':'#34d399');svg.append(bar)}else{const dot=document.createElementNS(ns,'circle');dot.setAttribute('cx',xx);dot.setAttribute('cy',y(v));dot.setAttribute('r','5');dot.setAttribute('fill','#dbeafe');svg.append(dot)}const hit=document.createElementNS(ns,'rect');hit.setAttribute('x',xx-Math.max(18,plotW/rows.length/2));hit.setAttribute('y',T);hit.setAttribute('width',Math.max(36,plotW/rows.length));hit.setAttribute('height',plotH);hit.setAttribute('fill','transparent');hit.addEventListener('pointerenter',e=>showTip(e,r,v));hit.addEventListener('pointermove',e=>positionTip(e));hit.addEventListener('pointerleave',()=>tip.style.display='none');svg.append(hit);const label=document.createElementNS(ns,'text');label.setAttribute('x',xx);label.setAttribute('y',H-B+25);label.setAttribute('text-anchor','end');label.setAttribute('transform',`rotate(-38 ${xx} ${H-B+25})`);label.setAttribute('fill','#94a3b8');label.setAttribute('font-size','12');label.textContent=monthFmt.format(new Date(r.date+'T00:00:00Z'));svg.append(label)});
  chart.prepend(svg)
}
function showTip(e,r,v){tip.innerHTML=`<strong>${dateFmt.format(new Date(r.date+'T00:00:00Z'))}</strong><br>${mode==='total'?'Площа':'Зміна'}: ${v>0&&mode==='change'?'+':''}${num.format(v)} км²`;tip.style.display='block';positionTip(e)}
function positionTip(e){const box=chart.getBoundingClientRect();tip.style.left=Math.min(e.clientX-box.left+12,box.width-tip.offsetWidth-5)+'px';tip.style.top=Math.max(4,e.clientY-box.top-tip.offsetHeight-12)+'px'}
document.querySelectorAll('button[data-mode]').forEach(button=>button.addEventListener('click',()=>{mode=button.dataset.mode;document.querySelectorAll('button[data-mode]').forEach(b=>b.classList.toggle('active',b===button));render()}));render();
</script></body></html>"""


def signed(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+,.0f}".replace(",", " ")


def render_outputs(summary: dict[str, Any]) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    c30 = summary["change_30_days_km2"]
    c365 = summary["change_12_months_km2"]
    html = HTML_TEMPLATE
    replacements = {
        "__LATEST_DATE__": summary["latest_date"],
        "__LATEST__": f"{summary['latest_occupied_km2']:,.0f}".replace(",", " "),
        "__CHANGE_30__": signed(c30),
        "__CHANGE_365__": signed(c365),
        "__C30_CLASS__": "positive" if c30 is not None and c30 >= 0 else "negative",
        "__C365_CLASS__": "positive" if c365 is not None and c365 >= 0 else "negative",
        "__GENERATED__": summary["generated_at"][:10],
        "__MONTHLY_DATA__": json.dumps(summary["monthly"], ensure_ascii=False, separators=(",", ":")),
    }
    for token, value in replacements.items():
        html = html.replace(token, str(value))
    HTML_PATH.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-fetch", action="store_true", help="Only render the current CSV cache")
    parser.add_argument("--bootstrap", action="store_true", help="Merge the complete upstream archive into the cache")
    parser.add_argument("--today", type=date.fromisoformat, help="Override UTC today for reproducible tests")
    args = parser.parse_args()
    today = args.today or datetime.now(timezone.utc).date()
    rows = load_history()
    if not args.skip_fetch:
        if not rows or args.bootstrap:
            print(f"Bootstrapping from {ARCHIVE_URL}")
            print(f"Loaded {bootstrap(rows)} historical observations")
        updated, missing = refresh_daily(rows, today)
        print(f"Refreshed {updated} daily files; {missing} dates not published")
        write_history(rows)
    summary = build_summary(rows, datetime.now(timezone.utc))
    render_outputs(summary)
    print(f"Latest: {summary['latest_date']} — {summary['latest_occupied_km2']:.1f} km²")


if __name__ == "__main__":
    main()
