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
| **All-sides mode: off** / **ON** | Off: sends the view you see. On: sends all sides at once, one folder per view |
| **View to CSP** | Sends the current view to the painting program. Shown when no view is open |
| **New view to CSP** | Same button while a view is open: finishes it and sends the new view |
| **All sides to CSP** | Same button with all-sides mode on |
| **Open again** | Opens the current view again, in case you closed the painting program |
| **Discard** | Forgets the current view and removes what you painted in it |
| **Save Blender file** | Saves the .blend with the painted texture |

## Usage

Tip: save your .blend once before you start, so all images live next to it.

1. Select the model and press **View to CSP**. The painting program opens a PSD with two layers: **paint** on top, **Template** below.
2. Click the **paint** layer and paint. You may lower its opacity to see the shading; Blender always projects the colour at full opacity.
3. Press Ctrl+S as often as you like. After every save the painting appears on the model by itself after about a second.
4. For the next side, rotate the model in Blender and press **New view to CSP**.
5. At the end press **Save Blender file**. Later you can open the .blend and the PSD again and simply keep painting.

With **All-sides mode: ON**, the button **All sides to CSP** sends one PSD with a folder per view (6, 8 or 10, default 10), each with **paint** and **Template**. Show one folder at a time, click its **paint** layer and paint. Blender reads every folder, hidden ones too.

The full guide with troubleshooting is in [Guide.txt](Guide.txt).

## How it works

- **Capture:** The view is captured with `paint.image_from_view`. Blender stores the view matrices in the image, as *Quick Edit* does.
- **PSD:** The add-on writes and reads layered 8-bit RGB PSD files itself (raw, RLE and ZIP channels), so no extra libraries are needed.
- **Template:** A copy of each template is kept as PNG. Only the paint layers are projected, and paint applied directly on a template layer is detected by comparing it with the copy. Layer opacity is ignored.
- **Projection:** `paint.project_image` with occlusion, backface culling and normal falloff.
- **All-sides mode:** one orthographic view per direction, each framed tightly around the model, one PSD folder each.
- **Live update:** A timer checks the PSD's modification time every second. The texture is backed up at capture time and restored before every update, so saving repeatedly never paints anything twice and *Discard* can undo everything. The backup is written next to the .blend on save, so painting can continue after a restart.
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
| **Rundum-Modus: aus** / **AN** | Aus: schickt die Ansicht, die du siehst. An: schickt alle Seiten auf einmal, ein Ordner pro Ansicht |
| **Ansicht an CSP** | Schickt die aktuelle Ansicht ans Mal-Programm. Steht da, wenn keine Ansicht offen ist |
| **Neue Ansicht an CSP** | Derselbe Knopf, solange eine Ansicht offen ist: schließt sie ab und schickt die neue |
| **Rundum an CSP** | Derselbe Knopf bei eingeschaltetem Rundum-Modus |
| **Nochmal öffnen** | Öffnet die aktuelle Ansicht nochmal, falls du das Mal-Programm zugemacht hast |
| **Verwerfen** | Vergisst die aktuelle Ansicht und entfernt, was du darin gemalt hast |
| **Blender-Datei speichern** | Speichert die .blend mit der bemalten Textur |

### Bedienung

Tipp: Speicher deine .blend einmal vor dem Start, dann liegen alle Bilder gleich daneben.

1. Modell anklicken und **Ansicht an CSP** drücken. Das Mal-Programm öffnet eine PSD mit zwei Ebenen: oben **malen**, darunter **Vorlage**.
2. Die Ebene **malen** anklicken und malen. Die Deckkraft darfst du senken, um die Schattierung zu sehen; Blender klebt die Farbe immer mit voller Deckkraft auf.
3. Strg+S drücken, so oft du willst. Nach jedem Speichern erscheint das Gemalte nach etwa einer Sekunde von selbst auf dem Modell.
4. Für die nächste Seite in Blender das Modell drehen und **Neue Ansicht an CSP** drücken.
5. Zum Schluss **Blender-Datei speichern** drücken. Später .blend und PSD wieder öffnen und einfach weitermalen.

Mit **Rundum-Modus: AN** schickt der Knopf **Rundum an CSP** eine PSD mit einem Ordner pro Ansicht (6, 8 oder 10, Standard 10), jeweils mit **malen** und **Vorlage**. Immer einen Ordner einblenden, dort **malen** anklicken und malen. Blender liest alle Ordner, auch die ausgeblendeten.

Die ausführliche Anleitung mit Fehlerhilfe steht in [Anleitung.txt](Anleitung.txt).

### Lizenz

GPL-3.0-or-later, siehe [LICENSE](LICENSE). Clip Studio Paint, Krita und PhotoCraft sind Namen ihrer jeweiligen Inhaber. Dieses Projekt ist mit keinem davon verbunden.
