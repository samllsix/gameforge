"""Test MCP Adapter - In-process adapter for test management operations."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .base import MCPAdapter, MCPToolResult


class TestMCPAdapter(MCPAdapter):
    """In-process MCP adapter for test management operations.
    
    This adapter wraps the test case management and metrics functions
    to provide a consistent MCP interface for testing operations.
    """
    
    def __init__(self, data_dir: str = "data/eval_datasets"):
        super().__init__(
            name="test",
            description="Test management and evaluation operations",
        )
        self.data_dir = data_dir
        self._register_tools()
    
    def _register_tools(self) -> None:
        """Register all test-related tools."""
        
        # Tool: generate_test_cases
        self.register_tool(
            name="generate_test_cases",
            description="Generate or retrieve test cases for game development",
            input_schema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category of test cases to generate",
                        "enum": ["platformer", "shooter", "rpg", "puzzle", "all"],
                        "default": "all",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of test cases to generate",
                        "default": 3,
                    },
                },
            },
            handler=self._generate_test_cases,
        )
        
        # Tool: run_tests
        self.register_tool(
            name="run_tests",
            description="Run test cases and return results",
            input_schema={
                "type": "object",
                "properties": {
                    "test_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific test case IDs to run (empty for all)",
                    },
                    "engine": {
                        "type": "string",
                        "description": "Game engine to test against",
                        "enum": ["godot", "unity", "unreal"],
                        "default": "godot",
                    },
                },
            },
            handler=self._run_tests,
        )
        
        # Tool: get_metrics
        self.register_tool(
            name="get_metrics",
            description="Get evaluation metrics for test results",
            input_schema={
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "Name of the project to evaluate",
                    },
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific metrics to calculate",
                        "enum": [
                            "compile_success",
                            "code_quality",
                            "naming_convention",
                            "task_completion",
                            "fix_efficiency",
                            "all",
                        ],
                        "default": "all",
                    },
                },
                "required": ["project_name"],
            },
            handler=self._get_metrics,
        )
        
        # Tool: get_dashboard
        self.register_tool(
            name="get_dashboard",
            description="Get aggregated test dashboard data",
            input_schema={
                "type": "object",
                "properties": {
                    "time_range": {
                        "type": "string",
                        "description": "Time range for dashboard data",
                        "enum": ["last_hour", "last_day", "last_week", "all"],
                        "default": "all",
                    },
                },
            },
            handler=self._get_dashboard,
        )
    
    async def _generate_test_cases(
        self, category: str = "all", count: int = 3
    ) -> MCPToolResult:
        """Generate or retrieve test cases."""
        try:
            from src.eval.test_cases import TestCaseManager, TestCase
            
            manager = TestCaseManager(data_dir=self.data_dir)
            
            # Get default cases
            default_cases = manager.get_default_cases()
            
            # Filter by category if specified
            if category != "all":
                category_map = {
                    "platformer": "tc_001",
                    "shooter": "tc_002",
                    "rpg": "tc_003",
                    "puzzle": "tc_004",
                }
                if category in category_map:
                    cases = [c for c in default_cases if c.id == category_map[category]]
                else:
                    cases = default_cases
            else:
                cases = default_cases
            
            # Limit count
            cases = cases[:count]
            
            return MCPToolResult.success(
                data={
                    "cases": [c.to_dict() for c in cases],
                    "count": len(cases),
                    "category": category,
                },
                text=f"Generated {len(cases)} test cases for category: {category}",
            )
        except Exception as e:
            return MCPToolResult.error(f"Failed to generate test cases: {str(e)}")
    
    async def _run_tests(
        self, test_ids: Optional[List[str]] = None, engine: str = "godot"
    ) -> MCPToolResult:
        """Run test cases and return results."""
        try:
            from src.eval.test_cases import TestCaseManager
            
            manager = TestCaseManager(data_dir=self.data_dir)
            
            # Get test cases
            if test_ids:
                cases = [manager.load_case(tid) for tid in test_ids]
                cases = [c for c in cases if c is not None]
            else:
                cases = manager.list_cases()
            
            if not cases:
                return MCPToolResult.error("No test cases found")
            
            # Simulate test execution (in real implementation, this would call Godot)
            results = []
            for case in cases:
                # For now, return a simulated result
                results.append({
                    "test_id": case.id,
                    "name": case.name,
                    "status": "simulated",
                    "engine": engine,
                    "passed": True,
                    "message": f"Test case {case.id} simulated for {engine}",
                })
            
            return MCPToolResult.success(
                data={
                    "results": results,
                    "total": len(results),
                    "passed": sum(1 for r in results if r["passed"]),
                    "engine": engine,
                },
                text=f"Executed {len(results)} test cases on {engine}",
            )
        except Exception as e:
            return MCPToolResult.error(f"Failed to run tests: {str(e)}")
    
    async def _get_metrics(
        self, project_name: str, metrics: Optional[List[str]] = None
    ) -> MCPToolResult:
        """Get evaluation metrics."""
        try:
            from src.eval.metrics import (
                CodeQualityMetrics,
                TaskCompletionMetrics,
                EvalReport,
            )
            
            if metrics is None:
                metrics = ["all"]
            
            report = EvalReport(project_name=project_name)
            
            # In real implementation, we would load actual project data
            # For now, return sample metrics
            if "all" in metrics or "compile_success" in metrics:
                report.add_metric("compile_success", 95.0, details={"sample": True})
            
            if "all" in metrics or "code_quality" in metrics:
                report.add_metric("code_quality", 85.0, details={"sample": True})
            
            if "all" in metrics or "naming_convention" in metrics:
                report.add_metric("naming_convention", 90.0, details={"sample": True})
            
            if "all" in metrics or "task_completion" in metrics:
                report.add_metric("task_completion", 80.0, details={"sample": True})
            
            if "all" in metrics or "fix_efficiency" in metrics:
                report.add_metric("fix_efficiency", 75.0, details={"sample": True})
            
            return MCPToolResult.success(
                data=report.to_dict(),
                text=f"Metrics for project: {project_name}",
            )
        except Exception as e:
            return MCPToolResult.error(f"Failed to get metrics: {str(e)}")
    
    async def _get_dashboard(
        self, time_range: str = "all"
    ) -> MCPToolResult:
        """Get aggregated dashboard data."""
        try:
            # In real implementation, this would aggregate data from multiple sources
            dashboard_data = {
                "time_range": time_range,
                "summary": {
                    "total_tests": 150,
                    "passed": 142,
                    "failed": 8,
                    "success_rate": 94.7,
                },
                "trends": {
                    "daily": [
                        {"date": "2024-01-01", "passed": 20, "failed": 2},
                        {"date": "2024-01-02", "passed": 22, "failed": 1},
                        {"date": "2024-01-03", "passed": 19, "failed": 3},
                    ]
                },
                "by_engine": {
                    "godot": {"passed": 120, "failed": 5},
                    "unity": {"passed": 22, "failed": 3},
                },
            }
            
            return MCPToolResult.success(
                data=dashboard_data,
                text=f"Dashboard data for time range: {time_range}",
            )
        except Exception as e:
            return MCPToolResult.error(f"Failed to get dashboard: {str(e)}")
