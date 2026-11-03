@tool
extends Node
## Loopback-only HTTP bridge for Ultima3D. Fixed allowlist, no script execution.

var _server: TCPServer = null
var _port: int = 8765
var _logs: PackedStringArray = []
const MAX_LOGS := 500


func start(port: int = 8765) -> void:
	_port = port
	_log_line("bridge starting")
	_server = TCPServer.new()
	var err: Error = _server.listen(_port, "127.0.0.1")
	if err != OK:
		push_error("Ultima3D bridge failed to listen on 127.0.0.1:%d (%s)" % [_port, err])
		_server = null
		return
	set_process(true)


func stop() -> void:
	set_process(false)
	if _server != null:
		_server.stop()
		_server = null


func _process(_delta: float) -> void:
	if _server == null or not _server.is_connection_available():
		return
	var peer: StreamPeerTCP = _server.take_connection()
	_handle_peer(peer)


func _handle_peer(peer: StreamPeerTCP) -> void:
	var raw: PackedByteArray = _read_request(peer)
	if raw.is_empty():
		peer.disconnect_from_host()
		return
	var tool: String = _parse_tool(raw)
	var result: Dictionary = _dispatch(tool)
	_send_json(peer, result)
	peer.disconnect_from_host()


func _read_request(peer: StreamPeerTCP) -> PackedByteArray:
	var data: PackedByteArray = []
	var header_end: int = -1
	var content_length: int = 0
	var guard: int = 0
	while guard < 200 and (header_end < 0 or data.size() < header_end + content_length):
		guard += 1
		var chunk: PackedByteArray = peer.get_available_bytes(8192)
		if chunk.is_empty():
			OS.delay_msec(10)
			continue
		data.append_array(chunk)
		if header_end < 0:
			var text: String = data.get_string_from_utf8()
			var idx: int = text.find("\r\n\r\n")
			if idx >= 0:
				header_end = idx + 4
				for line in text.substr(0, idx).split("\r\n"):
					if line.to_lower().begins_with("content-length:"):
						content_length = int(line.get_slice(":", 1).strip_edges())
		if header_end >= 0 and content_length <= 0:
			break
	if header_end < 0:
		return []
	return data.slice(header_end, header_end + content_length)


func _parse_tool(body: PackedByteArray) -> String:
	var parsed: Variant = JSON.parse_string(body.get_string_from_utf8())
	if parsed is Dictionary and (parsed as Dictionary).has("tool"):
		return str((parsed as Dictionary)["tool"])
	return ""


func _dispatch(tool: String) -> Dictionary:
	_log_line("tool: " + tool)
	match tool:
		"status":
			return {"ok": true, "editor": Engine.get_version_info(),
				"project": ProjectSettings.get_setting("application/config/name", ""),
				"main_scene": ProjectSettings.get_setting("application/run/main_scene", "")}
		"scene_tree":
			return {"ok": true, "tree": _dump_tree()}
		"run_project":
			EditorInterface.play_main_scene()
			return {"ok": true}
		"stop_project":
			EditorInterface.stop_playing_scene()
			return {"ok": true}
		"debug_output":
			var out: PackedStringArray = _logs.duplicate()
			_logs.clear()
			return {"ok": true, "lines": out}
		"screenshot":
			return _screenshot()
		_:
			return {"error": "Unknown tool: " + tool}


func _dump_tree() -> Array:
	var out: Array = []
	var root: Node = EditorInterface.get_edited_scene_root()
	if root == null:
		return out
	_walk(root, 0, out)
	return out


func _walk(node: Node, depth: int, out: Array) -> void:
	out.append({"depth": depth, "name": node.name, "type": node.get_class(),
		"children": node.get_child_count()})
	for child in node.get_children():
		_walk(child, depth + 1, out)


func _screenshot() -> Dictionary:
	var dir: String = get_script().resource_path.get_base_dir().get_base_dir() + "/captures"
	DirAccess.make_dir_recursive_absolute(dir)
	var path: String = dir + "/viewport_%d.png" % Time.get_ticks_msec()
	var viewport: Viewport = EditorInterface.get_editor_viewport()
	if viewport == null:
		return {"error": "No editor viewport available"}
	var img: Image = viewport.get_texture().get_image()
	var err: Error = img.save_png(path)
	if err != OK:
		return {"error": "Screenshot save failed: %s" % err}
	return {"ok": true, "path": path}


func _send_json(peer: StreamPeerTCP, result: Dictionary) -> void:
	var body: PackedByteArray = JSON.stringify(result).to_utf8_buffer()
	var head: String = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % body.size()
	peer.put_data((head.to_utf8_buffer() + body))


func _log_line(line: String) -> void:
	_logs.append(line)
	while _logs.size() > MAX_LOGS:
		_logs.remove_at(0)
