"""Stellarmesh geometry.

name: geometry.py
author: Alex Koen

desc: Geometry class represents a CAD geometry to be meshed.
"""
from __future__ import annotations

import logging
import warnings
from typing import Sequence, Union

from OCP.BOPAlgo import BOPAlgo_MakeConnected
from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape, TopoDS_Solid

logger = logging.getLogger(__name__)


class Geometry:
    """Geometry, representing an ordered list of solids, to be meshed."""

    solids: Sequence[TopoDS_Solid]
    material_names: Sequence[str]

    def __init__(
        self,
        solids: Sequence[Union["bd.Solid", "cq.Solid", TopoDS_Solid]],  # noqa: F821
        material_names: Sequence[str],
    ):
        """Construct geometry from solids.

        Args:
            solids: List of solids, where each solid is a build123d Solid, cadquery
            Solid, or OCP TopoDS_Solid.
            material_names: List of materials. Must match length of solids.
        """
        logger.info(f"Importing {len(solids)} solids to geometry")
        if len(material_names) != len(solids):
            raise ValueError(
                f"Number of material names ({len(material_names)}) must match length of"
                + " solids ({len(solids)})."
            )

        self.solids = []
        for i, s in enumerate(solids):
            if isinstance(s, TopoDS_Solid):
                self.solids.append(s)
            elif hasattr(s, "wrapped"):
                self.solids.append(s.wrapped)
            else:
                raise ValueError(
                    f"Solid {i} is of type {type(s).__name__}, not a cadquery Solid, "
                    + "build123d Solid, or TopoDS_Solid"
                )

        self.material_names = material_names

    @staticmethod
    def _solids_from_shape(shape: TopoDS_Shape) -> list[TopoDS_Solid]:
        """Return all the solids in this shape."""
        solids = []
        if shape.ShapeType() == TopAbs_ShapeEnum.TopAbs_SOLID:
            solids.append(TopoDS.Solid_s(shape))
        if shape.ShapeType() == TopAbs_ShapeEnum.TopAbs_COMPOUND:
            explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
            while explorer.More():
                assert explorer.Current().ShapeType() == TopAbs_ShapeEnum.TopAbs_SOLID
                solids.append(TopoDS.Solid_s(explorer.Current()))
                explorer.Next()
        return solids

    # TODO(akoen): from_step and from_brep are not DRY
    # https://github.com/Thea-Energy/stellarmesh/issues/2
    @classmethod
    def from_step(
        cls,
        filename: str,
        material_names: Sequence[str],
    ) -> Geometry:
        """Import model from a step file.

        Args:
            filename: File path to import.
            material_names: Ordered list of material names matching solids in file.

        Returns:
            Model.
        """
        logger.info(f"Importing {filename}")

        reader = STEPControl_Reader()
        read_status = reader.ReadFile(filename)
        if read_status != IFSelect_RetDone:
            raise ValueError(f"STEP File {filename} could not be loaded")
        for i in range(reader.NbRootsForTransfer()):
            reader.TransferRoot(i + 1)

        solids = []
        for i in range(reader.NbShapes()):
            shape = reader.Shape(i + 1)
            solids.extend(cls._solids_from_shape(shape))

        return cls(solids, material_names)

    @classmethod
    def import_step(
        cls,
        filename: str,
        material_names: Sequence[str],
    ) -> Geometry:
        """Import model from a step file.

        Args:
            filename: File path to import.
            material_names: Ordered list of material names matching solids in file.

        Returns:
            Model.
        """
        warnings.warn(
            "The import_step method is deprecated. Use from_step instead.",
            FutureWarning,
            stacklevel=2,
        )
        return cls.from_step(filename, material_names)

    @classmethod
    def from_brep(
        cls,
        filename: str,
        material_names: Sequence[str],
    ) -> Geometry:
        """Import model from a brep (cadquery, build123d native) file.

        Args:
            filename: File path to import.
            material_names: Ordered list of material names matching solids in file.

        Returns:
            Model.
        """
        logger.info(f"Importing {filename}")

        shape = TopoDS_Shape()
        builder = BRep_Builder()
        BRepTools.Read_s(shape, filename, builder)

        if shape.IsNull():
            raise ValueError(f"Could not import {filename}")
        solids = cls._solids_from_shape(shape)

        logger.info(f"Importing {len(solids)} from {filename}")
        return cls(solids, material_names)

    @classmethod
    def import_brep(
        cls,
        filename: str,
        material_names: Sequence[str],
    ) -> Geometry:
        """Import model from a brep (cadquery, build123d native) file.

        Args:
            filename: File path to import.
            material_names: Ordered list of material names matching solids in file.

        Returns:
            Model.
        """
        warnings.warn(
            "The import_brep method is deprecated. Use from_brep instead.",
            FutureWarning,
            stacklevel=2,
        )
        return cls.from_brep(filename, material_names)

    def imprint(self) -> Geometry:
        """Imprint faces of current geometry.

        Returns:
            A new geometry with the imprinted and merged geometry.
        """
        bldr = BOPAlgo_MakeConnected()
        bldr.SetRunParallel(theFlag=True)
        bldr.SetUseOBB(theUseOBB=True)

        for solid in self.solids:
            bldr.AddArgument(solid)

        bldr.Perform()
        res = bldr.Shape()
        res_solids = self._solids_from_shape(res)

        if (l0 := len(res_solids)) != (l1 := len(self.solids)):
            raise RuntimeError(
                f"Length of imprinted solids {l0} != length of original solids {l1}"
            )

        return type(self)(res_solids, self.material_names)
