# VECTOR's hologram models

This folder is VECTOR's own hologram collection. It holds the workstation, the Wick and
the emblem. The package installs the baked models, `manifest.json` and this README in
`/usr/share/bromigos/vector/holo/`. The sources stay in this repository.

The gallery shows these on VECTOR's shelf, next to your own models and bromigos-homelab's
rack. [docs/holograms.md](../../docs/holograms.md) describes the gallery and every other
collection.

## The models

Each model is split into parts. A part can show a live reading in a callout.

**`workstation`**, this machine. Made from `src/workstation.glb`, whose concept is
`concepts/workstation.jpg`.

- CPU: load, temperature, clock
- MEMORY
- GPU: utilization, VRAM, temperature, watts
- DRIVES: the fullest mounts, I/O
- EXHAUST: fans
- INTAKE: network
- PSU: GPU board power, uptime
- CHASSIS: host, kernel, load

**`wick`**, the Wick relay station. Made from `src/wick-station.glb`, whose concept is
`concepts/wick-station.jpg`.

- MAST: the relay beam, lit while every lab service answers
- DISH: the internet connection
- ANTENNAE: LAN clients, ISP latency
- SOLAR and SOLAR ARRAY: the house's solar array
- RACK ROOM: nodes, pods, alerts
- DOCK: the NAS pool
- HULL: the hypervisor

**`emblem`**, the burn-in. Built from the Wick theme's `emblem-ring.svg` and
`emblem-flame.svg` (`/usr/share/bromigos/themes/wick/emblem/`) by `tools/holo/emblem.py`.

- RING: the motto
- TUNING GAP: services answering
- FLAME: the beam lit
- FIRST RELAY: nodes ready

The rack lives in bromigOS's `homelab/holo/`, with bromigos-homelab.

`previews/*-parts.png` show each model's parts from the front, the side and three
quarters. `previews/holo-*.jpg` are the gallery and VECTOR's console, rendered offscreen
over bromigOS's empty den with made-up readings (`tools/offscreen.py` with `OFF_DEMO=1`,
[holograms.md](../../docs/holograms.md#using-the-renderer)).

## A collection's layout

Every collection has the same layout:

| Path | What it holds |
|---|---|
| `manifest.json` | Each model's source, size limits and parts |
| `<name>.holo.npz` | The baked model, the only file the desktop reads |
| `src/` | The GLB masters |
| `concepts/` | The images the masters came from |
| `previews/` | Part splits and renders |
| `generations.jsonl` | One line per generation job: provider, model, credits |

## Make or rebake a model

The tools in `tools/holo/` work on one collection. They use `--root DIR` if given, else
`$BROMIGOS_HOLO_ROOT`, else your own collection, `~/.config/bromigos/vector/holo/`.

They run in a build environment of their own. Create it once:

```bash
uv venv ~/.local/share/bromigos/venv --python /usr/bin/python3
VIRTUAL_ENV=~/.local/share/bromigos/venv uv pip install trimesh numpy scipy \
  fast-simplification shapely svgpathtools manifold3d mapbox-earcut networkx pillow
```

Rebake the models in this folder, with part previews:

```bash
~/.local/share/bromigos/venv/bin/python tools/holo/bake.py --root assets/holo --preview
```

The chain from an idea to a model has three steps. Each is a separate tool, so a creation
provider can run any of them.

1. **A concept image**, cut out from its background
   (`bromigos art make image.cutout --input concept.jpg --out concept-cut.png`).
2. **Image to mesh.** `gen3d.py concept-cut.png out.glb` asks bromigOS's creation
   providers (`bromigos art make model3d.generate`). `create.toml` picks the provider.
   A ComfyUI with Hunyuan3D costs no credits. nolgia costs credits.
3. **Parts and bake.** `make-holo.py glb NAME out.glb parts.json` adds the model to the
   manifest, copies the GLB into `src/` and bakes it.

Without an image, `make-holo.py add NAME spec.json` builds a model from simple shapes
(`prims.py`) and bakes it, for no credits.

VECTOR's terminal refuses `gen3d.py`, because it can spend credits. His own models go
through `make_hologram`, which stays on free hardware.

A model can say `"requires": "homelab"`. The gallery then hides it unless bromigos-homelab
has a source configured.

## Parts

Generated meshes come out as one fused shell, so parts are regions, not objects. In
`manifest.json`, each part lists boxes as `[x0, x1, y0, y1, z0, z1]`. The numbers are
fractions of the model's normalised bounding box, with y up and the front at +z.

- A face belongs to the first part whose box holds its centre. Faces in no box go to the
  part marked `"rest": true`.
- `explode` is the direction a part moves when the model explodes, and `distance` is how
  far.
- `bind` names the part's live reading, one of the keys in `holo/bind.py`. An empty
  `bind` makes a part that only shapes the model. It's drawn lit, with no callout.
- `hint` is the hover text.

To change a split, edit the boxes, rebake, and check `previews/<name>-parts.png`.

## The `.holo` format

The format is `bromigos-holo/1`. A file is a NumPy `.npz` archive, so `numpy.load` is all a
reader needs.

| Array | Type | Meaning |
|---|---|---|
| `meta` | uint8 | A UTF-8 JSON header, below |
| `pos` | float32 [V,3] | Vertices. y up, front +z, base at y = 0, tallest extent 1 |
| `nrm` | float16 [V,3] | Vertex normals |
| `part` | uint8 [V] | The part of each vertex. Vertices are split per part, so a moving part never tears its neighbours |
| `tri` | uint32 [T,3] | Surface triangles, for the faint shell, hidden lines and picking |
| `edge` | uint32 [E,2] | Feature edges: sharp creases, part borders and open boundaries. This is the wireframe |

`meta` holds `format`, `name`, `title`, `subtitle`, `source`, `source_sha1`, `up`,
`front`, `height`, `radius`, `counts` (`verts`, `tris`, `edges`), `edge_deg`, `baked` and
`parts`. Each part is an object:

- `id`, `label`, `bind`, `hint`
- `centroid`, `extent`
- `explode`, the part's full offset when the model is fully exploded
- `anchor`, where a callout's leader line attaches (add the same offset)
- `tris`, `edges`

A model is 140 to 600 KB, with 10k to 22k vertices, 19k to 44k triangles and 7k to 23k
edges.

```python
import json
import numpy as np

d = np.load("wick.holo.npz")
meta = json.loads(bytes(d["meta"]))
pos, part, edge = d["pos"], d["part"], d["edge"]
amount = 0.5                                                    # how far exploded, 0 to 1
offset = np.array([p["explode"] for p in meta["parts"]], np.float32)[part] * amount
lines = (pos + offset)[edge]                                    # [E, 2, 3] line segments
```

The renderer that draws these is `holo/render.py` and `holo/stage.py`
([docs/holograms.md](../../docs/holograms.md#using-the-renderer)).

## How these models were made

The workstation and the Wick were made on 2026-10-04 with nolgia, a paid generation
service. A concept image came first, then image-to-3D.

1. **Concepts.** nolgia `gpt-image-2.5-flare`, square, 8 credits each. Every prompt ended
   with the same isolation block: *"Isolated 3D product render on a plain pure white
   background, whole object fully in frame with margin, three-quarter view from slightly
   above, soft even studio lighting, no cast shadow, no text, no logos, no people, crisp
   hard-surface modelling with clear separable parts."* The subjects:
   - workstation: *"A stylized mid-tower desktop workstation PC with the side panel
     removed so the internals are visible: a tall tower air cooler on the CPU, four RAM
     sticks, a long triple-fan graphics card, a PSU shroud along the bottom, three large
     front intake fans behind a mesh front, a small drive cage, a rear exhaust fan.
     Chunky industrial retro sci-fi design with panel lines and vents, matte graphite and
     gunmetal."*
   - wick-station: *"A small decommissioned space relay station floating in space, as a
     free-standing model: a squat cylindrical hub module with a ring of small lit windows
     around a rack room, a tall lattice antenna mast rising from the top with three
     crossbars, a parabolic dish on a short arm, several whip antennae, two short folded
     solar panel wings, docking ring underneath, weathered patched hull plating, worn
     lived-in space-opera hardware."*
2. **Image to 3D.** nolgia `hunyuan3d-v3`, untextured, 13 credits each. A hologram needs
   only geometry. `generations.jsonl` records both jobs.
3. **Masters.** The raw outputs, 500k faces and about 9 MB each, stay out of the
   repository, in `~/.local/share/bromigos/3d-raw/`. nolgia can download them again by
   asset id. The committed GLBs are decimated to 100k faces, about 1.8 MB each.

The emblem wasn't generated. It's extruded from the canon vectors, because a generator
would garble the motto and the tuning gap.
