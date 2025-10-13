import os
import re
import csv
import json
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional, Dict

import folium
import requests
from shapely.geometry import LineString, Polygon
from shapely.prepared import prep
from html import escape

CSV_PATH = 'kitchener_centre_results.csv'
FED_KMZ_KML = 'tmp/FED.kml'
PD_KMZ_KML = 'tmp/PD_SW.kml'
ADVPD_KMZ_KML = 'tmp/ADVPD.kml'
HIGHWAYS_CACHE = 'tmp/kitchener_centre_highways.json'

KNS = {'kml': 'http://www.opengis.net/kml/2.2'}


def extract_table_value(html_text: str, key: str) -> Optional[str]:
    s = (html_text or '').replace('', '')
    m = re.search(rf"<td>\s*{re.escape(key)}\s*</td>\s*<td>([^<]+)</td>", s)
    return m.group(1).strip() if m else None


def load_results(csv_path: str) -> Dict[int, dict]:
    results: Dict[int, dict] = {}
    with open(csv_path, newline='') as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            try:
                poll = int(row['Poll'])
            except Exception:
                continue
            parties = ['Liberal', 'Conservative', 'Green']
            vals = {p: float(row[p]) if row.get(p) not in (None, '') else 0.0 for p in parties}
            winner = max(vals.items(), key=lambda kv: kv[1])[0]
            results[poll] = {'winner': winner, 'votes': row['Vote total'] , **vals}
    return results


def load_fed_boundary(fed_kml_path: str) -> Tuple[Optional[str], List[Tuple[float, float]]]:
    tree = ET.parse(fed_kml_path)
    root = tree.getroot()
    fed_num = None
    fed_coords: List[Tuple[float, float]] = []
    for pm in root.findall('.//kml:Placemark', KNS):
        name_el = pm.find('kml:name', KNS)
        if name_el is not None and (name_el.text or '').strip() == 'Kitchener Centre':
            desc = pm.find('kml:description', KNS)
            if desc is not None and desc.text:
                fed_num = extract_table_value(desc.text, 'FED_NUM')
            coords_el = pm.find('.//kml:Polygon/kml:outerBoundaryIs/kml:LinearRing/kml:coordinates', KNS)
            if coords_el is not None and coords_el.text:
                pts: List[Tuple[float, float]] = []
                for part in coords_el.text.strip().split():
                    toks = part.split(',')
                    if len(toks) >= 2:
                        lon, lat = map(float, toks[:2])
                        pts.append((lat, lon))
                fed_coords = pts
            break
    return fed_num, fed_coords


def load_pd_polygons(pd_kml_path: str, fed_num: str) -> List[dict]:
    tree = ET.parse(pd_kml_path)
    root = tree.getroot()
    pds: List[dict] = []
    for pm in root.findall('.//kml:Placemark', KNS):
        desc = pm.find('kml:description', KNS)
        if desc is None or not desc.text:
            continue
        fn = extract_table_value(desc.text, 'FED_NUM')
        if not fn or fn != str(fed_num):
            continue
        pd_num_val = extract_table_value(desc.text, 'PD_NUM')
        try:
            pd_num = int(pd_num_val)
        except Exception:
            continue
        adv_num = extract_table_value(desc.text, 'ADV_POLL_NUM')
        coords_el = pm.find('.//kml:Polygon/kml:outerBoundaryIs/kml:LinearRing/kml:coordinates', KNS)
        if coords_el is None or not coords_el.text:
            continue
        pts: List[Tuple[float, float]] = []
        for part in coords_el.text.strip().split():
            toks = part.split(',')
            if len(toks) >= 2:
                lon, lat = map(float, toks[:2])
                pts.append((lat, lon))
        if not pts:
            continue
        pds.append({'pd_num': pd_num, 'coords': pts, 'adv_num': adv_num})
    return pds


def load_advpd_polygons(advpd_kml_path: str, fed_num: str, adv_names: Optional[Dict[str, str]] = None) -> List[dict]:
    tree = ET.parse(advpd_kml_path)
    root = tree.getroot()
    adv: List[dict] = []
    for pm in root.findall('.//kml:Placemark', KNS):
        desc = pm.find('kml:description', KNS)
        if desc is None or not desc.text:
            continue
        fn = extract_table_value(desc.text, 'FED_NUM')
        if not fn or fn != str(fed_num):
            continue
        adv_num = extract_table_value(desc.text, 'ADV_POLL_N') or extract_table_value(desc.text, 'ADV_POLL_NUM')
        name = (adv_names.get(str(adv_num)) if adv_names else None) or extract_table_value(desc.text, 'POLL_NAME') or pm.findtext('kml:name', default='', namespaces=KNS)
        coords_el = pm.find('.//kml:Polygon/kml:outerBoundaryIs/kml:LinearRing/kml:coordinates', KNS)
        if coords_el is None or not coords_el.text:
            continue
        pts: List[Tuple[float, float]] = []
        for part in coords_el.text.strip().split():
            toks = part.split(',')
            if len(toks) >= 2:
                lon, lat = map(float, toks[:2])
                pts.append((lat, lon))
        if not pts:
            continue
        adv.append({'adv_num': adv_num, 'name': name, 'coords': pts})
    return adv


def latlon_bounds(coords: List[Tuple[float, float]]):
    lats = [lat for lat, _ in coords]
    lons = [lon for _, lon in coords]
    return (min(lats), min(lons), max(lats), max(lons))


ACCEPT_HIGHWAY_CLASSES = {
    'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link',
    'secondary', 'secondary_link', 'tertiary', 'tertiary_link', 'unclassified',
    'residential', 'living_street', 'service'
}


def fetch_highways_overpass(bbox, cache_path=HIGHWAYS_CACHE):
    if os.path.exists(cache_path):
        with open(cache_path, 'r') as f:
            return json.load(f)
    south, west, north, east = bbox
    query = f"""
    [out:json][timeout:60];
    (
      way["highway"]["name"]({south},{west},{north},{east});
    );
    out tags geom;
    """
    url = 'https://overpass-api.de/api/interpreter'
    resp = requests.post(url, data={'data': query.strip()})
    resp.raise_for_status()
    data = resp.json()
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, 'w') as f:
        json.dump(data, f)
    return data


def build_road_geoms(overpass_json):
    roads = []
    for el in overpass_json.get('elements', []):
        if el.get('type') != 'way':
            continue
        tags = el.get('tags', {})
        hwy = tags.get('highway')
        name = tags.get('name')
        if not name or not hwy:
            continue
        if hwy not in ACCEPT_HIGHWAY_CLASSES:
            continue
        geom = el.get('geometry')
        if not geom:
            continue
        coords = [(pt['lon'], pt['lat']) for pt in geom]
        try:
            ls = LineString(coords)
        except Exception:
            continue
        roads.append({'name': name, 'class': hwy, 'geom': ls})
    return roads


def streets_within_polygon(roads, poly: Polygon):
    names = set()
    ppoly = prep(poly)
    minx, miny, maxx, maxy = poly.bounds
    for r in roads:
        rxmin, rymin, rxmax, rymax = r['geom'].bounds
        if rxmax < minx or rxmin > maxx or rymax < miny or rymin > maxy:
            continue
        try:
            if ppoly.intersects(r['geom']):
                names.add(r['name'])
        except Exception:
            continue
    return sorted(names)


def main():
    # Optional mapping for advanced poll names
    adv_names: Dict[str, str] = {}
    if os.path.exists('adv_poll_names.csv'):
        with open('adv_poll_names.csv', newline='') as f:
            rdr = csv.reader(f)
            for row in rdr:
                if not row or len(row) < 2:
                    continue
                adv_names[row[0].strip()] = row[1].strip()

    results = load_results(CSV_PATH)
    fed_num, fed_coords = load_fed_boundary(FED_KMZ_KML)
    if not fed_num:
        raise SystemExit('Could not find Kitchener Centre in FED.kml')
    pds = load_pd_polygons(PD_KMZ_KML, fed_num)
    adv_pds = []
    try:
        adv_pds = load_advpd_polygons(ADVPD_KMZ_KML, fed_num, adv_names)
    except FileNotFoundError:
        adv_pds = []

    # Fetch roads for street names
    s_lat, s_lon, n_lat, e_lon = latlon_bounds(fed_coords)
    overpass_json = fetch_highways_overpass((s_lat, s_lon, n_lat, e_lon))
    roads = build_road_geoms(overpass_json)

    cent_lat = sum(p[0] for p in fed_coords) / len(fed_coords)
    cent_lon = sum(p[1] for p in fed_coords) / len(fed_coords)

    m = folium.Map(location=[cent_lat, cent_lon], zoom_start=12, tiles='CartoDB positron')

    # Panes for z-index
    folium.map.CustomPane('adv', z_index=400).add_to(m)
    folium.map.CustomPane('pd', z_index=650).add_to(m)

    # Riding outline
    if fed_coords:
        folium.PolyLine(locations=fed_coords, color='#000000', weight=3, opacity=0.8).add_to(m)

    # Fit to bounds
    m.fit_bounds([[s_lat, s_lon], [n_lat, e_lon]])

    COLORS = {
        'Liberal': '#d71920',
        'Conservative': '#1f77b4',
        'Green': '#2ca02c',
        'Other': '#aaaaaa',
    }

    # PD layer
    pds_fg = folium.FeatureGroup(name='Polling Divisions', show=True)
    for pd in pds:
        poly = Polygon([(lon, lat) for (lat, lon) in pd['coords']])
        if not poly.is_valid:
            poly = poly.buffer(0)
        street_names = streets_within_polygon(roads, poly)
        res = results.get(pd['pd_num'])
        if not res:
            color = COLORS['Other']
            tip = f"PD {pd['pd_num']} — no result | streets: {len(street_names)}"
        else:
            winner = res['winner']
            color = COLORS.get(winner, COLORS['Other'])
            tip = (
                f"PD {pd['pd_num']} — {winner} | streets: {len(street_names)}"
                f"<div>L: {res['Liberal']}% | C: {res['Conservative']}% | G: {res['Green']}%</div>"
                f"<div><b>Total votes cast:</b> {res['votes']}</div>"
            )
        tip_html = tip
        adv_info_html = ''
        if pd.get('adv_num'):
            adv_num = pd['adv_num']
            adv_nm = adv_names.get(adv_num, '')
            adv_info_html = f"<div><b>Advance station:</b> {escape(adv_num)}{(' — ' + escape(adv_nm)) if adv_nm else ''}</div>"
        full_text_html = escape(', '.join(street_names))
        html = f"""
        <div style="font-size:12px;">
          <b>PD {pd['pd_num']}</b><br/>
          <div style="margin:4px 0;">{tip_html}</div>
          {adv_info_html}
          <div style="margin-top:6px;"><b>All streets (select and copy):</b></div>
          <textarea style="width:100%;height:160px;border:1px solid #ddd;padding:6px;background:#fff;" readonly>{full_text_html}</textarea>
        </div>
        """
        popup = folium.Popup(html=html, max_width=360)
        folium.Polygon(
            locations=pd['coords'],
            color='#333333',
            weight=1,
            fill=True,
            fill_color=color,
            fill_opacity=0.6,
            popup=popup,
            pane='pd',
        ).add_to(pds_fg)

    # Advanced polling divisions layer
    if adv_pds:
        adv_fg = folium.FeatureGroup(name='Advanced Polling Divisions', show=True)
        for apd in adv_pds:
            name = (apd.get('name') or '').strip()
            folium.Polygon(
                locations=apd['coords'],
                color='#5a001a',  # dark maroon outline
                weight=4,
                fill=True,
                fill_color='#fbe3ea',  # pale maroon/pink fill
                fill_opacity=0.15,
                pane='adv',
            ).add_to(adv_fg)
            try:
                shp = Polygon([(lon, lat) for (lat, lon) in apd['coords']])
                c = shp.centroid
                lat_c, lon_c = c.y, c.x
            except Exception:
                lats = [lat for (lat, _) in apd['coords']]
                lons = [lon for (_, lon) in apd['coords']]
                lat_c = sum(lats) / len(lats)
                lon_c = sum(lons) / len(lons)
            folium.Marker(
                [lat_c, lon_c],
                icon=folium.DivIcon(html=f'<div class="adv-label">{escape(name)}</div>'),
            ).add_to(adv_fg)
        adv_fg.add_to(m)

    # Add PD on top
    pds_fg.add_to(m)

    # PD search control
    pd_index: Dict[str, dict] = {}
    for pd in pds:
        lats = [lat for (lat, _) in pd['coords']]
        lons = [lon for (_, lon) in pd['coords']]
        bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]
        cent = [(bounds[0][0] + bounds[1][0]) / 2.0, (bounds[0][1] + bounds[1][1]) / 2.0]
        adv_num = pd.get('adv_num') or ''
        pd_index[str(pd['pd_num'])] = {
            'b': bounds,
            'c': cent,
            'adv_num': adv_num,
            'adv_name': adv_names.get(adv_num, '') if adv_num else '',
        }

    nav_js = f"""
    <script>
    (function(){{
      window.PD_INDEX = {json.dumps(pd_index)};
      var SearchControl = L.Control.extend({{
        onAdd: function(map) {{
          var div = L.DomUtil.create('div', 'pd-nav-ctl leaflet-control');
          div.innerHTML = '<div class="pd-nav"><input id="pd-input" type="text" placeholder="Go to PD #"/><button id="pd-go">Go</button></div>';
          L.DomEvent.disableClickPropagation(div);
          return div;
        }},
        onRemove: function(map) {{
            // Nothing to do here
        }}
      }});

      function initSearchControl() {{
        var map_instance;
        for (var name in window) {{
            if (name.startsWith('map_') && window[name] instanceof L.Map) {{
                map_instance = window[name];
                break;
            }}
        }}

        if (map_instance) {{
            new SearchControl({{ position: 'topright' }}).addTo(map_instance);
            window._leaflet_map_instance = map_instance;
        }} else {{
            setTimeout(initSearchControl, 100);
        }}
      }}

      document.addEventListener('DOMContentLoaded', initSearchControl);

      function go(){{
        var el = document.getElementById('pd-input');
        if(!el) return; var key = (el.value||'').trim(); if(!key) return;
        var info = window.PD_INDEX && window.PD_INDEX[key]; if(!info) {{
          el.style.borderColor = '#c00';
          setTimeout(function(){{ el.style.borderColor=''; }}, 600);
          return;
        }}
        var m = window._leaflet_map_instance;
        if(!m) return;
        m.fitBounds(info.b, {{maxZoom: 16}});
        var parts = [];
        parts.push('<b>PD ' + key + '</b>');
        if(info.adv_num) parts.push('<div><b>Advance station:</b> ' + info.adv_num + (info.adv_name ? ' — ' + info.adv_name : '') + '</div>');
        var popup = L.popup().setLatLng(info.c).setContent('<div style="font-size:12px;line-height:1.2">' + parts.join('') + '</div>');
        popup.openOn(m);
      }}
      document.addEventListener('click', function(ev){{ if(ev.target && ev.target.id==='pd-go'){{ go(); }} }});
      document.addEventListener('keydown', function(ev){{ if(ev.key==='Enter'){{ var el=document.getElementById('pd-input'); if(el && document.activeElement===el) go(); }} }});
    }})();
    </script>
    """
    m.get_root().html.add_child(folium.Element(nav_js))

    nav_css = """
    <style>
      .pd-nav-ctl { background:#fff; padding:6px; border-radius:4px; box-shadow:0 1px 4px rgba(0,0,0,0.2); clear: both; }
      .pd-nav input { width:110px; padding:3px 6px; font-size:12px; margin-right:4px; }
      .pd-nav button { padding:3px 6px; font-size:12px; }
      .leaflet-top.leaflet-right .leaflet-control-layers + .pd-nav-ctl { margin-top: 10px; }
    </style>
    """
    m.get_root().header.add_child(folium.Element(nav_css))

    folium.LayerControl(collapsed=False).add_to(m)

    # Fit on load (safety)
    bounds_js = (
        '<script>'
        '(function(){'
        f'var sw = [{s_lat:.6f}, {s_lon:.6f}], ne = [{n_lat:.6f}, {e_lon:.6f}];'
        'function doFit(){var map = window._leaflet_map_instance || window.map; if(map && map.fitBounds){ map.fitBounds([sw, ne]); }}'
        "if (document.readyState !== 'loading') { setTimeout(doFit, 0); } else { document.addEventListener('DOMContentLoaded', function(){ setTimeout(doFit, 0); }); }"
        '})();'
        '</script>'
    )
    m.get_root().html.add_child(folium.Element(bounds_js))

    # Label styles
    label_css = """
    <style>
      .adv-label {
        font-weight: 700; color: #003366; background: transparent;
        border: none; border-radius: 0; padding: 0 2px;
        text-shadow: 0 1px 2px rgba(255,255,255,0.9); white-space: nowrap;
        pointer-events: none;
      }
    </style>
    """
    m.get_root().header.add_child(folium.Element(label_css))

    out_path = 'kitchener_centre_poll_map.html'
    m.save(out_path)
    print('Wrote', out_path)


if __name__ == '__main__':
    main()
