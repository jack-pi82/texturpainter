# SPDX-License-Identifier: GPL-3.0-or-later
# Texturpainter - Copyright (C) 2026 jack-pi82

bl_info = {
    "name": "Texturpainter - paint 3D models in your 2D painting app",
    "author": "jack-pi82",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > Texturpainter",
    "description": "Send the view to CSP/Krita/PhotoCraft, paint there, projected back onto the model live",
    "category": "Paint",
}

import math
import os
import re
import shutil
import subprocess
import time

import bpy
import numpy as np
from bpy.props import (
    BoolProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.app.translations import pgettext_iface as iface_
from mathutils import Euler, Vector

ADDON_ID = __package__ or __name__

# Meldungen in Blenders eingestellter Sprache (Fallback: Englisch)
rpt_ = getattr(bpy.app.translations, "pgettext_rpt", bpy.app.translations.pgettext_tip)

PAINTERS = [
    ("CSP", "Clip Studio Paint", "Clip Studio Paint"),
    ("KRITA", "Krita", "Krita"),
    ("PHOTOCRAFT", "PhotoCraft", "PhotoCraft (ArtCraft)"),
    ("CUSTOM", "Custom program", "Another painting program"),
]
PAINTER_NAMES = {key: name for key, name, _ in PAINTERS}
PAINTER_PATH_PROP = {
    "CSP": "path_csp",
    "KRITA": "path_krita",
    "PHOTOCRAFT": "path_photocraft",
    "CUSTOM": "path_custom",
}

# Blickrichtungen für den Rundum-Modus: Euler-Winkel der Ansicht in Grad
VIEW_ROTATIONS = {
    "front": (90, 0, 0),
    "right": (90, 0, 90),
    "back": (90, 0, 180),
    "left": (90, 0, -90),
    "top": (0, 0, 0),
    "bottom": (180, 0, 0),
    "front_right": (90, 0, 45),
    "front_left": (90, 0, -45),
    "back_right": (90, 0, 135),
    "back_left": (90, 0, -135),
}
VIEW_LAYOUTS = {
    "6": (4, ["front", "right", "back", "left", "top", "bottom"]),
    "8": (4, ["front", "right", "back", "left", "top", "bottom", "front_left", "front_right"]),
    "10": (5, ["front", "right", "back", "left", "top",
               "front_right", "back_right", "back_left", "front_left", "bottom"]),
}


class CSPError(Exception):
    pass


# Laufzeit-Daten pro Szene, werden nicht in der .blend gespeichert:
# backup = Textur vor der Aufnahme, live_mtime / live_time / live_error = Live-Status
_RUNTIME = {}


def runtime(state):
    return _RUNTIME.setdefault(state.id_data.name, {})


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------

class TEXTURPAINTER_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    painter: EnumProperty(name="Painting program", items=PAINTERS, default="CSP")
    path_csp: StringProperty(
        name="Clip Studio Paint", subtype="FILE_PATH",
        default=r"C:\Program Files\CELSYS\CLIP STUDIO 1.5\CLIP STUDIO PAINT\CLIPStudioPaint.exe",
    )
    path_krita: StringProperty(
        name="Krita", subtype="FILE_PATH",
        default=r"C:\Program Files\Krita (x64)\bin\krita.exe",
    )
    path_photocraft: StringProperty(
        name="PhotoCraft", subtype="FILE_PATH",
        default="",
    )
    path_custom: StringProperty(name="Custom program", subtype="FILE_PATH", default="")

    grab_size: IntProperty(
        name="Capture size (view)", default=2048, min=256, max=8192,
        description="Longest image side in pixels when a single view is sent",
    )
    tile_size: IntProperty(
        name="Tile size (all sides)", default=1024, min=256, max=4096,
        description="Longest side of each tile in the all-sides grid",
    )
    texture_size: EnumProperty(
        name="Texture size", default="2048",
        items=[("2048", "2048 px", "2048 x 2048"), ("4096", "4096 px", "4096 x 4096")],
        description="Size of a newly created texture",
    )
    views: EnumProperty(
        name="All-sides views", default="6",
        items=[
            ("6", "6 views", "front, back, left, right, top, bottom"),
            ("8", "8 views", "6 views + front left/right at 45°"),
            ("10", "10 views", "6 views + all four 45° views"),
        ],
    )
    normal_angle: IntProperty(
        name="Angle (view)", default=80, min=10, max=90,
        description="Faces tilted more than this angle are not painted. "
                    "Smaller = softer transitions, but less coverage",
    )
    grid_normal_angle: IntProperty(
        name="Angle (all sides)", default=60, min=55, max=90,
        description="Like Angle (view), but for all-sides mode. Below 55 leaves gaps, "
                    "above that the views paint over each other",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "painter")
        box = layout.box()
        box.label(text="Program paths (.exe)")
        box.prop(self, "path_csp")
        box.prop(self, "path_krita")
        box.prop(self, "path_photocraft")
        box.prop(self, "path_custom")
        box = layout.box()
        box.label(text="Sizes")
        box.prop(self, "grab_size")
        box.prop(self, "tile_size")
        box.prop(self, "texture_size")
        box.prop(self, "views")
        box.prop(self, "normal_angle")
        box.prop(self, "grid_normal_angle")


class TEXTURPAINTER_State(bpy.types.PropertyGroup):
    use_grid: BoolProperty(
        name="All-sides mode",
        description="Send all sides at once as one grid image to the painting program",
        default=False,
    )
    mode: EnumProperty(
        items=[("NONE", "None", ""), ("SINGLE", "View", ""), ("GRID", "All sides", "")],
        default="NONE",
    )
    png_path: StringProperty(subtype="FILE_PATH")
    capture_mtime: StringProperty()  # als Text, FloatProperty ist zu ungenau für Zeitstempel
    object_name: StringProperty()
    image_names: StringProperty()
    cols: IntProperty()
    rows: IntProperty()
    tile_w: IntProperty()
    tile_h: IntProperty()
    counter: IntProperty()


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def get_prefs(context):
    return context.preferences.addons[ADDON_ID].preferences


def painter_name(prefs):
    return iface_(PAINTER_NAMES.get(prefs.painter, "Painting program"))


def painter_exe(prefs):
    name = painter_name(prefs)
    raw = getattr(prefs, PAINTER_PATH_PROP[prefs.painter], "")
    path = bpy.path.abspath(raw.strip().strip('"'))
    if not path:
        raise CSPError(rpt_("No path set for {}. Please enter it in the add-on preferences.").format(name))
    if not os.path.isfile(path):
        short = "CSP" if prefs.painter == "CSP" else name
        raise CSPError(rpt_("{} not found: {} - please check the path in the add-on preferences.")
                       .format(short, path))
    return path


def open_in_painter(prefs, path):
    exe = painter_exe(prefs)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen([exe, path], close_fds=True, creationflags=flags)
    except OSError as ex:
        raise CSPError(rpt_("Could not start {}: {}").format(painter_name(prefs), ex))


def find_view3d(context):
    area = context.area if context.area and context.area.type == "VIEW_3D" else None
    if area is None and context.screen:
        area = next((a for a in context.screen.areas if a.type == "VIEW_3D"), None)
    if area is None:
        raise CSPError(rpt_("No 3D Viewport found. Please use the button in the 3D Viewport sidebar."))
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region is None:
        raise CSPError(rpt_("The 3D Viewport has no drawing area."))
    return area, region, area.spaces.active, region.data


def work_dir():
    if bpy.data.is_saved:
        blend = bpy.data.filepath
        base = os.path.splitext(os.path.basename(blend))[0]
        folder = os.path.join(os.path.dirname(blend), base + "_texturpainter")
    else:
        folder = os.path.join(bpy.app.tempdir, "texturpainter")
    os.makedirs(folder, exist_ok=True)
    return folder


PNG_NAME = re.compile(r"^[A-Za-z0-9_]+_(view|allsides|ansicht|rundum)_\d{3}\.png$")


def trusted_png_path(state):
    """Den in der .blend gespeicherten Pfad nur nutzen, wenn ihn das Addon selbst erzeugt hat.
    Reine Textprüfung ohne Dateizugriff: keine Netzwerkpfade, nur eigene Ordner und Dateinamen."""
    path = state.png_path
    if not path or path.startswith(("\\\\", "//")):
        return ""
    path = os.path.normpath(os.path.abspath(path))
    drive = os.path.splitdrive(path)[0]
    if os.name == "nt" and not re.fullmatch(r"[A-Za-z]:", drive):
        return ""
    folder = os.path.basename(os.path.dirname(path))
    if folder != "texturpainter" and not folder.endswith("_texturpainter"):
        return ""
    if not PNG_NAME.match(os.path.basename(path)):
        return ""
    return path


def remove_images(names):
    for name in names:
        img = bpy.data.images.get(name)
        if img is not None:
            bpy.data.images.remove(img)


def find_socket(sockets, identifier):
    return next((s for s in sockets if s.identifier == identifier), None)


def upstream_image_node(socket):
    """Sucht ein Image-Texture-Node, das (auch über Umwege) in diesen Eingang führt."""
    seen = set()
    todo = [link.from_node for link in socket.links]
    while todo:
        node = todo.pop(0)
        if node.as_pointer() in seen:
            continue
        seen.add(node.as_pointer())
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node
        for inp in node.inputs:
            for link in inp.links:
                todo.append(link.from_node)
    return None


def ensure_canvas(mat, size):
    """Sorgt dafür, dass das Material eine bemalbare Textur hat. Gibt das Bild zurück."""
    if mat.node_tree is None:
        try:
            mat.use_nodes = True
        except (AttributeError, TypeError):
            pass
    nt = mat.node_tree
    if nt is None:
        raise CSPError(rpt_("Material '{}' has no nodes.").format(mat.name))

    principled = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        principled = nt.nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if output is None:
            output = nt.nodes.new("ShaderNodeOutputMaterial")
            output.location = (300, 0)
        surface = find_socket(output.inputs, "Surface")
        if surface is not None and not surface.is_linked:
            nt.links.new(find_socket(principled.outputs, "BSDF"), surface)
    base = find_socket(principled.inputs, "Base Color")

    node = upstream_image_node(base) if base is not None and base.is_linked else None
    if node is None:
        node = next((n for n in nt.nodes if n.type == "TEX_IMAGE" and n.image is not None), None)

    if node is not None and node.image.size[0] > 0:
        img = node.image
    else:
        color = (0.8, 0.8, 0.8, 1.0)
        if base is not None and not base.is_linked:
            c = base.default_value
            color = (c[0], c[1], c[2], 1.0)
        img = bpy.data.images.new(f"{mat.name} Color", size, size, alpha=False)
        img.generated_color = color
        if node is None:
            node = nt.nodes.new("ShaderNodeTexImage")
            node.location = (principled.location.x - 350, principled.location.y)
        node.image = img
        if base is not None and not base.is_linked:
            nt.links.new(find_socket(node.outputs, "Color"), base)

    for n in nt.nodes:
        n.select = False
    node.select = True
    nt.nodes.active = node
    return img


def make_active(context, obj):
    if obj is None or obj.type != "MESH":
        raise CSPError(rpt_("Please select a model (mesh) first."))
    if not obj.visible_get():
        raise CSPError(rpt_("The model '{}' is hidden.").format(obj.name))
    view_layer = context.view_layer
    current = view_layer.objects.active
    if current is not obj:
        if current is not None and current.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in context.selected_objects:
            o.select_set(False)
        view_layer.objects.active = obj
    obj.select_set(True)


def ensure_setup(context, obj, prefs):
    """Erstes Mal: UVs, Material, Textur und Texture-Paint-Mode automatisch anlegen."""
    make_active(context, obj)

    if not obj.data.uv_layers:
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")

    size = int(prefs.texture_size)
    if len(obj.material_slots) == 0:
        mat = bpy.data.materials.new(f"{obj.name} Texturpainter")
        obj.data.materials.append(mat)
    else:
        fallback = None
        for slot in obj.material_slots:
            if slot.material is None:
                if fallback is None:
                    fallback = bpy.data.materials.new(f"{obj.name} Texturpainter")
                slot.material = fallback

    canvases = {}
    for slot in obj.material_slots:
        mat = slot.material
        if mat is not None and mat.name not in canvases:
            canvases[mat.name] = ensure_canvas(mat, size)

    if obj.mode != "TEXTURE_PAINT":
        bpy.ops.object.mode_set(mode="TEXTURE_PAINT")

    ip = context.scene.tool_settings.image_paint
    ip.mode = "MATERIAL"
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        img = canvases.get(mat.name)
        for i, slot_img in enumerate(mat.texture_paint_images):
            if slot_img == img:
                mat.paint_active_slot = i
                break
    return list(canvases.values())


def apply_paint_settings(context, angle):
    ip = context.scene.tool_settings.image_paint
    for attr, value in (
        ("use_occlude", True),
        ("use_backface_culling", True),
        ("use_normal_falloff", True),
        ("normal_angle", int(angle)),
    ):
        try:
            setattr(ip, attr, value)
        except (AttributeError, TypeError):
            pass
    try:
        ip.seam_bleed = max(ip.seam_bleed, 4)
    except (AttributeError, TypeError):
        pass


def persist_canvases(canvases):
    """Bemalte Texturen in die .blend packen, damit nichts verloren geht."""
    for img in canvases:
        if img is not None and img.is_dirty:
            try:
                img.pack()
            except RuntimeError:
                pass


class CleanViewport:
    """Neutrales Studio-Licht ohne Schatten, keine Overlays, andere Objekte aus - nur während der Aufnahme."""

    def __init__(self, context, space, obj):
        self.context = context
        self.space = space
        self.obj = obj
        self.saved = {}
        self.hidden = []

    def __enter__(self):
        shading = self.space.shading
        for attr in ("type", "light", "color_type", "show_shadows", "show_cavity",
                     "show_xray", "show_object_outline", "show_specular_highlight"):
            if hasattr(shading, attr):
                self.saved[("shading", attr)] = getattr(shading, attr)
        self.saved[("overlay", "show_overlays")] = self.space.overlay.show_overlays
        self.saved[("space", "show_gizmo")] = self.space.show_gizmo

        new_values = (
            ("type", "SOLID"), ("light", "STUDIO"), ("color_type", "TEXTURE"),
            ("show_shadows", False), ("show_cavity", False), ("show_xray", False),
            ("show_object_outline", False), ("show_specular_highlight", False),
        )
        for attr, value in new_values:
            try:
                setattr(shading, attr, value)
            except (AttributeError, TypeError):
                pass
        self.space.overlay.show_overlays = False
        self.space.show_gizmo = False

        for o in self.context.view_layer.objects:
            if o is not self.obj and o.visible_get():
                o.hide_set(True)
                self.hidden.append(o.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        for name in self.hidden:
            o = bpy.data.objects.get(name)
            if o is not None:
                try:
                    o.hide_set(False)
                except RuntimeError:
                    pass
        targets = {"shading": self.space.shading, "overlay": self.space.overlay, "space": self.space}
        # type zuerst, sonst werden manche Werte vom Shading-Typ überschrieben
        keys = sorted(self.saved.keys(), key=lambda k: 0 if k == ("shading", "type") else 1)
        for key in keys:
            try:
                setattr(targets[key[0]], key[1], self.saved[key])
            except (AttributeError, TypeError):
                pass
        return False


def grab_size_for_region(region, long_side):
    w, h = max(region.width, 1), max(region.height, 1)
    if w >= h:
        return long_side, max(16, round(long_side * h / w))
    return max(16, round(long_side * w / h)), long_side


def capture_image(context, area, region, size_wh):
    """Nimmt die aktuelle Ansicht auf. Die View-Matrizen landen im Bild (Quick Edit)."""
    context.scene.tool_settings.image_paint.screen_grab_size = size_wh
    before = {img.as_pointer() for img in bpy.data.images}
    try:
        with context.temp_override(area=area, region=region):
            bpy.ops.paint.image_from_view()
    except RuntimeError as ex:
        raise CSPError(rpt_("Capturing the view failed: {}").format(ex))
    new = [img for img in bpy.data.images if img.as_pointer() not in before]
    if not new:
        raise CSPError(rpt_("Capturing the view failed (no image was created)."))
    return new[0]


def save_png(img, path):
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()


def template_path(png_path):
    return os.path.join(os.path.dirname(png_path), "templates", os.path.basename(png_path))


def store_template(png_path):
    """Kopie der Vorlage behalten, damit beim Aufkleben nur Neues erkannt wird."""
    dst = template_path(png_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(png_path, dst)


def load_png_pixels(path):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        if w == 0 or h == 0:
            raise CSPError(rpt_("The image {} is empty or could not be loaded.").format(os.path.basename(path)))
        arr = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(arr)
    finally:
        bpy.data.images.remove(img)
    return arr.reshape(h, w, 4)


def remove_template(painted, template):
    """Pixel, die noch genau wie die Vorlage aussehen, werden durchsichtig.
    So muss die Vorlage-Ebene im Mal-Programm nicht ausgeblendet werden."""
    if template is None or template.shape != painted.shape:
        return painted, False
    tol = 3.0 / 255.0
    same = np.abs(painted - template).max(axis=-1) < tol
    # halbdurchsichtige Kanten der Vorlage: Farbe kann beim Speichern leicht wandern
    same |= (template[..., 3] < 0.999) & (np.abs(painted[..., 3] - template[..., 3]) < tol)
    painted = painted.copy()
    painted[same, 3] = 0.0
    return painted, True


def obj_bounds(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    center = (lo + hi) * 0.5
    radius = max((hi - lo).length * 0.5, 1e-4)
    return center, radius


def forget_capture(state):
    remove_images([n for n in state.image_names.split("\n") if n])
    state.mode = "NONE"
    state.image_names = ""
    state.png_path = ""
    state.object_name = ""
    _RUNTIME.pop(state.id_data.name, None)


def backup_canvases(state, canvases):
    """Textur vor dem Malen merken. Jedes (Live-)Aufkleben startet wieder von hier,
    so wird bei mehrmaligem Strg+S nichts doppelt aufgemalt."""
    data = {}
    for img in canvases:
        w, h = img.size
        if w == 0 or h == 0:
            continue
        arr = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(arr)
        data[img.name] = arr.astype(np.float16)  # halber Speicher, für 8-Bit-Texturen verlustfrei
    rt = runtime(state)
    rt.clear()
    rt["backup"] = data


def restore_canvases(state):
    restored = []
    for name, arr in runtime(state).get("backup", {}).items():
        img = bpy.data.images.get(name)
        if img is not None and img.size[0] * img.size[1] * 4 == arr.size:
            img.pixels.foreach_set(arr.astype(np.float32))
            img.update()
            restored.append(img)
    return restored


# ---------------------------------------------------------------------------
# Aufnehmen
# ---------------------------------------------------------------------------

def capture_single(context, prefs, state, obj, area, region, space):
    folder = work_dir()
    state.counter += 1
    clean = bpy.path.clean_name(obj.name)
    path = os.path.join(folder, f"{clean}_view_{state.counter:03d}.png")

    size = grab_size_for_region(region, prefs.grab_size)
    with CleanViewport(context, space, obj):
        img = capture_image(context, area, region, size)
    remove_images([f"Texturpainter View {clean}"])
    img.name = f"Texturpainter View {clean}"
    save_png(img, path)
    store_template(path)

    state.mode = "SINGLE"
    state.png_path = path
    state.capture_mtime = repr(os.path.getmtime(path))
    state.object_name = obj.name
    state.image_names = img.name
    return path


def capture_grid(context, prefs, state, obj, area, region, space, rv3d):
    folder = work_dir()
    state.counter += 1
    clean = bpy.path.clean_name(obj.name)
    tiles_dir = os.path.join(folder, f"{clean}_tiles")
    os.makedirs(tiles_dir, exist_ok=True)
    path = os.path.join(folder, f"{clean}_allsides_{state.counter:03d}.png")

    cols, keys = VIEW_LAYOUTS[prefs.views]
    rows = math.ceil(len(keys) / cols)
    size = grab_size_for_region(region, prefs.tile_size)
    center, radius = obj_bounds(obj)

    saved_view = (
        rv3d.view_rotation.copy(), rv3d.view_location.copy(),
        rv3d.view_distance, rv3d.view_perspective,
    )
    tiles = []
    try:
        with CleanViewport(context, space, obj):
            rv3d.view_perspective = "ORTHO"
            rv3d.view_location = center
            rv3d.view_rotation = Euler([math.radians(a) for a in VIEW_ROTATIONS["front"]]).to_quaternion()
            rv3d.view_distance = 1.0
            rv3d.update()
            wm = rv3d.window_matrix
            half_w = 1.0 / abs(wm[0][0]) if wm[0][0] else 1.0
            half_h = 1.0 / abs(wm[1][1]) if wm[1][1] else 1.0
            dist = radius * 1.05 / min(half_w, half_h)

            for key in keys:
                rv3d.view_location = center
                rv3d.view_rotation = Euler([math.radians(a) for a in VIEW_ROTATIONS[key]]).to_quaternion()
                rv3d.view_distance = dist
                rv3d.update()
                img = capture_image(context, area, region, size)
                name = f"Texturpainter Tile {clean} {key}"
                remove_images([name])
                img.name = name
                save_png(img, os.path.join(tiles_dir, f"{key}.png"))
                tiles.append(img)
    finally:
        rv3d.view_rotation, rv3d.view_location, rv3d.view_distance, rv3d.view_perspective = saved_view
        rv3d.update()

    tw, th = tiles[0].size
    for t in tiles:
        if tuple(t.size) != (tw, th):
            raise CSPError(rpt_("The tiles came out in different sizes. Please try again."))

    # Raster zusammensetzen (Blender-Pixel laufen von unten nach oben)
    grid = np.zeros((rows * th, cols * tw, 4), dtype=np.float32)
    buf = np.empty(tw * th * 4, dtype=np.float32)
    for i, t in enumerate(tiles):
        t.pixels.foreach_get(buf)
        col = i % cols
        row = rows - 1 - (i // cols)
        grid[row * th:(row + 1) * th, col * tw:(col + 1) * tw] = buf.reshape(th, tw, 4)

    grid_img = bpy.data.images.new(f"Texturpainter Grid {clean}", cols * tw, rows * th, alpha=True)
    try:
        grid_img.pixels.foreach_set(grid.ravel())
        save_png(grid_img, path)
    finally:
        bpy.data.images.remove(grid_img)
    store_template(path)

    state.mode = "GRID"
    state.png_path = path
    state.capture_mtime = repr(os.path.getmtime(path))
    state.object_name = obj.name
    state.image_names = "\n".join(t.name for t in tiles)
    state.cols, state.rows, state.tile_w, state.tile_h = cols, rows, tw, th
    return path


# ---------------------------------------------------------------------------
# Anwenden (aufs Modell projizieren)
# ---------------------------------------------------------------------------

def project(context, area, region, img):
    try:
        with context.temp_override(area=area, region=region):
            bpy.ops.paint.project_image(image=img.name)
    except RuntimeError as ex:
        raise CSPError(rpt_("Projecting onto the model failed: {}").format(ex))


def is_saved_since_capture(state):
    if state.mode == "NONE" or not os.path.isfile(trusted_png_path(state)):
        return False
    try:
        captured = float(state.capture_mtime or 0)
    except ValueError:
        captured = 0.0
    return os.path.getmtime(trusted_png_path(state)) > captured + 0.001


def check_painted(op, painted, masked, prefs):
    alpha = painted[..., 3]
    if float(alpha.max()) < 0.004:
        raise CSPError(rpt_("Nothing new painted - did you save in {} with Ctrl+S?").format(painter_name(prefs)))
    if op is not None and not masked and float(alpha.min()) > 0.996:
        op.report({"WARNING"}, rpt_("The image is opaque everywhere - the template gets projected too. "
                                    "Undo with Ctrl+Z."))


def apply_pending(op, context, prefs, state, area, region, final=True):
    """Gemaltes aufs Modell projizieren. final=False (Live): die Aufnahme bleibt offen."""
    obj = bpy.data.objects.get(state.object_name)
    if obj is None:
        forget_capture(state)
        raise CSPError(rpt_("The model of the last capture no longer exists. Capture discarded."))
    if not os.path.isfile(trusted_png_path(state)):
        forget_capture(state)
        raise CSPError(rpt_("The PNG of the last capture is missing. Capture discarded."))

    canvases = ensure_setup(context, obj, prefs)
    restore_canvases(state)
    angle = prefs.normal_angle if state.mode == "SINGLE" else prefs.grid_normal_angle
    apply_paint_settings(context, angle)
    names = [n for n in state.image_names.split("\n") if n]

    if state.mode == "SINGLE":
        img = bpy.data.images.get(names[0]) if names else None
        if img is None:
            forget_capture(state)
            raise CSPError(rpt_("The capture data is missing in Blender. Please capture again."))
        template = None
        if os.path.isfile(template_path(trusted_png_path(state))):
            template = load_png_pixels(template_path(trusted_png_path(state)))
        elif img.size[0] > 0:
            # ältere Aufnahme ohne Vorlage-Kopie: das Bild im Speicher ist noch die Vorlage
            w, h = img.size
            template = np.empty(w * h * 4, dtype=np.float32)
            img.pixels.foreach_get(template)
            template = template.reshape(h, w, 4)
        img.filepath_raw = trusted_png_path(state)
        img.reload()
        painted = load_png_pixels(trusted_png_path(state))
        painted, masked = remove_template(painted, template)
        check_painted(op, painted, masked, prefs)
        if tuple(img.size) != (painted.shape[1], painted.shape[0]):
            img.scale(painted.shape[1], painted.shape[0])
        img.pixels.foreach_set(np.ascontiguousarray(painted).ravel())
        img.update()
        project(context, area, region, img)
    else:
        grid = load_png_pixels(trusted_png_path(state))
        gh, gw = grid.shape[:2]
        tw, th, cols, rows = state.tile_w, state.tile_h, state.cols, state.rows
        if (gw, gh) != (cols * tw, rows * th):
            raise CSPError(rpt_("The grid has the wrong size ({} x {} instead of {} x {}). "
                                "Please do not scale or crop the canvas in the painting program.")
                           .format(gw, gh, cols * tw, rows * th))
        template = None
        if os.path.isfile(template_path(trusted_png_path(state))):
            template = load_png_pixels(template_path(trusted_png_path(state)))
        grid, masked = remove_template(grid, template)
        check_painted(op, grid, masked, prefs)

        for i, name in enumerate(names):
            tile = bpy.data.images.get(name)
            if tile is None:
                forget_capture(state)
                raise CSPError(rpt_("The capture data is missing in Blender. Please capture again."))
            col = i % cols
            row = rows - 1 - (i // cols)
            part = np.ascontiguousarray(grid[row * th:(row + 1) * th, col * tw:(col + 1) * tw])
            if float(part[..., 3].max()) < 0.004:
                continue  # auf dieser Kachel wurde nichts gemalt
            if tuple(tile.size) != (tw, th):
                tile.scale(tw, th)
            tile.pixels.foreach_set(part.ravel())
            tile.update()
            project(context, area, region, tile)

    persist_canvases(canvases)
    if final:
        forget_capture(state)


# ---------------------------------------------------------------------------
# Operatoren
# ---------------------------------------------------------------------------

class TEXTURPAINTER_OT_go(bpy.types.Operator):
    bl_idname = "texturpainter.go"
    bl_label = "New view"
    bl_description = "Finishes the last view and sends the current view to the painting program"
    bl_options = {"REGISTER"}

    def execute(self, context):
        prefs = get_prefs(context)
        state = context.scene.texturpainter
        try:
            area, region, space, rv3d = find_view3d(context)
            painter_exe(prefs)  # früh prüfen, bevor irgendwas passiert
            obj = context.view_layer.objects.active
            if state.mode != "NONE":
                if is_saved_since_capture(state):
                    apply_pending(self, context, prefs, state, area, region)
                else:
                    forget_capture(state)  # in der letzten Ansicht wurde nichts gemalt

            canvases = ensure_setup(context, obj, prefs)
            if state.use_grid:
                path = capture_grid(context, prefs, state, obj, area, region, space, rv3d)
            else:
                path = capture_single(context, prefs, state, obj, area, region, space)
            backup_canvases(state, canvases)
            open_in_painter(prefs, path)
        except CSPError as ex:
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}

        self.report({"INFO"}, rpt_("New view is open in {}.").format(painter_name(prefs)))
        return {"FINISHED"}


class TEXTURPAINTER_OT_save(bpy.types.Operator):
    bl_idname = "texturpainter.save"
    bl_label = "Save Blender file"
    bl_description = "Projects the latest painting (if needed) and saves the .blend file"

    def execute(self, context):
        prefs = get_prefs(context)
        state = context.scene.texturpainter
        try:
            path = trusted_png_path(state)
            if is_saved_since_capture(state) and os.path.getmtime(path) != runtime(state).get("live_mtime"):
                area, region, _space, _rv3d = find_view3d(context)
                mtime = os.path.getmtime(path)
                apply_pending(self, context, prefs, state, area, region, final=False)
                rt = runtime(state)
                rt["live_mtime"] = mtime
                rt["live_time"] = time.strftime("%H:%M:%S")
        except CSPError as ex:
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}
        if not bpy.data.is_saved:
            return bpy.ops.wm.save_as_mainfile("INVOKE_DEFAULT")
        bpy.ops.wm.save_mainfile()
        self.report({"INFO"}, rpt_("Saved: {}").format(os.path.basename(bpy.data.filepath)))
        return {"FINISHED"}


class TEXTURPAINTER_OT_reopen(bpy.types.Operator):
    bl_idname = "texturpainter.reopen"
    bl_label = "Open again"
    bl_description = "Opens the current capture again in the painting program"

    def execute(self, context):
        prefs = get_prefs(context)
        state = context.scene.texturpainter
        try:
            if state.mode == "NONE" or not os.path.isfile(trusted_png_path(state)):
                raise CSPError(rpt_("There is no open capture."))
            open_in_painter(prefs, trusted_png_path(state))
        except CSPError as ex:
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}
        return {"FINISHED"}


class TEXTURPAINTER_OT_discard(bpy.types.Operator):
    bl_idname = "texturpainter.discard"
    bl_label = "Discard"
    bl_description = "Forgets the current capture and removes what was already projected from it"

    def execute(self, context):
        state = context.scene.texturpainter
        if runtime(state).get("live_time"):
            persist_canvases(restore_canvases(state))
        forget_capture(state)
        self.report({"INFO"}, rpt_("Capture discarded."))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# N-Panel
# ---------------------------------------------------------------------------

class TEXTURPAINTER_PT_main(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Texturpainter"
    bl_label = "Texturpainter"

    def draw(self, context):
        layout = self.layout
        prefs = get_prefs(context)
        state = context.scene.texturpainter
        name = painter_name(prefs)

        layout.prop(prefs, "painter", text="")
        row = layout.row()
        row.scale_y = 1.6
        row.prop(state, "use_grid", toggle=True,
                 text="All-sides mode: ON" if state.use_grid else "All-sides mode: off",
                 icon="WORLD" if state.use_grid else "VIEW3D")

        rt = runtime(state)
        box = layout.box()
        if state.mode == "NONE":
            box.label(text="Ready. Rotate the model, then press the button.", icon="INFO")
        elif rt.get("live_time"):
            box.label(text=iface_("On the model ({})").format(rt["live_time"]), icon="CHECKMARK", translate=False)
            box.label(text="Keep painting + Ctrl+S, or rotate + button")
        else:
            box.label(text=iface_("Paint in {} + Ctrl+S").format(name), icon="TIME", translate=False)
        if state.mode != "NONE" and rt.get("live_error"):
            box.label(text=rt["live_error"], icon="ERROR", translate=False)

        if state.use_grid:
            label = iface_("All sides to {}")
        elif state.mode != "NONE":
            label = iface_("New view to {}")
        else:
            label = iface_("View to {}")
        row = layout.row()
        row.scale_y = 3.0
        row.operator("texturpainter.go", text=label.format(name), icon="BRUSH_DATA", translate=False)

        row = layout.row(align=True)
        row.scale_y = 1.6
        row.enabled = state.mode != "NONE"
        row.operator("texturpainter.reopen", text="Open again", icon="FILE_REFRESH")
        row.operator("texturpainter.discard", text="Discard", icon="X")

        row = layout.row()
        row.scale_y = 2.0
        row.operator("texturpainter.save", text="Save Blender file", icon="FILE_TICK")


class TEXTURPAINTER_PT_settings(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Texturpainter"
    bl_label = "Settings"
    bl_parent_id = "TEXTURPAINTER_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        prefs = get_prefs(context)
        col = self.layout.column()
        col.prop(prefs, "grab_size")
        col.prop(prefs, "tile_size")
        col.prop(prefs, "views", text="")
        col.prop(prefs, "texture_size", text="New texture")
        col.prop(prefs, "normal_angle")
        col.prop(prefs, "grid_normal_angle")
        col.prop(prefs, PAINTER_PATH_PROP[prefs.painter], text="Exe")


def live_update(window, scene):
    """Neu gespeicherte PNG sofort aufs Modell kleben (Live-Modus)."""
    state = scene.texturpainter
    path = trusted_png_path(state)
    if not path or not os.path.isfile(path) or not is_saved_since_capture(state):
        return
    mtime = os.path.getmtime(path)
    rt = runtime(state)
    if mtime == rt.get("live_mtime"):
        return
    if time.time() - mtime < 0.5:
        return  # Mal-Programm schreibt die Datei vielleicht noch
    area = next((a for a in window.screen.areas if a.type == "VIEW_3D"), None)
    region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
    if region is None:
        return
    rt["live_mtime"] = mtime
    context = bpy.context
    try:
        with context.temp_override(window=window, area=area, region=region, scene=scene):
            apply_pending(None, bpy.context, get_prefs(bpy.context), state, area, region, final=False)
        rt = runtime(state)
        rt["live_time"] = time.strftime("%H:%M:%S")
        rt.pop("live_error", None)
    except CSPError as ex:
        runtime(state)["live_error"] = str(ex)
    except (RuntimeError, ValueError, OSError) as ex:
        runtime(state)["live_error"] = rpt_("Live update failed: {}").format(ex)


# Live-Aktualisierung und Panel-Status, solange eine Aufnahme offen ist
def _redraw_timer():
    try:
        wm = bpy.context.window_manager
        if wm is None:
            return 1.0
        for window in wm.windows:
            scene = window.scene
            if scene is None or scene.texturpainter.mode == "NONE":
                continue
            live_update(window, scene)
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except (AttributeError, ReferenceError):
        pass
    return 1.0


classes = (
    TEXTURPAINTER_Preferences,
    TEXTURPAINTER_State,
    TEXTURPAINTER_OT_go,
    TEXTURPAINTER_OT_save,
    TEXTURPAINTER_OT_reopen,
    TEXTURPAINTER_OT_discard,
    TEXTURPAINTER_PT_main,
    TEXTURPAINTER_PT_settings,
)


# ---------------------------------------------------------------------------
# Übersetzungen: Englisch ist die Grundsprache, Blender zeigt automatisch die
# eingestellte Sprache, wenn es dafür eine Übersetzung gibt.
# ---------------------------------------------------------------------------

GERMAN = {
    # Einstellungen
    "Painting program": "Mal-Programm",
    "Custom program": "Eigenes Programm",
    "Another painting program": "Ein anderes Mal-Programm",
    "Program paths (.exe)": "Programm-Pfade (.exe)",
    "Sizes": "Größen",
    "Capture size (view)": "Aufnahmegröße (Ansicht)",
    "Longest image side in pixels when a single view is sent":
        "Längste Bildseite in Pixeln, wenn eine einzelne Ansicht geschickt wird",
    "Tile size (all sides)": "Kachelgröße (Rundum)",
    "Longest side of each tile in the all-sides grid": "Längste Seite jeder Kachel im Rundum-Raster",
    "Texture size": "Texturgröße",
    "Size of a newly created texture": "Größe einer neu angelegten Textur",
    "All-sides views": "Rundum-Ansichten",
    "6 views": "6 Ansichten",
    "8 views": "8 Ansichten",
    "10 views": "10 Ansichten",
    "front, back, left, right, top, bottom": "vorne, hinten, links, rechts, oben, unten",
    "6 views + front left/right at 45°": "6 Ansichten + schräg vorne links/rechts",
    "6 views + all four 45° views": "6 Ansichten + alle vier Schrägansichten",
    "Angle (view)": "Winkel (Ansicht)",
    "Faces tilted more than this angle are not painted. Smaller = softer transitions, but less coverage":
        "Ab diesem Winkel wird eine schräge Fläche nicht mehr bemalt. "
        "Kleiner = weichere Übergänge, aber weniger Abdeckung",
    "Angle (all sides)": "Winkel (Rundum)",
    "Like Angle (view), but for all-sides mode. Below 55 leaves gaps, above that the views paint over each other":
        "Wie Winkel (Ansicht), aber für den Rundum-Modus. Unter 55 entstehen Lücken, "
        "darüber malen sich die Ansichten gegenseitig über",
    "All-sides mode": "Rundum-Modus",
    "Send all sides at once as one grid image to the painting program":
        "Alle Seiten auf einmal als Raster an das Mal-Programm schicken",
    "Settings": "Einstellungen",
    "New texture": "Neue Textur",
    # Panel
    "All-sides mode: ON": "Rundum-Modus: AN",
    "All-sides mode: off": "Rundum-Modus: aus",
    "Ready. Rotate the model, then press the button.": "Bereit. Modell drehen, dann Knopf.",
    "On the model ({})": "Auf dem Modell ({})",
    "Keep painting + Ctrl+S, or rotate + button": "Weiter malen + Strg+S, oder drehen + Knopf",
    "Paint in {} + Ctrl+S": "In {} malen + Strg+S",
    "All sides to {}": "Rundum an {}",
    "New view to {}": "Neue Ansicht an {}",
    "View to {}": "Ansicht an {}",
    # Knöpfe
    "New view": "Neue Ansicht",
    "Finishes the last view and sends the current view to the painting program":
        "Schließt die letzte Ansicht ab und schickt die aktuelle Ansicht an das Mal-Programm",
    "Save Blender file": "Blender-Datei speichern",
    "Projects the latest painting (if needed) and saves the .blend file":
        "Klebt den letzten Stand auf (falls nötig) und speichert die .blend-Datei",
    "Open again": "Nochmal öffnen",
    "Opens the current capture again in the painting program":
        "Öffnet die aktuelle Aufnahme nochmal im Mal-Programm",
    "Discard": "Verwerfen",
    "Forgets the current capture and removes what was already projected from it":
        "Vergisst die aktuelle Aufnahme und entfernt, was davon schon aufgeklebt wurde",
    # Meldungen
    "No path set for {}. Please enter it in the add-on preferences.":
        "Kein Pfad für {} eingetragen. Bitte in den Addon-Einstellungen eintragen.",
    "{} not found: {} - please check the path in the add-on preferences.":
        "{} nicht gefunden: {} - bitte Pfad in den Addon-Einstellungen prüfen.",
    "Could not start {}: {}": "{} ließ sich nicht starten: {}",
    "No 3D Viewport found. Please use the button in the 3D Viewport sidebar.":
        "Keine 3D-Ansicht gefunden. Bitte den Knopf im N-Panel der 3D-Ansicht benutzen.",
    "The 3D Viewport has no drawing area.": "Die 3D-Ansicht hat keinen Zeichenbereich.",
    "Material '{}' has no nodes.": "Material '{}' hat keine Nodes.",
    "Please select a model (mesh) first.": "Bitte zuerst ein Modell (Mesh) anklicken.",
    "The model '{}' is hidden.": "Das Modell '{}' ist ausgeblendet.",
    "Capturing the view failed: {}": "Aufnahme der Ansicht fehlgeschlagen: {}",
    "Capturing the view failed (no image was created).":
        "Aufnahme der Ansicht fehlgeschlagen (kein Bild entstanden).",
    "The image {} is empty or could not be loaded.": "Das Bild {} ist leer oder ließ sich nicht laden.",
    "The tiles came out in different sizes. Please try again.":
        "Die Kacheln sind unterschiedlich groß geworden. Bitte nochmal versuchen.",
    "Projecting onto the model failed: {}": "Aufkleben fehlgeschlagen: {}",
    "Nothing new painted - did you save in {} with Ctrl+S?":
        "Nichts Neues gemalt - wurde in {} mit Strg+S gespeichert?",
    "The image is opaque everywhere - the template gets projected too. Undo with Ctrl+Z.":
        "Bild ist überall deckend - die Vorlage wird mit aufgeklebt. Rückgängig mit Strg+Z.",
    "The model of the last capture no longer exists. Capture discarded.":
        "Das Modell der letzten Aufnahme gibt es nicht mehr. Aufnahme verworfen.",
    "The PNG of the last capture is missing. Capture discarded.":
        "Die PNG der letzten Aufnahme fehlt. Aufnahme verworfen.",
    "The capture data is missing in Blender. Please capture again.":
        "Die Aufnahme-Daten fehlen in Blender. Bitte neu aufnehmen.",
    "The grid has the wrong size ({} x {} instead of {} x {}). "
    "Please do not scale or crop the canvas in the painting program.":
        "Das Raster hat die falsche Größe ({} x {} statt {} x {}). "
        "Bitte die Leinwand im Mal-Programm nicht skalieren oder zuschneiden.",
    "New view is open in {}.": "Neue Ansicht ist in {}.",
    "Saved: {}": "Gespeichert: {}",
    "There is no open capture.": "Es gibt keine offene Aufnahme.",
    "Capture discarded.": "Aufnahme verworfen.",
    "Live update failed: {}": "Live-Update fehlgeschlagen: {}",
}


def build_translations():
    de = {}
    for en, text in GERMAN.items():
        for ctxt in ("*", "Operator"):  # Operator-Namen nutzen einen eigenen Kontext
            de[(ctxt, en)] = text
    return {"de_DE": de}


def register():
    bpy.app.translations.register(__name__, build_translations())
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.texturpainter = PointerProperty(type=TEXTURPAINTER_State)
    if not bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.register(_redraw_timer, first_interval=1.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.unregister(_redraw_timer)
    del bpy.types.Scene.texturpainter
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    bpy.app.translations.unregister(__name__)


if __name__ == "__main__":
    register()
