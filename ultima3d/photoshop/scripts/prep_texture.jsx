// prep_texture.jsx — Photoshop CS3: normalize the active doc for tiling use.
// Flattens and forces RGB 8-bit so exports are predictable. Run inside CS3.
app.displayDialogs = DialogModes.NO;
if (app.documents.length > 0) {
    try {
        var doc = app.activeDocument;
        if (doc.mode != DocumentMode.RGB) doc.changeMode(ChangeMode.RGB);
        doc.bitsPerChannel = BitsPerChannelType.EIGHT;
        doc.flatten();
    } catch (e) { /* leave the doc untouched on error */ }
}
