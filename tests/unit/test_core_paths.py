"""src/core/paths.py 单一事实源的契约测试。

- 产物按 (project_id, run_id, task_kind, task_id) 编址
- 路径穿越被拒绝
- 其余模块不得自行拼 projects 根（grep 约束在本测试中落地）
"""
import json
import shutil
from pathlib import Path

import pytest

from src.core import paths


@pytest.fixture()
def isolated_roots(tmp_path, monkeypatch):
    """把 PROJECTS_ROOT/DATA_ROOT 指到临时目录，避免污染仓库。

    仓库 pytest 配置使用确定性 basetemp（.tmp/pytest/<测试名>），
    tmp_path 跨运行复用，因此先清空再挂载。
    """
    projects = tmp_path / "projects"
    data = tmp_path / "data"
    shutil.rmtree(projects, ignore_errors=True)
    shutil.rmtree(data, ignore_errors=True)
    monkeypatch.setattr(paths, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(paths, "DATA_ROOT", data)
    return projects, data


class TestValidateId:
    def test_accepts_normal_ids(self):
        assert paths.validate_id("gf_mtlcefkx0hp") == "gf_mtlcefkx0hp"
        assert paths.validate_id("GameForge_Project") == "GameForge_Project"
        assert paths.validate_id("20260906_120000") == "20260906_120000"

    @pytest.mark.parametrize("bad", ["", "..", "../evil", "a/b", "a\\b", ".hidden", None, "a b"])
    def test_rejects_traversal_and_invalid(self, bad):
        with pytest.raises(ValueError):
            paths.validate_id(bad)


class TestProjectAddressing:
    def test_project_dir_under_root(self, isolated_roots):
        projects, _ = isolated_roots
        assert paths.project_dir("p1") == projects / "p1"

    def test_ensure_project_dir_creates(self, isolated_roots):
        projects, _ = isolated_roots
        out = paths.ensure_project_dir("p1")
        assert out.is_dir()
        assert out == projects / "p1"

    def test_resolve_project_rejects_traversal(self, isolated_roots):
        with pytest.raises(ValueError):
            paths.resolve_project("../outside")

    def test_resolve_project_missing(self, isolated_roots):
        with pytest.raises(FileNotFoundError):
            paths.resolve_project("no_such_project")

    def test_resolve_project_ok(self, isolated_roots):
        projects, _ = isolated_roots
        (projects / "p1").mkdir(parents=True)
        assert paths.resolve_project("p1") == (projects / "p1").resolve()


class TestRunAddressing:
    def test_playtest_paths(self, isolated_roots):
        base = paths.run_dir("p1", "r1")
        assert base.name == "r1"
        assert base.parent.name == paths.GAMEFORGE_DIR_NAME
        assert paths.playtest_report_path("p1", "r1").name == "report.json"
        assert paths.playtest_actions_path("p1", "r1").name == "actions.json"
        assert paths.playtest_frames_dir("p1", "r1").name == "frames"

    def test_eval_and_review_paths(self, isolated_roots):
        assert paths.eval_summary_path("p1", "r1").name == "summary.json"
        assert paths.eval_summary_path("p1", "r1").parent.name == "eval"
        assert paths.visual_review_path("p1", "r1").name == "visual_review.json"

    def test_latest_run_id(self, isolated_roots):
        projects, _ = isolated_roots
        assert paths.latest_run_id("p1") is None
        root = paths.pipeline_root("p1")
        (root / "20260101_000000").mkdir(parents=True)
        (root / paths.DEFAULT_RUN_ID).mkdir(parents=True)
        (root / "20260202_000000").mkdir(parents=True)
        assert paths.latest_run_id("p1") == "20260202_000000"

    def test_new_run_id_format(self):
        rid = paths.new_run_id()
        assert len(rid) == 15 and rid[8] == "_"


class TestJsonHelpers:
    def test_write_read_roundtrip(self, tmp_path):
        target = tmp_path / "a" / "b.json"
        paths.write_json(target, {"k": "值", "n": 1})
        assert paths.read_json(target) == {"k": "值", "n": 1}

    def test_read_json_tolerates_missing_and_broken(self, tmp_path):
        assert paths.read_json(tmp_path / "missing.json") is None
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert paths.read_json(broken) is None

    def test_write_run_meta(self, isolated_roots):
        meta_path = paths.write_run_meta("p1", "r1", meta={"requirement": "平台跳跃"})
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        assert payload["project_id"] == "p1"
        assert payload["run_id"] == "r1"
        assert payload["requirement"] == "平台跳跃"


class TestGrepConstraint:
    def test_no_other_module_builds_projects_root(self):
        """项目约定：paths.py 之外不得出现 projects 根字面量。"""
        src_root = Path(__file__).resolve().parents[2] / "src"
        offenders = []
        for py in src_root.rglob("*.py"):
            if py.name == "paths.py":
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                # 允许注释、报错文案里出现；只拦真实拼路径的写法
                if ('"projects"' in line or "'projects'" in line) and (
                    "join(" in line or "/ " in line or " /" in line or "Path(" in line
                ):
                    offenders.append(f"{py.relative_to(src_root)}:{i}: {line.strip()}")
        assert offenders == []
