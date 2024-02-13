"""Test of a simple layered torus."""
import logging

import build123d as bd
import stellarmesh as sm

logging.getLogger("stellarmesh").setLevel(logging.INFO)

solids = [bd.Solid.make_torus(1000, 100)]
for _ in range(3):
    solids.append(solids[-1].faces()[0].thicken(100))
solids = solids[1:]

geometry = sm.Geometry(solids[::-1], material_names=["a", "a", "c"])
mesh = sm.Mesh.from_geometry(geometry, min_mesh_size=50, max_mesh_size=50)
mesh.write("test.msh")
mesh.render("doc/torus-mesh-reversed.png", rotation_xyz=(90, 0, -90), normals=15)

h5m = sm.DAGMCModel.from_mesh(mesh)
h5m.write("dagmc.h5m")
h5m.write("dagmc.vtk")
