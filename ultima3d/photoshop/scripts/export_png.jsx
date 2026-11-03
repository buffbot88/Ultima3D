// export_png.jsx — Photoshop CS3: save a PNG copy next to the active doc.
// Keeps the PSD open and unsaved; the PNG lands beside it with a .png suffix.
app.displayDialogs = DialogModes.NO;
if (app.documents.length > 0) {
    try {
        var doc = app.activeDocument;
        var opts = new PNGSaveOptions();
        opts.interlaced = false;
        var target = new File(doc.path + "/" + doc.name.replace(/\.[^.]+$/, "") + ".png");
        doc.saveAs(target, opts, true, Extension.LOWERCASE);
    } catch (e) { /* surface via manual check in Photoshop */ }
}
