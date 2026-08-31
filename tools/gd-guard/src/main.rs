// gd-guard — GameForge 生成脚本/场景静态安全闸门
//
// 用法:  gd-guard.exe scan <project_path>
// 输出:  stdout JSON  {"verdict":"allow|block","scanned":{...},"findings":[...]}
// 退出码: 0=allow  1=block  2=用法/IO错误
//
// 设计要点:
// - 零第三方依赖(手写 JSON 转义), 构建快且不依赖 crates.io
// - 对不可信输入健壮: 任何畸形文件只会产生 finding, 不会 panic
// - 信任边界: res://addons/gameforge/ 下的官方运行时脚本受信任(我们发布的),
//   其余所有 .gd 全部按不可信扫描; .tscn 内嵌 GDScript 一律按不可信扫描
// - 规则 = 危险 API 黑名单(执行/文件/网络/多手柄), 命中即 block(零容忍,
//   生成的游戏只需要节点/物理/HUD 类 API, 没有正当理由用这些)

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process;

// ── 危险 API 黑名单 ─────────────────────────────────────────────
const BLOCK_PATTERNS: &[(&str, &str)] = &[
    ("OS.execute", "执行任意系统命令"),
    ("OS.create_process", "创建任意进程"),
    ("OS.shell_open", "打开任意外部程序/URL"),
    ("FileAccess", "任意文件读写"),
    ("DirAccess", "任意目录遍历/删除"),
    ("FileAccess.open_encrypted", "加密文件读写"),
    ("ResourceSaver.save", "任意资源写盘"),
    ("TCPServer", "原始 TCP 服务"),
    ("StreamPeerTCP", "原始 TCP 连接"),
    ("UDPServer", "UDP 服务"),
    ("PacketPeerUDP", "UDP 报文"),
    ("PacketPeerStream", "流式报文"),
    ("HTTPRequest", "任意 HTTP 请求"),
    ("HTTPClient", "任意 HTTP 客户端"),
    ("WebSocketPeer", "WebSocket 外联"),
    ("WebSocketMultiplayerPeer", "WebSocket 多人外联"),
    ("ENetMultiplayerPeer", "ENet 联机"),
    ("MultiplayerAPI", "多人联机 API"),
    ("SceneMultiplayer", "多人联机 API"),
    ("JavaScriptBridge", "Web 环境逃逸"),
    ("ClassDB.class_call_static", "运行时反射静态调用(可绕过静态扫描)"),
    ("ClassDB.instantiate", "运行时反射实例化(可绕过静态扫描)"),
    ("Performance.get_monitor", "引擎性能探针(非游戏逻辑)"),
    ("Engine.get_frames()", "引擎内部状态探针"),
];

// 信任名单: 我们自己发布的运行时脚本(精确到文件, 防止向信任目录投毒绕过扫描)
const TRUSTED_FILES: &[&str] = &[
    // 官方运行时库(scene_to_godot.RUNTIME_SCRIPTS, 代码硬编码于 Python 侧)
    "res://addons/gameforge/runtime/bouncer.gd",
    "res://addons/gameforge/runtime/game_flow.gd",
    "res://addons/gameforge/runtime/grid_runtime.gd",
    "res://addons/gameforge/runtime/hud.gd",
    "res://addons/gameforge/runtime/mover.gd",
    "res://addons/gameforge/runtime/parallax_bg.gd",
    "res://addons/gameforge/runtime/pickup.gd",
    "res://addons/gameforge/runtime/player.gd",
    "res://addons/gameforge/runtime/rotator.gd",
    "res://addons/gameforge/runtime/walker.gd",
    // 预览基础设施(仓库静态 addons/gameforge/ 原样复制)
    "res://addons/gameforge/preview_runner.gd",
    "res://addons/gameforge/screenshot_server.gd",
    "res://addons/gameforge/settings.gd",
];

// 允许被 project.godot autoload 引用的前缀
const ALLOWED_AUTOLOAD_PREFIX: &str = "res://addons/gameforge/";

#[derive(Default)]
struct Report {
    verdict_block: bool,
    scanned_gd: u32,
    scanned_tscn: u32,
    scanned_pg: bool,
    findings: Vec<String>, // 已转义的 JSON 字符串
}

impl Report {
    fn finding(&mut self, file: &str, line: u32, rule: &str, detail: &str, snippet: &str) {
        self.verdict_block = true;
        self.findings.push(format!(
            "{{\"file\":\"{}\",\"line\":{},\"rule\":\"{}\",\"detail\":\"{}\",\"snippet\":\"{}\"}}",
            json_escape(file),
            line,
            json_escape(rule),
            json_escape(detail),
            json_escape(&truncate(snippet, 160)),
        ));
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        s.chars().take(max).collect()
    }
}

fn json_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

fn is_trusted(res_path: &str) -> bool {
    TRUSTED_FILES.contains(&res_path)
}

// 扫描一段 GDScript 源码(文件或 tscn 内嵌), 逐行匹配黑名单
fn scan_gd_source(report: &mut Report, display: &str, source: &str) {
    for (idx, raw_line) in source.lines().enumerate() {
        let line_no = (idx + 1) as u32;
        let line = raw_line.trim();
        // 去掉行注释后再匹配, 避免注释里的示例误报
        let code = line.split('#').next().unwrap_or("");
        if code.trim().is_empty() {
            continue;
        }
        for (pat, desc) in BLOCK_PATTERNS {
            if code.contains(pat) {
                report.finding(display, line_no, pat, desc, raw_line);
                break; // 每行报一条即可
            }
        }
    }
}

fn scan_gd_file(report: &mut Report, path: &Path, rel_res: &str) {
    let source = match fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) => {
            report.finding(rel_res, 0, "unreadable", &format!("读取失败: {e}"), "");
            return;
        }
    };
    report.scanned_gd += 1;
    scan_gd_source(report, rel_res, &source);
}

// ── .tscn 校验 ─────────────────────────────────────────────────

fn scan_tscn(report: &mut Report, path: &Path, rel_display: &str) {
    let content = match fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) => {
            report.finding(rel_display, 0, "unreadable", &format!("读取失败: {e}"), "");
            return;
        }
    };
    report.scanned_tscn += 1;

    // 1) ext_resource: 必须 res:// 且解析后不越出项目(拒绝 .. 与盘符/绝对路径)
    for (idx, raw) in content.lines().enumerate() {
        let line_no = (idx + 1) as u32;
        let line = raw.trim();
        if let Some(rest) = line.strip_prefix("[ext_resource") {
            let path_val = extract_quoted(rest, "path=");
            match path_val {
                Some(p) if p.starts_with("res://") => {
                    let rel = &p["res://".len()..];
                    if rel.contains("..") || rel.starts_with('/') || rel.contains(':') {
                        report.finding(
                            rel_display,
                            line_no,
                            "ext_resource_escape",
                            "ext_resource 路径越出项目目录",
                            raw,
                        );
                    }
                }
                Some(p) => {
                    report.finding(
                        rel_display,
                        line_no,
                        "ext_resource_non_res",
                        "ext_resource 必须使用 res:// 相对路径",
                        raw,
                    );
                    let _ = p;
                }
                None => {
                    report.finding(
                        rel_display,
                        line_no,
                        "ext_resource_no_path",
                        "ext_resource 缺少 path 字段",
                        raw,
                    );
                }
            }
        }
    }

    // 2) 内嵌 GDScript(sub_resource 的 script/source): 一律按不可信扫描
    let mut in_embedded: Option<(String, u32)> = None;
    let mut embed_buf = String::new();
    for (idx, raw) in content.lines().enumerate() {
        let line_no = (idx + 1) as u32;
        let line = raw.trim();
        if line.starts_with("[sub_resource type=\"GDScript\"") {
            in_embedded = Some((rel_display.to_string(), line_no));
            embed_buf.clear();
            continue;
        }
        if in_embedded.is_some() {
            if line.starts_with('[') {
                // 下一个资源块开始 → 结算内嵌脚本
                if !embed_buf.is_empty() {
                    scan_gd_source(report, &format!("{rel_display}#embedded@{line_no}"), &embed_buf);
                }
                in_embedded = None;
                embed_buf.clear();
            } else if let Some(rest) = line.strip_prefix("script/source=") {
                // Godot 导出的转义字符串, 粗略还原常见转义后扫描
                let unescaped = rest
                    .trim_matches('"')
                    .replace("\\n", "\n")
                    .replace("\\\"", "\"")
                    .replace("\\\\", "\\");
                embed_buf.push_str(&unescaped);
                embed_buf.push('\n');
            }
        }
    }
    if in_embedded.is_some() && !embed_buf.is_empty() {
        scan_gd_source(report, &format!("{rel_display}#embedded"), &embed_buf);
    }
}

// ── project.godot 校验 ─────────────────────────────────────────

fn scan_project_godot(report: &mut Report, path: &Path) {
    let content = match fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) => {
            report.finding("project.godot", 0, "unreadable", &format!("读取失败: {e}"), "");
            return;
        }
    };
    report.scanned_pg = true;
    let mut in_autoload = false;
    for (idx, raw) in content.lines().enumerate() {
        let line_no = (idx + 1) as u32;
        let line = raw.trim();
        if line.starts_with('[') {
            in_autoload = line == "[autoload]";
            continue;
        }
        if in_autoload {
            if let Some(p) = extract_quoted(line, "=\"*") .or_else(|| extract_quoted(line, "=\"")) {
                // autoload 必须指向项目内 gameforge 官方脚本
                if !p.starts_with(ALLOWED_AUTOLOAD_PREFIX) {
                    report.finding(
                        "project.godot",
                        line_no,
                        "autoload_untrusted",
                        "autoload 指向不受信任的脚本(仅允许 addons/gameforge/)",
                        raw,
                    );
                }
                if p.contains("..") {
                    report.finding(
                        "project.godot",
                        line_no,
                        "autoload_escape",
                        "autoload 路径含 ..",
                        raw,
                    );
                }
            }
        }
    }
}

// 从 "key=\"value\"" 片段提取 value(容忍 key 前有其它属性)
fn extract_quoted(s: &str, key: &str) -> Option<String> {
    let start = s.find(key)? + key.len();
    let rest = &s[start..];
    if !rest.starts_with('"') {
        return None;
    }
    let end = rest[1..].find('"')? + 1;
    Some(rest[1..end].to_string())
}

// ── 目录遍历 ───────────────────────────────────────────────────

// 生成项目禁止携带的原生库/二进制载荷(生成游戏只需 .gd/.tscn/资源文件)
const NATIVE_EXTS: &[&str] = &["gdextension", "dll", "so", "dylib", "pyd"];

fn collect(dir: &Path, exts: &[&str], out: &mut BTreeSet<PathBuf>) {
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_dir() {
                // .godot 导入缓存不扫
                if p.file_name().map(|n| n == ".godot") != Some(true) {
                    collect(&p, exts, out);
                }
            } else if let Some(ext) = p.extension().and_then(|e| e.to_str()) {
                if exts.contains(&ext) {
                    out.insert(p);
                }
            }
        }
    }
}

fn rel_res(project: &Path, p: &Path) -> String {
    "res://".to_string()
        + &p.strip_prefix(project)
            .unwrap_or(p)
            .to_string_lossy()
            .replace('\\', "/")
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 || args[1] != "scan" {
        eprintln!("用法: gd-guard scan <project_path>");
        process::exit(2);
    }
    let project = PathBuf::from(&args[2]);
    if !project.is_dir() {
        eprintln!("项目目录不存在: {}", project.display());
        process::exit(2);
    }

    let mut report = Report::default();

    // project.godot
    let pg = project.join("project.godot");
    if pg.is_file() {
        scan_project_godot(&mut report, &pg);
    } else {
        report.finding("project.godot", 0, "missing", "缺少 project.godot", "");
    }

    // 收集 .gd 与 .tscn
    let mut files = BTreeSet::new();
    collect(&project, &["gd"], &mut files);
    let mut scenes = BTreeSet::new();
    collect(&project, &["tscn"], &mut scenes);

    // .gd: 信任前缀之外的都扫
    for f in &files {
        let rel = rel_res(&project, f);
        if is_trusted(&rel) {
            continue;
        }
        scan_gd_file(&mut report, f, &rel);
    }
    // .tscn 全扫
    for f in &scenes {
        let rel = rel_res(&project, f);
        scan_tscn(&mut report, f, &rel);
    }
    // 原生库/二进制载荷: 生成项目出现即拦(无需扫描内容)
    let mut natives = BTreeSet::new();
    collect(&project, NATIVE_EXTS, &mut natives);
    for f in &natives {
        report.finding(
            &rel_res(&project, f),
            0,
            "native_binary",
            "生成项目禁止携带原生库/二进制载荷",
            "",
        );
    }

    let verdict = if report.verdict_block { "block" } else { "allow" };
    let code = if report.verdict_block { 1 } else { 0 };
    println!(
        "{{\"verdict\":\"{}\",\"scanned\":{{\"gd\":{},\"tscn\":{},\"project_godot\":{}}},\"findings\":[{}]}}",
        verdict,
        report.scanned_gd,
        report.scanned_tscn,
        report.scanned_pg as u32,
        report.findings.join(",")
    );
    process::exit(code);
}

// ── 单元测试 ────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_escape_handles_quotes_and_newlines() {
        assert_eq!(json_escape("a\"b\\c\nd"), "a\\\"b\\\\c\\nd");
    }

    #[test]
    fn extract_quoted_works_mid_line() {
        let s = "[ext_resource type=\"Script\" path=\"res://a.gd\" id=\"1\"]";
        assert_eq!(extract_quoted(s, "path=").as_deref(), Some("res://a.gd"));
        assert_eq!(extract_quoted(s, "missing="), None);
    }

    #[test]
    fn trusted_file_matching() {
        assert!(is_trusted("res://addons/gameforge/runtime/player.gd"));
        assert!(!is_trusted("res://scripts/evil.gd"));
        // 精确文件匹配: 信任目录内的新增文件(投毒)不被信任
        assert!(!is_trusted("res://addons/gameforge/runtime/evil.gd"));
        assert!(!is_trusted("res://addons/gameforge/runtime_malicious/evil.gd"));
    }

    #[test]
    fn scan_detects_dangerous_apis() {
        let mut r = Report::default();
        let src = "extends Node\nfunc _ready():\n\tOS.execute(\"cmd\", [])\n\tpass # FileAccess 在注释里不算\n";
        scan_gd_source(&mut r, "test.gd", src);
        assert!(r.verdict_block);
        assert_eq!(r.findings.len(), 1);
    }

    #[test]
    fn scan_clean_source_allows() {
        let mut r = Report::default();
        let src = "extends CharacterBody2D\nfunc _physics_process(d):\n\tvelocity.x = 10\n";
        scan_gd_source(&mut r, "ok.gd", src);
        assert!(!r.verdict_block);
        assert!(r.findings.is_empty());
    }

    #[test]
    fn comment_lines_do_not_trigger() {
        let mut r = Report::default();
        scan_gd_source(&mut r, "c.gd", "# OS.execute 示例注释\n");
        assert!(!r.verdict_block);
    }
}
