@tool
extends EditorPlugin
## Ultima3D bridge entrypoint; starts the loopback HTTP bridge on enable.

var _bridge: Node = null


func _enter_tree() -> void:
	var script: Script = load(get_script().resource_path.get_base_dir() + "/bridge.gd")
	_bridge = script.new()
	_bridge.name = "Ultima3DBridge"
	add_child(_bridge)
	_bridge.start(8765)
	print("Ultima3D Godot Bridge listening on http://127.0.0.1:8765")


func _exit_tree() -> void:
	if is_instance_valid(_bridge):
		_bridge.stop()
		_bridge.queue_free()
	_bridge = null
