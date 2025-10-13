# Kitchener Centre poll-by-poll map

This repo contains KMZ/KML files for federal electoral boundaries and polling divisions, plus a CSV of poll-by-poll results for Kitchener Centre.

I added a quick Python script that parses the KMLs and the CSV, then renders an interactive Leaflet map with each polling station colored by the winner:
- Liberal: red
- Conservative: blue
- Green: green
- Others/unmatched: grey

## How to run

Requirements: Python 3.9+ with the `folium` package.

On macOS, you may already have Python 3 via Command Line Tools. If folium is missing, install it for your user:

```
/Library/Developer/CommandLineTools/usr/bin/python3 -m pip install --user folium
```

Prepare KMLs (first time only):

```
mkdir -p tmp
unzip -p FED_CA_2025_EN.kmz doc.kml > tmp/FED.kml
unzip -p PD_ON-Southwest_2025_EN.kmz doc.kml > tmp/PD_SW.kml
# Advanced PDs layer (optional):
unzip -p ADVPD_ON_2025_EN.kmz doc.kml > tmp/ADVPD.kml
```

Then run the script below (already executed once to produce `kitchener_centre_poll_map.html`):

```
python3 scripts/build_map.py
```

Open the output in a browser:

```
open kitchener_centre_poll_map.html
```

## Notes
- The script extracts the Kitchener Centre boundary from FED_CA_2025_EN.kmz and PD polygons for that riding from PD_ON-Southwest_2025_EN.kmz.
- It will also add a layer for Advanced Polling Divisions from ADVPD_ON_2025_EN.kmz, always visible with blue outlines and labels derived from adv_poll_names.csv.
- It matches PD_NUM from the PD KML to "Poll" in the CSV.
- Advanced polls and specials in the CSV are ignored unless they have matching PD_NUM.
- Tooltips show the winner and percentage breakdown.
