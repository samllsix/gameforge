"""GameForge - 编译器接口模块

提供代码编译和语法检查功能。
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class CompileResult:
    """编译结果"""
    success: bool
    errors: List[str]
    warnings: List[str]
    output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "errors": self.errors,
            "warnings": self.warnings,
            "output": self.output,
        }


class CSharpCompiler:
    """C#代码编译器 - 提供语法检查和编译验证"""

    def __init__(self, config: Dict[str, Any]):
        self.unity_path = config.get("unity_editor_path", "")
        self.dotnet_path = config.get("dotnet_path", "dotnet")

    def check_syntax(self, code: str) -> CompileResult:
        """检查C#代码语法

        Args:
            code: C#代码

        Returns:
            编译结果
        """
        errors = []
        warnings = []

        brace_open = code.count("{")
        brace_close = code.count("}")
        if brace_open != brace_close:
            errors.append(f"大括号不匹配: {{ = {brace_open}, }} = {brace_close}")

        paren_open = code.count("(")
        paren_close = code.count(")")
        if paren_open != paren_close:
            errors.append(f"小括号不匹配: ( = {paren_open}, ) = {paren_close}")

        bracket_open = code.count("[")
        bracket_close = code.count("]")
        if bracket_open != bracket_close:
            errors.append(f"方括号不匹配: [ = {bracket_open}, ] = {bracket_close}")

        lines = code.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue

            if stripped.endswith(";") and stripped.count(";") > 1:
                if "for " not in stripped and "for(" not in stripped:
                    warnings.append(f"第{i+1}行可能有多个语句")

        if "using " in code and "namespace " in code:
            using_section = code[:code.index("namespace")]
            usings = [l.strip() for l in using_section.split("\n") if l.strip().startswith("using")]
            for using in usings:
                if not using.endswith(";"):
                    errors.append(f"using语句缺少分号: {using}")

        if "class " in code:
            class_match = re.search(r'class\s+(\w+)', code)
            if class_match:
                class_name = class_match.group(1)
                if not class_name[0].isupper():
                    warnings.append(f"类名 '{class_name}' 应使用PascalCase")

        return CompileResult(
            success=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def validate_unity_script(self, code: str, class_name: str) -> CompileResult:
        """验证Unity脚本

        Args:
            code: Unity C#脚本
            class_name: 类名

        Returns:
            验证结果
        """
        errors = []
        warnings = []

        syntax_result = self.check_syntax(code)
        errors.extend(syntax_result.errors)
        warnings.extend(syntax_result.warnings)

        if "using UnityEngine" not in code:
            warnings.append("缺少 using UnityEngine")

        if f"class {class_name}" not in code:
            errors.append(f"未找到类定义: {class_name}")

        if ": MonoBehaviour" in code:
            unity_methods = ["Awake", "Start", "Update", "FixedUpdate", "LateUpdate"]
            has_any = any(m + "(" in code for m in unity_methods)
            if not has_any:
                warnings.append("继承MonoBehaviour但未实现任何Unity生命周期方法")

        return CompileResult(
            success=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def extract_dependencies(self, code: str) -> List[str]:
        """提取代码依赖

        Args:
            code: C#代码

        Returns:
            依赖列表
        """
        dependencies = []

        usings = re.findall(r'using\s+([\w.]+)\s*;', code)
        dependencies.extend(usings)

        base_classes = re.findall(r':\s*(\w+(?:,\s*\w+)*)', code)
        for base in base_classes:
            for cls in base.split(","):
                cls = cls.strip()
                if cls and cls[0].isupper():
                    dependencies.append(cls)

        return list(set(dependencies))

    def generate_project_file(self, project_name: str, files: List[str]) -> str:
        """生成.csproj项目文件

        Args:
            project_name: 项目名称
            files: 源文件列表

        Returns:
            .csproj文件内容
        """
        file_items = "\n".join(f'    <Compile Include="{f}" />' for f in files)

        return f'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netstandard2.1</TargetFramework>
    <RootNamespace>{project_name}</RootNamespace>
    <AssemblyName>{project_name}</AssemblyName>
  </PropertyGroup>
  <ItemGroup>
{file_items}
  </ItemGroup>
</Project>'''
