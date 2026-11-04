// prep_texture.jsx — Photoshop CS3: normalize the active doc for tiling use.
// Flattens and forces RGB 8-bit so exports are predictable. Run inside CS3.
//
// Steps are guarded one by one so an unsupported conversion cannot skip the
// rest, dialog mode is restored before the script ends, and failures are
// rethrown at the end: a silent catch is what makes a failed run look like
// nothing happened (or like a crash).
var prevDialogs = app.displayDialogs;
app.displayDialogs = DialogModes.NO;

var failures = [];

function step(label, fn) {
    try { fn(); } catch (e) { failures.push(label + ": " + e); }
}

try {
    if (app.documents.length > 0) {
        var doc = app.activeDocument;
        step("rgb", function () {
            if (doc.mode != DocumentMode.RGB) doc.changeMode(ChangeMode.RGB);
        });
        step("8-bit", function () {
            doc.bitsPerChannel = BitsPerChannelType.EIGHT;
        });
        step("flatten", function () {
            if (doc.layers.length > 1) doc.flatten();
        });
    }
} finally {
    app.displayDialogs = prevDialogs;
}

if (failures.length > 0) {
    throw new Error("ultima3d prep_texture: " + failures.join("; "));
}
