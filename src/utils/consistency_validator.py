"""GameForge - 代码与场景一致性校验器

检查生成的代码和场景描述之间的一致性。
"""

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Set


@dataclass
class ValidationResult:
    """校验结果"""
    errors: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[Dict[str, str]] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def has_issues(self) -> bool:
        return len(self.errors) > 0 or len(self.warnings) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "errors": self.errors,
            "warnings": self.warnings,
            "suggestions": self.suggestions,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }


def validate_code_scene_consistency(
    code_files: Dict[str, str],
    scene_desc: Dict[str, Any],
    gdm: Dict[str, Any] = None,
    file_metadata: Dict[str, Any] = None,
) -> ValidationResult:
    """校验代码与场景的一致性

    Args:
        code_files: 生成的代码文件 {path: content}
        scene_desc: 场景描述JSON
        gdm: Game Design Model (可选)
        file_metadata: 代码文件元数据 (可选)

    Returns:
        ValidationResult
    """
    result = ValidationResult()

    # 提取代码中的类名
    generated_classes = _extract_classes_from_code(code_files)
    class_to_file = {cls: fpath for fpath, cls in generated_classes.items()}

    # 提取场景中引用的脚本组件
    scene_scripts = _extract_scripts_from_scene(scene_desc)

    # 1. 场景引用的脚本是否在生成代码中存在
    for script_name in scene_scripts:
        if script_name not in class_to_file:
            # 检查是否是Unity内置组件
            if not _is_unity_builtin(script_name):
                result.errors.append({
                    "type": "missing_script",
                    "message": f"场景引用了脚本 '{script_name}'，但该脚本未在生成代码中找到",
                    "script": script_name,
                })

    # 2. 代码中的 public class 是否和文件名一致
    for fpath, content in code_files.items():
        if not fpath.endswith(".cs"):
            continue
        class_match = re.search(r'public\s+(?:partial\s+)?class\s+(\w+)', content)
        if class_match:
            class_name = class_match.group(1)
            file_name = fpath.rsplit("/", 1)[-1].replace(".cs", "")
            if class_name != file_name:
                result.warnings.append({
                    "type": "class_filename_mismatch",
                    "message": f"类名 '{class_name}' 与文件名 '{file_name}' 不一致",
                    "file": fpath,
                    "class_name": class_name,
                    "file_name": file_name,
                })

    # 3. namespace 是否合法
    for fpath, content in code_files.items():
        if not fpath.endswith(".cs"):
            continue
        ns_match = re.search(r'namespace\s+([\w.]+)', content)
        if ns_match:
            ns = ns_match.group(1)
            if not re.match(r'^[A-Za-z_][\w.]*$', ns):
                result.errors.append({
                    "type": "invalid_namespace",
                    "message": f"namespace '{ns}' 不合法",
                    "file": fpath,
                })

    # 4. Unity生命周期方法拼写检查
    lifecycle_methods = [
        "Awake", "Start", "Update", "FixedUpdate", "LateUpdate",
        "OnEnable", "OnDisable", "OnDestroy", "OnTriggerEnter",
        "OnTriggerExit", "OnCollisionEnter", "OnCollisionExit",
        "OnTriggerEnter2D", "OnTriggerExit2D", "OnCollisionEnter2D",
    ]
    for fpath, content in code_files.items():
        if not fpath.endswith(".cs"):
            continue
        # 检查常见的拼写错误
        for method in lifecycle_methods:
            # 检查大小写错误
            pattern = re.compile(rf'\bvoid\s+{method.lower()}\s*\(', re.IGNORECASE)
            for match in pattern.finditer(content):
                actual = match.group().split("void ")[1].split("(")[0]
                if actual != method:
                    result.warnings.append({
                        "type": "lifecycle_spelling",
                        "message": f"Unity生命周期方法 '{actual}' 拼写错误，应为 '{method}'",
                        "file": fpath,
                    })

    # 5. 检查场景中required_components是否完整
    if file_metadata:
        for obj in scene_desc.get("game_objects", []):
            obj_name = obj.get("name", "")
            for comp in obj.get("components", []):
                comp_type = comp.get("type", "")
                # 如果是自定义脚本，检查其required_components
                if comp_type in class_to_file:
                    meta = file_metadata.get(class_to_file[comp_type], {})
                    required = meta.get("required_components", [])
                    obj_components = [c.get("type", "") for c in obj.get("components", [])]
                    for req_comp in required:
                        if req_comp not in obj_components and not _is_unity_builtin(req_comp):
                            result.warnings.append({
                                "type": "missing_component",
                                "message": f"GameObject '{obj_name}' 上的 '{comp_type}' 需要 '{req_comp}' 组件",
                                "game_object": obj_name,
                                "script": comp_type,
                                "missing_component": req_comp,
                            })

    # 6. Tags/Layers检查
    if gdm:
        required_tags = set(gdm.get("tags_layers", {}).get("tags", []))
        code_tags = set()
        for content in code_files.values():
            for m in re.finditer(r'CompareTag\("(\w+)"\)', content):
                code_tags.add(m.group(1))
            for m in re.finditer(r'tag\s*=\s*"(\w+)"', content):
                code_tags.add(m.group(1))

        # 场景中使用的tags
        scene_tags = set()
        for obj in scene_desc.get("game_objects", []):
            tag = obj.get("tag", "")
            if tag:
                scene_tags.add(tag)

        all_used_tags = code_tags | scene_tags
        for tag in all_used_tags:
            if tag not in required_tags and tag not in ("Untagged", "MainCamera", "Player", "Respawn", "Finish", "EditorOnly"):
                result.suggestions.append(f"Tag '{tag}' 在代码或场景中使用，建议在 ProjectSettings 中添加")

        # Layer检查
        required_layers = {l.get("name", ""): l.get("index", 0) for l in gdm.get("tags_layers", {}).get("layers", [])}
        code_layers = set()
        for content in code_files.values():
            for m in re.finditer(r'LayerMask\.GetMask\("(\w+)"\)', content):
                code_layers.add(m.group(1))
            for m in re.finditer(r'gameObject\.Layer\s*=\s*\d+', content):
                code_layers.add("custom_layer")

        scene_layers = set()
        for obj in scene_desc.get("game_objects", []):
            layer = obj.get("layer", 0)
            if isinstance(layer, int) and layer > 7:
                scene_layers.add(f"Layer_{layer}")

        for layer in code_layers:
            if layer not in required_layers:
                result.suggestions.append(f"Layer '{layer}' 在代码中使用，建议在 ProjectSettings 中配置")

    # 7. Input检查
    if gdm:
        defined_inputs = {inp.get("name", "") for inp in gdm.get("input_map", [])}
        code_inputs = set()
        for content in code_files.values():
            for m in re.finditer(r'Input\.Get(?:Axis|Button)(?:Raw)?\("(\w+)"\)', content):
                code_inputs.add(m.group(1))

        for inp in code_inputs:
            if inp not in defined_inputs and inp not in ("Horizontal", "Vertical", "Fire1", "Jump", "Mouse X", "Mouse Y"):
                result.suggestions.append(f"Input '{inp}' 在代码中使用但未在 input_map 中定义")

    return result


def _extract_classes_from_code(code_files: Dict[str, str]) -> Dict[str, str]:
    """从代码文件中提取类名 → 文件路径映射"""
    class_map = {}
    for fpath, content in code_files.items():
        if not fpath.endswith(".cs"):
            continue
        match = re.search(r'public\s+(?:partial\s+)?(?:class|struct|interface)\s+(\w+)', content)
        if match:
            class_map[fpath] = match.group(1)
    return class_map


def _extract_scripts_from_scene(scene_desc: Dict[str, Any]) -> Set[str]:
    """从场景描述中提取所有自定义脚本组件名"""
    scripts = set()
    for obj in scene_desc.get("game_objects", []):
        for comp in obj.get("components", []):
            comp_type = comp.get("type", "")
            if not _is_unity_builtin(comp_type):
                scripts.add(comp_type)
        # 检查子对象
        for child in obj.get("children", []):
            for comp in child.get("components", []):
                comp_type = comp.get("type", "")
                if not _is_unity_builtin(comp_type):
                    scripts.add(comp_type)
    return scripts


def _is_unity_builtin(type_name: str) -> bool:
    """检查是否是Unity内置组件类型"""
    builtins = {
        # Physics
        "Rigidbody", "Rigidbody2D", "BoxCollider", "BoxCollider2D",
        "SphereCollider", "CircleCollider2D", "CapsuleCollider",
        "CapsuleCollider2D", "MeshCollider", "PolygonCollider2D",
        "TerrainCollider", "WheelCollider",
        # Rendering
        "MeshRenderer", "SpriteRenderer", "SkinnedMeshRenderer",
        "LineRenderer", "TrailRenderer", "Projector", "ReflectionProbe",
        "CanvasRenderer",
        # UI
        "Canvas", "CanvasScaler", "GraphicRaycaster", "RectTransform",
        "EventSystem", "StandaloneInputModule", "InputModule",
        "Text", "TextMeshProUGUI", "TextMeshPro",
        "Image", "RawImage", "Button", "Slider", "Scrollbar",
        "Toggle", "Dropdown", "InputField", "ScrollRect",
        "GridLayoutGroup", "HorizontalLayoutGroup", "VerticalLayoutGroup",
        "ContentSizeFitter", "AspectRatioFitter", "LayoutElement",
        "Mask", "RectMask2D",
        # Animation / Audio
        "Animator", "Animation", "AudioSource", "AudioListener",
        # Camera / Lighting
        "Camera", "Light", "FlareLayer",
        # Navigation
        "CharacterController", "NavMeshAgent", "NavMeshObstacle",
        # Particles
        "ParticleSystem",
        # Joints
        "ConstantForce", "FixedJoint", "HingeJoint", "SpringJoint",
        "CharacterJoint", "ConfigurableJoint",
        "ConstantForce2D", "FixedJoint2D", "HingeJoint2D", "SpringJoint2D",
        "DistanceJoint2D", "SliderJoint2D", "WheelJoint2D",
        # Effectors
        "AreaEffector2D", "BuoyancyEffector2D", "PointEffector2D",
        "PlatformEffector2D", "SurfaceEffector2D",
        # Terrain
        "Terrain",
        # Misc
        "LODGroup", "OcclusionPortal", "OcclusionArea",
    }
    return type_name in builtins
