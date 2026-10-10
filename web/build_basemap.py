"""Build web/data/basemap.json: Natural Earth 110m land as one Equal Earth SVG path.

Input is the world-atlas TopoJSON (Natural Earth, public domain):
  curl -sfLo countries-110m.json https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json
  python3 web/build_basemap.py countries-110m.json   (run from the repo root)
Antarctica is cropped; rings crossing 180 deg are unwrapped and drawn twice.
js/viz.js projects the institution coordinates with the same formula and
constants (k, xmax, ymax), so dots and land line up. Rebuild only if the map
style changes; the output is committed."""
import json, math, pathlib, sys
A1, A2, A3, A4 = 1.340264, -0.081106, 0.000893, 0.003796
M = math.sqrt(3) / 2
W = 1000.0

def ee(lon, lat):
    l, p = math.radians(lon), math.radians(lat)
    t = math.asin(M * math.sin(p))
    t2 = t * t; t6 = t2 * t2 * t2
    x = l * math.cos(t) / (M * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)))
    y = t * (A1 + A2 * t2 + t6 * (A3 + A4 * t2))
    return x, y

topo = json.load(open(sys.argv[1]))
sx, sy = topo["transform"]["scale"]; tx, ty = topo["transform"]["translate"]
arcs = []
for arc in topo["arcs"]:
    x = y = 0; pts = []
    for dx, dy in arc:
        x += dx; y += dy
        pts.append((x * sx + tx, y * sy + ty))
    arcs.append(pts)

def ring(idx):
    out = []
    for i in idx:
        pts = arcs[i] if i >= 0 else arcs[~i][::-1]
        out.extend(pts if not out else pts[1:])
    return out

xmax = ee(180, 0)[0]; ymax = ee(0, 90)[1]
k = W / (2 * xmax)
H = 2 * ymax * k
LAT_MIN = -58  # crop Antarctica
H_CROP = (ymax - ee(0, LAT_MIN)[1]) * k
parts = []
for g in topo["objects"]["land"]["geometries"]:
    polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
    for poly in polys:
        for r in poly:
            pts = ring(r)
            if max(p[1] for p in pts) < -60:
                continue
            copies = [pts]
            if max(p[0] for p in pts) - min(p[0] for p in pts) > 300:
                # ring crosses the antimeridian (Russia, Fiji): unwrap to 0..360 and
                # draw it twice (east and west copy); the SVG viewport clips the overflow
                un, off, prev = [], 0.0, pts[0][0]
                for lo, la in pts:  # sequential unwrap: no step longer than 180 deg
                    if lo + off - prev > 180: off -= 360
                    elif lo + off - prev < -180: off += 360
                    un.append((lo + off, la)); prev = lo + off
                shift = -360 if max(p[0] for p in un) > 180 else 360
                copies = [un, [(lo + shift, la) for lo, la in un]]
            for c in copies:
                xy = [((ee(lo, la)[0] + xmax) * k, (ymax - ee(lo, la)[1]) * k) for lo, la in c]
                parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in xy) + "Z")
json.dump({"width": W, "height": round(H_CROP, 1), "k": k, "xmax": xmax, "ymax": ymax,
           "source": "Natural Earth 110m land via world-atlas 2.0.2 (public domain)",
           "path": "".join(parts)}, open(pathlib.Path(__file__).resolve().parent / "data" / "basemap.json", "w"))
print("basemap", len(parts), "rings", round(H_CROP, 1))
