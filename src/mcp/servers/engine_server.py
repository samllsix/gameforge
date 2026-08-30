"""GameForge - Engine MCP Server

MCP server for Godot engine operations (scene building, script compilation,
runtime testing, screenshots, and .tscn validation).

This server wraps the existing engine modules to provide MCP protocol interface.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import os
from typing import Any, Dict, List, Optional

# Add repo root to path for imports
def _find_repo_root() -> str:
    cwd = os.getcwd()
    if os.path.isdir(os.path.join(cwd, "src", "engine")):
        return cwd
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        if os.path.isdir(os.path.join(cur, "src", "engine")):
            return cur
        cur = os.path.dirname(cur)
    return cwd

_repo_root = _find_repo_root()
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from src.engine.godot import GodotEditor, GodotCompiler


class EngineMCPServer:
    """Engine MCP server for Godot operations."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.editor = GodotEditor(self.config)
        self.compiler = GodotCompiler(self.config)
    
    def tool_schemas(self) -> List[Dict[str, Any]]:
        """Return MCP tool schemas."""
        return [
            {
                "name": "compile_headless",
                "description": "Compile/check GDScript files using Godot headless mode",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "script_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of res:// script paths to check",
                        },
                    },
                    "required": ["script_paths"],
                },
            },
            {
                "name": "build_scene",
                "description": "Build a Godot scene from description and push to editor",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "scene_name": {
                            "type": "string",
                            "description": "Name of the scene",
                        },
                        "scene_data": {
                            "type": "object",
                            "description": "Scene description data",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Output path for .tscn file",
                            "default": "res://scenes/",
                        },
                    },
                    "required": ["scene_name", "scene_data"],
                },
            },
            {
                "name": "run_smoke_test",
                "description": "Run a quick smoke test of the Godot project",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds",
                            "default": 10,
                        },
                        "scene_path": {
                            "type": "string",
                            "description": "Scene to run (res:// path)",
                        },
                    },
                },
            },
            {
                "name": "capture_screenshot",
                "description": "Capture a screenshot from Godot headless renderer",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "scene_path": {
                            "type": "string",
                            "description": "Scene to render (res:// path)",
                            "default": "res://scenes/main.tscn",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Output path for PNG",
                        },
                        "width": {
                            "type": "integer",
                            "description": "Render width",
                            "default": 640,
                        },
                        "height": {
                            "type": "integer",
                            "description": "Render height",
                            "default": 360,
                        },
                    },
                },
            },
            {
                "name": "validate_tscn",
                "description": "Validate a .tscn scene file format",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tscn_content": {
                            "type": "string",
                            "description": "Content of .tscn file to validate",
                        },
                        "tscn_path": {
                            "type": "string",
                            "description": "Path to .tscn file (alternative to content)",
                        },
                    },
                },
            },
            {
                "name": "get_engine_info",
                "description": "Get Godot engine version and status information",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]
    
    async def handle_mcp_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle MCP protocol request."""
        method = request.get("method", "")
        
        if method == "tools/list":
            return {"tools": self.tool_schemas()}
        
        if method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            
            # Map tool names to methods
            tool_map = {
                "compile_headless": self._compile_headless,
                "build_scene": self._build_scene,
                "run_smoke_test": self._run_smoke_test,
                "capture_screenshot": self._capture_screenshot,
                "validate_tscn": self._validate_tscn,
                "get_engine_info": self._get_engine_info,
            }
            
            if tool_name not in tool_map:
                return {"error": f"Unknown tool: {tool_name}"}
            
            try:
                result = await tool_map[tool_name](**args)
                return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}
            except Exception as e:
                return {"error": f"Tool execution failed: {str(e)}"}
        
        return {"error": f"Unknown method: {method}"}
    
    async def _compile_headless(self, script_paths: List[str]) -> Dict[str, Any]:
        """Compile/check GDScript files."""
        # check_scripts 内部是阻塞的 subprocess.run，放到线程池避免卡死事件循环
        result = await asyncio.to_thread(self.editor.check_scripts, script_paths)
        return result.to_dict()
    
    async def _build_scene(
        self,
        scene_name: str,
        scene_data: Dict[str, Any],
        output_path: str = "res://scenes/",
    ) -> Dict[str, Any]:
        """Build a Godot scene from description."""
        # This would use scene_builder.py in a full implementation
        # For now, return a placeholder
        return {
            "status": "success",
            "scene_name": scene_name,
            "output_path": output_path,
            "message": "Scene build placeholder - implement with scene_builder.py",
        }
    
    async def _run_smoke_test(
        self,
        timeout: int = 10,
        scene_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a quick smoke test."""
        # This would use runtime_smoke.py in a full implementation
        valid, msg = self.editor.validate()
        if not valid:
            return {"status": "error", "message": msg}
        
        return {
            "status": "success",
            "message": "Smoke test placeholder - implement with runtime_smoke.py",
            "timeout": timeout,
            "scene_path": scene_path,
        }
    
    async def _capture_screenshot(
        self,
        scene_path: str = "res://scenes/main.tscn",
        output_path: Optional[str] = None,
        width: int = 640,
        height: int = 360,
    ) -> Dict[str, Any]:
        """Capture a screenshot."""
        if not self.editor.project_path:
            return {"status": "error", "message": "Project path not configured"}

        # render_screenshot_frame 内部是阻塞的 subprocess.run，放到线程池避免卡死事件循环
        result = await asyncio.to_thread(
            self.editor.render_screenshot_frame,
            project_path=self.editor.project_path,
            scene_path=scene_path,
            output_path=output_path,
            width=width,
            height=height,
        )
        return result
    
    async def _validate_tscn(
        self,
        tscn_content: Optional[str] = None,
        tscn_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate a .tscn scene file."""
        if tscn_path:
            try:
                with open(tscn_path, "r", encoding="utf-8") as f:
                    tscn_content = f.read()
            except Exception as e:
                return {"valid": False, "error": f"Failed to read file: {str(e)}"}
        
        if not tscn_content:
            return {"valid": False, "error": "No content provided"}
        
        # Basic validation
        errors = []
        warnings = []
        
        lines = tscn_content.split("\n")
        if not any(line.startswith("[gd_scene") for line in lines):
            errors.append("Missing [gd_scene] header")
        
        if not any(line.startswith("[ext_resource") for line in lines if line.strip()):
            warnings.append("No external resources defined")
        
        if not any(line.startswith("[node") for line in lines):
            errors.append("No nodes defined")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "line_count": len(lines),
        }
    
    async def _get_engine_info(self) -> Dict[str, Any]:
        """Get engine version and status."""
        valid, msg = self.editor.validate()
        # detect_version 内部是阻塞的 subprocess.run，放到线程池避免卡死事件循环
        version, version_str = await asyncio.to_thread(self.editor.detect_version)
        
        return {
            "configured": valid,
            "message": msg,
            "version": version,
            "version_string": version_str,
            "editor_path": self.editor.editor_path,
            "project_path": self.editor.project_path,
        }


def _main():
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Engine MCP Server")
    parser.add_argument("--stdio", action="store_true",
                        help="Run in stdio MCP mode")
    parser.add_argument("--config", type=str, help="Path to config.yaml")
    
    args = parser.parse_args()
    
    # Load config if provided
    config = {}
    if args.config:
        import yaml
        with open(args.config, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    
    server = EngineMCPServer(config)
    
    if args.stdio:
        _run_stdio(server)
        return
    
    # Print tool schemas and exit
    print(json.dumps(server.tool_schemas(), ensure_ascii=False, indent=2))


def _run_stdio(server: EngineMCPServer):
    """Run in stdio mode."""
    # Windows 下管道默认按本地编码（如 GBK）解码，而 MCP 客户端按 UTF-8 读写；
    # 统一重配为 UTF-8，避免中文请求/响应乱码甚至 UnicodeDecodeError 中断循环。
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("[engine-mcp] ready (stdio mode)", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"error": "invalid json"}))
            continue
        
        resp = asyncio.run(server.handle_mcp_request(req))
        print(json.dumps(resp, ensure_ascii=False))
        sys.stdout.flush()


if __name__ == "__main__":
    _main()
