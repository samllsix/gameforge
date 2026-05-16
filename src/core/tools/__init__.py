"""GameForge - 核心工具模块

提供文件操作、代码分析、项目管理等工具函数。
"""

import os
import re
import hashlib
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path


def read_file(file_path: str, encoding: str = "utf-8") -> Optional[str]:
    """安全读取文件内容

    Args:
        file_path: 文件路径
        encoding: 编码格式

    Returns:
        文件内容，读取失败返回None
    """
    try:
        with open(file_path, "r", encoding=encoding) as f:
            return f.read()
    except (IOError, UnicodeDecodeError):
        return None


def write_file(file_path: str, content: str, encoding: str = "utf-8") -> bool:
    """安全写入文件内容

    Args:
        file_path: 文件路径
        content: 文件内容
        encoding: 编码格式

    Returns:
        是否写入成功
    """
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding=encoding) as f:
            f.write(content)
        return True
    except IOError:
        return False


def list_files(directory: str, extensions: Optional[List[str]] = None) -> List[str]:
    """列出目录下的文件

    Args:
        directory: 目录路径
        extensions: 文件扩展名过滤列表

    Returns:
        文件路径列表
    """
    files = []
    if not os.path.isdir(directory):
        return files

    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            if extensions:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in extensions:
                    continue
            files.append(os.path.join(root, filename))

    return sorted(files)


def extract_code_blocks(content: str, language: str = "csharp") -> List[Dict[str, str]]:
    """从文本中提取代码块

    Args:
        content: 文本内容
        language: 代码语言

    Returns:
        代码块列表，每个包含file_path和content
    """
    blocks = []
    pattern = rf'```(?:{language}|cs)?\s*\n(.*?)\n```'
    matches = re.findall(pattern, content, re.DOTALL)

    for match in matches:
        match = match.strip()
        if not match:
            continue

        file_path_match = re.search(r'//\s*(?:文件|File):\s*(\S+)', match)
        if file_path_match:
            file_path = file_path_match.group(1)
            match = re.sub(r'//\s*(?:文件|File):\s*\S+\s*\n', '', match, count=1).strip()
        else:
            file_path = None

        blocks.append({"file_path": file_path, "content": match})

    return blocks


def calculate_code_metrics(content: str) -> Dict[str, Any]:
    """计算代码度量指标

    Args:
        content: 代码内容

    Returns:
        度量指标字典
    """
    lines = content.split("\n")
    total_lines = len(lines)
    blank_lines = sum(1 for line in lines if not line.strip())
    comment_lines = sum(1 for line in lines if line.strip().startswith("//") or line.strip().startswith("///"))
    code_lines = total_lines - blank_lines - comment_lines

    methods = re.findall(
        r'(?:public|private|protected|internal)?\s*(?:static\s+)?(?:async\s+)?\w+\s+\w+\s*\([^)]*\)\s*\{',
        content,
    )
    classes = re.findall(r'(?:public|private|protected|internal)?\s*(?:static\s+)?(?:partial\s+)?class\s+\w+', content)

    max_indent = 0
    for line in lines:
        stripped = line.lstrip()
        if stripped:
            indent = len(line) - len(stripped)
            max_indent = max(max_indent, indent)

    return {
        "total_lines": total_lines,
        "code_lines": code_lines,
        "blank_lines": blank_lines,
        "comment_lines": comment_lines,
        "comment_ratio": comment_lines / total_lines if total_lines > 0 else 0,
        "method_count": len(methods),
        "class_count": len(classes),
        "max_indent_depth": max_indent // 4,
        "avg_line_length": sum(len(line) for line in lines) / total_lines if total_lines > 0 else 0,
    }


def validate_csharp_syntax(content: str) -> Tuple[bool, List[str]]:
    """基础C#语法验证

    Args:
        content: C#代码内容

    Returns:
        (是否通过验证, 错误列表)
    """
    errors = []

    open_braces = content.count("{")
    close_braces = content.count("}")
    if open_braces != close_braces:
        errors.append(f"大括号不匹配: {{ = {open_braces}, }} = {close_braces}")

    open_parens = content.count("(")
    close_parens = content.count(")")
    if open_parens != close_parens:
        errors.append(f"小括号不匹配: ( = {open_parens}, ) = {close_parens}")

    if "using " in content and "namespace " in content:
        using_end = content.index("namespace ")
        using_section = content[:using_end]
        if ";" not in using_section:
            errors.append("using语句缺少分号")

    lines = content.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("//") and not stripped.startswith("/*") and not stripped.startswith("*"):
            if stripped.endswith(";") and stripped.count(";") > 1:
                if "for " not in stripped and "for(" not in stripped:
                    errors.append(f"第{i+1}行可能有多个语句")

    return len(errors) == 0, errors


def generate_file_hash(content: str) -> str:
    """生成文件内容哈希

    Args:
        content: 文件内容

    Returns:
        MD5哈希值
    """
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除非法字符

    Args:
        filename: 原始文件名

    Returns:
        清理后的文件名
    """
    illegal_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(illegal_chars, "_", filename)
    sanitized = sanitized.strip(". ")
    return sanitized or "unnamed"
