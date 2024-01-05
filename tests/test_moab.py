import build123d as bd
import pytest
import stellarmesh as sm
from pymoab.rng import Range


@pytest.fixture(scope="module")
def model():
    solids = [bd.Solid.make_sphere(10.0)]
    geom = sm.Geometry(solids, ["iron"])
    mesh = sm.Mesh.from_geometry(geom, max_mesh_size=5, dim=2)
    return sm.DAGMCModel.from_mesh(mesh)


def test_surfaces(model):
    assert isinstance(model.surfaces, list)
    assert len(model.surfaces) == 1
    assert isinstance(model.surfaces[0], sm.DAGMCSurface)


def test_volumes(model):
    assert isinstance(model.volumes, list)
    assert len(model.volumes) == 1
    assert isinstance(model.volumes[0], sm.DAGMCVolume)


def test_id(model):
    assert model.surfaces[0].id == 1
    assert model.volumes[0].id == 0


def test_adjacent_surfaces(model):
    vol = model.volumes[0]
    surfaces = vol.adjacent_surfaces
    assert len(surfaces) == 1
    assert surfaces == [model.surfaces[0]]


def test_adjacent_volumes(model):
    surf = model.surfaces[0]
    volumes = surf.adjacent_volumes
    assert len(volumes) == 1
    assert volumes == [model.volumes[0]]


def test_tets(model):
    assert model.tets.empty()


def test_triangles(model):
    all_tris = model.triangles
    assert isinstance(all_tris, Range)
    surf_tris = model.surfaces[0].triangles
    assert isinstance(surf_tris, Range)
    assert all_tris.contains(surf_tris)


def test_material(model):
    vol = model.volumes[0]
    assert vol.material == "iron"
    assert "mat:iron" in {group.name for group in vol.groups}

    vol.material = "plastic"
    assert vol.material == "plastic"
    vol_group_names = {group.name for group in vol.groups}
    assert "mat:iron" not in vol_group_names
    assert "mat:plastic" in vol_group_names

    all_group_names = {group.name for group in model.groups}
    assert "mat:plastic" in all_group_names


def test_group(model):
    vol = model.volumes[0]

    group = model.create_group("test_group")
    assert group.name == "test_group"

    group.name = "funny group"
    assert group.name == "funny group"

    group.add(vol)
    assert vol in group

    group.remove(vol)
    assert vol not in group
