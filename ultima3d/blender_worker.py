"""Blender-side worker for ultima3d.

Run by the MCP server as:

    blender.exe --background --factory-startup --python ultima3d/blender_worker.py

Protocol: one JSON request per line on stdin, one JSON response per line on
stdout. Blender logs go to stderr. A single worker instance serves many
requests so the scene (and its saved recipe/blueprint state) persists across
tool calls.

Every request has the shape {"id": ..., "op": ..., "params": {...}} and every
response {"id": ..., "ok": true/false, "result"/"error"}.
"""

import json
import math
import os
import random
import sys
import traceback

import bpy
import bmesh
import mathutils
from mathutils import Vector, Euler

# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

STATE = {
    "asset_name": None,
    "manifest": None,      # AssetState: per-node object registration
    "recipe": None,        # parametric recipe: {node_name: {builder, params}}
    "blueprint": None,     # hierarchical design document
    "last_render_dir": None,
}

ASSET_COLLECTION = "ASSET"
CUTTER_COLLECTION = "ASSET_CUTTERS"

# Detail profiles: the LLM specifies intent; these resolve to Blender density.
DETAIL_PROFILES = {
    "draft": {"segments": 8, "bevel_segments": 1, "sphere_segments": 12, "lathe_segments": 12},
    "game":  {"segments": 16, "bevel_segments": 2, "sphere_segments": 20, "lathe_segments": 16},
    "hero":  {"segments": 32, "bevel_segments": 3, "sphere_segments": 32, "lathe_segments": 32},
}

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _col(name=ASSET_COLLECTION, make=False):
    c = bpy.data.collections.get(name)
    if c is None and make:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
    return c


def _clear_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for block in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.images):
        for x in list(block):
            if x.users == 0:
                block.remove(x)
    STATE["asset_name"] = None
    STATE["recipe"] = None
    STATE["blueprint"] = None
    STATE["manifest"] = None


def _asset_objects():
    c = _col()
    return list(c.objects) if c else []


def _asset_meshes():
    """Mesh objects only; armatures/lights/etc. excluded."""
    return [o for o in _asset_objects() if o.type == "MESH"]


def _obj_by_name(name):
    ob = bpy.data.objects.get(name)
    if ob is None:
        raise KeyError(f"no object named {name!r}")
    return ob


def _ensure_material(ob, mat_name):
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (0.55, 0.5, 0.45, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.6
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    return mat


def _set_principled(mat, base_color=None, roughness=None, metallic=None, emission=None):
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if not bsdf:
        return mat
    if base_color:
        bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
    if roughness is not None:
        bsdf.inputs["Roughness"].default_value = float(roughness)
    if metallic is not None:
        try:
            bsdf.inputs["Metallic"].default_value = float(metallic)
        except KeyError:
            pass
    if emission:
        try:
            bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
            bsdf.inputs["Emission Strength"].default_value = 1.0
        except KeyError:
            pass
    return mat


def _simple_mat(name, base_color, roughness=0.6, metallic=0.0):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    return _set_principled(mat, base_color, roughness, metallic)


def _bevel_mod(ob, width, segments=2):
    m = ob.modifiers.new("Bevel", "BEVEL")
    m.width = float(width)
    m.segments = int(segments)
    m.limit_method = "ANGLE"
    m.angle_limit = math.radians(40)
    return m


def _solidify(ob, thickness):
    m = ob.modifiers.new("Solidify", "SOLIDIFY")
    m.thickness = float(thickness)
    return m


def _array_mod(ob, count, offset):
    m = ob.modifiers.new("Array", "ARRAY")
    m.count = int(count)
    m.use_relative_offset = False
    m.use_constant_offset = True
    m.constant_offset_displace = offset
    return m


def _shade_smooth_auto(ob, angle_deg=35):
    if hasattr(ob.data, "use_auto_smooth"):  # <=4.0
        ob.data.use_auto_smooth = True
        ob.data.auto_smooth_angle = math.radians(angle_deg)
        for p in ob.data.polygons:
            p.use_smooth = True
    else:  # 4.1+: smooth by angle modifier
        with bpy.context.temp_override(object=ob):
            bpy.ops.object.shade_smooth()
        try:
            m = ob.modifiers.new("Smooth by Angle", "NODES")
            ng = bpy.data.node_groups.get("Smooth by Angle")
            if ng:
                m.node_group = ng
        except Exception:
            pass


def _tri_count(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    n = sum(max(0, len(p.vertices) - 2) for p in me.polygons)
    ev.to_mesh_clear()
    return n


GEOMETRY_MODIFIERS = ("BEVEL", "SOLIDIFY", "ARRAY", "BOOLEAN", "SUBSURF", "DISPLACE")
# Tolerance shared by the vertex-merge pass and validate's duplicate-vertex check, so the
# assembled asset cannot satisfy one and fail the other.
DOUBLE_DIST = 1e-6


def _apply_geometry_modifiers(ob):
    """Bake stacking modifiers into the mesh so they survive join(); shading nodes are left alone."""
    if ob.type != "MESH" or not ob.modifiers:
        return
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    for m in list(ob.modifiers):
        if m.type in GEOMETRY_MODIFIERS:
            try:
                bpy.ops.object.modifier_apply(modifier=m.name)
            except Exception:
                ob.modifiers.remove(m)


def _convert_to_mesh(ob):
    """Turn a curve/text object into a mesh in place.

    Every downstream stage (manifest diffing, inspect, validate, finalize) walks
    _asset_meshes(), which is MESH-only, so curve-authored builders must be
    converted or they are invisible to the rest of the pipeline.
    """
    if ob.type == "MESH":
        return ob
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.convert(target="MESH")
    return bpy.context.view_layer.objects.active


def _merge_doubles(ob, dist=DOUBLE_DIST):
    """Weld vertices closer than dist so parts that merely touch are not duplicate geometry."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


# --------------------------------------------------------------------------
# Geometry compiler: deterministic low-level constructors.
# Each returns the created object, parented into the ASSET collection.
# --------------------------------------------------------------------------


def _asset_children():
    """Names of mesh objects currently in the ASSET collection (manifest diffing)."""
    return {o.name for o in _asset_meshes()}


def _register_node(manifest, node, before, mat_spec):
    """Record objects created by a node and apply its material to all of them."""
    created = sorted(_asset_meshes_names() - before)
    manifest["objects"][node] = created
    if mat_spec and created:
        for name in created:
            ob = _obj_by_name(name)
            _ensure_material(ob, mat_spec["name"])
            _set_principled(ob.data.materials[0], mat_spec.get("color"), mat_spec.get("roughness"),
                            mat_spec.get("metallic"), mat_spec.get("emission"))
    return created


def _asset_meshes_names():
    return {o.name for o in _asset_meshes()}


def _resolve_detail(builder, kwargs):
    """Apply the detail profile to density params the builder actually accepts.

    An explicit density param always wins over the profile value.
    """
    detail = kwargs.pop("detail", None)
    if not detail:
        return kwargs
    profile = DETAIL_PROFILES.get(detail)
    if not profile:
        raise ValueError(f"unknown detail {detail!r}; use one of {sorted(DETAIL_PROFILES)}")
    import inspect as _inspect
    params = _inspect.signature(builder).parameters
    kw = dict(kwargs)
    # builder knob -> profile key
    knobs = {"vertices": "segments", "segments": "segments",
             "ring_count": "sphere_segments", "bevel_segments": "bevel_segments"}
    for knob, pkey in knobs.items():
        if knob in params and pkey in profile and knob not in kw:
            kw[knob] = profile[pkey]
    return kw


def _place(ob, name, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1)):
    ob.name = name
    ob.location = location
    ob.rotation_euler = rotation
    ob.scale = scale
    c = _col(make=True)
    for uc in list(ob.users_collection):
        uc.objects.unlink(ob)
    c.objects.link(ob)
    return ob


def create_box(name, size=(1, 1, 1), location=(0, 0, 0), rotation=(0, 0, 0), bevel=0.0, **_):
    bpy.ops.mesh.primitive_cube_add()
    ob = bpy.context.active_object
    ob.dimensions = size
    bpy.ops.object.transform_apply(scale=True)
    if bevel:
        _bevel_mod(ob, bevel)
    return _place(ob, name, location, rotation)


def create_rounded_box(name, size=(1, 1, 1), radius=0.05, segments=3, location=(0, 0, 0), rotation=(0, 0, 0), **_):
    ob = create_box(name, size, location, rotation)
    _bevel_mod(ob, min(radius, min(size) * 0.45), segments)
    return ob


def create_cylinder(name, radius=0.5, depth=1.0, vertices=24, location=(0, 0, 0), rotation=(0, 0, 0), **_):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth)
    return _place(bpy.context.active_object, name, location, rotation)


def create_tapered_cylinder(name, r_top=0.3, r_bottom=0.5, depth=1.0, vertices=24,
                            location=(0, 0, 0), rotation=(0, 0, 0), **_):
    bpy.ops.mesh.primitive_cone_add(vertices=vertices, radius1=r_bottom, radius2=r_top, depth=depth)
    return _place(bpy.context.active_object, name, location, rotation)


def create_sphere(name, radius=0.5, segments=24, location=(0, 0, 0), **_):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=max(8, segments // 2), radius=radius)
    ob = _place(bpy.context.active_object, name, location)
    _shade_smooth_auto(ob)
    return ob


def create_torus(name, major_radius=0.5, minor_radius=0.1, location=(0, 0, 0), rotation=(0, 0, 0), **_):
    bpy.ops.mesh.primitive_torus_add(major_radius=major_radius, minor_radius=minor_radius)
    ob = _place(bpy.context.active_object, name, location, rotation)
    _shade_smooth_auto(ob)
    return ob


def create_arch(name, radius=0.5, thickness=0.1, depth=0.3, span_deg=180, segments=16,
                location=(0, 0, 0), rotation=(0, 0, 0), **_):
    """Curved slab: a swept arc extruded to depth. Good for roofs/handles."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    prev = None
    verts_top, verts_bot = [], []
    for i in range(segments + 1):
        a = math.radians(-span_deg / 2 + span_deg * i / segments)
        p_out = Vector((math.cos(a) * (radius + thickness / 2), math.sin(a) * (radius + thickness / 2), 0))
        p_in = Vector((math.cos(a) * (radius - thickness / 2), math.sin(a) * (radius - thickness / 2), 0))
        v_out, v_in = bm.verts.new(p_out), bm.verts.new(p_in)
        verts_top.append(v_out)
        verts_bot.append(v_in)
    for i in range(segments):
        bm.faces.new((verts_bot[i], verts_bot[i + 1], verts_top[i + 1], verts_top[i]))
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    _solidify(ob, depth)  # solidify along local normal gives depth
    ob.rotation_euler = Euler((math.radians(90), 0, 0))  # arc stands upright
    if rotation:
        ob.rotation_euler = Euler(rotation)
    return _place(ob, name, location)


def create_panel(name, size=(1, 1), thickness=0.05, location=(0, 0, 0), rotation=(0, 0, 0), **_):
    bpy.ops.mesh.primitive_plane_add(size=1)
    ob = bpy.context.active_object
    ob.dimensions = (size[0], size[1], 0)
    bpy.ops.object.transform_apply(scale=True)
    _solidify(ob, thickness)
    return _place(ob, name, location, rotation)


def create_pipe(name, path, radius=0.05, segments=12, location=(0, 0, 0), **_):
    """path: list of [x,y,z] waypoints; polygonal sweep along them."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(len(path) - 1)
    for i, p in enumerate(path):
        sp.points[i].co = (*p, 1.0)
    cu.bevel_depth = float(radius)
    cu.bevel_resolution = max(1, segments // 4)
    if hasattr(cu, "use_uv_as_generated"):
        cu.use_uv_as_generated = True
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    # A swept curve only lives in the CURVE namespace; convert it so the sweep is a
    # real mesh and downstream ops (inspect, validate, finalize, join) can see it.
    ob = _convert_to_mesh(ob)
    _shade_smooth_auto(ob)
    return _place(ob, name, location)


def create_curve(name, points, location=(0, 0, 0), **_):
    return create_pipe(name, points, radius=0.01, location=location)


def create_extrusion(name, profile2d, depth=0.5, location=(0, 0, 0), rotation=(0, 0, 0), **_):
    """profile2d: list of [x,y] points forming a closed polygon, extruded in Z."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    vs = [bm.verts.new((p[0], p[1], 0)) for p in profile2d]
    bm.faces.new(vs)
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    _solidify(ob, depth)
    return _place(ob, name, location, rotation)


def create_lathe(name, profile2d, segments=24, location=(0, 0, 0), **_):
    """profile2d: list of [radius, z] points revolved around the Z axis."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    rings = []
    for r, z in profile2d:
        ring = [bm.verts.new((math.cos(2 * math.pi * i / segments) * r,
                              math.sin(2 * math.pi * i / segments) * r, z))
                for i in range(segments)]
        rings.append(ring)
    for i in range(len(rings) - 1):
        for j in range(segments):
            k = (j + 1) % segments
            bm.faces.new((rings[i][j], rings[i][k], rings[i + 1][k], rings[i + 1][j]))
    # close the ends: n-gon cap for off-axis rings, fan to a pole for near-zero radius
    def _cap(ring, r, z, at_start):
        if r > 1e-6:
            f = bm.faces.new(ring)
            f.normal_update()
        else:
            pole = bm.verts.new((0, 0, z))
            for j in range(segments):
                k = (j + 1) % segments
                pair = (ring[k], ring[j]) if at_start else (ring[j], ring[k])
                bm.faces.new((*pair, pole))
    if profile2d:
        _cap(rings[0], profile2d[0][0], profile2d[0][1], True)
        _cap(rings[-1], profile2d[-1][0], profile2d[-1][1], False)
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    _shade_smooth_auto(ob)
    return _place(ob, name, location)


def create_boolean_cut(name, target, cutter, operation="DIFFERENCE", solver="EXACT", **_):
    """Boolean 'target' by 'cutter'; the cutter is parked outside the asset so it is never joined."""
    t = _obj_by_name(target) if isinstance(target, str) else target
    c = _obj_by_name(cutter) if isinstance(cutter, str) else cutter
    m = t.modifiers.new("Boolean", "BOOLEAN")
    m.operation = operation
    m.object = c
    m.solver = solver if solver in ("FLOAT", "EXACT", "MANIFOLD") else "EXACT"
    cc = _col(CUTTER_COLLECTION, make=True)
    for uc in list(c.users_collection):
        uc.objects.unlink(c)
    cc.objects.link(c)
    c.hide_viewport = True
    c.hide_render = True
    bpy.context.view_layer.update()
    return t


def create_array(name, source, count=3, offset=(0, 0.5, 0), **_):
    return _array_mod(_obj_by_name(source) if isinstance(source, str) else source, count, offset)


def create_radial_array(name, source, count=6, radius=1.0, axis="Z", **_):
    ob = _obj_by_name(source) if isinstance(source, str) else source
    m = ob.modifiers.new("RadialArray", "ARRAY")
    m.count = int(count)
    m.use_relative_offset = False
    m.use_object_offset = True
    # offset object trick: create an empty rotated around origin
    emp = bpy.data.objects.new(name + "_offset", None)
    bpy.context.scene.collection.objects.link(emp)
    emp.rotation_euler = (0, 0, math.radians(360.0 / max(1, count)))
    if axis == "X":
        emp.rotation_euler = (math.radians(360.0 / max(1, count)), 0, 0)
    elif axis == "Y":
        emp.rotation_euler = (0, math.radians(360.0 / max(1, count)), 0)
    m.offset_object = emp
    ob.location = (radius, 0, 0) if axis == "Z" else ob.location
    return ob


def create_beam(name, start, end, thickness=0.1, **_):
    s, e = Vector(start), Vector(end)
    mid = (s + e) / 2
    d = e - s
    ob = create_cylinder(name, radius=thickness / 2, depth=d.length, vertices=12)
    ob.location = mid
    ob.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    return ob


def create_rock(name, size=(1, 1, 1), detail=2, location=(0, 0, 0), seed=0, **_):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.5)
    ob = bpy.context.active_object
    ob.dimensions = size
    bpy.ops.object.transform_apply(scale=True)
    # Displace has no seed in 5.2, so the per-seed variation is baked into the vertices
    rng = random.Random(seed)
    amount = min(size) * 0.12
    for v in ob.data.vertices:
        v.co += Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))) * amount
    m = ob.modifiers.new("Displace", "DISPLACE")
    tex = bpy.data.textures.new(name + "_noise", "CLOUDS")
    tex.noise_scale = min(size) * 0.5
    m.texture = tex
    m.strength = min(size) * 0.25
    m.mid_level = 0.5
    sub = ob.modifiers.new("Subsurf", "SUBSURF")
    sub.levels = int(detail)
    sub.render_levels = int(detail)
    _shade_smooth_auto(ob)
    return _place(ob, name, location)


# --------------------------------------------------------------------------
# Higher-level builders (compose the primitives above)
# --------------------------------------------------------------------------


def build_barrel(name, radius=0.4, height=1.0, hoops=3, **kw):
    body = create_lathe(name, [
        [radius * 0.85, 0], [radius, height * 0.25], [radius, height * 0.75], [radius * 0.85, height],
    ], segments=24, location=kw.get("location", (0, 0, 0)))
    for i in range(hoops):
        z = height * (i + 1) / (hoops + 1)
        create_torus(f"{name}_hoop_{i}", major_radius=radius + 0.01, minor_radius=0.015,
                     location=(kw.get("location", (0, 0, 0))[0], kw.get("location", (0, 0, 0))[1], z),
                     rotation=(math.radians(90), 0, 0))
    return body


def build_crate(name, size=(0.8, 0.8, 0.8), plank_w=0.12, **kw):
    loc = kw.get("location", (0, 0, 0))
    w, d, h = size
    body = create_box(name, size, loc, bevel=0.005)
    n = max(2, int(h / plank_w))
    for i in range(1, n):
        z = h * i / n
        create_box(f"{name}_slat_{i}", (w * 1.02, d * 1.02, h * 0.04), (loc[0], loc[1], loc[2] - h / 2 + z))
    return body


def build_roof(name, width=1.0, depth=0.8, height=0.4, overhang=0.05, **kw):
    loc = kw.get("location", (0, 0, 0))
    profile = [[-width / 2 - overhang, 0], [0, height], [width / 2 + overhang, 0]]
    ob = create_extrusion(name, profile, depth=depth + overhang * 2, location=loc)
    ob.rotation_euler = Euler((math.radians(90), 0, 0))
    # Solidify extrudes along +Z, which the 90-degree X rotation maps to -Y, so centring the
    # ridge over the body needs a positive offset of half the extruded depth.
    ob.location = (loc[0], loc[1] + (depth + overhang * 2) / 2, loc[2])
    return ob


def build_window(name, width=1.0, height=1.0, frame=0.06, **kw):
    loc = kw.get("location", (0, 0, 0))
    f = create_box(name, (width, 0.1, height), loc)
    create_boolean_cut(name + "_cut", f, create_box(name + "_hole", (width - frame * 2, 0.3, height - frame * 2), loc))
    create_box(f"{name}_mullion_v", (frame, 0.12, height - frame * 2), loc)
    create_box(f"{name}_mullion_h", (width - frame * 2, 0.12, frame), loc)
    return f


def build_door(name, width=0.9, height=2.0, thickness=0.06, **kw):
    loc = kw.get("location", (0, 0, 0))
    d = create_rounded_box(name, (width, thickness, height), radius=0.02, location=loc)
    create_panel(f"{name}_panel", (width * 0.6, height * 0.5), thickness * 0.5,
                 (loc[0], loc[1] - thickness * 0.35, loc[2] + height * 0.15))
    return d


def build_stairs(name, width=1.0, steps=8, step_h=0.18, step_d=0.28, **kw):
    loc = kw.get("location", (0, 0, 0))
    base = create_box(name, (width, step_d * steps, step_h), (loc[0], loc[1] + step_d * steps / 2, loc[2]))
    for i in range(1, steps):
        create_box(f"{name}_step_{i}", (width, step_d, step_h * (i + 1)),
                   (loc[0], loc[1] + step_d * (i + 0.5), loc[2] - step_h * i / 2 + 0))
        # stack: each step taller
    return base


def build_column(name, radius=0.25, height=3.0, **kw):
    loc = kw.get("location", (0, 0, 0))
    create_box(f"{name}_base", (radius * 2.6, radius * 2.6, height * 0.06), (loc[0], loc[1], loc[2] + height * 0.03))
    c = create_cylinder(name, radius, height * 0.88, 20, (loc[0], loc[1], loc[2] + height * 0.5))
    create_box(f"{name}_cap", (radius * 2.6, radius * 2.6, height * 0.06), (loc[0], loc[1], loc[2] + height * 0.94))
    return c


def build_wall(name, length=4.0, height=2.5, thickness=0.2, **kw):
    return create_box(name, (length, thickness, height), kw.get("location", (0, 0, 0)))


def build_fence(name, length=4.0, height=1.0, posts=5, **kw):
    loc = kw.get("location", (0, 0, 0))
    for i in range(posts):
        x = loc[0] - length / 2 + length * i / max(1, posts - 1)
        create_box(f"{name}_post_{i}", (0.08, 0.08, height), (x, loc[1], loc[2] + height / 2))
    create_box(f"{name}_rail", (length, 0.05, 0.08), (loc[0], loc[1], loc[2] + height * 0.6))
    create_box(f"{name}_rail_2", (length, 0.05, 0.08), (loc[0], loc[1], loc[2] + height * 0.3))
    return _obj_by_name(f"{name}_rail")


def build_tree(name, height=4.0, trunk_ratio=0.4, **kw):
    loc = kw.get("location", (0, 0, 0))
    th = height * trunk_ratio
    create_tapered_cylinder(f"{name}_trunk", r_top=th * 0.12, r_bottom=th * 0.2, depth=th,
                            vertices=10, location=(loc[0], loc[1], loc[2] + th / 2))
    for i in range(3):
        s = 1.0 - i * 0.25
        create_sphere(f"{name}_foliage_{i}", radius=height * 0.22 * s,
                      location=(loc[0] + (i - 1) * 0.2, loc[1] + (i % 2) * 0.2, th + height * 0.18 * (i + 1)))
    return _obj_by_name(f"{name}_foliage_1")


def build_weapon_blade(name, length=1.0, width=0.12, thickness=0.02, **kw):
    loc = kw.get("location", (0, 0, 0))
    profile = [[-width / 2, 0], [-width / 4, length * 0.9], [0, length], [width / 4, length * 0.9], [width / 2, 0]]
    ob = create_extrusion(name, profile, depth=thickness, location=loc)
    ob.rotation_euler = Euler((math.radians(90), 0, 0))
    return ob


def build_weapon_handle(name, length=0.3, radius=0.03, **kw):
    return create_cylinder(name, radius, length, 12, kw.get("location", (0, 0, 0)))


# Registry used by the recipe compiler
BUILDERS = {
    "box": create_box, "rounded_box": create_rounded_box, "cylinder": create_cylinder,
    "tapered_cylinder": create_tapered_cylinder, "sphere": create_sphere, "torus": create_torus,
    "arch": create_arch, "panel": create_panel, "pipe": create_pipe, "curve": create_curve,
    "extrusion": create_extrusion, "lathe": create_lathe, "rock": create_rock, "beam": create_beam,
    "barrel": build_barrel, "crate": build_crate, "roof": build_roof, "window": build_window,
    "door": build_door, "stairs": build_stairs, "column": build_column, "wall": build_wall,
    "fence": build_fence, "tree": build_tree, "weapon_blade": build_weapon_blade,
    "weapon_handle": build_weapon_handle,
}


# --------------------------------------------------------------------------
# Pipeline ops
# --------------------------------------------------------------------------


def op_build(params):
    """Compile a recipe {node_name: {builder, params...}} into a Blender scene."""
    recipe = params["recipe"]
    blueprint = params.get("blueprint")
    if params.get("fresh", True):
        _clear_scene()
    STATE["blueprint"] = blueprint
    manifest = {"objects": {}, "materials": [], "root_collection": ASSET_COLLECTION}
    node_tris = {}
    for node_name, spec in recipe.items():
        builder = BUILDERS.get(spec.get("builder"))
        if not builder:
            raise ValueError(f"unknown builder {spec.get('builder')!r} for node {node_name!r}")
        kwargs = {k: v for k, v in spec.items() if k not in ("builder", "material")}
        kwargs = _resolve_detail(builder, kwargs)
        before = _asset_meshes_names()
        ob = builder(node_name, **kwargs)
        created = _register_node(manifest, node_name, before, spec.get("material"))
        if spec.get("material") and spec["material"]["name"] not in manifest["materials"]:
            manifest["materials"].append(spec["material"]["name"])
        if spec.get("triangle_budget"):
            t = sum(_tri_count(_obj_by_name(n)) for n in created)
            node_tris[node_name] = {"budget": spec["triangle_budget"], "triangles": t,
                                    "over_budget": t > spec["triangle_budget"], "objects": created}
    STATE["asset_name"] = params.get("name", STATE["asset_name"] or "asset")
    STATE["recipe"] = recipe
    STATE["manifest"] = manifest
    _join_and_normalize(params.get("join", True))
    return {"created": [n for objs in manifest["objects"].values() for n in objs],
            "manifest": {k: manifest[k] for k in ("objects", "materials", "root_collection")},
            "node_triangles": node_tris,
            "object_count": len(_asset_objects()),
            "triangles": sum(_tri_count(o) for o in _asset_objects())}


def _join_and_normalize(join):
    obs = _asset_meshes()
    # join() discards modifiers on every object but the active one, which would flatten
    # extrusions, arches, roofs and boolean cuts into sheets. Bake them first.
    for o in obs:
        _apply_geometry_modifiers(o)
    obs = _asset_meshes()
    if len(obs) > 1 and join:
        bpy.ops.object.select_all(action="DESELECT")
        for o in obs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = obs[0]
        bpy.ops.object.join()
    obs = _asset_meshes()
    if obs:
        main = obs[0]
        main.name = STATE["asset_name"] or "asset"
        bpy.ops.object.select_all(action="DESELECT")
        main.select_set(True)
        bpy.context.view_layer.objects.active = main
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        # sit on ground: min z at 0
        bbox = [main.matrix_world @ Vector(c) for c in main.bound_box]
        minz = min(v.z for v in bbox)
        main.location.z -= minz
    # Normalize the assembled asset so validate holds for every builder, not just the
    # primitive-based ones: composites stack boxes that touch face-to-face (stairs), and
    # bmesh-authored meshes arrive with no UV layer at all.
    for o in _asset_meshes():
        _merge_doubles(o)
        if not o.data.uv_layers:
            _uv_unwrap(o)


def op_set_material(params):
    targets = ([_obj_by_name(params["object"])] if params.get("object")
               else _asset_meshes())
    if not targets:
        raise ValueError("nothing to material")
    mat = _simple_mat(params["name"], params.get("color", (0.6, 0.6, 0.6)),
                      params.get("roughness", 0.6), params.get("metallic", 0.0))
    for ob in targets:
        ob.data.materials.clear()
        ob.data.materials.append(mat)
    return {"material": mat.name, "objects": [ob.name for ob in targets]}


def op_inspect(params):
    obs = _asset_meshes()
    if not obs:
        raise ValueError("no asset in scene — call build first")
    dg = bpy.context.evaluated_depsgraph_get()
    tris = 0
    verts = 0
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    nonmanifold = 0
    for o in obs:
        tris += _tri_count(o)
        verts += len(o.data.vertices)
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
        bm = bmesh.new()
        bm.from_mesh(o.data)
        nonmanifold += sum(1 for e in bm.edges if not e.is_manifold)
        bm.free()
    dims = [hi[i] - lo[i] for i in range(3)]
    main = obs[0]
    return {
        "name": STATE["asset_name"] or main.name,
        "root_collection": ASSET_COLLECTION,
        "objects": [o.name for o in obs],
        "object_count": len(obs),
        "triangles": tris,
        "vertices": verts,
        "bounds_min": [round(v, 4) for v in lo],
        "bounds_max": [round(v, 4) for v in hi],
        "dimensions": [round(d, 4) for d in dims],
        "materials": sorted({s.name for o in obs for s in o.data.materials if s}),
        "non_manifold_edges": nonmanifold,
        "has_uv": all(bool(o.data.uv_layers) for o in obs),
        "manifest": STATE["manifest"],
        "modifiers": {o.name: [(m.name, m.type) for m in o.modifiers] for o in obs},
        "recipe": STATE["recipe"],
        "blueprint": STATE["blueprint"],
    }


# ---- rendering ------------------------------------------------------------

VIEW_ANGLES = {
    "front": (90, 0), "back": (90, 180), "left": (90, -90), "right": (90, 90),
    "front_34": (60, 45), "back_34": (60, 135), "top": (10, 0), "wire": (60, 45),
}


def _setup_render(res=512):
    sc = bpy.context.scene
    engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    sc.render.resolution_x = res
    sc.render.resolution_y = res
    sc.render.film_transparent = False
    sc.world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    sc.world.use_nodes = True
    bg = sc.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.85, 0.85, 0.88, 1.0)
        bg.inputs[1].default_value = 1.0
    # sun light
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        bpy.ops.object.light_add(type="SUN", location=(3, -3, 6))
        sun = bpy.context.active_object
        sun.data.energy = 3.0
        sun.rotation_euler = Euler((math.radians(50), 0, math.radians(30)))


def _fit_camera(dist_factor=2.2):
    obs = _asset_objects()
    main = obs[0]
    bpy.ops.object.select_all(action="DESELECT")
    main.select_set(True)
    bpy.context.view_layer.objects.active = main
    bbox = [main.matrix_world @ Vector(c) for c in main.bound_box]
    center = sum(bbox, Vector()) / 8
    radius = max((v - center).length for v in bbox) or 1.0
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    return cam, center, radius


def op_render_views(params):
    views = params.get("views") or list(VIEW_ANGLES.keys())
    out_dir = os.path.abspath(params.get("dir") or os.path.join(bpy.app.tempdir or "/tmp", "ultima3d_renders"))
    os.makedirs(out_dir, exist_ok=True)
    res = int(params.get("resolution", 512))
    _setup_render(res)
    cam, center, radius = _fit_camera()
    sc = bpy.context.scene
    paths = {}
    for v in views:
        polar, azim = VIEW_ANGLES[v]
        theta = math.radians(polar)
        phi = math.radians(azim)
        d = radius * 2.4 + 0.5
        # spherical placement (z-up)
        cam.location = center + Vector((d * math.sin(theta) * math.cos(phi),
                                        d * math.sin(theta) * math.sin(phi),
                                        d * math.cos(theta)))
        direction = center - cam.location
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        sc.render.filepath = os.path.join(out_dir, f"{v}.png")
        if v == "wire":
            sc.render.engine = "BLENDER_WORKBENCH"
            sc.display.shading.light = "STUDIO"
            sc.display.shading.color_type = "MATERIAL"
        else:
            _setup_render(res)
        bpy.ops.render.render(write_still=True)
        paths[v] = sc.render.filepath
    STATE["last_render_dir"] = out_dir
    return {"renders": paths, "object_count": len(_asset_objects())}


# ---- validation -----------------------------------------------------------


def op_validate(params):
    obs = _asset_meshes()
    if not obs:
        raise ValueError("nothing to validate")
    budget = int(params.get("triangle_budget", 8000))
    tris = sum(_tri_count(o) for o in obs)
    checks = []
    ok = True

    def check(name, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        checks.append({"check": name, "pass": bool(passed), "detail": detail})

    dup = zero_area = nonmanifold = 0
    for o in obs:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        dup_result = bmesh.ops.find_doubles(bm, verts=bm.verts, dist=DOUBLE_DIST) if bm.verts else {}
        dup += len(dup_result.get("targetmap", {}))
        zero_area += sum(1 for f in bm.faces if f.calc_area() < 1e-9)
        nonmanifold += sum(1 for e in bm.edges if not e.is_manifold)
        bm.free()

    check("no_duplicate_vertices", dup == 0, f"{dup} doubles")
    check("no_zero_area_faces", zero_area == 0, f"{zero_area} zero-area faces")
    check("triangle_budget", tris <= budget, f"{tris}/{budget}")
    check("has_uv", all(bool(o.data.uv_layers) for o in obs), "UV layer on every mesh")
    check("has_material", all(any(o.data.materials) for o in obs), "every mesh has a material slot")
    check("scale_applied", all(all(abs(s - 1.0) < 1e-4 for s in o.scale) for o in obs),
          "all object scales applied")
    check("non_manifold_edges", nonmanifold == 0 or params.get("allow_non_manifold", True),
          f"{nonmanifold} non-manifold edges (ok for stylized hard-surface)")

    return {"pass": ok, "checks": checks, "triangles": tris, "budget": budget,
            "object_count": len(obs)}


# ---- finalize: clean, UV, LOD, collision, export ---------------------------


def _clean_mesh(ob):
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.modifier_apply(modifier="Bevel") if any(m.type == "BEVEL" for m in ob.modifiers) else None
    for m in list(ob.modifiers):
        if m.type in ("BEVEL", "SOLIDIFY", "ARRAY", "BOOLEAN", "SUBSURF", "DISPLACE", "NODES"):
            try:
                bpy.ops.object.modifier_apply(modifier=m.name)
            except Exception:
                ob.modifiers.remove(m)
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.remove_doubles(threshold=1e-5)
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.mesh.tris_convert_to_quads()
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass


def _uv_unwrap(ob, angle=66):
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    if not ob.data.uv_layers:
        ob.data.uv_layers.new(name="UVMap")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(angle), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


def _bake_textures(ob, res, channels, out_dir, prefix=None):
    """Bake PBR channels via bake-image nodes into shared per-channel PNGs in out_dir."""
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 16
    sc.render.bake.margin = 8
    sc.render.bake.use_selected_to_active = False
    try:  # bake device lives under preferences in some Blender versions
        prefs = bpy.context.preferences.addons.get("cycles")
        if prefs:
            prefs.preferences.compute_device_type = "NONE"
    except Exception:
        pass

    slots = [(i, s.material) for i, s in enumerate(ob.material_slots) if s.material]
    if not slots:
        return {"error": "no materials to bake"}

    # temporary metallic->emission values for the metallic channel (no Cycles pass exists)
    saved_emission = []
    metallic = ch_metallic = None
    if "metallic" in channels:
        for _, mat in slots:
            bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.use_nodes else None
            m = 0.0
            if bsdf and "Metallic" in bsdf.inputs:
                try:
                    m = bsdf.inputs["Metallic"].default_value
                except Exception:
                    m = 0.0
            saved_emission.append((mat,
                                   bsdf.inputs["Emission Color"].default_value[:] if bsdf and "Emission Color" in bsdf.inputs else None,
                                   bsdf.inputs["Emission Strength"].default_value if bsdf and "Emission Strength" in bsdf.inputs else None,
                                   bsdf))
            if bsdf and "Emission Color" in bsdf.inputs:
                bsdf.inputs["Emission Color"].default_value = (m, m, m, 1.0)
                bsdf.inputs["Emission Strength"].default_value = 1.0

    bake_imgs = {}
    bake_nodes = []
    out = {}
    try:
        for ch in channels:
            img = bpy.data.images.new(f"{ob.name}_bake_{ch}_{id(out)}", res, res,
                                      alpha=False, float_buffer=(ch == "normal"))
            bake_imgs[ch] = img
            for i, mat in slots:
                if not mat.use_nodes:
                    continue
                node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                node.image = img
                if ch == "normal":
                    node.image.colorspace_settings.name = "Non-Color"
                mat.node_tree.nodes.active = node
                bake_nodes.append((mat, node))

            if ch == "albedo":
                sc.cycles.bake_type = "DIFFUSE"
                sc.render.bake.use_pass_direct = False
                sc.render.bake.use_pass_indirect = False
                sc.render.bake.use_pass_color = True
            elif ch == "metallic":
                sc.cycles.bake_type = "EMIT"
            else:
                sc.cycles.bake_type = {"normal": "NORMAL", "roughness": "ROUGHNESS"}[ch]

            bpy.ops.object.select_all(action="DESELECT")
            ob.select_set(True)
            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.bake(type=sc.cycles.bake_type, use_clear=True)
            if ch == "albedo":
                sc.render.bake.use_pass_color = False

            path = os.path.join(out_dir, f"{prefix or ob.name}_{ch}.png")
            img.filepath_raw = path
            img.file_format = "PNG"
            img.save()
            out[ch] = path
    finally:
        for mat, node in bake_nodes:
            mat.node_tree.nodes.remove(node)
        for img in bake_imgs.values():
            bpy.data.images.remove(img)
        for mat, em_col, em_str, bsdf in saved_emission:
            if bsdf and em_col is not None:
                bsdf.inputs["Emission Color"].default_value = em_col
                bsdf.inputs["Emission Strength"].default_value = em_str or 0.0
    return out


def _export_glb(ob, path):
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                              export_yup=True, export_apply=True)


def op_finalize(params):
    obs = _asset_meshes()
    if not obs:
        raise ValueError("nothing to finalize")
    name = params.get("name") or (STATE["asset_name"] or obs[0].name)
    budget = int(params.get("triangle_budget", 8000))
    lods = params.get("lods")  # list of budgets; None -> none
    resolution = int(params.get("texture_resolution", 1024))
    channels = params.get("channels", ["albedo", "normal", "roughness"])
    with_collision = params.get("collision", "auto") == "auto"
    out_dir = os.path.abspath(params.get("dir") or os.path.join(bpy.app.tempdir or "/tmp", "ultima3d_export", name))
    os.makedirs(out_dir, exist_ok=True)

    for ob in obs:
        _clean_mesh(ob)
        _uv_unwrap(ob)
    total = sum(_tri_count(o) for o in obs)
    if total > budget:
        # proportional decimate on every mesh so the asset total meets the budget
        ratio = max(0.05, budget / total)
        for ob in obs:
            m = ob.modifiers.new("Decimate", "DECIMATE")
            m.ratio = ratio
            bpy.ops.object.modifier_apply(modifier="Decimate")

    arm_ob = next((m.object for ob in obs for m in ob.modifiers if m.type == "ARMATURE" and m.object), None)

    textures = {}
    if params.get("bake", True):
        for ob in obs:
            prefix = ob.name if len(obs) > 1 else name
            for ch, p in _bake_textures(ob, resolution, channels, out_dir, prefix=prefix).items():
                textures[ch] = p

    def _export_with_rig(path):
        bpy.ops.object.select_all(action="DESELECT")
        for ob in obs:
            ob.select_set(True)
        if arm_ob:
            arm_ob.select_set(True)
            bpy.context.view_layer.objects.active = obs[0]
        # export_apply is unsafe with armature modifiers; modifiers are already
        # manually applied, so skipping it is safe either way.
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                                  export_yup=True)

    paths = {"main": os.path.join(out_dir, f"{name}.glb")}
    _export_with_rig(paths["main"])
    main_tris = sum(_tri_count(o) for o in obs)  # measured before LOD decimation

    if lods:
        for i, lod_budget in enumerate(lods[1:], start=1):  # first entry = main budget
            t = sum(_tri_count(o) for o in obs)
            if t > lod_budget:
                ratio = max(0.03, lod_budget / t)
                for ob in obs:
                    m = ob.modifiers.new(f"LOD{i}_dec", "DECIMATE")
                    m.ratio = ratio
                    bpy.ops.object.modifier_apply(modifier=f"LOD{i}_dec")
            p = os.path.join(out_dir, f"{name}_LOD{i}.glb")
            _export_with_rig(p)
            paths[f"LOD{i}"] = p

    if with_collision:
        col = _collision_for(obs)
        if col:
            p = os.path.join(out_dir, f"{name}_collision.glb")
            bpy.ops.object.select_all(action="DESELECT")
            col.select_set(True)
            bpy.context.view_layer.objects.active = col
            bpy.ops.export_scene.gltf(filepath=p, export_format="GLB", use_selection=True, export_yup=True)
            paths["collision"] = p
            bpy.data.objects.remove(col, do_unlink=True)

    meta = {
        "name": name,
        "triangles": main_tris,
        "budget": budget,
        "lods": lods,
        "object_count": len(obs),
        "objects": [o.name for o in obs],
        "materials": sorted({s.name for o in obs for s in o.data.materials if s}),
        "manifest": STATE["manifest"],
        "textures": textures,
        "files": paths,
        "recipe": STATE["recipe"],
        "blueprint": STATE["blueprint"],
    }
    if arm_ob:
        arma = arm_ob.data
        meta["rig"] = {"armature": arm_ob.name, "bones": [b.name for b in arma.bones],
                       "joints_z": [round(b.head_local.z, 4) for b in arma.bones]}
    with open(os.path.join(out_dir, "asset.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def _collision_for(obs):
    """Convex hull of the union of all asset meshes."""
    bm = bmesh.new()
    for ob in obs:
        me = ob.data.copy()
        me.transform(ob.matrix_world)
        bm.from_mesh(me)
        bpy.data.meshes.remove(me)
    bmesh.ops.convex_hull(bm, input=bm.verts)
    hull_mesh = bpy.data.meshes.new(obs[0].name + "-Col")
    bm.to_mesh(hull_mesh)
    bm.free()
    hull = bpy.data.objects.new(obs[0].name + "-Col", hull_mesh)
    bpy.context.scene.collection.objects.link(hull)
    _ensure_material(hull, "collision")
    return hull


def op_rig(params):
    """Multi-bone auto-rig: mass-adaptive vertical chain, automatic weights."""
    meshes = _asset_meshes()
    if not meshes:
        raise ValueError("nothing to rig — call build first")
    ob = meshes[0]

    # idempotency: refuse to stack a second armature
    existing = next((m for m in ob.modifiers if m.type == "ARMATURE"), None)
    if existing and existing.object:
        arm = existing.object.data
        return {"armature": existing.object.name, "bones": [b.name for b in arm.bones],
                "joints_z": [b.head_local.z for b in arm.bones],
                "weights_source": "existing", "mesh": ob.name}

    # mass histogram over world-space Z, weighted by vertex count
    import numpy as np
    coords = np.empty(len(ob.data.vertices) * 3, dtype=np.float64)
    ob.data.vertices.foreach_get("co", coords)
    coords = coords.reshape(-1, 3)
    mw = np.array(ob.matrix_world)
    world_z = coords @ mw[:, 2][:3] + mw[2, 3]
    zmin, zmax = float(world_z.min()), float(world_z.max())
    if zmax - zmin < 1e-6:
        raise ValueError("mesh is flat in Z; cannot build a vertical chain")
    bins = 16
    hist, edges = np.histogram(world_z, bins=bins, range=(zmin, zmax))
    n_bones = int(params.get("bones") or min(8, max(3, int((hist > 0).sum()) // 2)))

    # joint placement: proportional to cumulative mass
    cum = np.cumsum(hist)
    total = cum[-1] or 1
    joints = [zmin]
    for k in range(1, n_bones):
        target = total * k / n_bones
        idx = int(np.searchsorted(cum, target))
        idx = min(idx, bins - 1)
        joints.append(float((edges[idx] + edges[idx + 1]) / 2))
    joints.append(zmax)
    joints = sorted(set(joints))
    n_bones = len(joints) - 1

    bbox = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    width = max(max(v.x for v in bbox) - min(v.x for v in bbox),
                max(v.y for v in bbox) - min(v.y for v in bbox))

    arma = bpy.data.armatures.new(ob.name + "_rig")
    arm_ob = bpy.data.objects.new(ob.name + "_rig", arma)
    c = _col(make=True)
    c.objects.link(arm_ob)
    bpy.context.view_layer.objects.active = arm_ob
    bpy.ops.object.mode_set(mode="EDIT")
    taper = max(0.02, width * 0.06)
    for i in range(n_bones):
        e = arma.edit_bones.new(f"bone_{i + 1:02d}")
        e.head = (0, 0, joints[i])
        e.tail = (0, 0, joints[i + 1])
        e.roll = 0.0
        if i > 0:
            e.use_connect = True
        e.head_radius = taper * (1.0 - 0.5 * i / max(1, n_bones - 1))
        e.tail_radius = taper * (1.0 - 0.5 * (i + 1) / max(1, n_bones - 1))
    bpy.ops.object.mode_set(mode="OBJECT")

    # automatic weights with honest fallback; force_fallback exercises the fallback path
    weights_source = "auto"
    if params.get("force_fallback"):
        raise_armature_auto = True
    else:
        raise_armature_auto = False
    try:
        if raise_armature_auto:
            raise RuntimeError("forced fallback (force_fallback=True)")
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        arm_ob.select_set(True)
        bpy.context.view_layer.objects.active = arm_ob
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    except Exception:
        weights_source = "fallback"
        ob.parent = arm_ob
        ob.parent_type = "OBJECT"
        m0 = ob.modifiers.new("Armature", "ARMATURE")
        m0.object = arm_ob
        # Empty groups would be dropped by glTF export (no skin), so each vertex
        # is assigned rigidly (weight 1) to the bone spanning its world Z.
        import numpy as _np
        edges_arr = _np.array(joints)
        mw = _np.array(ob.matrix_world)
        cos = _np.empty(len(ob.data.vertices) * 3, dtype=_np.float64)
        ob.data.vertices.foreach_get("co", cos)
        cos = cos.reshape(-1, 3) @ mw[:, 2][:3] + mw[2, 3]
        idx = _np.clip(_np.searchsorted(edges_arr, cos) - 1, 0, len(joints) - 2)
        bone_names = [b.name for b in arma.bones]
        for b in bone_names:
            ob.vertex_groups.new(name=b)
        per_bone = {}
        for vi, bi in enumerate(idx):
            per_bone.setdefault(int(bi), []).append(vi)
        for bi, vis in per_bone.items():
            ob.vertex_groups[bone_names[bi]].add(vis, 1.0, "REPLACE")

    return {"armature": arm_ob.name,
            "bones": [b.name for b in arma.bones],
            "joints_z": [round(z, 4) for z in joints],
            "weights_source": weights_source, "mesh": ob.name}


def op_import_glb(params):
    """Import a GLB into the (cleared) scene for round-trip verification."""
    path = params.get("path")
    if not path or not os.path.isfile(path):
        raise ValueError(f"no such file: {path!r}")
    before = len(bpy.data.objects)
    # TEMPERANCE avoids the importer's BLENDER-heuristic bind-pose helper object
    bpy.ops.import_scene.gltf(filepath=path, bone_heuristic="TEMPERANCE")
    imported = list(bpy.data.objects)[before:]
    # belt and braces: remove importer helper leftovers (e.g. 'Icosphere') with no skin role
    for ob in list(imported):
        if ob.type == "MESH" and ob.name.startswith("Icosphere") and not ob.vertex_groups:
            bpy.data.objects.remove(ob, do_unlink=True)
            imported.remove(ob)
    # imported objects land in the scene; register meshes as the asset
    c = _col(make=True)
    for ob in imported:
        for uc in list(ob.users_collection):
            uc.objects.unlink(ob)
        c.objects.link(ob)
    STATE["manifest"] = None
    insp = op_inspect({})
    arm = next((o for o in c.objects if o.type == "ARMATURE"), None)
    insp["armature"] = arm.name if arm else None
    insp["bones"] = [b.name for b in arm.data.bones] if arm else []
    return insp


def _probe_deform(params):
    """Test probe: rotate a bone and confirm mesh vertices move."""
    meshes = _asset_meshes()
    arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if not meshes or not arm:
        return {"deformed": False, "max_disp": 0.0}
    ob = meshes[0]
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me0 = ev.to_mesh()
    before = [v.co.copy() for v in me0.vertices]
    ev.to_mesh_clear()
    bone_name = params.get("bone")
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    pb = arm.pose.bones.get(bone_name)
    pb.rotation_mode = "XYZ"  # pose bones default to quaternion; euler assignment is ignored otherwise
    pb.rotation_euler = (0.5, 0, 0)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me1 = ev.to_mesh()
    max_disp = max((a - b).length for a, b in zip(before, (v.co for v in me1.vertices))) if before else 0.0
    ev.to_mesh_clear()
    pb.rotation_euler = (0, 0, 0)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()
    return {"deformed": max_disp > 1e-4, "max_disp": max_disp}


def _probe_vertex_groups(params):
    meshes = _asset_meshes()
    if not meshes:
        return {"count": 0}
    return {"count": len(meshes[0].vertex_groups)}


def _probe_import_meta(params):
    ic = bpy.data.objects.get("Icosphere")
    if not ic:
        return {"present": False}
    return {"present": True, "hide_render": ic.hide_render, "hide_viewport": ic.hide_viewport,
            "hide_get": ic.hide_get(), "users": ic.data.users, "user_zero": ic.data.users == 0,
            "verts": len(ic.data.vertices)}


def _probe_scene(params):
    return [{"name": o.name, "type": o.type, "dims": list(o.dimensions),
             "loc": [round(v, 3) for v in o.location], "scale": list(o.scale),
             "parent": o.parent.name if o.parent else None}
            for o in _asset_objects()]


def _probe_scale(params):
    ob = bpy.data.objects.get(params.get("object", ""))
    return list(ob.scale) if ob else [0, 0, 0]


def op_save_recipe(params):
    path = params.get("path")
    if not path:
        raise ValueError("path required")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({"asset": STATE["asset_name"], "recipe": STATE["recipe"],
                   "blueprint": STATE["blueprint"]}, f, indent=2)
    return {"saved": path}


def op_load_recipe(params):
    with open(params["path"]) as f:
        data = json.load(f)
    STATE["recipe"] = data.get("recipe")
    STATE["blueprint"] = data.get("blueprint")
    STATE["asset_name"] = data.get("asset")
    return {"loaded": params["path"], "nodes": list((STATE["recipe"] or {}).keys())}


# --------------------------------------------------------------------------
# RPC loop
# --------------------------------------------------------------------------

OPS = {
    "ping": lambda p: {"pong": True, "blender": bpy.app.version_string},
    "clear": lambda p: (_clear_scene(), {"cleared": True})[1],
    "build": op_build,
    "inspect": op_inspect,
    "set_material": op_set_material,
    "render_views": op_render_views,
    "validate": op_validate,
    "finalize": op_finalize,
    "rig": op_rig,
    "import_glb": op_import_glb,
    "_probe_deform": _probe_deform,
    "_probe_vertex_groups": _probe_vertex_groups,
    "_probe_import_meta": _probe_import_meta,
    "_probe_scene": _probe_scene,
    "_probe_scale": _probe_scale,
    "save_recipe": op_save_recipe,
    "load_recipe": op_load_recipe,
}


def _handle(line):
    req = {}
    try:
        req = json.loads(line)
        rid, op, params = req.get("id"), req.get("op"), req.get("params") or {}
        fn = OPS.get(op)
        if fn is None:
            raise ValueError(f"unknown op {op!r}")
        return {"id": rid, "ok": True, "result": fn(params)}
    except Exception as e:
        return {"id": req.get("id") if "req" in dir() else None, "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(limit=3)}


def main():
    print("ultima3d worker ready", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = _handle(line)
        # Tag responses so the client can skip Blender's own stdout noise.
        sys.stdout.write("@JSON@" + json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
