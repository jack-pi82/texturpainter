# SPDX-License-Identifier: GPL-3.0-or-later
# Texturpainter - Copyright (C) 2026 jack-pi82

bl_info = {
    "name": "Texturpainter - paint 3D models in your 2D painting app",
    "author": "jack-pi82",
    "version": (1, 3, 1),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > Texturpainter",
    "description": "Send the view to CSP/Krita/PhotoCraft, paint there, projected back onto the model live",
    "category": "Paint",
}

import math
import os
import re
import shutil
import struct
import subprocess
import time
import zlib

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
VIEW_ORDER = {
    "6": ["front", "right", "back", "left", "top", "bottom"],
    "8": ["front", "front_right", "right", "back", "left", "front_left", "top", "bottom"],
    "10": ["front", "front_right", "right", "back_right", "back", "back_left", "left", "front_left",
           "top", "bottom"],
}
VIEW_LABELS = {
    "front": "front",
    "right": "right",
    "back": "back",
    "left": "left",
    "top": "top",
    "bottom": "bottom",
    "front_right": "front right",
    "front_left": "front left",
    "back_right": "back right",
    "back_left": "back left",
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
        name="Image size (all sides)", default=2048, min=256, max=8192,
        description="Longest side of each view in all-sides mode",
    )
    texture_size: EnumProperty(
        name="Texture size", default="2048",
        items=[("2048", "2048 px", "2048 x 2048"), ("4096", "4096 px", "4096 x 4096")],
        description="Size of a newly created texture",
    )
    views: EnumProperty(
        name="All-sides views", default="10",
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
    view_names: StringProperty()  # Ordnernamen in der PSD, gleiche Reihenfolge wie image_names
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


def work_dir(blend=None):
    blend = blend or (bpy.data.filepath if bpy.data.is_saved else "")
    if blend:
        base = os.path.splitext(os.path.basename(blend))[0]
        folder = os.path.join(os.path.dirname(blend), base + "_texturpainter")
    else:
        folder = os.path.join(bpy.app.tempdir, "texturpainter")
    os.makedirs(folder, exist_ok=True)
    return folder


PNG_NAME = re.compile(r"^[A-Za-z0-9_]+_(view|allsides|ansicht|rundum)_\d{3}\.(png|psd)$")


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


def template_path(capture_path):
    """Kopie der Vorlage als PNG, damit beim Aufkleben nur Neues erkannt wird."""
    base = os.path.splitext(os.path.basename(capture_path))[0]
    return os.path.join(os.path.dirname(capture_path), "templates", base + ".png")


def to_rgba8(img):
    """Blender-Bild als (h, w, 4) uint8, Zeilen von oben nach unten."""
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return (np.clip(buf.reshape(h, w, 4)[::-1], 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def view_layers(rgba):
    """Ebenen einer Ansicht von unten nach oben: Vorlage, darüber die leere Mal-Ebene.
    Die Deckkraft der Mal-Ebene wird beim Aufkleben ignoriert, man kann sie also senken,
    um die Schattierung der Vorlage beim Malen zu sehen."""
    return [
        PsdLayer.from_pixels(iface_("Template"), rgba),
        PsdLayer(iface_("paint")),
    ]


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


def forget_capture(state):
    path = trusted_png_path(state)
    if path and os.path.isfile(backup_path(path)):
        try:
            os.remove(backup_path(path))
        except OSError:
            pass
    remove_images([n for n in state.image_names.split("\n") if n])
    state.mode = "NONE"
    state.image_names = ""
    state.png_path = ""
    state.object_name = ""
    _RUNTIME.pop(state.id_data.name, None)


def backup_path(capture_path):
    return os.path.splitext(capture_path)[0] + "_backup.npz"


def save_backup(state):
    """Textur-Stand vor der Runde auf die Platte, damit es nach einem Neustart weitergeht."""
    path = trusted_png_path(state)
    data = runtime(state).get("backup")
    if not path or not data or os.path.isfile(backup_path(path)):
        return
    np.savez_compressed(backup_path(path), **data)


def load_backup(state):
    rt = runtime(state)
    if "backup" in rt:
        return
    path = trusted_png_path(state)
    if not path or not os.path.isfile(backup_path(path)):
        return
    data = {}
    try:
        if os.path.getsize(backup_path(path)) > 2 * 1024 ** 3:
            return
        with np.load(backup_path(path), allow_pickle=False) as stored:
            for key in stored.files:
                img = bpy.data.images.get(key)
                if img is None:
                    continue
                arr = stored[key]
                if arr.dtype in (np.float16, np.float32) and arr.size == img.size[0] * img.size[1] * 4:
                    data[key] = arr
    except (OSError, ValueError, zlib.error):
        return  # kaputte Sicherung: dann eben ohne
    rt["backup"] = data


def move_capture_next_to_blend(state, blend_path):
    """Liegt die offene Aufnahme noch im Temp-Ordner, kommt sie beim Speichern neben die .blend."""
    old = trusted_png_path(state)
    if not old or not os.path.isfile(old):
        return
    new_folder = work_dir(blend_path)
    if os.path.normcase(os.path.dirname(old)) == os.path.normcase(new_folder):
        return
    new = os.path.join(new_folder, os.path.basename(old))
    shutil.copy2(old, new)  # copy2 behält das Änderungsdatum, Live-Erkennung bleibt gleich
    for src, dst in ((template_path(old), template_path(new)), (backup_path(old), backup_path(new))):
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    for dir_for in (tiles_dir_for, compare_dir_for):
        if os.path.isdir(dir_for(old)):
            shutil.copytree(dir_for(old), dir_for(new), dirs_exist_ok=True)
    for name in [n for n in state.image_names.split("\n") if n]:
        img = bpy.data.images.get(name)
        if img is None or not img.filepath_raw:
            continue
        base = os.path.basename(bpy.path.abspath(img.filepath_raw))
        for folder in (tiles_dir_for(new), os.path.dirname(template_path(new))):
            if os.path.isfile(os.path.join(folder, base)):
                img.filepath_raw = os.path.join(folder, base)
                break
    state.png_path = new
    runtime(state)["moved"] = True


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    _RUNTIME.clear()  # Sicherungen gehören zur alten Datei; die neue lädt ihre von der Platte


@bpy.app.handlers.persistent
def _on_save_pre(*args):
    blend_path = next((a for a in args if isinstance(a, str) and a), bpy.data.filepath)
    if not blend_path:
        return
    for scene in bpy.data.scenes:
        state = scene.texturpainter
        if state.mode == "NONE":
            continue
        try:
            move_capture_next_to_blend(state, blend_path)
            save_backup(state)
        except OSError as ex:
            runtime(state)["live_error"] = rpt_("Live update failed: {}").format(ex)


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
    load_backup(state)
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
    path = os.path.join(folder, f"{clean}_view_{state.counter:03d}.psd")

    size = grab_size_for_region(region, prefs.grab_size)
    with CleanViewport(context, space, obj):
        img = capture_image(context, area, region, size)
    remove_images([f"Texturpainter View {clean}"])
    img.name = f"Texturpainter View {clean}"
    os.makedirs(tiles_dir_for(path), exist_ok=True)
    save_png(img, os.path.join(tiles_dir_for(path), "view.png"))
    template_file = template_path(path)
    os.makedirs(os.path.dirname(template_file), exist_ok=True)
    shutil.copy2(os.path.join(tiles_dir_for(path), "view.png"), template_file)
    rgba = to_rgba8(img)
    psd_write(path, img.size[0], img.size[1], view_layers(rgba), rgba)

    state.mode = "SINGLE"
    state.png_path = path
    state.capture_mtime = repr(os.path.getmtime(path))
    state.object_name = obj.name
    state.image_names = img.name
    return path


def world_vertices(context, obj):
    depsgraph = context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", co)
    finally:
        ev.to_mesh_clear()
    mw = np.array(obj.matrix_world)
    return co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]


def tiles_dir_for(png_path):
    """Bilder der Ansichten. Auf diese Dateien zeigen die Blender-Bilder (mit View-Matrizen)."""
    base = os.path.splitext(os.path.basename(png_path))[0]
    return os.path.join(os.path.dirname(png_path), base + "_views")


def compare_dir_for(png_path):
    """Unveränderte Kopien der Vorlagen zum Vergleichen. Kein Blender-Bild zeigt hierhin,
    damit sie nie versehentlich mit Gemaltem überschrieben werden."""
    base = os.path.splitext(os.path.basename(png_path))[0]
    return os.path.join(os.path.dirname(png_path), base + "_templates")


def capture_grid(context, prefs, state, obj, area, region, space, rv3d):
    """Rundum-Modus: jede Ansicht als eigener Ordner (Vorlage + Mal-Ebene) in einer PSD."""
    folder = work_dir()
    state.counter += 1
    clean = bpy.path.clean_name(obj.name)
    path = os.path.join(folder, f"{clean}_allsides_{state.counter:03d}.psd")
    tiles_dir = tiles_dir_for(path)
    os.makedirs(tiles_dir, exist_ok=True)

    keys = VIEW_ORDER[prefs.views]
    size = grab_size_for_region(region, prefs.tile_size)
    verts = world_vertices(context, obj)

    saved_view = (
        rv3d.view_rotation.copy(), rv3d.view_location.copy(),
        rv3d.view_distance, rv3d.view_perspective,
    )
    tiles = []
    try:
        with CleanViewport(context, space, obj):
            rv3d.view_perspective = "ORTHO"
            rv3d.view_distance = 1.0
            rv3d.update()
            wm = rv3d.window_matrix
            half_w = 1.0 / abs(wm[0][0]) if wm[0][0] else 1.0
            half_h = 1.0 / abs(wm[1][1]) if wm[1][1] else 1.0

            for key in keys:
                quat = Euler([math.radians(a) for a in VIEW_ROTATIONS[key]]).to_quaternion()
                rot = np.array(quat.to_matrix())
                local = verts @ rot  # Koordinaten im Blick-Raum: x rechts, y oben
                lo, hi = local.min(axis=0), local.max(axis=0)
                mid = (lo + hi) * 0.5
                ext = np.maximum((hi - lo) * 0.5, 1e-4)
                # jede Ansicht eng um das Modell einrahmen
                rv3d.view_rotation = quat
                rv3d.view_location = Vector(rot @ mid)
                rv3d.view_distance = 1.05 * max(ext[0] / half_w, ext[1] / half_h)
                rv3d.update()
                img = capture_image(context, area, region, size)
                name = f"Texturpainter View {clean} {key}"
                remove_images([name])
                img.name = name
                save_png(img, os.path.join(tiles_dir, f"{key}.png"))
                os.makedirs(compare_dir_for(path), exist_ok=True)
                shutil.copy2(os.path.join(tiles_dir, f"{key}.png"), os.path.join(compare_dir_for(path), f"{key}.png"))
                tiles.append(img)
    finally:
        rv3d.view_rotation, rv3d.view_location, rv3d.view_distance, rv3d.view_perspective = saved_view
        rv3d.update()

    tw, th = tiles[0].size
    for t in tiles:
        if tuple(t.size) != (tw, th):
            raise CSPError(rpt_("The tiles came out in different sizes. Please try again."))

    view_names = [iface_(VIEW_LABELS[k]) for k in keys]
    layers = []
    first_rgba = None
    # PSD-Ebenen laufen von unten nach oben: erste Ansicht (vorne) ganz oben
    for i in reversed(range(len(tiles))):
        rgba = to_rgba8(tiles[i])
        if i == 0:
            first_rgba = rgba
        layers.append(PsdLayer.divider())
        layers.extend(view_layers(rgba))
        layers.append(PsdLayer.folder(view_names[i], hidden=i != 0, opened=i == 0))
    psd_write(path, tw, th, layers, first_rgba)

    state.mode = "GRID"
    state.png_path = path
    state.capture_mtime = repr(os.path.getmtime(path))
    state.object_name = obj.name
    state.image_names = "\n".join(t.name for t in tiles)
    state.view_names = "\n".join(view_names)
    state.cols, state.rows, state.tile_w, state.tile_h = len(tiles), 1, tw, th
    return path


# ---------------------------------------------------------------------------
# PSD: minimaler Schreiber/Leser für 8-Bit-RGB mit Ebenen und Ordnern
# ---------------------------------------------------------------------------

class PsdLayer:
    """Eine PSD-Ebene. rgba = (h, w, 4) uint8, Zeilen von oben nach unten."""

    def __init__(self, name, rgba=None, top=0, left=0, opacity=255, hidden=False, section=0, locked=False,
                 blend=b"norm"):
        self.name = name
        self.rgba = rgba
        self.top = top
        self.left = left
        self.opacity = opacity
        self.hidden = hidden
        self.section = section  # 0 Ebene, 1 offener Ordner, 2 geschlossener Ordner, 3 Ordner-Ende
        self.locked = locked
        self.blend = blend

    @classmethod
    def from_pixels(cls, name, rgba, locked=False, blend=b"norm"):
        """Auf den nicht durchsichtigen Bereich zuschneiden, damit die Datei klein bleibt."""
        ys, xs = np.nonzero(rgba[..., 3])
        if len(ys) == 0:
            return cls(name, locked=locked, blend=blend)
        t, b, l, r = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        return cls(name, np.ascontiguousarray(rgba[t:b, l:r]), top=int(t), left=int(l), locked=locked,
                   blend=blend)

    @classmethod
    def folder(cls, name, hidden=False, opened=False):
        return cls(name, hidden=hidden, section=1 if opened else 2)

    @classmethod
    def divider(cls):
        return cls("</Layer group>", section=3)


def _psd_block(key, payload):
    payload += b"\0" * ((-len(payload)) % 4)
    return b"8BIM" + key + struct.pack(">I", len(payload)) + payload


def _psd_pascal(name):
    raw = name.encode("ascii", "replace")[:255]
    data = bytes([len(raw)]) + raw
    return data + b"\0" * ((-len(data)) % 4)


def psd_write(path, width, height, layers, composite):
    """layers von unten nach oben. Pixel unkomprimiert (Raw), das versteht jedes Programm."""
    records = bytearray()
    channel_data = bytearray()
    for layer in layers:
        if layer.rgba is None:
            t = l = b = r = 0
            planes = [b""] * 4
        else:
            h, w = layer.rgba.shape[:2]
            t, l, b, r = layer.top, layer.left, layer.top + h, layer.left + w
            planes = [np.ascontiguousarray(layer.rgba[..., c]).tobytes() for c in (3, 0, 1, 2)]
        records += struct.pack(">iiiiH", t, l, b, r, 4)
        for cid, plane in zip((-1, 0, 1, 2), planes):
            records += struct.pack(">hI", cid, len(plane) + 2)
            channel_data += struct.pack(">H", 0) + plane
        flags = 0x02 if layer.hidden else 0
        if layer.section:
            flags |= 0x18
        records += b"8BIM" + layer.blend + struct.pack(">BBBB", layer.opacity, 0, flags, 0)
        extra = struct.pack(">II", 0, 0) + _psd_pascal(layer.name)
        extra += _psd_block(b"luni", struct.pack(">I", len(layer.name)) + layer.name.encode("utf-16-be"))
        if layer.section:
            extra += _psd_block(b"lsct", struct.pack(">I", layer.section))
        if layer.locked:
            extra += _psd_block(b"lspf", struct.pack(">I", 0x02 | 0x04))  # Pixel + Position gesperrt
        records += struct.pack(">I", len(extra)) + extra

    info = struct.pack(">h", -len(layers)) + bytes(records) + bytes(channel_data)
    info += b"\0" * (len(info) % 2)
    layer_mask = struct.pack(">I", len(info)) + info + struct.pack(">I", 0)

    with open(path, "wb") as f:
        f.write(b"8BPS" + struct.pack(">H6xHIIHH", 1, 4, height, width, 8, 3))
        f.write(struct.pack(">I", 0))  # Farbmodus-Daten
        f.write(struct.pack(">I", 0))  # Bild-Ressourcen
        f.write(struct.pack(">I", len(layer_mask)))
        f.write(layer_mask)
        f.write(struct.pack(">H", 0))
        for c in (0, 1, 2, 3):
            f.write(np.ascontiguousarray(composite[..., c]).tobytes())


def _unpackbits(data, size):
    out = bytearray()
    i, n = 0, len(data)
    while i < n and len(out) < size:
        c = data[i]
        i += 1
        if c < 128:
            out += data[i:i + c + 1]
            i += c + 1
        elif c > 128:
            out += bytes((data[i],)) * (257 - c)
            i += 1
    if len(out) < size:
        out += b"\0" * (size - len(out))
    return bytes(out[:size])


def _psd_channel(data, comp, h, w):
    size = h * w
    if comp == 0:
        raw = data[:size]
    elif comp == 1:
        raw = _unpackbits(data[2 * h:], size)  # Zeilenlängen überspringen
    elif comp in (2, 3):
        raw = zlib.decompressobj().decompress(data, size)  # nie mehr als die Ebene groß ist
        raw += b"\0" * (size - len(raw))
    else:
        raise CSPError(rpt_("Unsupported PSD compression ({}).").format(comp))
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(h, w)
    if comp == 3:
        arr = (np.cumsum(arr, axis=1, dtype=np.uint64) & 0xFF).astype(np.uint8)
    return arr


PSD_MAX_SIDE = 16384


def psd_read(path):
    """Gibt (Breite, Höhe, Ebenen von unten nach oben) zurück. Kaputte Dateien -> CSPError."""
    try:
        return _psd_read(path)
    except (struct.error, IndexError, ValueError, zlib.error, MemoryError) as ex:
        raise CSPError(rpt_("The file is not a PSD file. Please save as PSD.") + f" ({ex})")


def _psd_read(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"8BPS" or struct.unpack(">H", data[4:6])[0] != 1:
        raise CSPError(rpt_("The file is not a PSD file. Please save as PSD."))
    _nch, height, width, depth, mode = struct.unpack(">HIIHH", data[12:26])
    if depth != 8 or mode != 3:
        raise CSPError(rpt_("Only 8-bit RGB PSD files are supported."))
    if not (0 < width <= PSD_MAX_SIDE and 0 < height <= PSD_MAX_SIDE):
        raise CSPError(rpt_("The file is not a PSD file. Please save as PSD."))
    pos = 26
    for _ in range(2):  # Farbmodus-Daten, Bild-Ressourcen
        pos += 4 + struct.unpack(">I", data[pos:pos + 4])[0]
    pos += 4  # Länge Ebenen-/Maskenbereich
    info_len = struct.unpack(">I", data[pos:pos + 4])[0]
    pos += 4
    if info_len == 0:
        return width, height, []
    count = abs(struct.unpack(">h", data[pos:pos + 2])[0])
    pos += 2

    records = []
    for _ in range(count):
        t, l, b, r, nch = struct.unpack(">iiiiH", data[pos:pos + 18])
        pos += 18
        channels = []
        for _c in range(nch):
            cid, clen = struct.unpack(">hI", data[pos:pos + 6])
            channels.append((cid, clen))
            pos += 6
        opacity, flags = data[pos + 8], data[pos + 10]
        extra_len = struct.unpack(">I", data[pos + 12:pos + 16])[0]
        pos += 16
        end = pos + extra_len
        p = pos
        p += 4 + struct.unpack(">I", data[p:p + 4])[0]  # Ebenenmaske
        p += 4 + struct.unpack(">I", data[p:p + 4])[0]  # Mischbereiche
        name_len = data[p]
        name = data[p + 1:p + 1 + name_len].decode("latin-1")
        p += 1 + name_len
        p += (-(1 + name_len)) % 4
        section = 0
        while p + 12 <= end and data[p:p + 4] in (b"8BIM", b"8B64"):
            key = data[p + 4:p + 8]
            blen = struct.unpack(">I", data[p + 8:p + 12])[0]
            block = data[p + 12:p + 12 + blen]
            if key == b"luni" and len(block) >= 4:
                n = struct.unpack(">I", block[:4])[0]
                name = block[4:4 + 2 * n].decode("utf-16-be", "replace")
            elif key in (b"lsct", b"lsdk") and len(block) >= 4:
                section = struct.unpack(">I", block[:4])[0]
            p += 12 + blen
        pos = end
        layer = PsdLayer(name, top=t, left=l, opacity=opacity, hidden=bool(flags & 0x02), section=section)
        layer._rect = (t, l, b, r)
        layer._channels = channels
        records.append(layer)

    for layer in records:
        t, l, b, r = layer._rect
        h, w = b - t, r - l
        if not (0 <= h <= height and 0 <= w <= width):
            h = w = 0  # Ebene größer als die Leinwand: Pixel nicht auspacken
        planes = {}
        for cid, clen in layer._channels:
            comp = struct.unpack(">H", data[pos:pos + 2])[0]
            chunk = data[pos + 2:pos + clen]
            pos += clen
            if h > 0 and w > 0 and cid in (-1, 0, 1, 2):
                planes[cid] = _psd_channel(chunk, comp, h, w)
        if h > 0 and w > 0 and all(c in planes for c in (0, 1, 2)):
            alpha = planes.get(-1, np.full((h, w), 255, dtype=np.uint8))
            layer.rgba = np.dstack([planes[0], planes[1], planes[2], alpha])
    return width, height, records


def psd_folders(layers):
    """Oberste Ordner als [(Name, Ebenen von unten nach oben)], in Dateireihenfolge."""
    stack = [[]]
    for layer in layers:
        if layer.section == 3:
            stack.append([])
        elif layer.section in (1, 2):
            children = stack.pop() if len(stack) > 1 else []
            stack[-1].append((layer.name, children))
        else:
            stack[-1].append(layer)

    def leaves(children):
        for child in children:
            if isinstance(child, tuple):
                yield from leaves(child[1])
            else:
                yield child

    return [(child[0], list(leaves(child[1]))) for child in stack[0] if isinstance(child, tuple)]


TEMPLATE_LAYER_NAMES = {"Template", "Vorlage"}


def _layer_canvas(layer, width, height):
    """Ebenen-Pixel auf voller Leinwand. Die Ebenen-Deckkraft zählt absichtlich nicht:
    sie ist nur zum Sehen beim Malen da, aufs Modell kommt die volle Farbe."""
    out = np.zeros((height, width, 4), dtype=np.float32)
    if layer.rgba is None:
        return out
    t, l = layer.top, layer.left
    h, w = layer.rgba.shape[:2]
    y0, x0, y1, x1 = max(t, 0), max(l, 0), min(t + h, height), min(l + w, width)
    if y1 > y0 and x1 > x0:
        out[y0:y1, x0:x1] = layer.rgba[y0 - t:y1 - t, x0 - l:x1 - l].astype(np.float32) / 255.0
    return out


def _over(dst, src):
    a_src, a_dst = src[..., 3:4], dst[..., 3:4]
    a_out = a_src + a_dst * (1.0 - a_src)
    rgb = src[..., :3] * a_src + dst[..., :3] * a_dst * (1.0 - a_src)
    dst[..., :3] = np.where(a_out > 0, rgb / np.maximum(a_out, 1e-6), 0.0)
    dst[..., 3:4] = a_out


def compose_view(leaves, width, height, template):
    """Alles, was in einem Ordner neu gemalt wurde. Die Vorlage selbst zählt nur, wo sie übermalt wurde."""
    out = np.zeros((height, width, 4), dtype=np.float32)
    for layer in leaves:
        if layer.rgba is None:
            continue
        if layer.name in TEMPLATE_LAYER_NAMES and template is None:
            continue
        pixels = _layer_canvas(layer, width, height)
        if layer.name in TEMPLATE_LAYER_NAMES:
            pixels, _ = remove_template(pixels, template)
        _over(out, pixels)
    return out


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
        path = trusted_png_path(state)
        template_file = template_path(path)
        template = load_png_pixels(template_file) if os.path.isfile(template_file) else None  # unten→oben
        if path.endswith(".psd"):
            width, height, layers = psd_read(path)
            if tuple(img.size) != (width, height):
                raise CSPError(rpt_("The image has the wrong size ({} x {} instead of {} x {}). "
                                    "Please do not scale or crop the canvas in the painting program.")
                               .format(width, height, img.size[0], img.size[1]))
            leaves = [layer for layer in layers if layer.section == 0]
            painted = compose_view(leaves, width, height, None if template is None else template[::-1])[::-1]
            check_painted(op, painted, True, prefs)
        else:
            # Aufnahme aus einer älteren Version (flache PNG)
            painted, masked = remove_template(load_png_pixels(path), template)
            check_painted(op, painted, masked, prefs)
        if tuple(img.size) != (painted.shape[1], painted.shape[0]):
            img.scale(painted.shape[1], painted.shape[0])
        img.pixels.foreach_set(np.ascontiguousarray(painted).ravel())
        img.update()
        project(context, area, region, img)
    else:
        path = trusted_png_path(state)
        tw, th = state.tile_w, state.tile_h
        if not (16 <= tw <= PSD_MAX_SIDE and 16 <= th <= PSD_MAX_SIDE):
            forget_capture(state)
            raise CSPError(rpt_("The capture data is missing in Blender. Please capture again."))
        width, height, layers = psd_read(path)
        if (width, height) != (tw, th):
            raise CSPError(rpt_("The image has the wrong size ({} x {} instead of {} x {}). "
                                "Please do not scale or crop the canvas in the painting program.")
                           .format(width, height, tw, th))
        folders = psd_folders(layers)
        by_name = {fname: leaves for fname, leaves in folders}
        view_names = [n for n in state.view_names.split("\n") if n]
        tiles_dir = tiles_dir_for(path)
        painted_any = False

        for i, name in enumerate(names):
            tile = bpy.data.images.get(name)
            if tile is None:
                forget_capture(state)
                raise CSPError(rpt_("The capture data is missing in Blender. Please capture again."))
            leaves = by_name.get(view_names[i]) if i < len(view_names) else None
            if leaves is None and len(folders) == len(names):
                leaves = folders[len(folders) - 1 - i][1]  # Ordner umbenannt: über die Reihenfolge
            if not leaves:
                continue
            key = name.rsplit(" ", 1)[-1]
            template = None
            if key in VIEW_ROTATIONS:  # nur feste Ansichtsnamen, nie frei aus dem Bildnamen
                compare_dir = compare_dir_for(path)
                template_file = os.path.join(compare_dir if os.path.isdir(compare_dir) else tiles_dir, f"{key}.png")
                if os.path.isfile(template_file):
                    template = load_png_pixels(template_file)[::-1]
            part = compose_view(leaves, width, height, template)
            if float(part[..., 3].max()) < 0.004:
                continue  # in diesem Ordner wurde nichts gemalt
            painted_any = True
            if tuple(tile.size) != (tw, th):
                tile.scale(tw, th)
            tile.pixels.foreach_set(np.ascontiguousarray(part[::-1]).ravel())
            tile.update()
            project(context, area, region, tile)
        if not painted_any:
            raise CSPError(rpt_("Nothing new painted - did you save in {} with Ctrl+S?").format(painter_name(prefs)))

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
            runtime(state).pop("moved", None)
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
        if state.mode != "NONE" and rt.get("moved"):
            box.label(text="The image now lives next to the .blend file.", icon="FILE_FOLDER")
            box.label(text="Press 'Open again', close the old tab.")

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
    except Exception as ex:  # eine kaputte Datei darf das Live-Update nie stoppen
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
    except Exception:  # der Timer muss weiterlaufen, egal was passiert
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
    "Image size (all sides)": "Bildgröße (Rundum)",
    "Longest side of each view in all-sides mode": "Längste Seite jeder Ansicht im Rundum-Modus",
    # Ordner und Ebenen in der PSD
    "Template": "Vorlage",
    "paint": "malen",
    "front": "vorne",
    "right": "rechts",
    "back": "hinten",
    "left": "links",
    "top": "oben",
    "bottom": "unten",
    "front right": "vorne rechts",
    "front left": "vorne links",
    "back right": "hinten rechts",
    "back left": "hinten links",
    "Unsupported PSD compression ({}).": "Nicht unterstützte PSD-Komprimierung ({}).",
    "The file is not a PSD file. Please save as PSD.": "Die Datei ist keine PSD. Bitte als PSD speichern.",
    "Only 8-bit RGB PSD files are supported.": "Nur 8-Bit-RGB-PSD-Dateien werden unterstützt.",
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
    "The image now lives next to the .blend file.": "Das Bild liegt jetzt neben der .blend-Datei.",
    "Press 'Open again', close the old tab.": "'Nochmal öffnen' drücken, alten Tab schließen.",
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
    "The image has the wrong size ({} x {} instead of {} x {}). "
    "Please do not scale or crop the canvas in the painting program.":
        "Das Bild hat die falsche Größe ({} x {} statt {} x {}). "
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
    if _on_save_pre not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_on_save_pre)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if _on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_on_save_pre)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.unregister(_redraw_timer)
    del bpy.types.Scene.texturpainter
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    bpy.app.translations.unregister(__name__)


if __name__ == "__main__":
    register()
