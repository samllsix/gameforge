"""Unity 完整项目模板生成器

生成可直接用 Unity 打开的项目目录结构：
- Packages/manifest.json
- ProjectSettings/*.asset（TagManager、InputManager、QualitySettings 等）
- Assets/**/*.meta（脚本 GUID 文件）
"""

import hashlib
import json
from typing import Any, Dict, List, Optional


class UnityProjectGenerator:
    """从 GameDevState 生成完整 Unity 项目文件"""

    def generate_all(self, state: Dict[str, Any]) -> Dict[str, str]:
        """入口：返回 {相对路径: 文件内容} 字典，可直接写入输出目录。

        Args:
            state: GameDevState 字典，需包含 project_context、game_design_model、
                   code_generated、scene_description 等字段。

        Returns:
            路径→内容的字典，例如 {"Packages/manifest.json": "...", ...}
        """
        files: Dict[str, str] = {}

        project_ctx = state.get("project_context", {})
        gdm = state.get("game_design_model", {})
        code_generated = state.get("code_generated", {})
        scene_desc = state.get("scene_description", {})

        project_name = project_ctx.get("project_name", "GameForgeProject")
        engine = project_ctx.get("engine", "unity")
        is_2d = self._is_2d_project(gdm, code_generated)

        # Packages
        files["Packages/manifest.json"] = self._generate_manifest_json(engine)

        # ProjectSettings
        files["ProjectSettings/ProjectSettings.asset"] = (
            self._generate_project_settings(project_name)
        )
        files["ProjectSettings/TagManager.asset"] = self._generate_tag_manager(gdm)
        files["ProjectSettings/InputManager.asset"] = self._generate_input_manager(gdm)
        files["ProjectSettings/QualitySettings.asset"] = (
            self._generate_quality_settings()
        )
        files["ProjectSettings/EditorBuildSettings.asset"] = (
            self._generate_editor_build_settings(scene_desc)
        )

        if is_2d:
            files["ProjectSettings/Physics2DSettings.asset"] = (
                self._generate_physics2d_settings(gdm)
            )

        # .meta files for every .cs file
        files.update(self._generate_meta_files(code_generated))

        return files

    # ── Packages/manifest.json ───────────────────────────────────

    def _generate_manifest_json(self, engine: str = "unity") -> str:
        manifest = {
            "dependencies": {
                "com.unity.2d.sprite": "1.0.0",
                "com.unity.2d.tilemap": "1.0.0",
                "com.unity.inputsystem": "1.7.0",
                "com.unity.textmeshpro": "3.0.6",
                "com.unity.timeline": "1.7.6",
                "com.unity.ugui": "1.0.0",
                "com.unity.modules.animation": "1.0.0",
                "com.unity.modules.audio": "1.0.0",
                "com.unity.modules.imageconversion": "1.0.0",
                "com.unity.modules.imgui": "1.0.0",
                "com.unity.modules.physics": "1.0.0",
                "com.unity.modules.physics2d": "1.0.0",
                "com.unity.modules.ui": "1.0.0",
                "com.unity.modules.uielements": "1.0.0",
            }
        }
        return json.dumps(manifest, indent=2)

    # ── ProjectSettings/ProjectSettings.asset ────────────────────

    def _generate_project_settings(self, project_name: str) -> str:
        return (
            _YAML_HEADER
            + "PlayerSettings:\n"
            + "  productName: " + project_name + "\n"
            + "  companyName: GameForge\n"
            + "  defaultScreenWidth: 1920\n"
            + "  defaultScreenHeight: 1080\n"
            + "  displayResolutionDialog: 0\n"
            + "  defaultIsNativeResolution: 0\n"
            + "  runInBackground: 1\n"
            + "  captureSingleScreen: 0\n"
            + "  muteOtherAudioSources: 0\n"
            + "  Prepare IOS For Recording: 0\n"
            + "  Force Internet Permission: 0\n"
            + "  Force SD Card Permission: 0\n"
            + "  Create Wallpapers: 0\n"
            + "  APK Expansion Files: 0\n"
            + "  keepLoadedShadersAlive: 0\n"
            + "  StripUnusedMeshComponents: 1\n"
            + "  strictShaderVariantMatching: 0\n"
            + "  VertexChannelCompressionMask: 4054\n"
            + "  iPhoneSdkVersion: 988\n"
            + "  iOSTargetOSVersionString: 12.0\n"
            + "  tvOSSdkVersion: 0\n"
            + "  tvOSRequireExtendedGameController: 0\n"
            + "  tvOSTargetOSVersionString: 12.0\n"
            + "  uIPrerenderedIcon: 0\n"
            + "  uIRequiresPersistentWiFi: 0\n"
            + "  uIRequiresFullScreen: 1\n"
            + "  uIStatusBarHidden: 1\n"
            + "  uIExitOnSuspend: 0\n"
            + "  uIStatusBarStyle: 0\n"
            + "  defaultInterfaceOrientation: 4\n"
            + "  allowedAutorotateToPortrait: 1\n"
            + "  allowedAutorotateToPortraitUpsideDown: 1\n"
            + "  allowedAutorotateToLandscapeRight: 1\n"
            + "  allowedAutorotateToLandscapeLeft: 1\n"
            + "  useOSAutorotation: 1\n"
            + "  use32BitDisplayBuffer: 1\n"
            + "  preserveFramebufferAlpha: 0\n"
            + "  disableDepthAndStencilBuffers: 0\n"
            + "  androidBlitType: 0\n"
            + "  defaultIsFullScreen: 1\n"
            + "  defaultIsNativeResolution: 0\n"
            + "  macRetinaSupport: 1\n"
            + "  useMacAppStoreValidation: 0\n"
            + "  gpuSkinning: 1\n"
            + "  xboxPIXTextureCapture: 0\n"
            + "  xboxEnableAvatar: 0\n"
            + "  xboxEnableKinect: 0\n"
            + "  xboxEnableKinectAutoTracking: 0\n"
            + "  xboxEnableFitness: 0\n"
            + "  visibleInBackground: 1\n"
            + "  allowFullscreenSwitch: 1\n"
            + "  fullscreenMode: 1\n"
            + "  xboxSpeechDB: 0\n"
            + "  xboxEnableHeadOrientation: 0\n"
            + "  xboxEnableGuest: 0\n"
            + "  xboxEnablePIXSampling: 0\n"
            + "  metalFramebufferOnly: 0\n"
            + "  n3dsDisableStereoscopicView: 0\n"
            + "  n3dsEnableSharedListOpt: 1\n"
            + "  n3dsEnableVSync: 0\n"
            + "  xboxOneResolution: 0\n"
            + "  xboxOneSResolution: 0\n"
            + "  xboxOneXResolution: 3\n"
            + "  xboxOneMonoLoggingLevel: 0\n"
            + "  ps4VideoOutPixelFormat: 0\n"
            + "  ps4VideoOutInitialWidth: 1920\n"
            + "  ps4VideoOutBaseModeInitialWidth: 1920\n"
            + "  ps4VideoOutReprojectionRate: 120\n"
            + "  ps4PronunciationSIGPath: \n"
            + "  ps4PronunciationSSMLPath: \n"
            + "  ps4BackgroundImagePath: \n"
            + "  ps4StartupImagePath: \n"
            + "  ps4StartupImagesFolder: \n"
            + "  ps4IconImagesFolder: \n"
            + "  ps4SaveDataImagePath: \n"
            + "  vulkanNumSwapchainBuffers: 3\n"
            + "  vulkanEnableSetSRGBWrite: 0\n"
            + "  vulkanEnableLateAcquireNextImage: 0\n"
            + "  vulkanEnableCommandBufferRecycling: 1\n"
            + "  m_ActiveColorSpace: 1\n"
        )

    # ── ProjectSettings/TagManager.asset ─────────────────────────

    def _generate_tag_manager(self, gdm: Dict[str, Any]) -> str:
        tags_layers = gdm.get("tags_layers", {})
        custom_tags = tags_layers.get("tags", [])
        custom_layers = tags_layers.get("layers", [])

        lines = [_YAML_HEADER, "TagManager:\n"]

        # Tags
        lines.append("  tags:\n")
        for tag in custom_tags:
            if tag not in _UNITY_BUILTIN_TAGS:
                lines.append(f"  - {tag}\n")

        # Layers (slots 0-7 are reserved by Unity)
        lines.append("  layers:\n")
        for i in range(32):
            layer_name = ""
            for layer_def in custom_layers:
                if isinstance(layer_def, dict) and layer_def.get("index") == i:
                    layer_name = layer_def.get("name", "")
                    break
            if i < 8:
                layer_name = _UNITY_BUILTIN_LAYERS.get(i, "")
            lines.append(f"  - {layer_name}\n")

        # Sorting Layers
        lines.append("  m_SortingLayers:\n")
        lines.append("  - name: Default\n")
        lines.append("    uniqueID: 0\n")
        lines.append("    locked: 0\n")

        return "".join(lines)

    # ── ProjectSettings/InputManager.asset ───────────────────────

    def _generate_input_manager(self, gdm: Dict[str, Any]) -> str:
        input_map = gdm.get("input_map", [])
        lines = [_YAML_HEADER, "InputManager:\n"]
        lines.append("  m_Axes:\n")

        # Always include Unity defaults
        default_axes = ["Horizontal", "Vertical", "Fire1", "Jump", "Mouse X", "Mouse Y"]
        seen_names: set = set()

        for name in default_axes:
            _append_axis(lines, name)
            seen_names.add(name)

        # Add GDM-defined axes
        for entry in input_map:
            name = entry.get("name", "")
            if not name or name in seen_names:
                continue
            _append_axis(lines, name)
            seen_names.add(name)

        return "".join(lines)

    # ── ProjectSettings/QualitySettings.asset ────────────────────

    def _generate_quality_settings(self) -> str:
        return (
            _YAML_HEADER
            + "QualitySettings:\n"
            + "  m_CurrentQuality: 2\n"
            + "  m_QualitySettings:\n"
            + "  - name: Low\n"
            + "    pixelLightCount: 0\n"
            + "    shadows: 0\n"
            + "    shadowResolution: 0\n"
            + "    shadowProjection: 1\n"
            + "    shadowCascades: 1\n"
            + "    shadowDistance: 20\n"
            + "    shadowNearPlaneOffset: 2\n"
            + "    shadowCascade2Split: 0.33333334\n"
            + "    shadowCascade4Split: {x: 0.06666667, y: 0.2, z: 0.46666667}\n"
            + "    shadowmaskMode: 0\n"
            + "    blendWeights: 1\n"
            + "    textureQuality: 1\n"
            + "    anisotropicTextures: 0\n"
            + "    antiAliasing: 0\n"
            + "    softParticles: 0\n"
            + "    softVegetation: 0\n"
            + "    realtimeReflectionProbes: 0\n"
            + "    billboardsFaceCameraPosition: 0\n"
            + "    vSyncCount: 0\n"
            + "    lodBias: 0.4\n"
            + "    maximumLODLevel: 0\n"
            + "    streamingMipmapsActive: 0\n"
            + "    streamingMipmapsAddAllCameras: 1\n"
            + "    streamingMipmapsMemoryBudget: 512\n"
            + "    streamingMipmapsRenderersPerFrame: 512\n"
            + "    streamingMipmapsMaxLevelReduction: 2\n"
            + "    streamingMipmapsMaxFileIORequests: 1024\n"
            + "    particleRaycastBudget: 16\n"
            + "    asyncUploadTimeSlice: 2\n"
            + "    asyncUploadBufferSize: 16\n"
            + "    asyncUploadPersistentBuffer: 1\n"
            + "    resolutionScalingFixedDPIFactor: 1\n"
            + "  - name: Medium\n"
            + "    pixelLightCount: 1\n"
            + "    shadows: 1\n"
            + "    shadowResolution: 0\n"
            + "    shadowProjection: 1\n"
            + "    shadowCascades: 1\n"
            + "    shadowDistance: 25\n"
            + "    shadowNearPlaneOffset: 2\n"
            + "    shadowCascade2Split: 0.33333334\n"
            + "    shadowCascade4Split: {x: 0.06666667, y: 0.2, z: 0.46666667}\n"
            + "    shadowmaskMode: 0\n"
            + "    blendWeights: 2\n"
            + "    textureQuality: 0\n"
            + "    anisotropicTextures: 1\n"
            + "    antiAliasing: 0\n"
            + "    softParticles: 0\n"
            + "    softVegetation: 0\n"
            + "    realtimeReflectionProbes: 0\n"
            + "    billboardsFaceCameraPosition: 0\n"
            + "    vSyncCount: 1\n"
            + "    lodBias: 0.7\n"
            + "    maximumLODLevel: 0\n"
            + "    streamingMipmapsActive: 0\n"
            + "    particleRaycastBudget: 64\n"
            + "    asyncUploadTimeSlice: 2\n"
            + "    asyncUploadBufferSize: 16\n"
            + "    asyncUploadPersistentBuffer: 1\n"
            + "    resolutionScalingFixedDPIFactor: 1\n"
            + "  - name: High\n"
            + "    pixelLightCount: 2\n"
            + "    shadows: 2\n"
            + "    shadowResolution: 1\n"
            + "    shadowProjection: 1\n"
            + "    shadowCascades: 2\n"
            + "    shadowDistance: 40\n"
            + "    shadowNearPlaneOffset: 2\n"
            + "    shadowCascade2Split: 0.33333334\n"
            + "    shadowCascade4Split: {x: 0.06666667, y: 0.2, z: 0.46666667}\n"
            + "    shadowmaskMode: 1\n"
            + "    blendWeights: 2\n"
            + "    textureQuality: 0\n"
            + "    anisotropicTextures: 1\n"
            + "    antiAliasing: 2\n"
            + "    softParticles: 1\n"
            + "    softVegetation: 1\n"
            + "    realtimeReflectionProbes: 1\n"
            + "    billboardsFaceCameraPosition: 1\n"
            + "    vSyncCount: 1\n"
            + "    lodBias: 1\n"
            + "    maximumLODLevel: 0\n"
            + "    streamingMipmapsActive: 0\n"
            + "    particleRaycastBudget: 256\n"
            + "    asyncUploadTimeSlice: 2\n"
            + "    asyncUploadBufferSize: 16\n"
            + "    asyncUploadPersistentBuffer: 1\n"
            + "    resolutionScalingFixedDPIFactor: 1\n"
            + "  m_PerPlatformDefaultQuality:\n"
            + "    Android: 0\n"
            + "    Standalone: 2\n"
            + "    WebGL: 1\n"
            + "    Windows Store Apps: 2\n"
            + "    iPhone: 0\n"
        )

    # ── ProjectSettings/EditorBuildSettings.asset ────────────────

    def _generate_editor_build_settings(self, scene_desc: Dict[str, Any]) -> str:
        scene_name = scene_desc.get("scene_name", "SampleScene")
        return (
            _YAML_HEADER
            + "EditorBuildSettings:\n"
            + "  m_Scenes:\n"
            + "  - enabled: 1\n"
            + f"    path: Assets/Scenes/{scene_name}.unity\n"
            + "    guid: 00000000000000000000000000000000\n"
        )

    # ── ProjectSettings/Physics2DSettings.asset ──────────────────

    def _generate_physics2d_settings(self, gdm: Dict[str, Any]) -> str:
        physics = gdm.get("physics_settings", {})
        gravity_y = physics.get("gravity", -9.81)
        if isinstance(gravity_y, (list, tuple)):
            gravity_y = gravity_y[1] if len(gravity_y) > 1 else -9.81

        return (
            _YAML_HEADER
            + "Physics2DSettings:\n"
            + "  m_Gravity: {x: 0, y: " + f"{gravity_y}" + "}\n"
            + "  m_DefaultMaterial: {fileID: 0}\n"
            + "  m_VelocityIterations: 8\n"
            + "  m_PositionIterations: 3\n"
            + "  m_VelocityThreshold: 1\n"
            + "  m_MaxLinearCorrection: 0.2\n"
            + "  m_MaxAngularCorrection: 8\n"
            + "  m_MaxTranslationSpeed: 100\n"
            + "  m_MaxRotationSpeed: 360\n"
            + "  m_BaumgarteScale: 0.2\n"
            + "  m_BaumgarteTimeOfImpactScale: 0.75\n"
            + "  m_TimeToSleep: 0.5\n"
            + "  m_LinearSleepTolerance: 0.01\n"
            + "  m_AngularSleepTolerance: 2\n"
            + "  m_DefaultContactOffset: 0.01\n"
            + "  m_JobOptions:\n"
            + "    useMultithreadedSolver: 0\n"
            + "    useConsistencySorting: 0\n"
            + "    m_InterpolationPosesPerJob: 100\n"
            + "    m_NewContactsPerJob: 30\n"
            + "    m_CollideContactsPerJob: 100\n"
            + "    m_ClearFlagsPerJob: 200\n"
            + "    m_ClearBodyForcesPerJob: 200\n"
            + "    m_SyncDiscreteFixturesPerJob: 50\n"
            + "    m_SyncContinuousFixturesPerJob: 50\n"
            + "    m_FindNearestContactsPerJob: 100\n"
            + "    m_UpdateTriggerContactsPerJob: 100\n"
            + "    m_IslandSolverCostThreshold: 100\n"
            + "    m_IslandSolverBodyCostScale: 1\n"
            + "    m_IslandSolverContactCostScale: 10\n"
            + "    m_IslandSolverJointCostScale: 10\n"
            + "    m_IslandSolverBodiesPerJob: 50\n"
            + "    m_IslandSolverContactsPerJob: 50\n"
            + "  m_AutoSimulation: 1\n"
            + "  m_QueriesHitTriggers: 1\n"
            + "  m_QueriesStartInColliders: 1\n"
            + "  m_CallbacksOnDisable: 1\n"
            + "  m_ReuseColliderHash: 0\n"
            + "  m_AutoSyncTransforms: 0\n"
            + "  m_AlwaysShowColliders: 0\n"
            + "  m_ShowColliderSleep: 1\n"
            + "  m_ShowColliderContacts: 0\n"
            + "  m_ShowColliderAABB: 0\n"
            + "  m_ContactArrowScale: 0.2\n"
            + "  m_ColliderAwakeColor: {r: 0.5686275, g: 0.95686275, b: 0.54509807, a: 0.7529412}\n"
            + "  m_ColliderAsleepColor: {r: 0.5686275, g: 0.95686275, b: 0.54509807, a: 0.36078432}\n"
            + "  m_ColliderContactColor: {r: 1, g: 0, b: 1, a: 0.6862745}\n"
            + "  m_ColliderAABBColor: {r: 1, g: 1, b: 0, a: 0.2509804}\n"
            + "  m_LayerCollisionMatrix: ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff\n"
        )

    # ── .meta 文件生成 ───────────────────────────────────────────

    def _generate_meta_files(self, code_generated: Dict[str, str]) -> Dict[str, str]:
        meta_files: Dict[str, str] = {}
        for file_path, content in code_generated.items():
            if not file_path.endswith(".cs"):
                continue
            guid = _deterministic_guid(file_path)
            meta_files[file_path + ".meta"] = (
                "fileFormatVersion: 2\n"
                f"guid: {guid}\n"
                "MonoImporter:\n"
                "  externalObjects: {}\n"
                "  serializedVersion: 2\n"
                "  defaultReferences: []\n"
                "  executionOrder: 0\n"
                "  icon: {instanceID: 0}\n"
                "  userData: \n"
                "  assetBundleName: \n"
                "  assetBundleVariant: \n"
            )
        return meta_files

    # ── 辅助判断 ─────────────────────────────────────────────────

    def _is_2d_project(
        self, gdm: Dict[str, Any], code_generated: Dict[str, str]
    ) -> bool:
        camera_mode = gdm.get("camera_mode", "")
        if "2d" in str(camera_mode).lower():
            return True
        if gdm.get("physics_settings", {}).get("type", "") == "2d":
            return True
        for content in code_generated.values():
            if not isinstance(content, str):
                continue
            if "Rigidbody2D" in content or "Physics2D" in content:
                return True
            if "SpriteRenderer" in content or "Camera.main.orthographic" in content:
                return True
        return False


# ── 模块级常量与辅助函数 ────────────────────────────────────────

_YAML_HEADER = (
    "%YAML 1.1\n"
    "%TAG !u! tag:unity3d.com,2011:\n"
    "--- !u!29 &1\n"
)

_UNITY_BUILTIN_TAGS = {
    "Untagged", "MainCamera", "Player", "Respawn", "Finish", "EditorOnly",
}

_UNITY_BUILTIN_LAYERS = {
    0: "Default",
    1: "TransparentFX",
    2: "Ignore Raycast",
    4: "Water",
    5: "UI",
}


def _deterministic_guid(path: str) -> str:
    """基于文件路径生成确定性 GUID（32 位 hex，与 Unity 格式一致）。"""
    return hashlib.md5(path.encode("utf-8")).hexdigest()


def _append_axis(lines: list, name: str) -> None:
    """追加一个 InputManager axis 条目（Unity 序列化格式）。"""
    lines.append("  - serializedVersion: 3\n")
    lines.append(f"    m_Name: {name}\n")
    lines.append("    descriptiveName: \n")
    lines.append("    descriptiveNegativeName: \n")
    lines.append("    negativeButton: \n")
    lines.append("    positiveButton: \n")
    lines.append("    altNegativeButton: \n")
    lines.append("    altPositiveButton: \n")
    lines.append("    gravity: 3\n")
    lines.append("    dead: 0.001\n")
    lines.append("    sensitivity: 1\n")
    lines.append("    snap: 0\n")
    lines.append("    invert: 0\n")
    lines.append("    type: 0\n")
    lines.append("    axis: 0\n")
    lines.append("    joyNum: 0\n")
