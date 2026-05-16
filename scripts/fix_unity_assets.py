"""
fix_unity_assets.py - 自动修复 Unity 项目素材导入问题

修复内容:
  1. 安装 Vector Graphics 包 (解决 SVG 导入报错)
  2. 解析 Kenney spritesheet XML → 生成正确的 Unity .meta 文件 (自动切片)
  3. 输出仍需手动处理的步骤
"""

import json
import os
import struct
from hashlib import md5
from pathlib import Path
from xml.etree import ElementTree as ET

UNITY_PROJECT = Path("D:/Unity/GameForge")
SPRITESHEET_DIR = UNITY_PROJECT / "Assets/Art/Spritesheets"
MANIFEST_PATH = UNITY_PROJECT / "Packages/manifest.json"


# ── 1. 安装 Vector Graphics 包 ──
def fix_vector_graphics():
    print("[1/3] 检查 Unity Vector Graphics 包...")
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    pkg = "com.unity.vector.graphics"
    if pkg in manifest.get("dependencies", {}):
        print(f"  [OK] {pkg} 已安装")
        return

    # 注意: Unity 2022+ 推荐版本
    manifest["dependencies"][pkg] = "2.0.0"
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  [FIX] 已添加 {pkg} 到 manifest.json")
    print(f"  [下一步] 打开 Unity Editor，Package Manager 会自动解析并安装")


# ── 2. 计算 Unity spriteID ──
def make_sprite_id(tex_guid: str, name: str, x: int, y: int, w: int, h: int) -> str:
    """
    模拟 Unity InternalEditorUtility.CreateSpriteID 的算法:
    MD5(name_bytes + tex_guid_bytes + rect + metadata) → 128-bit GUID
    """
    tex_guid_bytes = bytes.fromhex(tex_guid)
    data = bytearray()
    data += name.encode("utf-8")
    data += tex_guid_bytes
    data += struct.pack("<iiii", x, y, w, h)
    data += struct.pack("<i", 2)   # serializedVersion
    data += struct.pack("<i", 0)   # alignment
    data += struct.pack("<ff", 0.5, 0.5)  # pivot
    data += struct.pack("<iiii", 0, 0, 0, 0)  # border
    h = md5(data)
    # Unity 格式: 每4字节一组，hex 小写
    parts = []
    for i in range(0, 16, 4):
        parts.append(h.digest()[i:i+4].hex())
    return "".join(parts)


# ── 3. 从 XML 解析子精灵 ──
def parse_kenney_xml(xml_path: Path) -> list[dict]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    sprites = []
    for sub in root.findall("SubTexture"):
        sprites.append({
            "name": sub.get("name"),
            "x": int(sub.get("x")),
            "y": int(sub.get("y")),
            "width": int(sub.get("width")),
            "height": int(sub.get("height")),
        })
    return sprites


def gen_texture_meta(png_path: Path, xml_path: Path, tex_guid: str) -> str:
    sprites = parse_kenney_xml(xml_path)
    # 判断像素单位: tiles 是 64, characters/enemies/backgrounds 使用实际尺寸
    if "tiles" in png_path.stem.lower():
        pixels_per_unit = 64
    elif "backgrounds" in png_path.stem.lower():
        pixels_per_unit = 128
    else:
        pixels_per_unit = 128  # characters, enemies 都是 128

    sprite_entries = []
    for s in sprites:
        sid = make_sprite_id(tex_guid, s["name"], s["x"], s["y"], s["width"], s["height"])
        entry = (
            f"    - serializedVersion: 2\n"
            f"      name: {s['name']}\n"
            f"      rect:\n"
            f"        serializedVersion: 2\n"
            f"        x: {s['x']}\n"
            f"        y: {s['y']}\n"
            f"        width: {s['width']}\n"
            f"        height: {s['height']}\n"
            f"      alignment: 0\n"
            f"      pivot: {{x: 0.5, y: 0.5}}\n"
            f"      border: {{x: 0, y: 0, z: 0, w: 0}}\n"
            f"      outline: []\n"
            f"      physicsShape: []\n"
            f"      tessellationDetail: 0\n"
            f"      bones: []\n"
            f"      spriteID: {sid}\n"
        )
        sprite_entries.append(entry)

    sprites_yaml = "\n".join(sprite_entries)
    sprite_count = len(sprites)

    return f"""fileFormatVersion: 2
guid: {tex_guid}
TextureImporter:
  internalIDToNameTable: []
  externalObjects: {{}}
  serializedVersion: 12
  mipmaps:
    mipMapMode: 0
    enableMipMap: 0
    sRGBTexture: 1
    linearTexture: 0
    fadeOut: 0
    borderMipMap: 0
    mipMapsPreserveCoverage: 0
    alphaTestReferenceValue: 0.5
    mipMapFadeDistanceStart: 1
    mipMapFadeDistanceEnd: 3
  bumpmap:
    convertToNormalMap: 0
    externalNormalMap: 0
    heightScale: 0.25
    normalMapFilter: 0
  isReadable: 0
  streamingMipmaps: 0
  streamingMipmapsPriority: 0
  vTOnly: 0
  ignoreMipmapLimit: 0
  grayScaleToAlpha: 0
  generateCubemap: 6
  cubemapConvolution: 0
  seamlessCubemap: 0
  textureFormat: 1
  maxTextureSize: 2048
  textureSettings:
    serializedVersion: 2
    filterMode: 0
    aniso: 1
    mipBias: 0
    wrapU: 1
    wrapV: 1
    wrapW: 1
  nPOTScale: 0
  lightmap: 0
  compressionQuality: 50
  spriteMode: 2
  spriteExtrude: 0
  spriteMeshType: 1
  alignment: 0
  spritePivot: {{x: 0.5, y: 0.5}}
  spritePixelsToUnits: {pixels_per_unit}
  spriteBorder: {{x: 0, y: 0, z: 0, w: 0}}
  spriteGenerateFallbackPhysicsShape: 1
  alphaUsage: 1
  alphaIsTransparency: 1
  spriteTessellationDetail: -1
  textureType: 8
  textureShape: 1
  singleChannelComponent: 0
  flipbookRows: 1
  flipbookColumns: 1
  maxTextureSizeSet: 0
  compressionQualitySet: 0
  textureFormatSet: 0
  ignorePngGamma: 0
  applyGammaDecoding: 0
  swizzle: 50462976
  cookieLightType: 0
  platformSettings:
  - serializedVersion: 3
    buildTarget: DefaultTexturePlatform
    maxTextureSize: 2048
    resizeAlgorithm: 0
    textureFormat: -1
    textureCompression: 1
    compressionQuality: 50
    crunchedCompression: 0
    allowsAlphaSplitting: 0
    overridden: 0
    androidETC2FallbackOverride: 0
    forceMaximumCompressionQuality_BC6H_BC7: 0
  spriteSheet:
    serializedVersion: 2
    sprites:
{sprites_yaml}
    spritePackingTag: 
    pSDRemoveMatte: 0
    userData: 
    assetBundleName: 
    assetBundleVariant: 
"""


def fix_spritesheets():
    print("\n[2/3] 修复 Spritesheet 切片配置...")
    png_files = sorted(SPRITESHEET_DIR.glob("*.png"))
    fixed = 0

    for png_path in png_files:
        xml_path = png_path.with_suffix(".xml")
        if not xml_path.exists():
            print(f"  [SKIP] {png_path.name}: 缺少对应 XML")
            continue

        meta_path = Path(str(png_path) + ".meta")
        # 保留原 GUID
        if meta_path.exists():
            old_content = meta_path.read_text(encoding="utf-8")
            guid_line = [l for l in old_content.splitlines() if l.startswith("guid:")]
            old_guid = guid_line[0].split(": ")[1] if guid_line else None
        else:
            old_guid = None

        tex_guid = old_guid or md5(png_path.name.encode()).hexdigest()[:32]

        meta_content = gen_texture_meta(png_path, xml_path, tex_guid)
        meta_path.write_text(meta_content, encoding="utf-8", newline="\n")

        sprites = parse_kenney_xml(xml_path)
        print(f"  [FIX] {png_path.name} → {len(sprites)} 个子精灵已配置")
        fixed += 1

    print(f"\n  共修复 {fixed} 个 spritesheet")


def main():
    print("=" * 60)
    print(" GameForge Unity 素材自动修复工具")
    print("=" * 60)

    # 1. Vector Graphics
    fix_vector_graphics()

    # 2. Spritesheet 切片
    fix_spritesheets()

    # 3. 仍需手动处理的部分
    print(f"""
[3/3] 仍需手动处理的步骤
  ───────────────────────────────────────────────

  A. SVG 素材（可选）
     安装 Vector Graphics 包后，Unity 重启会自动导入 SVG。
     如果不需要 SVG（Sprites/ 下有 PNG 版本），可以删除
     {UNITY_PROJECT / 'Assets/Art/Vector/'}

  B. 场景精灵引用
     GameScene.unity 中 Player/Ground/Platform 的 SpriteRenderer
     [33m  目前引用均为 "None"[0m，需要在 Unity Editor 中：
     1. 打开 GameScene.unity
     2. 选中 Player → 在 Inspector 中将 Sprite 字段拖入角色精灵
        (推荐: Assets/Art/Sprites/Characters/Default/character_beige_idle)
     3. Ground/Platform1/Platform2 → 拖入地块精灵
        (推荐: Assets/Art/Sprites/Tiles/Default/ 中的 terrain_grass_* 或 block_green 等)
     4. Ctrl+S 保存场景

  C. 音效
     Assets/Art/Sounds/ 下的 .ogg 文件 Unity 已自动识别为 AudioClip，
     无需额外处理。如需使用可在 AudioSource 组件中引用。

  ───────────────────────────────────────────────
  [32m修复完成！打开 Unity Editor 让资源重新导入即可。[0m
""")


if __name__ == "__main__":
    main()
