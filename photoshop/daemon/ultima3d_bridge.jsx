// ultima3d_bridge.jsx — live-control bridge for Photoshop CS3 (portable-safe).
//
// Run ONCE inside the open Photoshop: File > Scripts > Browse > this file.
// It listens on 127.0.0.1:8767 and speaks one JSON object per LINE:
//   request  {"id":1,"op":"exec","code":"app.documents.length"}
//   reply    {"id":1,"ok":true,"result":"1"}
// ops: "exec" runs ExtendScript and returns the stringified result,
//      "ping" answers with the app version, "quit" stops the bridge.
//
// Why line framing: CS3's counted read waits for the FULL count, so the old
// read-until-eof loop never returned on an idle or empty connection —
// Photoshop's only thread stayed blocked and Windows reported the app as not
// responding. readln()/writeln() return as soon as one line arrives, and a bare
// connect/close probe (the client's health check) is ignored without touching
// the socket.
//
// Photoshop is in agent mode while this runs, so it hands itself back: after
// IDLE_SECONDS without a request, or MAX_MINUTES in total. Dialog mode is
// restored on every exit path, including "quit". No system changes, no COM.
var PORT = 8767;
var IDLE_SECONDS = 300;   // 5 min with no request -> hand Photoshop back
var MAX_MINUTES = 40;     // absolute cap, mirrors the host session budget

var prevDialogs = app.displayDialogs;
app.displayDialogs = DialogModes.NO;

function esc(s) {
    return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"')
        .replace(/\r/g, "\\r").replace(/\n/g, "\\n").replace(/\t/g, "\\t") + '"';
}

function nowMs() {
    return new Date().getTime();
}

function reply(conn, id, ok, payload) {
    try {
        conn.writeln('{"id":' + id + ',"ok":' + (ok ? "true" : "false") +
            ',' + (ok ? '"result":' : '"error":') + esc(payload) + '}');
        $.sleep(50);   // CS3 drops unread-on-close buffers: let the line leave
    } catch (e) {}
    try { conn.close(); } catch (e2) {}
}

var listener = new Socket();
var armed = false;
try { armed = listener.listen(PORT); } catch (e0) { armed = false; }

if (armed) {
    var started = nowMs();
    var last = started;
    var running = true;
    while (running) {
        var conn = null;
        try { conn = listener.poll(); } catch (e1) { conn = null; }
        if (conn === null) {
            if (nowMs() - last > IDLE_SECONDS * 1000) break;
            if (nowMs() - started > MAX_MINUTES * 60000) break;
            $.sleep(100);
            continue;
        }
        last = nowMs();
        var text = "";
        try { text = conn.readln(); } catch (e2) { text = ""; }
        text = (text === null || text === undefined) ? "" : String(text);
        if (text.replace(/^\s+|\s+$/g, "") === "") {
            try { conn.close(); } catch (e3) {}   // health probe, no request
            continue;
        }
        var id = 0, ok = true, out = "";
        try {
            var req = eval('(' + text + ')');
            id = req.id;
            if (req.op === "quit") { out = "bridge stopped"; running = false; }
            else if (req.op === "ping") { out = "pong CS3 " + app.version; }
            else if (req.op === "exec") {
                var v = eval(String(req.code));
                out = (v === undefined) ? "undefined" : String(v);
            } else { ok = false; out = "unknown op: " + req.op; }
        } catch (e4) { ok = false; out = String(e4); }
        reply(conn, id, ok, out);
    }
    try { listener.close(); } catch (e5) {}
}

// Hand Photoshop back in a usable state whatever way the loop ended.
app.displayDialogs = prevDialogs;
try { app.refresh(); } catch (e6) {}
