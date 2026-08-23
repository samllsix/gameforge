"""隐私扫描器：扫所有 git tracked 文件，找硬编码的密钥、API Key、token、密码。

排除 .env（已在 .gitignore）、.env.example（无隐私）、*.lock / *.lock.* 等。

输出：命中行 + 文件 + 行号，按敏感度排序。
"""
import os
import re
import subprocess
import sys

# 已知高敏感 patterns
PATTERNS = [
    # 各种 LLM key 格式
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/DouDeep API Key"),
    (r"sk-ant-[a-zA-Z0-9-]{20,}", "Anthropic API Key"),
    (r"lsv2_pt_[a-zA-Z0-9_]{20,}", "LangSmith API Key"),
    (r"tp-[a-zA-Z0-9]{20,}", "Token Plan API Key"),
    (r"sk-GYzifLo[A-Za-z0-9]{20,}", "SenseNova API Key"),
    # 飞书/钉钉/Lark
    (r"t-[a-f0-9]{32}", "Feishu App Token"),
    # URL 含密码
    (r"mysql(\+pymysql)?://[a-zA-Z0-9_]+:[^@]{4,}@", "MySQL URL with password"),
    (r"postgres(ql)?://[a-zA-Z0-9_]+:[^@]{4,}@", "PostgreSQL URL with password"),
    # Qdrant API Key (我们的 .env 里是空，但其他可能泄漏)
    (r"QDRANT_API_KEY=[^$\s]{8,}", "Qdrant API Key"),
    # 通用 token / password / secret 字段
    (r"(?i)(password|passwd|secret|api_key|api-key|token)\s*[=:]\s*['\"]?[a-zA-Z0-9_+/=-]{8,}",
        "Generic password/secret literal"),
    # Bearer tokens
    (r"Bearer\s+[a-zA-Z0-9_-]{20,}", "Bearer token"),
    # JWT
    (r"eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}", "JWT"),
    # Private key
    (r"-----BEGIN (RSA |EC )?PRIVATE KEY-----", "Private key PEM"),
]


def is_safe_line(line: str) -> bool:
    """排除明显无害的命中。"""
    # .env.example / .env.template 这种纯模板
    if any(t in line for t in [
        "your_", "xxx", "your-", "replace-with",
        "replace_in_production", "replace-with-vault",
        "your_deepseek", "your_mimo", "your_openai",
        "your_anthropic", "your_langsmith", "your_qdrant",
        "your_redis_password", "your_zhipu", "your_kimi",
        "your_secret_key", "<vault-secret>", "<vault-secret>",
    ]):
        return True
    # 注释里的占位符
    if re.search(r"^\s*#", line) and ("your_" in line or "替换" in line or "example" in line):
        return True
    return False


def main():
    # 取所有 tracked files
    out = subprocess.run(
        ["git", "ls-files"], cwd=".", capture_output=True, text=True, encoding="utf-8",
    )
    files = out.stdout.splitlines()
    print(f"Scanning {len(files)} tracked files...")

    findings = []
    for f in files:
        # 跳过二进制、lock、.gitignore 自身
        if any(p in f for p in [
            ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp",
            ".ogg", ".mp3", ".wav", ".zip", ".pdf",
            "assets/kenney", "node_modules/",
            ".lock", "package-lock.json", "yarn.lock",
            "scripts/audit_privacy.py",  # 自身
        ]):
            continue
        # 跳过 .env 文件（.env.example 是模板可扫）
        if f == ".env":
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception:
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            for pat, name in PATTERNS:
                m = re.search(pat, line)
                if m and not is_safe_line(line):
                    findings.append((f, line_no, name, m.group(0), line.strip()[:200]))

    if not findings:
        print("\n[OK] No sensitive data found in tracked files.")
        return 0

    print(f"\n[!] Found {len(findings)} potential secrets:")
    print("=" * 80)
    for f, ln, kind, match, text in findings:
        print(f"{f}:{ln}  [{kind}]")
        print(f"  match: {match}")
        print(f"  line:  {text}")
        print()

    return 1


if __name__ == "__main__":
    sys.exit(main())