"""GameForge - Asset MCP Server

MCP server for asset management operations (register, query, deduplicate,
convert format, import to Godot).

This server provides shared access semantics with write locks for concurrent
agent operations.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional
from pathlib import Path

# Add repo root to path
def _find_repo_root() -> str:
    cwd = os.getcwd()
    if os.path.isdir(os.path.join(cwd, "src", "mcp")):
        return cwd
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        if os.path.isdir(os.path.join(cur, "src", "mcp")):
            return cur
        cur = os.path.dirname(cur)
    return cwd

_repo_root = _find_repo_root()
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


class AssetMCPServer:
    """Asset MCP server with shared access semantics."""
    
    def __init__(
        self,
        output_dir: str = "assets/generated",
        db_path: str = "data/asset_manifest.db",
        audit_log: str = "data/asset_audit.log",
    ):
        self.output_dir = output_dir
        self.db_path = db_path
        self.audit_log = audit_log
        
        # Ensure directories exist
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(os.path.dirname(audit_log), exist_ok=True)
        
        # Initialize SQLite database
        self._init_db()
        
        # Write lock for concurrent access
        self._write_lock = asyncio.Lock()
        self._active_writers = 0
    
    def _init_db(self):
        """Initialize the asset manifest database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                type TEXT,
                tags TEXT,
                hash TEXT,
                size INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS asset_metadata (
                asset_id TEXT,
                key TEXT,
                value TEXT,
                FOREIGN KEY (asset_id) REFERENCES assets(id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def _log_audit(self, trace_id: str, action: str, details: Dict[str, Any]):
        """Log audit event."""
        entry = {
            "trace_id": trace_id,
            "timestamp": time.time(),
            "action": action,
            "details": details,
        }
        with open(self.audit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    def _calculate_hash(self, file_path: str) -> str:
        """Calculate file hash."""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception:
            return ""
    
    def tool_schemas(self) -> List[Dict[str, Any]]:
        """Return MCP tool schemas."""
        return [
            {
                "name": "register_asset",
                "description": "Register a new asset in the manifest database",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Asset name",
                        },
                        "path": {
                            "type": "string",
                            "description": "Relative path to asset file",
                        },
                        "type": {
                            "type": "string",
                            "description": "Asset type (image, scene, script, etc.)",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tags for the asset",
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Additional metadata",
                        },
                    },
                    "required": ["name", "path"],
                },
            },
            {
                "name": "query_assets",
                "description": "Query assets from the manifest database",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by tags",
                        },
                        "type": {
                            "type": "string",
                            "description": "Filter by asset type",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results",
                            "default": 50,
                        },
                    },
                },
            },
            {
                "name": "deduplicate",
                "description": "Find and merge duplicate assets based on hash",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dry_run": {
                            "type": "boolean",
                            "description": "Only find duplicates without merging",
                            "default": True,
                        },
                    },
                },
            },
            {
                "name": "convert_format",
                "description": "Convert asset format (PNG <-> WebP, etc.)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "asset_id": {
                            "type": "string",
                            "description": "Asset ID to convert",
                        },
                        "target_format": {
                            "type": "string",
                            "description": "Target format (webp, png, etc.)",
                        },
                        "quality": {
                            "type": "integer",
                            "description": "Quality for lossy formats (1-100)",
                            "default": 90,
                        },
                    },
                    "required": ["asset_id", "target_format"],
                },
            },
            {
                "name": "import_to_godot",
                "description": "Import asset to Godot project",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "asset_id": {
                            "type": "string",
                            "description": "Asset ID to import",
                        },
                        "target_path": {
                            "type": "string",
                            "description": "Target path in Godot project (res://)",
                        },
                    },
                    "required": ["asset_id"],
                },
            },
            {
                "name": "get_asset_info",
                "description": "Get detailed information about an asset",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "asset_id": {
                            "type": "string",
                            "description": "Asset ID",
                        },
                    },
                    "required": ["asset_id"],
                },
            },
        ]
    
    async def handle_mcp_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle MCP protocol request."""
        method = request.get("method", "")
        trace_id = request.get("trace_id", str(time.time()))
        
        if method == "tools/list":
            return {"tools": self.tool_schemas()}
        
        if method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            
            # Map tool names to methods
            tool_map = {
                "register_asset": self._register_asset,
                "query_assets": self._query_assets,
                "deduplicate": self._deduplicate,
                "convert_format": self._convert_format,
                "import_to_godot": self._import_to_godot,
                "get_asset_info": self._get_asset_info,
            }
            
            if tool_name not in tool_map:
                return {"error": f"Unknown tool: {tool_name}"}
            
            try:
                result = await tool_map[tool_name](**args, trace_id=trace_id)
                return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}
            except Exception as e:
                return {"error": f"Tool execution failed: {str(e)}"}
        
        return {"error": f"Unknown method: {method}"}
    
    async def _register_asset(
        self,
        name: str,
        path: str,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: str = "",
    ) -> Dict[str, Any]:
        """Register a new asset."""
        async with self._write_lock:
            self._active_writers += 1
            try:
                # Calculate hash if file exists
                full_path = os.path.join(self.output_dir, path)
                # 防路径穿越：path 不得越出 output_dir（含绝对路径 / ".." / 跨盘符）
                out_root = os.path.realpath(self.output_dir)
                try:
                    inside = os.path.commonpath([out_root, os.path.realpath(full_path)]) == out_root
                except ValueError:
                    inside = False
                if not inside:
                    return {"status": "error", "message": f"path 越出 assets 输出目录，已拒绝: {path}"}
                file_hash = ""
                file_size = 0
                if os.path.exists(full_path):
                    file_hash = self._calculate_hash(full_path)
                    file_size = os.path.getsize(full_path)
                
                # Generate asset ID
                asset_id = hashlib.md5(f"{name}:{path}".encode()).hexdigest()[:12]
                
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()

                    # Check if asset already exists
                    cursor.execute("SELECT id FROM assets WHERE path = ?", (path,))
                    existing = cursor.fetchone()

                    if existing:
                        # Update existing asset
                        cursor.execute("""
                            UPDATE assets
                            SET name = ?, type = ?, tags = ?, hash = ?, size = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE path = ?
                        """, (name, type, json.dumps(tags or []), file_hash, file_size, path))
                        asset_id = existing[0]
                    else:
                        # Insert new asset
                        cursor.execute("""
                            INSERT INTO assets (id, name, path, type, tags, hash, size)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (asset_id, name, path, type, json.dumps(tags or []), file_hash, file_size))

                    # Add metadata
                    if metadata:
                        cursor.execute("DELETE FROM asset_metadata WHERE asset_id = ?", (asset_id,))
                        for key, value in metadata.items():
                            cursor.execute("""
                                INSERT INTO asset_metadata (asset_id, key, value)
                                VALUES (?, ?, ?)
                            """, (asset_id, key, json.dumps(value)))

                    conn.commit()
                finally:
                    # 异常路径也必须关闭连接，否则 Windows 下会一直持有 db 文件锁
                    conn.close()
                
                # Audit log
                self._log_audit(trace_id, "register_asset", {
                    "asset_id": asset_id,
                    "name": name,
                    "path": path,
                })
                
                return {
                    "status": "success",
                    "asset_id": asset_id,
                    "name": name,
                    "path": path,
                    "hash": file_hash,
                }
            finally:
                self._active_writers -= 1
    
    async def _query_assets(
        self,
        tags: Optional[List[str]] = None,
        type: Optional[str] = None,
        limit: int = 50,
        trace_id: str = "",
    ) -> Dict[str, Any]:
        """Query assets from the database."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            query = "SELECT * FROM assets WHERE 1=1"
            params = []

            if type:
                query += " AND type = ?"
                params.append(type)

            if tags:
                # Simple tag filtering (could be improved with proper JSON queries)
                for tag in tags:
                    query += " AND tags LIKE ?"
                    params.append(f"%{tag}%")

            query += " LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()

            assets = []
            for row in rows:
                asset = dict(zip(columns, row))
                # Parse tags JSON
                if asset.get("tags"):
                    try:
                        asset["tags"] = json.loads(asset["tags"])
                    except:
                        asset["tags"] = []
                assets.append(asset)
        finally:
            conn.close()
        
        self._log_audit(trace_id, "query_assets", {
            "filters": {"tags": tags, "type": type},
            "result_count": len(assets),
        })
        
        return {
            "assets": assets,
            "count": len(assets),
        }
    
    async def _deduplicate(
        self, dry_run: bool = True, trace_id: str = ""
    ) -> Dict[str, Any]:
        """Find and merge duplicate assets."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Find assets with same hash
            cursor.execute("""
                SELECT hash, GROUP_CONCAT(id) as ids, COUNT(*) as count
                FROM assets
                WHERE hash != '' AND hash IS NOT NULL
                GROUP BY hash
                HAVING count > 1
            """)

            duplicates = []
            for row in cursor.fetchall():
                hash_val, ids_str, count = row
                ids = ids_str.split(",")
                duplicates.append({
                    "hash": hash_val,
                    "ids": ids,
                    "count": count,
                })

            if dry_run:
                return {
                    "status": "dry_run",
                    "duplicates_found": len(duplicates),
                    "duplicates": duplicates,
                }

            # Merge duplicates (keep first, mark others as duplicates)
            merged_count = 0
            for dup in duplicates:
                keep_id = dup["ids"][0]
                remove_ids = dup["ids"][1:]

                for remove_id in remove_ids:
                    # Update metadata to reference kept asset
                    cursor.execute("""
                        UPDATE asset_metadata
                        SET value = ?
                        WHERE asset_id = ? AND key = 'duplicate_of'
                    """, (keep_id, remove_id))

                    # Optionally delete the duplicate asset record
                    # cursor.execute("DELETE FROM assets WHERE id = ?", (remove_id,))
                    merged_count += 1

            conn.commit()
        finally:
            conn.close()
        
        self._log_audit(trace_id, "deduplicate", {
            "duplicates_found": len(duplicates),
            "merged_count": merged_count,
        })
        
        return {
            "status": "success",
            "duplicates_found": len(duplicates),
            "merged_count": merged_count,
        }
    
    async def _convert_format(
        self,
        asset_id: str,
        target_format: str,
        quality: int = 90,
        trace_id: str = "",
    ) -> Dict[str, Any]:
        """Convert asset format."""
        # Get asset info
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
            row = cursor.fetchone()

            if not row:
                return {"status": "error", "message": f"Asset not found: {asset_id}"}

            columns = [description[0] for description in cursor.description]
            asset = dict(zip(columns, row))
        finally:
            conn.close()
        
        # Check if conversion is possible
        current_path = asset.get("path", "")
        if not current_path.endswith((".png", ".webp", ".jpg", ".jpeg")):
            return {"status": "error", "message": "Unsupported format for conversion"}
        
        # Generate new path
        base, ext = os.path.splitext(current_path)
        new_path = f"{base}.{target_format}"
        
        # For now, just register the new path (actual conversion would need PIL)
        result = await self._register_asset(
            name=f"{asset['name']} ({target_format})",
            path=new_path,
            type=asset.get("type"),
            tags=json.loads(asset.get("tags", "[]")) if isinstance(asset.get("tags"), str) else asset.get("tags", []),
            metadata={"converted_from": asset_id, "quality": quality},
            trace_id=trace_id,
        )
        
        return {
            "status": "success",
            "original_asset_id": asset_id,
            "new_asset_id": result.get("asset_id"),
            "target_format": target_format,
            "new_path": new_path,
        }
    
    async def _import_to_godot(
        self,
        asset_id: str,
        target_path: Optional[str] = None,
        trace_id: str = "",
    ) -> Dict[str, Any]:
        """Import asset to Godot project."""
        # Get asset info
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
            row = cursor.fetchone()

            if not row:
                return {"status": "error", "message": f"Asset not found: {asset_id}"}

            columns = [description[0] for description in cursor.description]
            asset = dict(zip(columns, row))
        finally:
            conn.close()
        
        # Determine target path
        if not target_path:
            target_path = f"res://assets/{os.path.basename(asset.get('path', ''))}"
        
        # Copy file to Godot project (would need project path from config)
        # For now, just log the import
        self._log_audit(trace_id, "import_to_godot", {
            "asset_id": asset_id,
            "source_path": asset.get("path"),
            "target_path": target_path,
        })
        
        return {
            "status": "success",
            "asset_id": asset_id,
            "source_path": asset.get("path"),
            "target_path": target_path,
            "message": "Asset import logged - implement with actual Godot project path",
        }
    
    async def _get_asset_info(
        self, asset_id: str, trace_id: str = ""
    ) -> Dict[str, Any]:
        """Get detailed asset information."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
            row = cursor.fetchone()

            if not row:
                return {"status": "error", "message": f"Asset not found: {asset_id}"}

            columns = [description[0] for description in cursor.description]
            asset = dict(zip(columns, row))

            # Get metadata
            cursor.execute("SELECT key, value FROM asset_metadata WHERE asset_id = ?", (asset_id,))
            metadata = {}
            for key, value in cursor.fetchall():
                try:
                    metadata[key] = json.loads(value)
                except:
                    metadata[key] = value
        finally:
            conn.close()
        
        asset["metadata"] = metadata
        if isinstance(asset.get("tags"), str):
            try:
                asset["tags"] = json.loads(asset["tags"])
            except:
                asset["tags"] = []
        
        return {"asset": asset}


def _main():
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Asset MCP Server")
    parser.add_argument("--stdio", action="store_true",
                        help="Run in stdio MCP mode")
    parser.add_argument("--output-dir", default="assets/generated",
                        help="Asset output directory")
    parser.add_argument("--db-path", default="data/asset_manifest.db",
                        help="SQLite database path")
    
    args = parser.parse_args()
    
    server = AssetMCPServer(
        output_dir=args.output_dir,
        db_path=args.db_path,
    )
    
    if args.stdio:
        _run_stdio(server)
        return
    
    # Print tool schemas and exit
    print(json.dumps(server.tool_schemas(), ensure_ascii=False, indent=2))


def _run_stdio(server: AssetMCPServer):
    """Run in stdio mode."""
    # Windows 下管道默认按本地编码（如 GBK）解码，而 MCP 客户端按 UTF-8 读写；
    # 统一重配为 UTF-8，避免中文请求/响应乱码甚至 UnicodeDecodeError 中断循环。
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("[asset-mcp] ready (stdio mode)", file=sys.stderr)
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
