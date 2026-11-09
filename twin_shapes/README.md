# Twin shape files

Copies of the frontend's (joy-craft-show) shape files, which `twin_mesh.py` builds each user's twin mesh from:

| File | From the frontend |
|---|---|
| `female_slim.glb` … `male_fuller.glb` | `public/meshes/` |
| `body-shapes.json` | `src/lib/body-shapes.json` |
| `base-bodies.json` | `src/lib/base-bodies.json` |

They're made from Anny by the frontend's `scripts/measure_base_bodies.py` and `scripts/build_body_shapes.py`. When those change, copy all eight files here again. `body-shapes.json`'s `version` is a hash of all of them: the backend works it out again when it starts and logs an error if the copy doesn't match, and each saved mesh records the version it was built with next to the app's.
