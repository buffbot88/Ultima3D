// ultima3d_bridge.jsx — live-control bridge for Photoshop CS3 (portable-safe).
//
// Run ONCE inside the open Photoshop: File > Scripts > Browse > this file.
// It listens on 127.0.0.1:8767. Each connection sends one JSON message like
//   {"id":1,"op":"exec","code":"app.documents.add(1024,1024)"}
// and gets one JSON reply like {"id":1,"ok":true,"result":"..."}.
// ops: "exec" runs ExtendScript and returns the stringified result,
//      "ping" answers with the app version, "quit" stops the bridge.
//
// While this runs Photoshop is in agent mode (UI waits on the listener).
// Send {"op":"quit"} to hand Photoshop back. No system changes, no COM.
var PORT = 8767;
app.displayDialogs = DialogModes.NO;

function esc(s) {
    return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"')
        .replace(/\r/g, "\\r").replace(/\n/g, "\\n").replace(/\t/g, "\\t") + '"';
}

function sendReply(conn, id, ok, payload) {
    conn.write('{"id":' + id + ',"ok":' + (ok ? "true" : "false") +
        ',' + (ok ? '"result":' : '"error":') + esc(payload) + '}');
    // CS3 drops unread-on-close send buffers: let the bytes leave first.
    $.sleep(500);
    conn.close();
}

var listener = new Socket();
if (!listener.listen(PORT)) {
    // Port busy: a bridge is already running. Nothing to do.
} else {
    var running = true;
    while (running) {
        var conn = listener.poll();
        if (conn === null) { $.sleep(100); continue; }
        $.sleep(50);
        var text = "";
        while (!conn.eof) {
            var chunk = conn.read(32767);
            if (chunk === "") break;
            text += chunk;
        }
        var id = 0, ok = true, out = "";
        try {
            var req = eval('(' + text + ')');
            id = req.id;
            if (req.op === "quit") { out = "bridge stopped"; running = false; }
            else if (req.op === "ping") { out = "pong CS3 " + app.version; }
            else {
                var v = eval(String(req.code));
                out = (v === undefined) ? "undefined" : String(v);
            }
        } catch (e) { ok = false; out = String(e); }
        try { sendReply(conn, id, ok, out); } catch (e2) {}
    }
    listener.close();
}
