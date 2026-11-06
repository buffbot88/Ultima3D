"""Blender-side worker for ultima3d.

Run by the MCP server as:

    blender.exe --background --factory-startup --python blender/worker.py

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
from mathutils import Matrix, Vector, Euler

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


def _srgb_to_linear(c):
    """sRGB display value (0..1) -> Blender scene-linear."""
    c = float(c)
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def material_color(mat_spec, default=None):
    """Resolve a material spec to Blender-linear RGB.

    `color` keeps its historical meaning: the numbers are linear Base Color
    values, passed through untouched. `color_srgb` is new and takes display
    sRGB values -- either 0..1 floats or 0..255 ints -- and converts them, so
    recipes can carry the numbers an artist reads off a colour picker.
    Setting both is ambiguous and is rejected rather than guessed at.
    """
    if not isinstance(mat_spec, dict):
        return default
    lin = mat_spec.get("color")
    srgb = mat_spec.get("color_srgb")
    if lin is not None and srgb is not None:
        raise ValueError(
            f"material {mat_spec.get('name')!r}: set `color` (linear) or "
            f"`color_srgb` (display sRGB), not both")
    if srgb is None:
        return lin if lin is not None else default
    if not isinstance(srgb, (list, tuple)) or len(srgb) != 3:
        raise ValueError(f"material {mat_spec.get('name')!r}: color_srgb needs 3 numbers")
    try:
        vals = [float(c) for c in srgb]
    except (TypeError, ValueError):
        raise ValueError(f"material {mat_spec.get('name')!r}: color_srgb must be numeric")
    # One scale for the whole triple: mixing 0..1 and 0..255 in one colour is a bug.
    scale = 255.0 if max(vals) > 1.0 else 1.0
    out = []
    for v in vals:
        if v < 0 or v > scale:
            raise ValueError(
                f"material {mat_spec.get('name')!r}: color_srgb {srgb} out of range "
                f"(expected 0..1 or 0..255)")
        out.append(_srgb_to_linear(v / scale))
    return out


def _simple_mat(name, base_color, roughness=0.6, metallic=0.0, texture=None,
                tex_scale=3.0, tex_blend=0.5, tex_seed=0):
    """Flat PBR, or a procedural preset when texture names one."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    _set_principled(mat, base_color, roughness, metallic)
    if texture:
        _apply_procedural(mat, texture, base_color, tex_scale, tex_blend, tex_seed)
    return mat


# Preset names accepted by the material spec's `texture` key. Unknown names
# fall back to flat color with a warning, never a crashed build.
TEXTURE_PRESETS = ("wood_grain", "bark", "stone", "mossy", "grass_blend",
                   "worn_metal")


def _mat_fresh(mat):
    """Keep Principled + Output, drop every other node for a deterministic rebuild."""
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    princ = nodes.get("Principled BSDF") or nodes.new("ShaderNodeBsdfPrincipled")
    out = nodes.get("Material Output") or nodes.new("ShaderNodeOutputMaterial")
    for n in list(nodes):
        if n not in (princ, out):
            nodes.remove(n)
    links.clear() if hasattr(links, "clear") else None
    for l in list(links):
        links.remove(l)
    links.new(princ.outputs.get("BSDF"), out.inputs.get("Surface"))
    return nodes, links, princ


def _tex_chain(mat, tex_node_type, seed=0, scale=3.0):
    """TexCoord -> Mapping -> texture node; seed offsets mapping for variation."""
    nodes, links, princ = _mat_fresh(mat)
    tex_coord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (float(scale),) * 3
    # deterministic per-seed offset (noise/voronoi have no stable seed socket)
    mapping.inputs["Location"].default_value = (float(seed) * 3.7 % 10,
                                                  float(seed) * 7.3 % 10, 0.0)
    tex = nodes.new(tex_node_type)
    links.new(tex_coord.outputs.get("Generated"), mapping.inputs.get("Vector"))
    links.new(mapping.outputs.get("Vector"), tex.inputs.get("Vector"))
    return nodes, links, princ, tex


def _ramp(nodes, links, fac_out, c0, c1, pos1=0.5):
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*[float(c) for c in c0], 1.0)
    ramp.color_ramp.elements[1].position = max(0.01, min(0.99, float(pos1)))
    ramp.color_ramp.elements[1].color = (*[float(c) for c in c1], 1.0)
    links.new(fac_out, ramp.inputs.get("Fac"))
    return ramp


def _bump(nodes, links, princ, height_out, strength=0.5, distance=0.2):
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = float(strength)
    bump.inputs["Distance"].default_value = float(distance)
    links.new(height_out, bump.inputs.get("Height"))
    links.new(bump.outputs.get("Normal"), princ.inputs.get("Normal"))
    return bump


def _shade(base, f, toward):
    """Lerp base toward a target color by f (0..1), clamped."""
    return [max(0.0, min(1.0, b + (t - b) * f)) for b, t in zip(base, toward)]


def _apply_procedural(mat, preset, base_color=(0.6, 0.6, 0.6), scale=3.0,
                      blend=0.5, seed=0):
    """Build a small EEVEE-safe node chain for a named preset. Warns on unknown."""
    base = [float(c) for c in (base_color or (0.6, 0.6, 0.6))]
    if preset == "wood_grain":
        nodes, links, princ, tex = _tex_chain(mat, "ShaderNodeTexWave", seed, scale)
        tex.wave_type = "BANDS"
        tex.bands_direction = "X"
        try:
            tex.inputs["Scale"].default_value = 1.0
        except (KeyError, IndexError):
            pass
        ramp = _ramp(nodes, links, tex.outputs.get("Fac"),
                     _shade(base, 0.35, (0.25, 0.15, 0.08)),
                     _shade(base, -0.3, (0.2, 0.12, 0.06)), 0.5 + blend * 0.3)
        links.new(ramp.outputs.get("Color"), princ.inputs.get("Base Color"))
        _bump(nodes, links, princ, tex.outputs.get("Fac"), 0.3, 0.1)
    elif preset == "bark":
        nodes, links, princ, tex = _tex_chain(mat, "ShaderNodeTexVoronoi", seed, scale * 2)
        try:
            tex.feature = "F1"
        except AttributeError:
            pass
        ramp = _ramp(nodes, links, tex.outputs.get("Distance"),
                     _shade(base, 0.45, (0.12, 0.08, 0.05)), base, 0.35 + blend * 0.4)
        links.new(ramp.outputs.get("Color"), princ.inputs.get("Base Color"))
        _bump(nodes, links, princ, tex.outputs.get("Distance"), 0.9, 0.4)
    elif preset == "stone":
        nodes, links, princ, tex = _tex_chain(mat, "ShaderNodeTexVoronoi", seed, scale)
        try:
            tex.feature = "F1"
        except AttributeError:
            pass
        ramp = _ramp(nodes, links, tex.outputs.get("Distance"),
                     _shade(base, 0.3, (0.2, 0.2, 0.22)),
                     _shade(base, -0.25, (0.75, 0.75, 0.78)), 0.45 + blend * 0.3)
        links.new(ramp.outputs.get("Color"), princ.inputs.get("Base Color"))
        _bump(nodes, links, princ, tex.outputs.get("Distance"), 0.4, 0.15)
    elif preset == "mossy":
        nodes, links, princ, tex = _tex_chain(mat, "ShaderNodeTexNoise", seed, scale * 0.7)
        try:
            tex.inputs["Roughness"].default_value = 0.7
        except (KeyError, IndexError):
            pass
        ramp = _ramp(nodes, links, tex.outputs.get("Fac"),
                     _shade(base, -0.4, (0.45, 0.45, 0.46)), base, 0.4 + blend * 0.35)
        links.new(ramp.outputs.get("Color"), princ.inputs.get("Base Color"))
        _bump(nodes, links, princ, tex.outputs.get("Fac"), 0.6, 0.3)
    elif preset == "grass_blend":
        nodes, links, princ, tex = _tex_chain(mat, "ShaderNodeTexNoise", seed, scale * 1.5)
        ramp = _ramp(nodes, links, tex.outputs.get("Fac"),
                     _shade(base, 0.3, (0.15, 0.3, 0.1)),
                     _shade(base, -0.3, (0.45, 0.55, 0.25)), 0.5)
        links.new(ramp.outputs.get("Color"), princ.inputs.get("Base Color"))
        _bump(nodes, links, princ, tex.outputs.get("Fac"), 0.15, 0.05)
    elif preset == "worn_metal":
        nodes, links, princ, tex = _tex_chain(mat, "ShaderNodeTexNoise", seed, scale * 2)
        ramp = _ramp(nodes, links, tex.outputs.get("Fac"),
                     base, _shade(base, 0.5, (0.3, 0.18, 0.1)), 0.55 + blend * 0.3)
        links.new(ramp.outputs.get("Color"), princ.inputs.get("Base Color"))
        try:
            princ.inputs["Metallic"].default_value = 0.9
        except KeyError:
            pass
        _bump(nodes, links, princ, tex.outputs.get("Fac"), 0.2, 0.05)
    else:
        import warnings
        warnings.warn(f"unknown texture preset {preset!r}; using flat color")
    return mat


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
# Recipe keys the compiler consumes itself, so they are not builder parameters.
# (`detail` is popped by _resolve_detail before these checks run.)
RECIPE_META_KEYS = ("triangle_budget",)


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
        base = material_color(mat_spec)
        for name in created:
            ob = _obj_by_name(name)
            _ensure_material(ob, mat_spec["name"])
            _set_principled(ob.data.materials[0], base, mat_spec.get("roughness"),
                            mat_spec.get("metallic"), mat_spec.get("emission"))
            if mat_spec.get("texture"):
                _apply_procedural(ob.data.materials[0], mat_spec["texture"],
                                  base or (0.6, 0.6, 0.6),
                                  mat_spec.get("tex_scale", 3.0),
                                  mat_spec.get("tex_blend", 0.5),
                                  mat_spec.get("tex_seed", 0))
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


def _builder_params(builder):
    """Named parameters a builder accepts, excluding its name and any **catch-all."""
    import inspect as _inspect
    params = _inspect.signature(builder).parameters
    return sorted(n for n, p in params.items()
                  if n != "name" and p.kind is not _inspect.Parameter.VAR_KEYWORD)


def _unrecognized_params(builder, kwargs):
    """Recipe keys this builder will silently swallow through its **_ catch-all.

    Every builder ends in **_, so a misspelled parameter is dropped without complaint and
    the asset is built with the default instead -- success that looks like success.
    """
    accepted = set(_builder_params(builder))
    # recipe-level keys the compiler consumes itself rather than passing to the builder
    return sorted(k for k in kwargs if k not in accepted and k not in RECIPE_META_KEYS)


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


# --------------------------------------------------------------------------
# Vista builders: composed environment scenes (terrain + scatter + sky).
#
# A vista is a recipe like any other asset, e.g. terrain + a tree node +
# a scatter node that stamps the tree across the terrain. Placement and the
# terrain mesh share one height function, so instances sit on the ground by
# construction; a seed makes the layout reproducible.
# --------------------------------------------------------------------------


def _hash01(ix, iy, seed):
    """Deterministic pseudo-random float in [0, 1) from integer lattice coords."""
    h = (int(ix) * 374761393 + int(iy) * 668265263 + int(seed) * 2246822519) & 0xFFFFFFFFFFFFFFFF
    h ^= h >> 13
    h = (h * 1274126177) & 0xFFFFFFFFFFFFFFFF
    h ^= h >> 16
    return (h & 0xFFFFFFFF) / 2.0 ** 32


def _value_noise(x, y, seed):
    """Smooth value noise in [0, 1]: hashed lattice + smoothstep interpolation."""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    ux, uy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a = _hash01(ix, iy, seed)
    b = _hash01(ix + 1, iy, seed)
    c = _hash01(ix, iy + 1, seed)
    d = _hash01(ix + 1, iy + 1, seed)
    return a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy


def _terrain_height(x, y, size, height, seed):
    """Terrain surface height at (x, y): 3-octave value noise in [-height/2, +height/2]."""
    sx = max(float(size[0]), 1e-6)
    total, amp, freq, norm = 0.0, 1.0, 3.0, 0.0
    for octave in range(3):
        total += amp * _value_noise(x / sx * freq + octave * 13.7, y / sx * freq - octave * 7.3, seed)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return (total / norm - 0.5) * 2.0 * float(height) * 0.5


def build_terrain(name, size=(24, 24), segments=48, height=1.6, seed=0, **kw):
    loc = kw.get("location", (0, 0, 0))
    sx, sy = float(size[0]), float(size[1])
    n = max(int(segments), 1)
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    grid = []
    for j in range(n + 1):
        row = []
        for i in range(n + 1):
            x = -sx / 2 + sx * i / n
            y = -sy / 2 + sy * j / n
            row.append(bm.verts.new((x, y, _terrain_height(x, y, (sx, sy), height, seed))))
        grid.append(row)
    for j in range(n):
        for i in range(n):
            bm.faces.new((grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]))
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    _shade_smooth_auto(ob)
    return _place(ob, name, loc)


def _vista_source_objects(node, manifest):
    """Mesh objects a scatter node should stamp: an earlier node's outputs by name."""
    names = (manifest.get("objects") or {}).get(node, [])
    obs = [bpy.data.objects[n] for n in names if n in bpy.data.objects]
    if not obs:
        ob = bpy.data.objects.get(node)
        obs = [ob] if ob is not None else []
    return [o for o in obs if o.type == "MESH"]


def build_scatter(name, source, terrain=None, count=25, area=None, seed=1,
                  scale_min=0.8, scale_max=1.2, sink=0.05, **kw):
    bpy.context.view_layer.update()
    manifest = STATE.get("manifest") or {"objects": {}}
    src = _vista_source_objects(source, manifest)
    if not src:
        raise ValueError(f"scatter {name!r}: unknown source node {source!r} -- build it earlier in the recipe")
    tloc = (0, 0, 0)
    tsize, theight, tseed = (10, 10), 0.0, 0
    if terrain is not None:
        spec = (STATE.get("recipe") or {}).get(terrain)
        if not isinstance(spec, dict):
            raise ValueError(f"scatter {name!r}: unknown terrain node {terrain!r}")
        tsize = spec.get("size", tsize)
        theight = spec.get("height", theight)
        tseed = spec.get("seed", tseed)
        tloc = spec.get("location", tloc)
    ax, ay = (float(area[0]), float(area[1])) if area else (float(tsize[0]) * 0.8, float(tsize[1]) * 0.8)
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for o in src:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    anchor = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    rng = random.Random(seed)
    first = None
    for i in range(int(count)):
        x = rng.uniform(-ax / 2, ax / 2) + float(tloc[0])
        y = rng.uniform(-ay / 2, ay / 2) + float(tloc[1])
        ground = _terrain_height(x - float(tloc[0]), y - float(tloc[1]), tsize, theight, tseed) if terrain is not None else 0.0
        target = Vector((x, y, float(tloc[2]) + ground - float(sink)))
        s = rng.uniform(float(scale_min), float(scale_max))
        yaw = rng.uniform(0, math.tau)
        rot = Matrix.Rotation(yaw, 3, "Z")
        for j, o in enumerate(src):
            dup = o.copy()
            dup.data = o.data.copy()
            bpy.context.scene.collection.objects.link(dup)
            # per-instance yaw + scale about the anchor keeps multi-object sources rigid;
            # scale is baked into the mesh verts so object scale stays applied (validate)
            rel = rot @ ((Vector(o.location) - anchor) * s)
            dup.location = target + rel
            e = dup.rotation_euler
            dup.rotation_euler = Euler((e.x, e.y, e.z + yaw))
            sv = Vector(o.scale) * s
            for v in dup.data.vertices:
                v.co = Vector((v.co.x * sv.x, v.co.y * sv.y, v.co.z * sv.z))
            _place(dup, f"{name}_{i}_{j}", dup.location, dup.rotation_euler, dup.scale)
            if first is None:
                first = dup
    return first


def build_skydome(name, radius=120, segments=32,
                  top_color=(0.25, 0.45, 0.8), horizon_color=(0.85, 0.87, 0.9), **kw):
    loc = kw.get("location", (0, 0, 0))
    ob = create_sphere(name, radius=float(radius), segments=max(int(segments), 8), location=loc)
    mat = bpy.data.materials.new(name + "_sky")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    tex = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.5
    ramp.color_ramp.elements[0].color = (*[float(c) for c in horizon_color], 1.0)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (*[float(c) for c in top_color], 1.0)
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.0
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(tex.outputs["Generated"], sep.inputs["Vector"])
    links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], emit.inputs["Color"])
    links.new(emit.outputs["Emission"], out.inputs["Surface"])
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    # Backdrop, not asset geometry: the dome stays in the scene for renders but
    # out of the ASSET collection, so inspect/validate/finalize/export ignore it.
    for uc in list(ob.users_collection):
        uc.objects.unlink(ob)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def build_text(name, text="POST OFFICE", size=0.3, depth=0.06, max_width=None,
               location=(0, 0, 0), rotation=(0, 0, 0), **_):
    """    Extruded 3D lettering from Blender's built-in font: export-safe signage
    with no image textures. `size` is the cap height in units; `depth` the
    extrusion; `max_width` uniformly shrinks the line to fit (for sign boards).
    The lettering is baked upright facing +Y (toward a front/south viewer),
    reading left-to-right, so recipes place it with identity rotation and
    `location.y` at the board's front face (extrusion runs 0..depth toward +Y).
    Geometry is recentered on X so placement is deterministic."""
    if not text or not str(text).strip():
        raise ValueError(f"text {name!r} needs a non-empty string")
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = str(text)
    cu.size = float(size)
    try:
        cu.align_x = "CENTER"
    except (AttributeError, TypeError):
        pass
    cu.extrude = float(depth)
    try:
        cu.bevel_depth = 0.0
    except (AttributeError, TypeError):
        pass
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    ob = _convert_to_mesh(ob)
    xs = [v.co.x for v in ob.data.vertices]
    if xs:
        cx = (max(xs) + min(xs)) / 2
        if abs(cx) > 1e-9:
            for v in ob.data.vertices:
                v.co.x -= cx
    if max_width:
        xs = [v.co.x for v in ob.data.vertices]
        w = (max(xs) - min(xs)) if xs else 0.0
        if w > float(max_width) > 0:
            s = float(max_width) / w
            for v in ob.data.vertices:
                v.co.x *= s
                v.co.y *= s
                v.co.z *= s
    # Stand the lettering up to face +Y, upright and reading left-to-right
    # as seen by a front/south viewer: (x, y, z) -> (-x, z, y). A plain
    # X-90 object rotation would leave it upside-down and reversed here.
    for v in ob.data.vertices:
        x, y, z = v.co.x, v.co.y, v.co.z
        v.co.x, v.co.y, v.co.z = -x, z, y
    return _place(ob, name, location, rotation)


# --------------------------------------------------------------------------
# Foliage canopy: surface-attached leaves on irregular overlapping clusters.
# Design: docs/foliage-cluster-builder-spec.md. The builder owns the cluster
# displacement field, so every leaf is placed against the surface the core
# actually has -- which is what stops the "floating card" failure of hand-
# placed recipe leaves.
# --------------------------------------------------------------------------

_FOLIAGE_DEFAULT_PALETTE = [
    {"name": "leaf_deep", "color_srgb": [41, 82, 36], "roughness": 0.92, "weight": 0.35},
    {"name": "leaf_mid", "color_srgb": [64, 122, 51], "roughness": 0.90, "weight": 0.40},
    {"name": "leaf_warm", "color_srgb": [97, 148, 61], "roughness": 0.88, "weight": 0.18},
    {"name": "leaf_gold", "color_srgb": [133, 173, 76], "roughness": 0.85, "weight": 0.07},
]

# Leaf silhouette in card-local space: x = width (-0.5..0.5), y = 0 at base..1 at tip.
# The base root sits at -0.18 so a leaf can be buried inside the cluster surface.
_LEAF_PROFILE = ((0.0, -0.18), (0.30, -0.10), (0.50, 0.10), (0.34, 0.55),
                 (0.0, 1.0), (-0.34, 0.55), (-0.50, 0.10), (-0.30, -0.10))


def _smoothstep(t):
    return t * t * (3.0 - 2.0 * t)


def _noise3(x, y, z, seed):
    """Trilinear value noise in [-1, 1], from the shared hash lattice."""
    xi, yi, zi = math.floor(x), math.floor(y), math.floor(z)
    u, v, w = _smoothstep(x - xi), _smoothstep(y - yi), _smoothstep(z - zi)

    def h(dx, dy, dz):
        return _hash01(xi + dx + (yi + dy) * 57 + (zi + dz) * 131,
                       (zi + dz) * 73 + (yi + dy) * 11 + dx * 5, seed)

    c00 = h(0, 0, 0) * (1 - u) + h(1, 0, 0) * u
    c10 = h(0, 1, 0) * (1 - u) + h(1, 1, 0) * u
    c01 = h(0, 0, 1) * (1 - u) + h(1, 0, 1) * u
    c11 = h(0, 1, 1) * (1 - u) + h(1, 1, 1) * u
    lo = c00 * (1 - v) + c10 * v
    hi = c01 * (1 - v) + c11 * v
    return (lo * (1 - w) + hi * w) * 2.0 - 1.0


def _foliage_sectors(seed):
    """Per-octant radial bias: the term that makes a canopy asymmetric."""
    return [0.85 + 0.30 * _hash01(i * 37 + 5, 11, int(seed)) for i in range(8)]


def _foliage_surface(dirv, seed, jitter, scale, sectors):
    """Radial multiplier at a unit direction: one coherent field per canopy."""
    n = (0.65 * _noise3(dirv.x * scale, dirv.y * scale, dirv.z * scale, seed)
         + 0.35 * _noise3(dirv.x * scale * 2.3 + 11.7, dirv.y * scale * 2.3 + 3.1,
                          dirv.z * scale * 2.3 + 7.9, seed + 101))
    ang = math.atan2(dirv.y, dirv.x)
    return 1.0 + jitter * n * sectors[int((ang + math.pi) / (math.pi / 4.0)) % 8]


def _patch_rect(i, count):
    """UV patch for palette entry i inside one shared atlas."""
    cols = 2 if count <= 4 else 3
    rows = max(1, int(math.ceil(count / float(cols))))
    c, r = i % cols, i // cols
    inset = 0.02
    return (c / cols + inset, r / rows + inset, (c + 1) / cols - inset, (r + 1) / rows - inset)


def _add_leaf(bm, uv_layer, base, dirv, side, length, width, thickness, curl,
              uv_rect, mat_index):
    """One solidified leaf card, base buried at `base`, growing along `dirv`."""
    nrm = dirv.cross(side)
    if nrm.length < 1e-6:
        side = Vector((0.0, 0.0, 1.0)) if abs(dirv.z) < 0.9 else Vector((1.0, 0.0, 0.0))
        nrm = dirv.cross(side)
    nrm.normalize()

    def at(x, y, z):
        bend = curl * (y * y) * 0.55 - curl * 0.30 * (x * x)
        return (base + side * (x * width) + dirv * (y * length)
                + nrm * (bend * length + z * thickness))

    if thickness <= 1e-6:
        # Flat card: 6 tris instead of 28. Real foliage is single-sided, and the
        # budget saved here buys 3-4x more leaves, which is what the eye reads.
        # The material keeps backface culling off, so glTF exports it doubleSided.
        faces = [bm.faces.new([bm.verts.new(at(x, y, 0.0)) for x, y in _LEAF_PROFILE])]
    else:
        front = [bm.verts.new(at(x, y, 0.5)) for x, y in _LEAF_PROFILE]
        back = [bm.verts.new(at(x, y, -0.5)) for x, y in _LEAF_PROFILE]
        faces = [bm.faces.new(list(reversed(front))), bm.faces.new(back)]
        for i in range(len(front)):
            j = (i + 1) % len(front)
            faces.append(bm.faces.new((front[i], front[j], back[j], back[i])))
    umin, vmin, umax, vmax = uv_rect
    for f in faces:
        f.material_index = mat_index
        f.smooth = False
        for loop in f.loops:
            v = loop.vert.co - base
            x = v.dot(side) / max(width, 1e-6)
            y = v.dot(dirv) / max(length, 1e-6)
            loop[uv_layer].uv = (umin + min(max(x + 0.5, 0.0), 1.0) * (umax - umin),
                                 vmin + min(max((y + 0.18) / 1.18, 0.0), 1.0) * (vmax - vmin))


def _foliage_materials(palette):
    """Material per palette entry; colours resolved through the shared contract."""
    mats = []
    for entry in palette:
        linear = material_color(entry, (0.2, 0.35, 0.18))
        mat = _simple_mat(entry["name"], linear, entry.get("roughness", 0.9))
        mats.append((mat, _patch_rect(len(mats), len(palette)), entry))
    return mats


def build_foliage_cluster(name, center=(0.0, 0.0, 4.2), radii=(2.6, 2.4, 2.0), seed=1,
                          layers=3, cluster_count=12, cluster_size=(0.55, 1.0),
                          overlap=0.88, silhouette_jitter=0.35, noise_scale=1.6,
                          openness=0.35, leaf=None, palette=None, skirt=None,
                          branch_tips=None, trunk_top=None, subdiv=1, **_):
    """Irregular canopy of overlapping clusters with surface-attached leaf cards.

    One object out: cores, leaves and branch cuffs are joined here so
    render_asset frames the whole canopy and the recipe stays one node.
    """
    leaf = dict(leaf or {})
    pal = list(palette or _FOLIAGE_DEFAULT_PALETTE)
    if not pal:
        raise ValueError(f"foliage_cluster {name!r}: palette needs at least one entry")
    seed = int(seed)
    cx, cy, cz = (float(v) for v in center)
    rx, ry, rz = (float(v) for v in radii)
    mean_r = (rx + ry + rz) / 3.0
    sectors = _foliage_sectors(seed)
    jitter = float(silhouette_jitter)
    scale = float(noise_scale)

    per_cluster = int(leaf.get("count_per_cluster", 3))   # rosettes per cluster
    per_rosette = max(1, int(leaf.get("leaves_per_rosette", 5)))
    length = float(leaf.get("length", 0.22))
    width = float(leaf.get("width", 0.13))
    thick = float(leaf.get("thickness", 0.012))
    curl = float(leaf.get("curl", 0.35))
    inset = float(leaf.get("inset", 0.25))

    mats = _foliage_materials(pal)
    deepest = 0

    # ---- 1. cluster placement: jittered golden-angle spiral ----------------
    n = max(1, int(cluster_count))
    rng = random.Random(seed * 7919 + 13)
    placed = []
    for i in range(n):
        t = (i + 0.5) / n
        polar = math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * t)))
        az = i * 2.399963229728653 + rng.uniform(-0.25, 0.25)
        d = Vector((math.sin(polar) * math.cos(az), math.sin(polar) * math.sin(az),
                    math.cos(polar)))
        layer = 0 if d.z < -0.22 else (2 if d.z > 0.34 else 1)
        # Spread through the volume, not just the shell: a hollow shell of small
        # cores reads as a ring of blobs with gaps, which is exactly the failure
        # mode this builder exists to avoid.
        radial = (0.45, 0.72, 0.95)[layer] if layers > 1 else 1.0
        surf = _foliage_surface(d, seed, jitter, scale, sectors)
        c = Vector((cx, cy, cz)) + d * (mean_r * surf * radial)
        size = mean_r * (float(cluster_size[0]) if len(cluster_size) < 2 else
                         rng.uniform(float(cluster_size[0]), float(cluster_size[1]))) * 0.62
        placed.append({"c": c, "d": d, "r": size, "layer": layer})

    # Adjacency: grow radii until neighbours overlap, so the silhouette is one
    # mass instead of N blobs. Orphans that cannot reach anyone are re-seeded.
    cap = float(cluster_size[1] if len(cluster_size) > 1 else cluster_size[0]) * mean_r * 0.62 * 1.30
    for _ in range(14):
        changed = False
        for i, a in enumerate(placed):
            best = None
            for j, b in enumerate(placed):
                if i == j:
                    continue
                gap = (a["c"] - b["c"]).length - (a["r"] + b["r"]) * float(overlap)
                if best is None or gap < best:
                    best = gap
            if best is not None and best > 0:
                # Grow toward overlap but cap the growth: uncapped, a sparse
                # placement balloons each core into a giant bubble that dwarfs
                # the leaves and the tree.
                a["r"] = min(a["r"] * 1.12, cap)
                changed = True
        if not changed:
            break

    # ---- 2. cores ---------------------------------------------------------
    parts = []
    for idx, cl in enumerate(placed):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=max(0, int(subdiv)), radius=1.0)
        ob = bpy.context.active_object
        for v in ob.data.vertices:
            d = v.co.normalized()
            local = _noise3(d.x * 3.1 + idx, d.y * 3.1 + idx * 0.5, d.z * 3.1, seed + 71) * 0.18
            v.co = d * (1.0 + local)
        ob.scale = (cl["r"] * 0.95, cl["r"] * 0.95, cl["r"] * 0.78)
        ob.location = cl["c"]
        for p in ob.data.polygons:
            p.use_smooth = True
        mat_index = 0 if cl["layer"] == 0 else min(1, len(mats) - 1) if len(mats) > 1 else 0
        ob.data.materials.append(mats[mat_index][0])
        parts.append(ob)

    # ---- 3. leaves on each cluster's outward surface ----------------------
    bms = {}
    uv_layers = {}
    for i in range(len(mats)):
        bm = bmesh.new()
        bms[i] = bm
        uv_layers[i] = bm.loops.layers.uv.new("UVMap")

    floats = []
    total_leaves = 0
    for idx, cl in enumerate(placed):
        d0 = cl["d"]
        up = Vector((0.0, 0.0, 1.0))
        t1 = d0.cross(up)
        if t1.length < 1e-4:
            t1 = Vector((1.0, 0.0, 0.0))
        t1.normalize()
        t2 = d0.cross(t1)
        # Flat count per cluster: a fixed, predictable triangle cost per node.
        count = max(1, int(per_cluster))
        for k in range(count):
            a = k * 2.399963229728653 + rng.uniform(-0.3, 0.3)
            tilt = 0.35 + 0.5 * rng.random()
            rdir = (t1 * math.cos(a) + t2 * math.sin(a)) * math.sin(tilt) + d0 * math.cos(tilt)
            if rdir.length < 1e-5:
                continue
            rdir.normalize()
            # Attachment point comes from the same field the core was displaced
            # with, so it lies on the core surface rather than on an ideal sphere.
            local = _noise3(rdir.x * 3.1 + idx, rdir.y * 3.1 + idx * 0.5, rdir.z * 3.1,
                            seed + 71) * 0.18
            surf = cl["r"] * (1.0 + local)
            point = cl["c"] + rdir * surf
            side = rdir.cross(t1)
            if side.length < 1e-4:
                side = t1
            side.normalize()
            at_axis = rdir.cross(side).normalized()
            # palette: skirt darker, crown gold; deepest entries first
            if cl["layer"] == 0:
                mi = 0
            elif cl["layer"] == 2:
                mi = len(mats) - 1 if rng.random() < 0.45 else max(0, len(mats) - 2)
            else:
                mi = min(len(mats) - 1, 1 + (1 if rng.random() < 0.3 else 0))
            # A rosette: several leaves fanned around the patch normal. The base
            # stays on the cluster surface, so leaves overlap each other rather
            # than sitting apart as isolated cards.
            for pidx in range(per_rosette):
                spin = (pidx * (2.0 * math.pi / per_rosette)) + rng.uniform(-0.25, 0.25)
                s2 = side * math.cos(spin) + at_axis * math.sin(spin)
                splay = 0.35 + 0.45 * rng.random()
                ldir = (rdir * math.cos(splay) + s2 * math.sin(splay)).normalized()
                lscale = 0.7 + 0.65 * rng.random()
                base = point - ldir * (inset * length * lscale)
                floats.append(0.0)  # buried by construction; validate tripwire
                _add_leaf(bms[mi], uv_layers[mi], base, ldir, s2, length * lscale,
                          width * (0.85 + 0.3 * rng.random()), thick, curl,
                          mats[mi][1], 0)
                total_leaves += 1

    # ---- 4. drooping skirt chains at the rim ------------------------------
    sk = dict(skirt or {})
    if sk.get("enabled"):
        chains = int(sk.get("count", 5))
        chain_len = int(sk.get("leaves", 4))
        rim = [cl for cl in placed if cl["layer"] == 0]
        for ci in range(min(chains, len(rim))):
            cl = rim[ci]
            d = (cl["c"] - Vector((cx, cy, cz)))
            if d.length < 1e-4:
                continue
            d.z = 0.0
            d.normalize()
            start = cl["c"] + d * cl["r"] * 0.9
            for j in range(chain_len):
                base = start + Vector((0.0, 0.0, -0.13 * j))
                down = Vector((rng.uniform(-0.25, 0.25), rng.uniform(-0.25, 0.25), -1.0)).normalized()
                side = down.cross(Vector((0.0, 0.0, 1.0)))
                if side.length < 1e-4:
                    side = Vector((1.0, 0.0, 0.0))
                side.normalize()
                _add_leaf(bms[0], uv_layers[0], base, down, side, length * 1.15, width,
                          thick, curl * 1.6, mats[0][1], 0)
                total_leaves += 1

    # ---- 5. branch cuffs at authored tips ---------------------------------
    tips = [Vector(t) for t in (branch_tips or [])]
    if tips and trunk_top:
        top = Vector(trunk_top)
        for i, tip in enumerate(tips):
            d = tip - top
            if d.length < 1e-4:
                continue
            d.normalize()
            bpy.ops.mesh.primitive_cone_add(vertices=10, radius1=0.085, radius2=0.12, depth=0.30)
            cuff = bpy.context.active_object
            cuff.location = tip - d * 0.16
            cuff.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
            for p in cuff.data.polygons:
                p.use_smooth = True
            cuff.data.materials.append(bpy.data.materials.get("bark"))
            parts.append(cuff)

    for i, bm in bms.items():
        if not bm.verts:
            bm.free()
            continue
        mesh = bpy.data.meshes.new(f"{name}_leaves_{i}")
        bm.to_mesh(mesh)
        bm.free()
        ob = bpy.data.objects.new(f"{name}_leaves_{i}", mesh)
        bpy.context.scene.collection.objects.link(ob)
        ob.data.materials.append(mats[i][0])
        parts.append(ob)

    # ---- 6. one object out -----------------------------------------------
    bpy.ops.object.select_all(action="DESELECT")
    for ob in parts:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    canopy = bpy.context.active_object
    canopy.name = name
    # Apply LOCATION too: join() keeps parts[0]'s own offset, so zeroing the
    # object location without baking it first shifts the whole canopy down by
    # that offset (it landed at the trunk base in the first v3 build).
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    _place(canopy, name)

    STATE.setdefault("foliage", []).append({
        "node": name,
        "clusters": len(placed),
        "leaves": total_leaves,
        "leaf_float_max": max(floats) if floats else 0.0,
        "palette": [e["name"] for e in pal],
    })
    return canopy


_KERB_DEFAULT_PALETTE = [
    {"name": "kerb_stone", "color_srgb": [186, 176, 154], "roughness": 0.95, "texture": "stone"},
    {"name": "kerb_stone_light", "color_srgb": [210, 200, 178], "roughness": 0.9, "texture": "stone"},
]


def _arc_holes(spans):
    """Uncovered arcs of a (a0, a1) block-span list, wrap seam included."""
    if len(spans) < 2:
        return []
    spans = sorted(spans)
    holes = [b0 - a1 for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]) if b0 > a1]
    seam = (spans[0][0] + 2.0 * math.pi) - spans[-1][1]
    if seam > 0:
        holes.append(seam)
    return holes


def _kerb_block(bm, uv_layer, cx, cy, a0, a1, r_mid, thick, z0, z1, chamfer, mat_index):
    """One annular-sector stone, chamfered on top, as a closed 6-quad solid."""
    r_in, r_out = r_mid - thick / 2.0, r_mid + thick / 2.0
    inset = min(chamfer, thick * 0.25)

    def at(a, r, z):
        return bm.verts.new((cx + r * math.cos(a), cy + r * math.sin(a), z))

    b0, b1 = at(a0, r_in, z0), at(a0, r_out, z0)
    b2, b3 = at(a1, r_out, z0), at(a1, r_in, z0)
    t0, t1 = at(a0, r_in + inset, z1), at(a0, r_out - inset, z1)
    t2, t3 = at(a1, r_out - inset, z1), at(a1, r_in + inset, z1)
    faces = [
        (b0, b3, b2, b1),   # underside, -Z
        (t0, t1, t2, t3),   # worn top face, +Z
        (b1, b2, t2, t1),   # outer face, +radial
        (b3, b0, t0, t3),   # inner face, -radial (retains the bed soil)
        (b0, b1, t1, t0),   # start end
        (b2, b3, t3, t2),   # finish end
    ]
    for corners in faces:
        f = bm.faces.new(corners)
        f.material_index = mat_index
        f.smooth = False
        f.normal_update()


def build_stone_kerb(name, radius=3.0, thickness=0.22, height=0.3, segments=34,
                     center=(0.0, 0.0), seed=1, gaps=0, gap_deg=20.0, cap=True,
                     cap_height=0.07, cap_overhang=0.035, chamfer=0.02, jitter=0.05,
                     palette=None, **_):
    """Raised masonry kerb ring: a block base course plus an overhanging cap course.

    `radius` is the kerb centreline, so the footprint is radius + thickness/2 +
    cap_overhang. Blocks are laid with a hairline joint (no shared vertices, or
    validate counts them as doubles) and a per-block size/radius jitter, so the
    ring reads as hand-laid stone instead of an extruded donut. `gaps` punches
    evenly spaced planting gaps of `gap_deg` for path throats.

    One object out, two material slots (base stone, cap stone).
    """
    pal = list(palette or _KERB_DEFAULT_PALETTE)
    if not pal:
        raise ValueError(f"stone_kerb {name!r}: palette needs at least one entry")
    seed = int(seed)
    rng = random.Random(seed * 6151 + 7)
    cx, cy = (float(v) for v in center)
    r_mid = float(radius)
    thick = float(thickness)
    top = float(height)
    n = max(8, int(segments))
    cap_h = float(cap_height) if cap else 0.0
    if cap_h >= top:
        raise ValueError(f"stone_kerb {name!r}: cap_height {cap_h} must be under height {top}")
    gap_n = max(0, int(gaps))
    gap_half = math.radians(float(gap_deg)) / 2.0
    # Gaps sit on the diagonals by default: the cardinal throats stay closed
    # unless a caller asks for a gap on one.
    gap_centres = [(-3.0 * math.pi / 4.0 + i * (2.0 * math.pi / max(1, gap_n)))
                   if gap_n else 0.0 for i in range(gap_n)]

    step = 2.0 * math.pi / n
    joint = step * 0.06  # hairline joint, in radians
    courses = [(0.0, top - cap_h, thick, 0.0, 0)]
    if cap:
        courses.append((top - cap_h, top, thick + 2.0 * cap_overhang, step * 0.5, 1))

    mats = []
    for i in range(2):
        entry = pal[min(i, len(pal) - 1)]
        mats.append(_simple_mat(entry["name"],
                                material_color(entry, (0.72, 0.69, 0.6)),
                                entry.get("roughness", 0.93),
                                texture=entry.get("texture"),
                                tex_scale=entry.get("tex_scale", 3.0),
                                tex_seed=seed))

    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    blocks = 0
    course_spans = []  # one (a0, a1) list per course, for the continuity check
    max_top = 0.0
    for z0, z1, c_thick, offset, mat_index in courses:
        spans = []
        for i in range(n):
            a0 = offset + i * step
            a1 = a0 + step
            if gap_n and any(abs(((a0 + step / 2.0) - g + math.pi) % (2.0 * math.pi) - math.pi)
                             < gap_half for g in gap_centres):
                continue
            aa0, aa1 = a0 + joint / 2.0, a1 - joint / 2.0
            dr = r_mid * (1.0 + rng.uniform(-jitter, jitter) * 0.25)
            dt = c_thick * (1.0 + rng.uniform(-jitter, jitter))
            zt = z1 + rng.uniform(-0.4, 0.4) * 0.012 if mat_index == 1 else z1
            _kerb_block(bm, uv_layer, cx, cy, aa0, aa1, dr, dt, z0, zt, chamfer, mat_index)
            spans.append((aa0, aa1))
            blocks += 1
            max_top = max(max_top, zt)
        course_spans.append(spans)

    mesh = bpy.data.meshes.new(f"{name}_mesh")
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    for mat in mats:
        ob.data.materials.append(mat)
    _place(ob, name)

    # Largest uncovered arc, judged course by course: one course can hide the
    # other's holes (the capped course is offset half a block), so a per-course
    # number is the honest tripwire for a bed boundary that has to close.
    holes = [h for spans in course_spans for h in _arc_holes(spans)]
    laid = min((sum(a1 - a0 for a0, a1 in spans) for spans in course_spans),
               default=0.0)
    STATE.setdefault("kerb", []).append({
        "node": name,
        "radius": r_mid,
        "thickness": thick,
        "height": max_top,
        "segments": n,
        "gaps": gap_n,
        "gap_deg": float(gap_deg),
        "blocks": blocks,
        "outer_radius": r_mid + thick / 2.0 + (float(cap_overhang) if cap else 0.0),
        "inner_radius": r_mid - thick / 2.0 - (float(cap_overhang) if cap else 0.0),
        "courses": len(courses),
        "hole_max_deg": math.degrees(max(holes)) if holes else 0.0,
        "coverage_deg": math.degrees(laid),
    })
    return ob


# Registry used by the recipe compiler
BUILDERS = {
    "box": create_box, "rounded_box": create_rounded_box, "cylinder": create_cylinder,
    "tapered_cylinder": create_tapered_cylinder, "sphere": create_sphere, "torus": create_torus,
    "arch": create_arch, "panel": create_panel, "pipe": create_pipe, "curve": create_curve,
    "extrusion": create_extrusion, "lathe": create_lathe, "rock": create_rock, "beam": create_beam,
    "barrel": build_barrel, "crate": build_crate, "roof": build_roof, "window": build_window,
    "door": build_door, "stairs": build_stairs, "column": build_column, "wall": build_wall,
    "fence": build_fence, "tree": build_tree, "foliage_cluster": build_foliage_cluster,
    "stone_kerb": build_stone_kerb,
    "weapon_blade": build_weapon_blade,
    "weapon_handle": build_weapon_handle,
    "text": build_text,
    "terrain": build_terrain, "scatter": build_scatter, "skydome": build_skydome,
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
    # Visible early so order-dependent builders (scatter reads the terrain node
    # spec and earlier nodes' outputs) resolve against the recipe being built.
    STATE["recipe"] = recipe
    STATE["manifest"] = manifest
    STATE["foliage"] = []
    STATE["kerb"] = []
    node_tris = {}
    ignored_params = {}
    for node_name, spec in recipe.items():
        builder = BUILDERS.get(spec.get("builder"))
        if not builder:
            raise ValueError(f"unknown builder {spec.get('builder')!r} for node {node_name!r}")
        kwargs = {k: v for k, v in spec.items() if k not in ("builder", "material")}
        kwargs = _resolve_detail(builder, kwargs)
        unknown = _unrecognized_params(builder, kwargs)
        if unknown:
            # Name the accepted parameters too, so the caller can fix it in one step instead
            # of guessing again.
            ignored_params[node_name] = {"ignored": unknown,
                                         "accepted": _builder_params(builder)}
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
            "ignored_params": ignored_params,
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
        old_name = main.name
        main.name = STATE["asset_name"] or "asset"
        if main.name != old_name:
            # keep the manifest truthful: it recorded pre-assembly node names
            for objs in (STATE.get("manifest") or {}).get("objects", {}).values():
                objs[:] = [main.name if n == old_name else n for n in objs]
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
    mat = _simple_mat(params["name"], material_color(params, (0.6, 0.6, 0.6)),
                      params.get("roughness", 0.6), params.get("metallic", 0.0),
                      params.get("texture"), params.get("tex_scale", 3.0),
                      params.get("tex_blend", 0.5), params.get("tex_seed", 0))
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

# Flat world background for renders (linear); doubled as the exclusion color
# for color-coverage measurement. Single source of truth with _setup_render.
RENDER_BG = (0.85, 0.85, 0.88)


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
        bg.inputs[0].default_value = (*RENDER_BG, 1.0)
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

    # Foliage-specific checks, only when a canopy was built this session.
    for rec in STATE.get("foliage") or []:
        float_gap = float(rec.get("leaf_float_max", 0.0))
        check(f"foliage_leaf_contact[{rec['node']}]", float_gap <= 0.02,
              f"{float_gap:.3f} max leaf-base gap ({rec.get('leaves')} leaves)")
        # Silhouette irregularity: radial spread of the canopy in 8 yaw sectors.
        # A sphere or a ring of equal blobs scores low; an organic canopy does not.
        pts = [o.matrix_world @ v.co for o in obs for v in o.data.vertices]
        if pts:
            cxx = sum(p.x for p in pts) / len(pts)
            cyy = sum(p.y for p in pts) / len(pts)
            radii = [0.0] * 8
            for p in pts:
                ang = math.atan2(p.y - cyy, p.x - cxx)
                k = int((ang + math.pi) / (math.pi / 4.0)) % 8
                d = math.hypot(p.x - cxx, p.y - cyy)
                radii[k] = max(radii[k], d)
            mean = sum(radii) / 8.0 or 1e-6
            spread = (sum((r - mean) ** 2 for r in radii) / 8.0) ** 0.5 / mean
            check(f"foliage_silhouette[{rec['node']}]", spread >= 0.05,
                  f"{spread:.3f} radial spread across 8 sectors (>= 0.05)")

    # Kerb-specific check: a raised bed boundary has to close. The joint is a
    # hairline, so anything wider than 2 degrees means a block never got laid.
    for rec in STATE.get("kerb") or []:
        allowed = (rec.get("gap_deg", 0.0) + 2.0) if rec.get("gaps") else 2.0
        check(f"kerb_continuity[{rec['node']}]", rec.get("hole_max_deg", 0.0) <= allowed,
              f"{rec.get('hole_max_deg', 0.0):.2f} deg largest hole across "
              f"{rec.get('courses')} course(s) / {rec.get('blocks')} blocks, "
              f"{rec.get('coverage_deg', 0.0):.1f} deg laid per course")

    return {"pass": ok, "checks": checks, "triangles": tris, "budget": budget,
            "object_count": len(obs),
            "foliage": STATE.get("foliage") or []}


# ---- blueprint: compile the v1 contract to recipe nodes, check assertions ---


RELATION_PREDICATES = ("sits_on", "centered_on", "attached_to", "inset_in", "beside")


def _builder_required(builder):
    """Recipe keys a builder cannot work without: params with no default."""
    import inspect as _inspect
    kinds = (_inspect.Parameter.POSITIONAL_OR_KEYWORD, _inspect.Parameter.KEYWORD_ONLY)
    return sorted(n for n, p in _inspect.signature(builder).parameters.items()
                  if n != "name" and p.kind in kinds and p.default is _inspect.Parameter.empty)


def _builder_accepts_location(builder):
    """Whether a location param reaches the builder, explicitly or via **kw."""
    import inspect as _inspect
    params = _inspect.signature(builder).parameters
    if "location" in params:
        return True
    return any(p.kind is _inspect.Parameter.VAR_KEYWORD for p in params.values())


def op_compile_blueprint(params):
    """Validate a v1 asset blueprint and compile it to recipe nodes plus warnings."""
    bp = params.get("blueprint")
    if not isinstance(bp, dict):
        raise ValueError("blueprint must be an object")
    parts = bp.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("blueprint needs a non-empty parts list")
    materials = bp.get("materials") or {}
    if not isinstance(materials, dict):
        raise ValueError("blueprint materials must be an object")
    profile = (bp.get("detail") or {}).get("profile", "game")
    if profile not in DETAIL_PROFILES:
        raise ValueError(f"unknown detail profile {profile!r}; use one of {sorted(DETAIL_PROFILES)}")
    relations = bp.get("relations") or []
    if not isinstance(relations, list):
        raise ValueError("blueprint relations must be a list")
    recipe, warnings = {}, []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("every part must be an object")
        node = part.get("node")
        if not node or not isinstance(node, str):
            raise ValueError("every part needs a string node name")
        if node in recipe:
            raise ValueError(f"duplicate node {node!r}")
        bname = part.get("builder")
        builder = BUILDERS.get(bname)
        if not builder:
            raise ValueError(f"unknown builder {bname!r} for node {node!r}")
        pparams = part.get("params") or {}
        if not isinstance(pparams, dict):
            raise ValueError(f"node {node!r} params must be an object")
        missing = [k for k in _builder_required(builder) if k not in pparams]
        if missing:
            raise ValueError(f"node {node!r} misses required {bname} params: {missing}")
        mref = part.get("material")
        if mref is not None:
            mat = materials.get(mref)
            if not isinstance(mat, dict):
                raise ValueError(f"node {node!r} references unknown material {mref!r}")
            c = mat.get("color")
            if c is not None and not (isinstance(c, (list, tuple)) and len(c) == 3 and
                                      all(isinstance(v, (int, float)) for v in c)):
                raise ValueError(f"material {mref!r} color must be [r, g, b]")
        spec = dict(pparams)
        if "location" not in spec and _builder_accepts_location(builder):
            c = part.get("center")
            if c is None:
                warnings.append(f"node {node!r} has no placement; defaults to origin")
            elif not (isinstance(c, (list, tuple)) and len(c) == 3 and
                      all(isinstance(v, (int, float)) for v in c)):
                raise ValueError(f"node {node!r} center must be [x, y, z]")
            else:
                spec["location"] = list(c)
        if mref is not None:
            mat = materials[mref]
            spec["material"] = {"name": mref, **{k: mat[k] for k in
                                                     ("color", "roughness", "metallic", "emission")
                                                     if k in mat}}
        spec.setdefault("detail", profile)
        recipe[node] = {"builder": bname, **spec}
        if isinstance(part.get("confidence"), (int, float)) and part["confidence"] < 0.7:
            warnings.append(f"node {node!r} confidence {part['confidence']} — verify or refine after build")
    for rel in relations:
        if not isinstance(rel, dict):
            raise ValueError("every relation must be an object")
        for end in ("subject", "object"):
            if rel.get(end) not in recipe:
                raise ValueError(f"relation names unknown node {rel.get(end)!r}")
        if rel.get("predicate") not in RELATION_PREDICATES:
            warnings.append(f"relation {rel.get('subject')!r}->{rel.get('object')!r} "
                            f"uses non-standard predicate {rel.get('predicate')!r}")
    depth = bp.get("depth") or {}
    if not isinstance(depth, dict):
        raise ValueError("blueprint depth must be an object")
    if "view" in depth and depth["view"] not in _VIEW_RAYS:
        raise ValueError(f"unknown depth view {depth['view']!r}")
    for pair in depth.get("occludes") or []:
        if not isinstance(pair, dict):
            raise ValueError("every depth occludes entry must be an object")
        for end in ("front", "behind"):
            if pair.get(end) not in recipe:
                raise ValueError(f"depth pair names unknown node {pair.get(end)!r}")
    return {"recipe": recipe, "warnings": warnings}


def _bounds_of(obs):
    """World-space [sx, sy, sz] union sizes of the given objects."""
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for o in obs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return [hi[i] - lo[i] for i in range(3)]


def _measure_operand(token):
    """A 'node.dim' operand to a size, or (None, reason) when not measurable."""
    name, sep, dim = token.rpartition(".") if isinstance(token, str) else ("", "", "")
    if not sep or dim not in "xyz":
        return None, f"bad operand {token!r}; use 'node.x|y|z'"
    manifest = STATE.get("manifest") or {}
    names = (manifest.get("objects") or {}).get(name, [])
    obs = [bpy.data.objects[n] for n in names if n in bpy.data.objects]
    if not obs:
        ob = bpy.data.objects.get(name)
        obs = [ob] if ob is not None else []
    obs = [o for o in obs if o.type == "MESH"]
    if not obs:
        if name in (manifest.get("objects") or {}):
            return None, f"{name!r} has no live objects (joined away?)"
        return None, f"unknown node {name!r}"
    return _bounds_of(obs)["xyz".index(dim)], None


def _symmetry_score(axis="x"):
    """Share of asset verts mirrored across the asset center within 2% of max dim."""
    import mathutils
    obs = _asset_meshes()
    pts = [o.matrix_world @ v.co for o in obs for v in o.data.vertices]
    if not pts:
        return 0.0
    ax = "xyz".index(axis)
    size = max(max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3)) or 1.0
    c = (max(p[ax] for p in pts) + min(p[ax] for p in pts)) / 2
    kd = mathutils.kdtree.KDTree(len(pts))
    for i, p in enumerate(pts):
        kd.insert(p, i)
    kd.balance()
    tol = 0.02 * size
    ok = 0
    for p in pts:
        m = Vector(p)
        m[ax] = 2 * c - p[ax]
        if kd.find(m)[2] <= tol:
            ok += 1
    return ok / len(pts)


def _linear_to_srgb(c):
    """Scene-linear to sRGB; renders are display-transformed, recipe colors are not."""
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _view_coverage(path, target, tol):
    """Fraction of non-background pixels near target color in one render."""
    import numpy as _np
    img = bpy.data.images.load(path)
    try:
        w, h = img.size
        if w <= 0 or h <= 0:
            return None
        px = _np.array(img.pixels[:], dtype=_np.float64).reshape(-1, 4)[:, :3]
    finally:
        bpy.data.images.remove(img)
    bg = _np.array([_linear_to_srgb(c) for c in RENDER_BG])
    asset = _np.linalg.norm(px - bg, axis=1) > 0.08
    if asset.sum() == 0:
        return None
    tgt = _np.array([_linear_to_srgb(c) for c in target])
    match = asset & (_np.linalg.norm(px - tgt, axis=1) <= tol)
    return match.sum() / asset.sum(), asset.sum()


def _color_coverage(target, tol):
    """Pooled coverage over rendered views (wire excluded); None when unmeasurable."""
    d = STATE.get("last_render_dir")
    if not d or not os.path.isdir(d):
        return None, "no renders — run render_asset first"
    views, m_sum, a_sum = {}, 0, 0
    for v in sorted(VIEW_ANGLES):
        if v == "wire":
            continue  # workbench shading, not PBR colors
        p = os.path.join(d, f"{v}.png")
        if not os.path.isfile(p):
            continue
        try:
            got = _view_coverage(p, target, tol)
        except Exception:
            continue
        if got is None:
            continue
        cov, n = got
        views[v] = cov
        m_sum += cov * n
        a_sum += n
    if not views:
        return None, "no usable renders — run render_asset first"
    return m_sum / a_sum, views


# Viewer side -> ray travel dir; matches the VIEW_ANGLES render cameras.
_VIEW_RAYS = {
    "front": ((1, 0, 0), (-1, 0, 0)),
    "back": ((-1, 0, 0), (1, 0, 0)),
    "left": ((0, -1, 0), (0, 1, 0)),
    "right": ((0, 1, 0), (0, -1, 0)),
    "top": ((0, 0, 1), (0, 0, -1)),
}


def _node_live_objects(node):
    """Manifest objects for a node that still exist, else a same-named live mesh."""
    manifest = STATE.get("manifest") or {}
    names = (manifest.get("objects") or {}).get(node, [])
    obs = [bpy.data.objects[n] for n in names if n in bpy.data.objects]
    if not obs:
        ob = bpy.data.objects.get(node)
        obs = [ob] if ob is not None else []
    obs = [o for o in obs if o.type == "MESH"]
    if not obs:
        if node in (manifest.get("objects") or {}):
            return None, f"{node!r} has no live objects (joined away?)"
        return None, f"unknown node {node!r}"
    return obs, None


def _ray_hit_world(ob, origin, direction):
    """World distance to the first hit along a world ray, or None. Rest-pose geometry."""
    inv = ob.matrix_world.inverted()
    d = inv.to_3x3() @ Vector(direction)
    if d.length < 1e-12:
        return None
    hit, loc, _, _ = ob.ray_cast(inv @ Vector(origin), d.normalized())
    if not hit:
        return None
    return (ob.matrix_world @ loc - Vector(origin)).length


def _check_occlusion(a):
    """Share of behind-node surface hidden behind front-node geometry from a view."""
    for k in ("front", "behind"):
        if not isinstance(a.get(k), str):
            return {"status": "fail", "detail": f"occlusion needs string {k} node name"}
    view = a.get("view", "front")
    if view not in _VIEW_RAYS:
        return {"status": "fail", "detail": f"unknown view {view!r}"}
    fobs, fr = _node_live_objects(a["front"])
    bobs, br = _node_live_objects(a["behind"])
    if fr or br:
        return {"status": "manual", "detail": fr or br}
    try:
        minimum = float(a.get("min", 0.5))
    except (TypeError, ValueError):
        return {"status": "fail", "detail": "occlusion needs a numeric min"}
    to_viewer = Vector(_VIEW_RAYS[view][0])
    size = max(_bounds_of(_asset_meshes())) or 1.0
    eps = 1e-4 * size
    n = occ = 0
    for ob in bobs:
        for v in ob.data.vertices:
            o = ob.matrix_world @ v.co + to_viewer * eps
            n += 1
            for fob in fobs:
                if _ray_hit_world(fob, o, to_viewer) is not None:
                    occ += 1
                    break
    score = occ / n if n else 0.0
    ok = score >= minimum
    return {"status": "pass" if ok else "fail", "measured": round(score, 4),
            "expected": minimum,
            "detail": f"{score:.4f} of {a['behind']!r} hidden behind {a['front']!r} "
                      f"from {view} vs min {minimum}"}


def _check_mirror_depth(a):
    """Hidden-side second opinion: front depths vs mirrored back depths over a ray grid."""
    axis = a.get("axis", "x")
    if axis not in "xyz" or len(axis) != 1:
        return {"status": "fail", "detail": f"bad axis {axis!r}"}
    try:
        n = max(4, min(int(a.get("resolution", 32)), 64))
    except (TypeError, ValueError):
        return {"status": "fail", "detail": "mirror_depth needs an integer resolution"}
    try:
        minimum = float(a.get("min", 0.9))
    except (TypeError, ValueError):
        return {"status": "fail", "detail": "mirror_depth needs a numeric min"}
    obs = _asset_meshes()
    pts = [o.matrix_world @ v.co for o in obs for v in o.data.vertices]
    if not pts:
        return {"status": "fail", "detail": "no vertices to sample"}
    ax = "xyz".index(axis)
    u, v = [i for i in range(3) if i != ax]
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    size = max(hi[i] - lo[i] for i in range(3)) or 1.0
    margin, tol = 0.1 * size, 0.02 * size

    def depth(side):
        base = list(lo)
        base[ax] = hi[ax] + margin if side > 0 else lo[ax] - margin
        direction = [0, 0, 0]
        direction[ax] = -side
        out = []
        for iu in range(n):
            cu = lo[u] + (iu + 0.5) / n * (hi[u] - lo[u])
            for iv in range(n):
                cell = list(base)
                cell[u], cell[v] = cu, lo[v] + (iv + 0.5) / n * (hi[v] - lo[v])
                best = None
                for ob in obs:
                    t = _ray_hit_world(ob, cell, direction)
                    if t is not None and (best is None or t < best):
                        best = t
                out.append(best)
        return out

    match = 0
    front, back = depth(1), depth(-1)
    cells = len(front)
    for dp, dm in zip(front, back):
        if dp is None and dm is None:
            match += 1
        elif dp is not None and dm is not None and abs(dp - dm) <= tol:
            match += 1
    score = match / cells
    ok = score >= minimum
    return {"status": "pass" if ok else "fail", "measured": round(score, 4),
            "expected": minimum, "detail": f"{score:.4f} mirrored cells vs min {minimum}"}


def _check_one(a, materials):
    """One assertion to a result dict; unknown checks fail, vision-side checks stay manual."""
    name = a.get("name") if isinstance(a, dict) else None
    kind = a.get("check") if isinstance(a, dict) else None
    if kind == "part_count":
        try:
            expected = int(a["expected"])
        except (KeyError, TypeError, ValueError):
            return {"check": kind, "name": name, "status": "fail",
                    "detail": "part_count needs an integer expected"}
        manifest = STATE.get("manifest")
        if manifest and manifest.get("objects") is not None:
            n, basis = len(manifest["objects"]), "manifest nodes"
        else:
            n, basis = len(_asset_meshes()), "meshes"
        ok = n == expected
        return {"check": kind, "name": name, "status": "pass" if ok else "fail",
                "measured": n, "expected": expected, "detail": f"{n} vs {expected} ({basis})"}
    if kind == "ratio":
        of = a.get("of")
        if not (isinstance(of, list) and len(of) == 2):
            return {"check": kind, "name": name, "status": "fail",
                    "detail": "ratio needs of: [a.dim, b.dim]"}
        va, ra = _measure_operand(of[0])
        vb, rb = _measure_operand(of[1])
        if ra or rb:
            return {"check": kind, "name": name, "status": "manual",
                    "detail": ra or rb}
        try:
            expected, tol = float(a["expected"]), float(a.get("tolerance", 0.1))
        except (KeyError, TypeError, ValueError):
            return {"check": kind, "name": name, "status": "fail",
                    "detail": "ratio needs a numeric expected"}
        if vb == 0:
            return {"check": kind, "name": name, "status": "fail", "detail": "zero denominator"}
        m = va / vb
        ok = abs(m - expected) <= tol
        return {"check": kind, "name": name, "status": "pass" if ok else "fail",
                "measured": round(m, 4), "expected": expected,
                "detail": f"{m:.4f} vs {expected} ± {tol}"}
    if kind in ("symmetry_x", "symmetry_y", "symmetry_z"):
        try:
            minimum = float(a.get("min", 0.8))
        except (TypeError, ValueError):
            return {"check": kind, "name": name, "status": "fail",
                    "detail": "symmetry needs a numeric min"}
        s = _symmetry_score(kind.split("_")[1])
        ok = s >= minimum
        return {"check": kind, "name": name, "status": "pass" if ok else "fail",
                "measured": round(s, 4), "expected": minimum,
                "detail": f"{s:.4f} vs min {minimum}"}
    if kind == "color_present":
        target, mref = a.get("color"), a.get("material")
        if target is None and mref is not None:
            mat = materials.get(mref)
            target = mat.get("color") if isinstance(mat, dict) else None
        if target is None:
            return {"check": kind, "name": name, "status": "manual",
                    "expected": mref, "detail": "no resolvable color — verify against renders"}
        if not (isinstance(target, (list, tuple)) and len(target) == 3 and
                all(isinstance(v, (int, float)) for v in target)):
            return {"check": kind, "name": name, "status": "fail",
                    "detail": f"bad color {target!r}; use [r, g, b]"}
        try:
            minimum = float(a["min_coverage"])
        except (KeyError, TypeError, ValueError):
            return {"check": kind, "name": name, "status": "fail",
                    "detail": "color_present needs a numeric min_coverage"}
        try:
            tol = float(a.get("tolerance", 0.3))
        except (TypeError, ValueError):
            return {"check": kind, "name": name, "status": "fail",
                    "detail": "color tolerance must be numeric"}
        cov, info = _color_coverage(target, tol)
        if cov is None:
            return {"check": kind, "name": name, "status": "manual",
                    "expected": minimum, "detail": info}
        ok = cov >= minimum
        return {"check": kind, "name": name, "status": "pass" if ok else "fail",
                "measured": round(cov, 4), "expected": minimum,
                "detail": f"{cov:.4f} vs min {minimum} pooled over {len(info)} views",
                "views": {v: round(c, 4) for v, c in info.items()}}
    if kind == "occlusion":
        r = _check_occlusion(a)
        return {"check": kind, "name": name, **r}
    if kind == "mirror_depth":
        r = _check_mirror_depth(a)
        return {"check": kind, "name": name, **r}
    return {"check": kind, "name": name, "status": "fail",
            "detail": f"unknown check {kind!r}"}


def op_check_assertions(params):
    """Measure blueprint assertions against the live asset; vision-side ones stay manual."""
    assertions = params.get("assertions")
    if not isinstance(assertions, list):
        raise ValueError("assertions must be a list")
    if not _asset_meshes():
        raise ValueError("nothing to check — build first")
    materials = params.get("materials") or {}
    if not isinstance(materials, dict):
        raise ValueError("materials must be an object")
    results = [_check_one(a, materials) for a in assertions]
    return {"pass": all(r["status"] in ("pass", "manual") for r in results),
            "results": results}


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


def _principled_of(mat):
    """The Principled BSDF of a node material, or None."""
    if mat is None or not mat.use_nodes:
        return None
    return mat.node_tree.nodes.get("Principled BSDF")


def _load_bake_image(path, non_color):
    img = bpy.data.images.load(path)
    if non_color:
        try:
            img.colorspace_settings.name = "Non-Color"
        except (AttributeError, TypeError):
            pass
    return img


def _apply_baked_materials(ob, baked):
    """Rebuild every material slot of a finalized copy around its baked PNGs.

    Procedural node chains (wood grain, stone, ...) evaluate fine in Blender
    but the glTF exporter cannot see through them: a linked Base Color socket
    exports as default white. Pointing each slot at the just-baked albedo /
    roughness / normal / metallic images makes the export match the render.
    Emission and metallic scalars are carried over from the originals.
    Copies are discarded after export, so scene originals keep live materials.
    Returns the names of the export materials created.
    """
    if not isinstance(baked, dict) or "albedo" not in baked:
        return []
    wired = []
    for slot in ob.material_slots:
        src = slot.material
        if src is None:
            continue
        bsdf = _principled_of(src)
        mat = bpy.data.materials.new((src.name or "mat") + "_baked")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        for n in list(nodes):
            nodes.remove(n)
        out = nodes.new("ShaderNodeOutputMaterial")
        princ = nodes.new("ShaderNodeBsdfPrincipled")
        links.new(princ.outputs.get("BSDF"), out.inputs.get("Surface"))
        try:
            alb = nodes.new("ShaderNodeTexImage")
            alb.image = _load_bake_image(baked["albedo"], False)
            links.new(alb.outputs.get("Color"), princ.inputs.get("Base Color"))
        except (KeyError, RuntimeError):
            pass
        if "roughness" in baked:
            try:
                rgh = nodes.new("ShaderNodeTexImage")
                rgh.image = _load_bake_image(baked["roughness"], True)
                links.new(rgh.outputs.get("Color"), princ.inputs.get("Roughness"))
            except (KeyError, RuntimeError):
                pass
        if "normal" in baked:
            try:
                nrm = nodes.new("ShaderNodeTexImage")
                nrm.image = _load_bake_image(baked["normal"], True)
                nmap = nodes.new("ShaderNodeNormalMap")
                links.new(nrm.outputs.get("Color"), nmap.inputs.get("Color"))
                links.new(nmap.outputs.get("Normal"), princ.inputs.get("Normal"))
            except (KeyError, RuntimeError):
                pass
        if "metallic" in baked:
            try:
                met = nodes.new("ShaderNodeTexImage")
                met.image = _load_bake_image(baked["metallic"], True)
                links.new(met.outputs.get("Color"), princ.inputs.get("Metallic"))
            except (KeyError, RuntimeError):
                pass
        elif bsdf is not None and "Metallic" in bsdf.inputs:
            try:
                princ.inputs["Metallic"].default_value = float(bsdf.inputs["Metallic"].default_value)
            except (KeyError, TypeError, ValueError):
                pass
        if bsdf is not None:
            for key in ("Emission Color", "Emission Strength"):
                if key in bsdf.inputs and key in princ.inputs:
                    try:
                        princ.inputs[key].default_value = bsdf.inputs[key].default_value
                    except (KeyError, TypeError):
                        pass
        slot.material = mat
        wired.append(mat.name)
    return wired


def _merge_identical_materials(ob):
    """Collapse material slots that sample the same baked texture set.

    After baking, every slot's colour, roughness and normal live in one shared
    atlas at its own UV region, so slots pointing at the same images render
    identically. Merging them removes glTF primitives (one draw call per
    material slot) without losing a single texel of variation.
    """
    if not ob.material_slots:
        return 0
    key_to_index = {}
    keep = []
    remap = {}
    for i, slot in enumerate(ob.material_slots):
        mat = slot.material
        if mat is None or not mat.use_nodes:
            remap[i] = len(keep)
            keep.append(mat)
            continue
        paths = tuple(sorted(n.image.filepath for n in mat.node_tree.nodes
                             if n.type == "TEX_IMAGE" and n.image is not None))
        if not paths:
            remap[i] = len(keep)
            keep.append(mat)
            continue
        if paths not in key_to_index:
            key_to_index[paths] = len(keep)
            keep.append(mat)
        remap[i] = key_to_index[paths]
    if len(keep) == len(ob.material_slots):
        return 0
    for poly in ob.data.polygons:
        poly.material_index = remap.get(poly.material_index, poly.material_index)
    ob.data.materials.clear()
    for mat in keep:
        ob.data.materials.append(mat)
    return len(ob.material_slots) and (len(remap) - len(keep))


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

    arm_ob = next((m.object for ob in obs for m in ob.modifiers if m.type == "ARMATURE" and m.object), None)

    # Finalize decimates temporary copies, never the scene: originals keep their
    # geometry and modifier stacks, so inspect/validate stay honest afterwards and a
    # second finalize cannot compound the reduction. Copies take the originals' object
    # and mesh names (originals renamed out of the way) so exported GLBs are unchanged.
    created = []
    try:
        for ob in obs:
            if ob.data.shape_keys:
                raise ValueError(f"finalize does not support shape keys (object {ob.name!r})")
            rec = {"cp": None, "data": None, "orig": ob,
                   "name": ob.name, "data_name": ob.data.name}
            created.append(rec)  # tracked before any mutation, so mid-setup failures restore
            ob.name = rec["name"] + "__src"
            ob.data.name = rec["data_name"] + "__src"
            cp = ob.copy()
            cp.data = ob.data.copy()
            cp.name = rec["name"]
            cp.data.name = rec["data_name"]
            bpy.context.scene.collection.objects.link(cp)
            rec["cp"], rec["data"] = cp, cp.data
        copies = [r["cp"] for r in created]

        def _decimate(targets, mod_name, ratio):
            for ob in targets:
                m = ob.modifiers.new(mod_name, "DECIMATE")  # appended last: order preserved
                m.ratio = ratio
                bpy.ops.object.select_all(action="DESELECT")
                ob.select_set(True)
                bpy.context.view_layer.objects.active = ob
                bpy.ops.object.modifier_apply(modifier=mod_name)

        if total > budget:
            # proportional decimate on every copy so the asset total meets the budget
            _decimate(copies, "Decimate", max(0.05, budget / total))

        textures = {}
        baked_by_copy = {}
        if params.get("bake", True):
            for ob in copies:
                prefix = ob.name if len(obs) > 1 else name
                baked = {ch: p for ch, p in
                         _bake_textures(ob, resolution, channels, out_dir, prefix=prefix).items()
                         if ch in channels}
                for ch, p in baked.items():
                    textures[ch if len(obs) == 1 else f"{prefix}_{ch}"] = p
                if baked:
                    baked_by_copy[ob.name] = baked
        # Point the export copies at their baked PNGs so the GLB matches the
        # render: procedural chains alone export as white (see
        # _apply_baked_materials). Scene originals are untouched.
        exported_materials = []
        for ob in copies:
            if ob.name in baked_by_copy:
                exported_materials.extend(_apply_baked_materials(ob, baked_by_copy[ob.name]))
        # Default on: after baking, every slot samples the same atlas images and a
        # material only contributes a glTF primitive (draw call). Merging is
        # pixel-identical because each region's colour and roughness are baked
        # into its own UV patch. Pass merge_materials=false to keep slots apart.
        merged_slots = 0
        if params.get("merge_materials", True):
            for ob in copies:
                if ob.name in baked_by_copy:
                    merged_slots += _merge_identical_materials(ob)

        def _export_with_rig(path):
            bpy.ops.object.select_all(action="DESELECT")
            for ob in copies:
                ob.select_set(True)
            if arm_ob:
                arm_ob.select_set(True)
                bpy.context.view_layer.objects.active = copies[0]
            # export_apply is unsafe with armature modifiers; modifiers are already
            # manually applied, so skipping it is safe either way.
            bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                                      export_yup=True)

        paths = {"main": os.path.join(out_dir, f"{name}.glb")}
        _export_with_rig(paths["main"])
        main_tris = sum(_tri_count(o) for o in copies)  # measured before LOD decimation

        if lods:
            for i, lod_budget in enumerate(lods[1:], start=1):  # first entry = main budget
                t = sum(_tri_count(o) for o in copies)
                if t > lod_budget:
                    _decimate(copies, f"LOD{i}_dec", max(0.03, lod_budget / t))
                p = os.path.join(out_dir, f"{name}_LOD{i}.glb")
                _export_with_rig(p)
                paths[f"LOD{i}"] = p

        if with_collision:
            col = _collision_for(copies)
            if col:
                p = os.path.join(out_dir, f"{name}_collision.glb")
                bpy.ops.object.select_all(action="DESELECT")
                col.select_set(True)
                bpy.context.view_layer.objects.active = col
                bpy.ops.export_scene.gltf(filepath=p, export_format="GLB", use_selection=True, export_yup=True)
                paths["collision"] = p
                bpy.data.objects.remove(col, do_unlink=True)
    finally:
        # clone objects first, then their zero-user mesh datablocks, then names back
        for rec in created:
            if rec["cp"] is not None:
                bpy.data.objects.remove(rec["cp"], do_unlink=True)
        for rec in created:
            if rec["data"] is not None and rec["data"].users == 0:
                bpy.data.meshes.remove(rec["data"])
        for rec in created:
            rec["orig"].name = rec["name"]
            rec["orig"].data.name = rec["data_name"]

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
        "baked_materials": sorted(set(exported_materials)),
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


def _probe_object_tris(params):
    """Per-object triangle counts of the asset; test instrumentation for which objects changed."""
    return {o.name: _tri_count(o) for o in _asset_meshes()}


def _probe_sleep(params):
    """Sleep without responding; test instrumentation for the server-side call timeout."""
    import time as _time
    _time.sleep(min(float(params.get("seconds", 60)), 300))
    return {"slept": True}


def _probe_material_nodes(params):
    """Node types in a material; test instrumentation for procedural presets."""
    mat = bpy.data.materials.get(params.get("material", ""))
    if mat is None or not mat.use_nodes:
        return {"node_types": [], "node_count": 0}
    kinds = sorted(n.bl_idname for n in mat.node_tree.nodes)
    return {"node_types": kinds, "node_count": len(kinds)}


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
    "compile_blueprint": op_compile_blueprint,
    "check_assertions": op_check_assertions,
    "finalize": op_finalize,
    "rig": op_rig,
    "import_glb": op_import_glb,
    "_probe_deform": _probe_deform,
    "_probe_vertex_groups": _probe_vertex_groups,
    "_probe_import_meta": _probe_import_meta,
    "_probe_scene": _probe_scene,
    "_probe_scale": _probe_scale,
    "_probe_object_tris": _probe_object_tris,
    "_probe_sleep": _probe_sleep,
    "probe_material_nodes": _probe_material_nodes,
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
