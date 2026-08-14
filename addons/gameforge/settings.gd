## GameForge 插件设置管理
## 存储和读取插件配置
@tool
extends RefCounted

## 配置文件路径
const CONFIG_PATH := "res://addons/gameforge/config.cfg"

## 默认配置
const DEFAULTS := {
	"backend_url": "http://localhost:8000",
	"ws_url": "ws://localhost:8766",
	"http_port": 8765,
	"http_api_token": "",
	"api_key": "",
	"auto_import": true,
	"auto_compile": true,
	"godot_version": 4,
}

## 配置缓存
var _config: Dictionary = {}


func _init() -> void:
	load_config()


func load_config() -> void:
	"""加载配置"""
	var config = ConfigFile.new()
	var err = config.load(CONFIG_PATH)

	if err == OK:
		for key in DEFAULTS:
			_config[key] = config.get_value("gameforge", key, DEFAULTS[key])
	else:
		_config = DEFAULTS.duplicate()
		save_config()


func save_config() -> void:
	"""保存配置"""
	var config = ConfigFile.new()
	for key in _config:
		config.set_value("gameforge", key, _config[key])
	config.save(CONFIG_PATH)


func get_value(key: String, default = null):
	"""获取配置值"""
	return _config.get(key, default)


func set_value(key: String, value) -> void:
	"""设置配置值"""
	_config[key] = value
	save_config()


func get_backend_url() -> String:
	"""获取后端 URL"""
	return _config.get("backend_url", DEFAULTS["backend_url"])


func get_ws_url() -> String:
	"""获取 WebSocket URL"""
	return _config.get("ws_url", DEFAULTS["ws_url"])


func get_api_key() -> String:
	"""获取 API Key"""
	return _config.get("api_key", "")
