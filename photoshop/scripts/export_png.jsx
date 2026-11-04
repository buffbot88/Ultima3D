// export_png.jsx — Photoshop CS3: save a PNG copy next to the active doc.
// Exports from a duplicate, so the open PSD keeps its layers, bit depth and
// unsaved state; 16/32-bit and CMYK documents are converted on the copy only.
// An unsaved document has no folder, so that is reported instead of building a
// bogus "undefined/..." path (which is what used to fail invisibly).
var prevDialogs = app.displayDialogs;
app.displayDialogs = DialogModes.NO;

var failure = null;
var out = "";

try {
    if (app.documents.length === 0) {
        failure = "open a document first";
    } else {
        var doc = app.activeDocument;
        var folder = "";
        try { folder = doc.path ? String(doc.path.fsName) : ""; } catch (ePath) { folder = ""; }
        if (!folder) {
            failure = "save the document once first — it has no folder yet";
        } else {
            var copy = doc.duplicate("ultima3d_png");
            try {
                if (copy.mode != DocumentMode.RGB) copy.changeMode(ChangeMode.RGB);
                copy.bitsPerChannel = BitsPerChannelType.EIGHT;
                if (copy.layers.length > 1) copy.flatten();
                var opts = new PNGSaveOptions();
                opts.interlaced = false;
                var target = new File(folder + "/" + doc.name.replace(/\.[^.]+$/, "") + ".png");
                copy.saveAs(target, opts, true, Extension.LOWERCASE);
                out = "wrote " + target.fsName;
            } finally {
                copy.close(SaveOptions.DONOTSAVECHANGES);
            }
        }
    }
} catch (e) {
    failure = String(e);
} finally {
    app.displayDialogs = prevDialogs;
}

if (failure) throw new Error("ultima3d export_png: " + failure);
out;
