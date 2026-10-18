# Texturpainter

**English** · [Deutsch](#deutsch)

Blender add-on for painting 3D models in Clip Studio Paint, Krita or PhotoCraft. You paint with the brushes of your 2D painting program, and Blender projects the result onto the model live. You never have to touch UVs.

Tested with Blender 5.3 and 5.2 LTS and with Clip Studio Paint, Krita and PhotoCraft. The add-on uses the language set in Blender: English or German, and other languages fall back to English.

## Installation

1. Blender: *Edit → Preferences → Add-ons*
2. Top right, the arrow (v) → *Install from Disk*
3. Select `texturpainter.py` and tick “Texturpainter”
4. In the 3D Viewport press N → tab **Texturpainter**

The default paths for Clip Studio Paint and Krita are filled in. For PhotoCraft or another program, enter the path to its `.exe` in the add-on preferences.

## Usage

1. Select the model and press the big button. The painting program opens with the current view.
2. In the painting program, create a new layer and paint. The template may stay visible.
3. Press Ctrl+S as often as you like. After every save the painting appears on the model by itself after about a second.
4. For the next side, rotate the model in Blender and press the big button again.
5. At the end press **Save Blender file**.

**All-sides mode** sends 6, 8 or 10 views as one grid image, so one Ctrl+S covers every side.

The full guide with troubleshooting is in [Guide.txt](Guide.txt).

## How it works

- **Capture:** The view is captured with `paint.image_from_view`. Blender stores the view matrices in the image, as *Quick Edit* does.
- **Template:** A copy of the template is kept in `templates/`. When projecting, every pixel that is unchanged compared to the template becomes transparent, so only new paint is projected.
- **Projection:** `paint.project_image` with occlusion, backface culling and normal falloff.
- **All-sides mode:** orthographic views are combined into one grid with numpy and cut apart again when projecting.
- **Live update:** A timer checks the PNG's modification time every second. The texture is backed up in memory at capture time and restored before every update, so saving repeatedly never paints anything twice and *Discard* can undo everything.
- **First-time setup:** Missing UVs, materials and textures are created automatically. Painted textures are packed into the .blend.
- **Translations:** English strings in the code, German via `bpy.app.translations`.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE). Blender add-ons that use the Blender Python API are licensed under a GPL-compatible license.

Clip Studio Paint, Krita and PhotoCraft are names of their respective owners. This project is not affiliated with any of them.

---

## Deutsch

Blender-Addon zum Bemalen von 3D-Modellen in Clip Studio Paint, Krita oder PhotoCraft. Du malst mit den Pinseln deines Mal-Programms, und Blender projiziert das Ergebnis live aufs Modell. UVs musst du nie anfassen.

Getestet mit Blender 5.3 und 5.2 LTS sowie mit Clip Studio Paint, Krita und PhotoCraft. Das Addon nutzt die Sprache, die in Blender eingestellt ist: Deutsch oder Englisch, andere Sprachen zeigen Englisch.

### Installation

1. Blender: *Bearbeiten → Einstellungen → Add-ons*
2. Oben rechts den Pfeil (v) → *Von Datenträger installieren*
3. `texturpainter.py` auswählen und den Haken bei „Texturpainter“ setzen
4. In der 3D-Ansicht N drücken → Reiter **Texturpainter**

Die Standardpfade zu Clip Studio Paint und Krita sind eingetragen. Für PhotoCraft oder ein anderes Programm trägst du den Pfad zur `.exe` in den Addon-Einstellungen ein.

### Bedienung

1. Modell anklicken und den großen Knopf drücken. Das Mal-Programm öffnet sich mit der aktuellen Ansicht.
2. Im Mal-Programm eine neue Ebene anlegen und malen. Die Vorlage darf sichtbar bleiben.
3. Strg+S drücken, so oft du willst. Nach jedem Speichern erscheint das Gemalte nach etwa einer Sekunde von selbst auf dem Modell.
4. Für die nächste Seite in Blender das Modell drehen und wieder den großen Knopf drücken.
5. Zum Schluss **Blender-Datei speichern** drücken.

Der **Rundum-Modus** schickt 6, 8 oder 10 Ansichten als ein Raster-Bild, ein Strg+S reicht dann für alle Seiten.

Die ausführliche Anleitung mit Fehlerhilfe steht in [Anleitung.txt](Anleitung.txt).

### Lizenz

GPL-3.0-or-later, siehe [LICENSE](LICENSE). Clip Studio Paint, Krita und PhotoCraft sind Namen ihrer jeweiligen Inhaber. Dieses Projekt ist mit keinem davon verbunden.
