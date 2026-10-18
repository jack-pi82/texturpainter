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

## The panel

All buttons are in the 3D Viewport sidebar (N), tab **Texturpainter**. Instead of “CSP”, the buttons show the program you picked, e.g. **View to Krita**.

| Button | What it does |
| --- | --- |
| **Clip Studio Paint** (dropdown) | Picks the painting program: Clip Studio Paint, Krita, PhotoCraft or Custom program |
| **All-sides mode: off** / **ON** | Off: sends the view you see. On: sends all sides at once as one grid image |
| **View to CSP** | Sends the current view to the painting program. Shown when no view is open |
| **New view to CSP** | Same button while a view is open: finishes it and sends the new view |
| **All sides to CSP** | Same button with all-sides mode on |
| **Open again** | Opens the current view again, in case you closed the painting program |
| **Discard** | Forgets the current view and removes what you painted in it |
| **Save Blender file** | Saves the .blend with the painted texture |

## Usage

1. Select the model and press **View to CSP**. The painting program opens with the current view.
2. In the painting program, create a new layer and paint. The template may stay visible.
3. Press Ctrl+S as often as you like. After every save the painting appears on the model by itself after about a second.
4. For the next side, rotate the model in Blender and press **New view to CSP**.
5. At the end press **Save Blender file**.

With **All-sides mode: ON**, the button **All sides to CSP** sends 6, 8 or 10 views as one grid image, so one Ctrl+S covers every side.

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

### Das Panel

Alle Knöpfe sind in der 3D-Ansicht im N-Panel, Reiter **Texturpainter**. Statt „CSP“ steht auf den Knöpfen das gewählte Programm, z. B. **Ansicht an Krita**.

| Knopf | Was er macht |
| --- | --- |
| **Clip Studio Paint** (Auswahlliste) | Wählt das Mal-Programm: Clip Studio Paint, Krita, PhotoCraft oder Eigenes Programm |
| **Rundum-Modus: aus** / **AN** | Aus: schickt die Ansicht, die du siehst. An: schickt alle Seiten auf einmal als ein Raster-Bild |
| **Ansicht an CSP** | Schickt die aktuelle Ansicht ans Mal-Programm. Steht da, wenn keine Ansicht offen ist |
| **Neue Ansicht an CSP** | Derselbe Knopf, solange eine Ansicht offen ist: schließt sie ab und schickt die neue |
| **Rundum an CSP** | Derselbe Knopf bei eingeschaltetem Rundum-Modus |
| **Nochmal öffnen** | Öffnet die aktuelle Ansicht nochmal, falls du das Mal-Programm zugemacht hast |
| **Verwerfen** | Vergisst die aktuelle Ansicht und entfernt, was du darin gemalt hast |
| **Blender-Datei speichern** | Speichert die .blend mit der bemalten Textur |

### Bedienung

1. Modell anklicken und **Ansicht an CSP** drücken. Das Mal-Programm öffnet sich mit der aktuellen Ansicht.
2. Im Mal-Programm eine neue Ebene anlegen und malen. Die Vorlage darf sichtbar bleiben.
3. Strg+S drücken, so oft du willst. Nach jedem Speichern erscheint das Gemalte nach etwa einer Sekunde von selbst auf dem Modell.
4. Für die nächste Seite in Blender das Modell drehen und **Neue Ansicht an CSP** drücken.
5. Zum Schluss **Blender-Datei speichern** drücken.

Mit **Rundum-Modus: AN** schickt der Knopf **Rundum an CSP** 6, 8 oder 10 Ansichten als ein Raster-Bild, ein Strg+S reicht dann für alle Seiten.

Die ausführliche Anleitung mit Fehlerhilfe steht in [Anleitung.txt](Anleitung.txt).

### Lizenz

GPL-3.0-or-later, siehe [LICENSE](LICENSE). Clip Studio Paint, Krita und PhotoCraft sind Namen ihrer jeweiligen Inhaber. Dieses Projekt ist mit keinem davon verbunden.
