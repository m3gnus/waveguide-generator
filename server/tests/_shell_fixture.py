"""Write small, standards-valid STEP files with an explicit shell layout.

**Synthetic, and not a captured Fusion export.** What these reproduce is the
topology AP214 allows and Fusion uses for a surface body -- one
``SHELL_BASED_SURFACE_MODEL`` holding one ``OPEN_SHELL`` of several
``ADVANCED_FACE`` records -- because no such file existed to test against.
Gmsh's own STEP writer emits one shell per face, so it cannot produce the shape
in question; that is why this writes the records directly.

Nothing here is trusted on its own: every fixture is imported through OCC and
meshed by the tests that use it, so a file that is merely parseable cannot pass
for geometry.
"""

from __future__ import annotations

from pathlib import Path

_HEADER = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('{description}'),'2;1');
FILE_NAME('{name}','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN {{ 1 0 10303 214 1 1 1 1 }}'));
ENDSEC;
DATA;
"""


class _Step:
    """An entity table that hands out its own record numbers."""

    def __init__(self) -> None:
        self._records: list[str] = []

    def add(self, text: str) -> int:
        self._records.append(text)
        return len(self._records)

    def render(self, *, name: str, description: str) -> str:
        body = "\n".join(f"#{index + 1} = {text};" for index, text in enumerate(self._records))
        return _HEADER.format(name=name, description=description) + body + "\nENDSEC;\nEND-ISO-10303-21;\n"

    # -- geometry -------------------------------------------------------
    def point(self, x: float, y: float, z: float) -> int:
        return self.add(f"CARTESIAN_POINT('',({x:.6f},{y:.6f},{z:.6f}))")

    def direction(self, x: float, y: float, z: float) -> int:
        return self.add(f"DIRECTION('',({x:.6f},{y:.6f},{z:.6f}))")

    def vertex(self, point: int) -> int:
        return self.add(f"VERTEX_POINT('',#{point})")

    def edge(self, start: int, end: int, origin: int, direction: int) -> int:
        vector = self.add(f"VECTOR('',#{direction},1.0)")
        line = self.add(f"LINE('',#{origin},#{vector})")
        return self.add(f"EDGE_CURVE('',#{start},#{end},#{line},.T.)")

    def face(self, edges: list[int], origin: int, normal: int, reference: int, name: str) -> int:
        oriented = [self.add(f"ORIENTED_EDGE('',*,*,#{tag},.T.)") for tag in edges]
        loop = self.add("EDGE_LOOP('',(" + ",".join(f"#{tag}" for tag in oriented) + "))")
        bound = self.add(f"FACE_OUTER_BOUND('',#{loop},.T.)")
        placement = self.add(f"AXIS2_PLACEMENT_3D('',#{origin},#{normal},#{reference})")
        plane = self.add(f"PLANE('',#{placement})")
        return self.add(f"ADVANCED_FACE('{name}',(#{bound}),#{plane},.T.)")

    def context(self) -> int:
        length = self.add("( NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) LENGTH_UNIT() )")
        angle = self.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
        solid = self.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
        tolerance = self.add(
            f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#{length},"
            "'distance_accuracy_value','')"
        )
        return self.add(
            "( GEOMETRIC_REPRESENTATION_CONTEXT(3) "
            f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{tolerance})) "
            f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{length},#{angle},#{solid})) "
            "REPRESENTATION_CONTEXT('',''))"
        )

    def product(self) -> int:
        application = self.add("APPLICATION_CONTEXT('automotive design')")
        self.add(
            "APPLICATION_PROTOCOL_DEFINITION('international standard',"
            f"'automotive_design',2000,#{application})"
        )
        product_context = self.add(f"PRODUCT_CONTEXT('',#{application},'mechanical')")
        product = self.add(f"PRODUCT('shell','shell','',(#{product_context}))")
        formation = self.add(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
        definition_context = self.add(
            f"PRODUCT_DEFINITION_CONTEXT('part definition',#{application},'design')"
        )
        definition = self.add(
            f"PRODUCT_DEFINITION('design','',#{formation},#{definition_context})"
        )
        return self.add(f"PRODUCT_DEFINITION_SHAPE('','',#{definition})")


def _fan(step: _Step, offset: float, faces: int, label: str) -> list[int]:
    """``faces`` planar quads hinged on one shared spine, like pages of a book.

    Every face reuses the **same** ``EDGE_CURVE`` and ``VERTEX_POINT`` records
    for the spine. That reuse is the whole point: OCC decides two faces are one
    shell because they share a topological edge, not because they happen to
    touch in space, and a fixture that merely put coincident edges side by side
    would import as N loose sheets and prove nothing.

    The pages are in different planes so no healing step can fold them into one
    face and quietly make the count right for the wrong reason.
    """

    import math

    spine_bottom = step.point(0.0, 0.0, offset)
    spine_top = step.point(0.0, 0.0, offset + 1.0)
    corner_bottom = step.vertex(spine_bottom)
    corner_top = step.vertex(spine_top)
    up = step.direction(0.0, 0.0, 1.0)
    down = step.direction(0.0, 0.0, -1.0)
    spine = step.edge(corner_bottom, corner_top, spine_bottom, up)

    made: list[int] = []
    for index in range(faces):
        angle = math.pi * (index + 1) / (faces + 1)
        dx, dy = math.cos(angle), math.sin(angle)
        outer_top = step.point(dx, dy, offset + 1.0)
        outer_bottom = step.point(dx, dy, offset)
        vertex_top = step.vertex(outer_top)
        vertex_bottom = step.vertex(outer_bottom)
        outward = step.direction(dx, dy, 0.0)
        inward = step.direction(-dx, -dy, 0.0)
        normal = step.direction(dy, -dx, 0.0)
        edges = [
            spine,
            step.edge(corner_top, vertex_top, spine_top, outward),
            step.edge(vertex_top, vertex_bottom, outer_top, down),
            step.edge(vertex_bottom, corner_bottom, outer_bottom, inward),
        ]
        made.append(step.face(edges, spine_bottom, normal, outward, f"{label}-{index}"))
    return made


def write_open_shell(path: Path, *, faces: int = 2) -> Path:
    """One ``SHELL_BASED_SURFACE_MODEL`` over one ``OPEN_SHELL`` of ``faces``."""

    step = _Step()
    shape = step.product()
    context = step.context()
    made = _fan(step, 0.0, faces, "face")
    shell = step.add("OPEN_SHELL('shell',(" + ",".join(f"#{tag}" for tag in made) + "))")
    model = step.add(f"SHELL_BASED_SURFACE_MODEL('surface-body',(#{shell}))")
    representation = step.add(
        f"MANIFOLD_SURFACE_SHAPE_REPRESENTATION('',(#{model}),#{context})"
    )
    step.add(f"SHAPE_DEFINITION_REPRESENTATION(#{shape},#{representation})")
    path.write_text(
        step.render(name=path.name, description=f"one open shell, {faces} faces"),
        encoding="ascii",
    )
    return path


def write_disjoint_shells(path: Path) -> Path:
    """Two surface bodies that share no edge -- an undeclared extra shell."""

    step = _Step()
    shape = step.product()
    context = step.context()
    models = []
    for index, offset in enumerate((0.0, 10.0)):
        made = _fan(step, offset, 2, f"body{index}")
        shell = step.add(
            f"OPEN_SHELL('shell{index}',(" + ",".join(f"#{tag}" for tag in made) + "))"
        )
        models.append(step.add(f"SHELL_BASED_SURFACE_MODEL('surface-body-{index}',(#{shell}))"))
    representation = step.add(
        "MANIFOLD_SURFACE_SHAPE_REPRESENTATION('',("
        + ",".join(f"#{tag}" for tag in models)
        + f"),#{context})"
    )
    step.add(f"SHAPE_DEFINITION_REPRESENTATION(#{shape},#{representation})")
    path.write_text(
        step.render(name=path.name, description="two disjoint open shells"), encoding="ascii"
    )
    return path
