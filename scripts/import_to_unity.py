"""GameForge - Unity导入脚本

自动将生成的代码复制到Unity项目。
"""

import os
import shutil
import sys
from pathlib import Path


def import_to_unity(unity_project_path: str, output_dir: str = "output"):
    """导入代码到Unity项目

    Args:
        unity_project_path: Unity项目路径
        output_dir: 生成代码的输出目录
    """
    unity_path = Path(unity_project_path)
    output_path = Path(output_dir)

    # 检查Unity项目是否存在
    if not unity_path.exists():
        print(f"[ERROR] Unity项目路径不存在: {unity_path}")
        return False

    # 检查Assets目录
    assets_path = unity_path / "Assets"
    if not assets_path.exists():
        print(f"[ERROR] Assets目录不存在: {assets_path}")
        return False

    # 检查输出目录
    if not output_path.exists():
        print(f"[ERROR] 输出目录不存在: {output_path}")
        return False

    # 复制Scripts目录
    source_scripts = output_path / "Assets" / "Scripts"
    target_scripts = assets_path / "Scripts"

    if not source_scripts.exists():
        print(f"[ERROR] 源Scripts目录不存在: {source_scripts}")
        return False

    # 如果目标已存在，备份
    if target_scripts.exists():
        backup_path = assets_path / "Scripts_backup"
        if backup_path.exists():
            shutil.rmtree(backup_path)
        shutil.copytree(target_scripts, backup_path)
        print(f"[INFO] 已备份原Scripts目录到: {backup_path}")

    # 复制文件
    shutil.copytree(source_scripts, target_scripts, dirs_exist_ok=True)

    print(f"[SUCCESS] 代码已导入到: {target_scripts}")

    # 列出导入的文件
    print("\n导入的文件:")
    for cs_file in target_scripts.rglob("*.cs"):
        rel_path = cs_file.relative_to(assets_path)
        print(f"  - {rel_path}")

    return True


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("用法: python import_to_unity.py <Unity项目路径>")
        print("示例: python import_to_unity.py D:/UnityProjects/GameForge")
        sys.exit(1)

    unity_project_path = sys.argv[1]
    success = import_to_unity(unity_project_path)

    if success:
        print("\n" + "=" * 60)
        print("导入成功!")
        print("=" * 60)
        print("\n下一步:")
        print("1. 打开Unity Editor")
        print("2. 在Project窗口中刷新 (Ctrl+R)")
        print("3. 创建Player和GameManager对象")
        print("4. 添加组件并配置参数")
        print("5. 点击Play运行测试")
    else:
        print("\n导入失败，请检查路径是否正确")


if __name__ == "__main__":
    main()
