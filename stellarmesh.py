"""GMSH wrapper and DAGMC geometry creator."""
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional, Sequence

import build123d as bd
import gmsh
import numpy as np
import pymoab.core
import pymoab.types
from OCP.BOPAlgo import BOPAlgo_MakeConnected

logger = logging.getLogger(__name__)


class Geometry:
    """Geometry, representing an ordered list of solids, to be meshed."""

    solids: Sequence[bd.Solid]
    material_names: Sequence[str]

    def __init__(self, solids: Sequence[bd.Solid], material_names: Sequence[str]):
        """Construct geometry from solids.

        Args:
            solids: Solids.
            material_names: List of materials. Must match length of solids.
        """
        logger.info(f"Importing {len(solids)} to geometry")
        if len(material_names) != len(solids):
            raise ValueError("Length of material_names must match length of solids.")
        self.solids = solids
        self.material_names = material_names

    # TODO(akoen): import_step and import_brep are not DRY
    # https://github.com/Thea-Energy/stellarmesh/issues/2
    @classmethod
    def import_step(
        cls,
        filename: str,
        material_names: str,
    ) -> "Geometry":
        """Import model from a step file.

        Args:
            filename: File path to import.
            material_names: Ordered list of material names matching solids in file.

        Returns:
            Model.
        """
        geometry = bd.import_step(filename)
        solids = geometry.solids()
        if len(material_names) != len(solids):
            raise ValueError(
                "Length of material_names must match number of solids in file."
            )
        logger.info(f"Importing {len(solids)} from {filename}")
        return cls(solids, material_names)

    @classmethod
    def import_brep(
        cls,
        filename: str,
        material_names: str,
    ) -> "Geometry":
        """Import model from a brep (cadquery, build123d native) file.

        Args:
            filename: File path to import.
            material_names: Ordered list of material names matching solids in file.

        Returns:
            Model.
        """
        geometry = bd.import_brep(filename)
        solids = geometry.solids()
        if len(material_names) != len(solids):
            raise ValueError(
                "Length of material_names must match number of solids in file."
            )
        logger.info(f"Importing {len(solids)} from {filename}")
        return cls(solids, material_names)

    def imprint(self) -> "Geometry":
        """Imprint faces of current geometry.

        Returns:
            A new geometry with the imprinted and merged geometry.
        """
        bldr = BOPAlgo_MakeConnected()
        bldr.SetRunParallel(theFlag=True)
        bldr.SetUseOBB(theUseOBB=True)

        for solid in self.solids:
            if solid.wrapped is not None:
                bldr.AddArgument(solid.wrapped)

        bldr.Perform()
        res = bd.Shape(bldr.Shape())
        return type(self)(res.solids(), self.material_names)


class Mesh:
    """Mesh."""

    _mesh_filename: str

    def __init__(self, mesh_filename: Optional[str] = None):
        """Initialize a mesh from a .msh file.

        Args:
            mesh_filename: Optional .msh filename. If not provided defaults to a
            temporary file. Defaults to None.
        """
        if not mesh_filename:
            with tempfile.NamedTemporaryFile(suffix=".msh", delete=False) as mesh_file:
                mesh_filename = mesh_file.name
        self._mesh_filename = mesh_filename

    def __enter__(self):
        """Enter mesh context, setting gmsh commands to operate on this mesh."""
        if not gmsh.is_initialized():
            gmsh.initialize()

        gmsh.option.setNumber(
            "General.Terminal",
            1 if logger.getEffectiveLevel() <= logging.INFO else 0,
        )
        gmsh.open(self._mesh_filename)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cleanup (finalize) gmsh."""
        gmsh.finalize()

    def _save_changes(self, *, save_all: bool = True):
        gmsh.option.set_number("Mesh.SaveAll", 1 if save_all else 0)
        gmsh.write(self._mesh_filename)

    def write(self, filename: str, *, save_all: bool = True):
        """Write mesh to a .msh file.

        Args:
            filename: Path to write file.
            save_all: Whether to save all entities (or just physical groups). See
            documentation for Mesh.SaveAll. Defaults to True.
        """
        with self:
            gmsh.option.set_number("Mesh.SaveAll", 1 if save_all else 0)
            gmsh.write(filename)

    @classmethod
    def mesh_geometry(
        cls,
        geometry: Geometry,
        min_mesh_size: float = 50,
        max_mesh_size: float = 50,
    ):
        """Mesh solids with Gmsh.

        Args:
            geometry: Geometry to be meshed.
            mesh_filename: Optional filename to store .msh file. Defaults to None.
            min_mesh_size: Min mesh element size. Defaults to 50.
            max_mesh_size: Max mesh element size. Defaults to 50.
        """
        logger.info(f"Meshing solids with mesh size {min_mesh_size}, {max_mesh_size}")

        with cls() as mesh:
            gmsh.model.add("stellarmesh_model")

            material_solid_map = {}
            for s, m in zip(geometry.solids, geometry.material_names):
                dim_tags = gmsh.model.occ.import_shapes_native_pointer(
                    s.wrapped._address()
                )
                if dim_tags[0][0] != 3:
                    raise TypeError("Importing non-solid geometry.")

                solid_tag = dim_tags[0][1]
                if m not in material_solid_map:
                    material_solid_map[m] = [solid_tag]
                else:
                    material_solid_map[m].append(solid_tag)

            gmsh.model.occ.synchronize()

            for material, solid_tags in material_solid_map.items():
                gmsh.model.add_physical_group(3, solid_tags, name=f"mat:{material}")

            gmsh.option.set_number("Mesh.MeshSizeMin", min_mesh_size)
            gmsh.option.set_number("Mesh.MeshSizeMax", max_mesh_size)
            gmsh.model.mesh.generate(2)

            mesh._save_changes(save_all=True)
            return mesh

    def render(
        self,
        output_filename: Optional[str] = None,
        rotation_xyz: tuple[float, float, float] = (0, 0, 0),
        normals: int = 0,
        *,
        clipping: bool = True,
    ) -> str:
        """Render mesh as an image.

        Args:
            output_filename: Optional output filename. Defaults to None.
            rotation_xyz: Rotation in Euler angles. Defaults to (0, 0, 0).
            normals: Normal render size. Defaults to 0.
            clipping: Whether to enable mesh clipping. Defaults to True.

        Returns:
            Path to image file, either passed output_filename or a temporary file.
        """
        with self:
            gmsh.option.set_number("Mesh.SurfaceFaces", 1)
            gmsh.option.set_number("Mesh.Clip", 1 if clipping else 0)
            gmsh.option.set_number("Mesh.Normals", normals)
            gmsh.option.set_number("General.Trackball", 0)
            gmsh.option.set_number("General.RotationX", rotation_xyz[0])
            gmsh.option.set_number("General.RotationY", rotation_xyz[1])
            gmsh.option.set_number("General.RotationZ", rotation_xyz[2])
            if not output_filename:
                with tempfile.NamedTemporaryFile(
                    delete=False, mode="w", suffix=".png"
                ) as temp_file:
                    output_filename = temp_file.name

            try:
                gmsh.fltk.initialize()
                gmsh.write(output_filename)
            finally:
                gmsh.fltk.finalize()
            return output_filename


class _MOABEntity:
    _core: pymoab.core.Core
    handle: np.uint64

    def __init__(self, core: pymoab.core.Core, handle: np.uint64):
        self._core = core
        self.handle = handle


class MOABSurface(_MOABEntity):
    """MOAB surface entity."""

    @property
    def adjacent_volumes(self) -> list["MOABVolume"]:
        """Get adjacent volumes.

        Returns:
            Adjacent volumes.
        """
        parent_entities = self._core.get_parent_meshsets(self.handle)
        return [MOABVolume(self._core, e) for e in parent_entities]


class MOABVolume(_MOABEntity):
    """MOAB volume entity."""

    @property
    def adjacent_surfaces(self) -> list["MOABSurface"]:
        """Get adjacent surfaces.

        Returns:
            Adjacent surfaces.
        """
        child_entities = self._core.get_child_meshsets(self.handle)
        return [MOABSurface(self._core, e) for e in child_entities]


@dataclass
class _Surface:
    """Internal class for surface sense handling."""

    handle: np.uint64
    forward_volume: np.uint64 = field(default=np.uint64(0))
    reverse_volume: np.uint64 = field(default=np.uint64(0))

    def sense_data(self) -> list[np.uint64]:
        """Get MOAB tag sense data.

        Returns:
            Sense data.
        """
        return [self.forward_volume, self.reverse_volume]


class MOABModel:
    """MOAB Model."""

    # h5m_filename: str
    _core: pymoab.core.Core

    def __init__(self, core: pymoab.core.Core):
        """Initialize model from a pymoab core object.

        Args:
            core: Pymoab core.
        """
        self._core = core

    @classmethod
    def read_file(cls, h5m_file: str) -> "MOABModel":
        """Initialize model from .h5m file.

        Args:
            h5m_file: File to load.

        Returns:
            Initialized model.
        """
        core = pymoab.core.Core()
        core.load_file(h5m_file)
        return cls(core)

    def write(self, filename: str):
        """Write MOAB model to .h5m, .vtk, or other file.

        Args:
            filename: Filename with format-appropriate extension.
        """
        self._core.write_file(filename)

    @staticmethod
    def make_watertight(
        input_filename: str,
        output_filename: str,
        binary_path: str = "make_watertight",
    ):
        """Make mesh watertight.

        Args:
            input_filename: Input .h5m filename.
            output_filename: Output watertight .h5m filename.
            binary_path: Path to make_watertight or default to find in path. Defaults to
            "make_watertight".
        """
        subprocess.run(
            [binary_path, input_filename, "-o", output_filename],  # noqa
            check=True,
        )

    @staticmethod
    def _get_moab_tag_handles(core: pymoab.core.Core) -> dict[str, np.uint64]:
        tag_handles = {}

        sense_tag_name = "GEOM_SENSE_2"
        sense_tag_size = 2
        tag_handles["surf_sense"] = core.tag_get_handle(
            sense_tag_name,
            sense_tag_size,
            pymoab.types.MB_TYPE_HANDLE,
            pymoab.types.MB_TAG_SPARSE,
            create_if_missing=True,
        )

        tag_handles["category"] = core.tag_get_handle(
            pymoab.types.CATEGORY_TAG_NAME,
            pymoab.types.CATEGORY_TAG_SIZE,
            pymoab.types.MB_TYPE_OPAQUE,
            pymoab.types.MB_TAG_SPARSE,
            create_if_missing=True,
        )

        tag_handles["name"] = core.tag_get_handle(
            pymoab.types.NAME_TAG_NAME,
            pymoab.types.NAME_TAG_SIZE,
            pymoab.types.MB_TYPE_OPAQUE,
            pymoab.types.MB_TAG_SPARSE,
            create_if_missing=True,
        )

        # TODO(akoen): C2D and C2O set tag type to DENSE, while cubit plugin is SPARSE
        # https://github.com/Thea-Energy/stellarmesh/issues/1
        geom_dimension_tag_size = 1
        tag_handles["geom_dimension"] = core.tag_get_handle(
            pymoab.types.GEOM_DIMENSION_TAG_NAME,
            geom_dimension_tag_size,
            pymoab.types.MB_TYPE_INTEGER,
            pymoab.types.MB_TAG_SPARSE,
            create_if_missing=True,
        )

        faceting_tol_tag_name = "FACETING_TOL"
        faceting_tol_tag_size = 1
        tag_handles["faceting_tol"] = core.tag_get_handle(
            faceting_tol_tag_name,
            faceting_tol_tag_size,
            pymoab.types.MB_TYPE_DOUBLE,
            pymoab.types.MB_TAG_SPARSE,
            create_if_missing=True,
        )

        # Default tag, does not need to be created
        tag_handles["global_id"] = core.tag_get_handle(pymoab.types.GLOBAL_ID_TAG_NAME)

        return tag_handles

    @classmethod
    def make_from_mesh(  # noqa: PLR0915
        cls,
        mesh: Mesh,
    ):
        """Compose DAGMC MOAB .h5m file from mesh.

        Args:
            mesh: Mesh from which to build DAGMC geometry.
            filename: Filename of the output .h5m file.
        """
        core = pymoab.core.Core()

        tag_handles = cls._get_moab_tag_handles(core)

        known_surfaces: dict[int, _Surface] = {}
        known_groups: dict[int, np.uint64] = {}

        with mesh:
            volume_dimtags = gmsh.model.get_entities(3)
            volume_tags = [v[1] for v in volume_dimtags]
            for i, volume_tag in enumerate(volume_tags):
                # Add volume set
                volume_set_handle = core.create_meshset()
                global_id = volume_set_handle
                core.tag_set_data(tag_handles["global_id"], global_id, i)
                core.tag_set_data(tag_handles["geom_dimension"], volume_set_handle, 3)
                core.tag_set_data(tag_handles["category"], volume_set_handle, "Volume")

                # Add volume to its physical group, which stores metadata incl. material
                # TODO(akoen): should this be a parent-child relationship?
                # https://github.com/Thea-Energy/neutronics-cad/issues/2
                vol_groups = gmsh.model.get_physical_groups_for_entity(3, volume_tag)
                if (num_groups := len(vol_groups)) != 1:
                    raise ValueError(
                        f"Volume with tag {volume_tag} and global_id {global_id} "
                        f"belongs to {num_groups} physical groups, should be 1"
                    )

                if (vol_group := vol_groups[0]) not in known_groups:
                    mat_name = gmsh.model.get_physical_name(3, vol_group)
                    group_set = core.create_meshset()
                    core.tag_set_data(tag_handles["category"], group_set, "Group")
                    core.tag_set_data(tag_handles["name"], group_set, f"{mat_name}")
                    core.tag_set_data(tag_handles["global_id"], group_set, vol_group)
                    known_groups[vol_group] = group_set
                else:
                    group_set = known_groups[vol_group]

                core.add_entity(group_set, volume_set_handle)

                # Add surfaces to MOAB core, respecting surface sense.
                # Logic: Gmsh meshes volumes in order. When it gets to the first volume,
                # it points all adjacent surfaces normals outward. For each subsequent
                # volume, it points the surface normals outwards iff the surface hasn't
                # yet been encountered. Thus, the first time a surface is encountered,
                # the current volume has a forward sense and the second time a reverse
                # sense.
                adjacencies = gmsh.model.get_adjacencies(3, volume_tag)
                surface_tags = adjacencies[1]
                for surface_tag in surface_tags:
                    if surface_tag not in known_surfaces:
                        surface_set_handle = core.create_meshset()
                        surface = _Surface(handle=surface_set_handle)
                        surface.forward_volume = volume_set_handle
                        known_surfaces[surface_tag] = surface

                        core.tag_set_data(
                            tag_handles["global_id"], surface.handle, surface_tag
                        )
                        core.tag_set_data(
                            tag_handles["geom_dimension"], surface.handle, 2
                        )
                        core.tag_set_data(
                            tag_handles["category"], surface.handle, "Surface"
                        )
                        core.tag_set_data(
                            tag_handles["surf_sense"],
                            surface.handle,
                            surface.sense_data(),
                        )

                        # Write surface to MOAB. STL export/import is very efficient.
                        with tempfile.NamedTemporaryFile(
                            suffix=".stl", delete=True
                        ) as stl_file:
                            group_tag = gmsh.model.add_physical_group(2, [surface_tag])
                            gmsh.write(stl_file.name)
                            gmsh.model.remove_physical_groups([(2, group_tag)])
                            core.load_file(stl_file.name, surface_set_handle)

                    else:
                        # Surface already has a forward volume, so this must be the
                        # reverse volume.
                        surface = known_surfaces[surface_tag]
                        surface.reverse_volume = volume_set_handle
                        core.tag_set_data(
                            tag_handles["surf_sense"],
                            surface.handle,
                            surface.sense_data(),
                        )

                    core.add_parent_child(volume_set_handle, surface.handle)

            all_entities = core.get_entities_by_handle(0)
            file_set = core.create_meshset()
            # TODO(akoen): faceting tol set to a random value
            # https://github.com/Thea-Energy/neutronics-cad/issues/5
            # faceting_tol required to be set for make_watertight, although its
            # significance is not clear
            core.tag_set_data(tag_handles["faceting_tol"], file_set, 1e-3)
            core.add_entities(file_set, all_entities)

            return cls(core)

    def _get_entities_of_geom_dimension(self, dim: int) -> list[np.uint64]:
        dim_tag = self._core.tag_get_handle(pymoab.types.GEOM_DIMENSION_TAG_NAME)
        return self._core.get_entities_by_type_and_tag(
            0, pymoab.types.MBENTITYSET, dim_tag, [dim]
        )

    @property
    def surfaces(self):
        """Get surfaces in this model.

        Returns:
            Surfaces.
        """
        surface_handles = self._get_entities_of_geom_dimension(2)
        return [MOABSurface(self._core, h) for h in surface_handles]

    @property
    def volumes(self):
        """Get volumes in this model.

        Returns:
            Volumes.
        """
        volume_handles = self._get_entities_of_geom_dimension(3)
        return [MOABVolume(self._core, h) for h in volume_handles]