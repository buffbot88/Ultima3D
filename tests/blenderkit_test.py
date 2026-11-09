"""BlenderKit integration: pure-logic tests, fully offline.

blender/addon.py cannot be imported normally (it needs bpy), so this suite
executes its source with stubbed bpy/mathutils modules and faked HTTP, then
exercises the real shipped helpers: query building, record summarising, file
picking, the download-ticket exchange, search/status parsing, tool
registration, and the addon protocol version handshake.
"""
import asyncio
import inspect
import json
import os
import re
import sys
import tempfile
import types
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(label, cond, detail=""):
    print(f"  [{'ok' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        failures.append(label)


def make_bpy_stub(scene):
    bpy_mod = types.ModuleType("bpy")
    props_mod = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "StringProperty", "IntProperty",
                 "EnumProperty", "FloatProperty"):
        setattr(props_mod, name, lambda *a, **k: (a, k))
    bpy_mod.props = props_mod
    types_mod = types.SimpleNamespace(
        AddonPreferences=type("AddonPreferences", (), {}),
        Panel=type("Panel", (), {}),
        Operator=type("Operator", (), {}),
    )
    bpy_mod.types = types_mod
    app_mod = types.ModuleType("bpy.app")
    handlers_mod = types.ModuleType("bpy.app.handlers")
    handlers_mod.persistent = lambda fn: fn
    app_mod.handlers = handlers_mod
    bpy_mod.app = app_mod
    bpy_mod.utils = types.SimpleNamespace(
        register_class=lambda c: None, unregister_class=lambda c: None)
    bpy_mod.context = SimpleNamespace(
        preferences=SimpleNamespace(
            addons=SimpleNamespace(get=lambda name: None)),
        scene=scene)
    return {"bpy": bpy_mod, "bpy.props": props_mod, "bpy.app": app_mod,
            "bpy.app.handlers": handlers_mod,
            "mathutils": types.SimpleNamespace(Vector=tuple)}


def ensure_requests_stub():
    """Blender ships requests; this env does not. Stub it so addon.py execs.

    The stub only raises when actually called unpatched; every test below
    replaces .get with a canned fake first."""
    try:
        import requests  # noqa: F401
        return
    except ImportError:
        pass
    stub = types.ModuleType("requests")

    class _Timeout(Exception):
        pass

    def _unpatched(*args, **kwargs):
        raise AssertionError("requests.get called without installing a fake")

    stub.exceptions = SimpleNamespace(Timeout=_Timeout)
    stub.get = _unpatched
    stub.utils = SimpleNamespace(default_headers=lambda: {})
    sys.modules["requests"] = stub


def load_addon(scene):
    ensure_requests_stub()
    stubs = make_bpy_stub(scene)
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        path = os.path.join(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))), "blender", "addon.py")
        ns = {"__name__": "blender_addon_under_test"}
        exec(compile(open(path, encoding="utf-8").read(), path, "exec"), ns)
        return ns
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


class FakeResponse:
    def __init__(self, status=200, payload=None, content=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.headers = headers or {}
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON here")
        return self._payload

    def iter_content(self, chunk_size=1 << 20):
        yield self.content


SEARCH_PAGE = {
    "count": 2,
    "results": [
        {"id": "aaaaaaaa-1111-2222-3333-444444444444",
         "displayName": "Oak Chair", "name": "oak_chair",
         "assetType": "model", "isFree": True,
         "author": {"firstName": "Jane", "lastName": "Doe"},
         "license": "Royalty Free", "ratingsMedian": 4.5, "ratingsCount": 12,
         "url": "https://www.blenderkit.com/asset-gallery-detail/aaa/",
         "files": [{"fileType": "blend", "downloadUrl": "https://dl/ticket-blend"},
                    {"fileType": "gltf", "downloadUrl": "https://dl/ticket-gltf"}]},
        {"id": "bbbbbbbb-1111-2222-3333-444444444444",
         "displayName": "Velvet", "name": "velvet",
         "assetType": "material", "isFree": False,
         "author": {"firstName": "", "lastName": ""},
         "ratingsMedian": None, "ratingsCount": 0,
         "url": None,
         "files": [{"fileType": "blend", "downloadUrl": "https://dl/ticket-mat"}]},
    ],
}


def main():
    scene = SimpleNamespace(blendermcp_use_blenderkit=True)
    ns = load_addon(scene)
    build_terms = ns["_blenderkit_build_terms"]
    summarize = ns["_blenderkit_summarize"]
    pick_file = ns["_blenderkit_pick_file"]
    available = ns["_blenderkit_available_types"]
    resolve_url = ns["_blenderkit_resolve_file_url"]
    download_file = ns["_blenderkit_download_file"]
    Server = ns["BlenderMCPServer"]

    # --- query terms -------------------------------------------------------
    check("terms carry asset_type + free filter",
          build_terms("chair", "model", True) == "asset_type:model is_free:True chair")
    check("all drops the asset_type term",
          build_terms("chair", "all", True) == "is_free:True chair")
    check("empty query is just filters",
          build_terms(None, "model", True) == "asset_type:model is_free:True")
    check("free_only off drops the term",
          build_terms("chair", "hdr", False) == "asset_type:hdr chair")
    try:
        build_terms("x", "scene", True)
        check("unknown asset type rejected", False, "no ValueError")
    except ValueError:
        check("unknown asset type rejected", True)

    # --- summarising -------------------------------------------------------
    first = summarize(SEARCH_PAGE["results"][0])
    check("summary keeps id/name/type/free/author/licence",
          first["id"] == "aaaaaaaa-1111-2222-3333-444444444444"
          and first["name"] == "Oak Chair" and first["asset_type"] == "model"
          and first["free"] is True and first["author"] == "Jane Doe"
          and first["license"] == "Royalty Free",
          str(first))
    second = summarize(SEARCH_PAGE["results"][1])
    check("missing author degrades to Unknown, missing url kept",
          second["author"] == "Unknown" and second["url"] is None
          and second["free"] is False)

    # --- file picking ------------------------------------------------------
    files = SEARCH_PAGE["results"][0]["files"]
    check("blend preferred over gltf",
          pick_file(files, ["blend", "gltf"])["fileType"] == "blend")
    check("gltf fallback when blend absent",
          pick_file([f for f in files if f["fileType"] != "blend"],
                    ["blend", "gltf"])["fileType"] == "gltf")
    check("missing kind is None", pick_file(files, ["hdr"]) is None)
    check("available types listed",
          available(files) == ["blend", "gltf"], str(available(files)))

    # --- download ticket exchange (mocked HTTP) -----------------------------
    import requests
    real_get = requests.get
    ticket = {"filePath": "https://cdn.example/signed.blend",
              "filePaths": [{"url": "https://cdn.example/signed.blend"}]}

    def fake_get(url, params=None, headers=None, timeout=None, stream=False):
        assert "Authorization" not in (headers or {}), \
            "no auth header on any download-path request"
        return FakeResponse(200, ticket)

    requests.get = fake_get
    try:
        check("ticket resolves to the signed CDN url",
              resolve_url("https://dl/ticket-blend") == "https://cdn.example/signed.blend")
    finally:
        requests.get = real_get

    def fake_bytes(url, headers=None, timeout=None, stream=False):
        assert "Authorization" not in (headers or {}), "key must not leak to CDN hosts"
        return FakeResponse(200, content=b"BLENDER-binary")

    requests.get = fake_bytes
    try:
        dest = os.path.join(tempfile.mkdtemp(prefix="bk-test-"), "a.blend")
        download_file("https://cdn.example/signed.blend", dest)
        check("streamed bytes hit disk",
              open(dest, "rb").read() == b"BLENDER-binary")
    finally:
        requests.get = real_get

    # --- status reporting ---------------------------------------------------
    os.environ.pop("BLENDERKIT_API_KEY", None)
    server = Server()
    free_status = server.get_blenderkit_status()
    check("no key still enables free assets",
          free_status.get("enabled") is True
          and "without an API key" in free_status.get("message", ""))

    def fake_me(url, params=None, headers=None, timeout=None, stream=False):
        if url.endswith("/api/v1/me/"):
            assert headers.get("Authorization") == "Bearer test-key-123", headers
            return FakeResponse(200, {"username": "tester"})
        raise AssertionError(f"unexpected url {url}")

    os.environ["BLENDERKIT_API_KEY"] = "test-key-123"
    requests.get = fake_me
    try:
        keyed = server.get_blenderkit_status()
        check("valid key validates",
              keyed.get("enabled") is True and "valid" in keyed.get("message", ""))
    finally:
        requests.get = real_get
        os.environ.pop("BLENDERKIT_API_KEY", None)

    def fake_bad_key(url, params=None, headers=None, timeout=None, stream=False):
        return FakeResponse(401, {"detail": "bad"})

    os.environ["BLENDERKIT_API_KEY"] = "bogus"
    requests.get = fake_bad_key
    try:
        bad = server.get_blenderkit_status()
        check("bad key reported, not enabled",
              bad.get("enabled") is False and "401" in bad.get("message", ""))
    finally:
        requests.get = real_get
        os.environ.pop("BLENDERKIT_API_KEY", None)

    scene_off = SimpleNamespace(blendermcp_use_blenderkit=False)
    ns_off = load_addon(scene_off)
    off = ns_off["BlenderMCPServer"]().get_blenderkit_status()
    check("disabled integration says how to enable",
          off.get("enabled") is False and "MCP for Blender" in off.get("message", ""))

    # --- search parsing (mocked HTTP) ---------------------------------------
    calls = {}

    def fake_search(url, params=None, headers=None, timeout=None, stream=False):
        calls.update(params or {})
        return FakeResponse(200, SEARCH_PAGE)

    requests.get = fake_search
    try:
        result = server.search_blenderkit_assets("chair", "model", True, 20)
        check("search returns both assets with counts",
              result.get("returned_count") == 2 and result.get("total_count") == 2
              and len(result.get("assets", [])) == 2, str(sorted(result)))
        check("search sends server-side terms, page size and dict params",
              calls.get("query") == "asset_type:model is_free:True chair"
              and calls.get("page_size") == 20 and calls.get("dict_parameters") == 1,
              str(calls))
    finally:
        requests.get = real_get

    def fake_429(url, params=None, headers=None, timeout=None, stream=False):
        return FakeResponse(429, {"detail": "slow down"})

    requests.get = fake_429
    try:
        limited = server.search_blenderkit_assets("chair")
        check("rate limit is a message, not a traceback",
              "error" in limited and "rate limiting" in limited["error"])
    finally:
        requests.get = real_get

    bad_type = server.search_blenderkit_assets("chair", "scene")
    check("bad asset type is an error dict", "error" in bad_type)

    # --- preview parsing (mocked HTTP) --------------------------------------
    def fake_preview(url, params=None, headers=None, timeout=None, stream=False):
        if "/assets/" in url:
            return FakeResponse(200, {"displayName": "Oak Chair",
                                      "thumbnailMiddleUrl": "https://cdn.example/t.jpg",
                                      "author": {"firstName": "Jane", "lastName": "Doe"},
                                      "license": "Royalty Free",
                                      "url": "https://www.blenderkit.com/x/"})
        return FakeResponse(200, content=b"\xff\xd8fakejpeg",
                            headers={"Content-Type": "image/jpeg"})

    requests.get = fake_preview
    try:
        import base64
        preview = server.get_blenderkit_asset_preview(
            "aaaaaaaa-1111-2222-3333-444444444444")
        check("preview carries base64 jpeg + attribution",
              preview.get("success") is True and preview.get("format") == "jpeg"
              and base64.b64decode(preview["image_data"]) == b"\xff\xd8fakejpeg"
              and preview.get("author") == "Jane Doe")
    finally:
        requests.get = real_get

    def fake_404(url, params=None, headers=None, timeout=None, stream=False):
        return FakeResponse(404, {"detail": "Not found."})

    requests.get = fake_404
    try:
        missing = server.get_blenderkit_asset_preview("no-such-id")
        check("unknown id names itself in the error",
              "error" in missing and "no-such-id" in missing["error"],
              str(missing))
    finally:
        requests.get = real_get

    # --- download validation before any bpy import work ----------------------
    bad_dl = server.download_blenderkit_asset("", "model")
    check("empty id refused", "error" in bad_dl)
    bad_kind = server.download_blenderkit_asset("some-id", "scene")
    check("unsupported kind refused", "error" in bad_kind)

    # --- protocol handshake --------------------------------------------------
    from ultima3d.live import addon_manager
    check("addon protocol bumped with the manager",
          ns["ADDON_PROTOCOL_VERSION"] == 10
          and addon_manager.EXPECTED_ADDON_PROTOCOL_VERSION == 10)
    rust = open(os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))),
        "rust", "crates", "ultima3d-server", "src", "live.rs"),
        encoding="utf-8").read()
    check("rust expects protocol 10",
          "EXPECTED_ADDON_PROTOCOL_VERSION: i64 = 10" in rust)

    # --- host registration ----------------------------------------------------
    import ultima3d.live.server  # noqa: F401 (registers the 4 tools)
    from ultima3d.registry import mcp
    from ultima3d.server import LIVE_TOOLS, tool_module

    async def _names():
        return {tool.name for tool in await mcp.list_tools()}

    names = asyncio.run(_names())

    wanted = {"get_blenderkit_status", "search_blenderkit_assets",
              "get_blenderkit_asset_preview", "download_blenderkit_asset"}
    check("all four BlenderKit tools registered", wanted <= names,
          f"missing={sorted(wanted - names)}")
    check("LIVE_TOOLS covers them for the daemon gate", wanted <= LIVE_TOOLS)
    check("daemon maps them to the live module",
          all(tool_module(name) == "live" for name in wanted))

    print()
    if failures:
        print(f"BLENDERKIT TEST FAILED ({len(failures)}):")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print("BLENDERKIT TEST OK (offline)")


if __name__ == "__main__":
    main()
