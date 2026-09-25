"""GameForge - GDM 条目归一化（src.core.gdm）单元测试。

覆盖 audio_generator / ui_generator / game_designer 三处曾经各自复制
「dict-or-str 容错」的统一规则，防止行为漂移：
- dict 条目原样返回；
- str 条目整串转成名称（可选附带描述）；
- defaults 只补缺失键，不覆盖已有值；
- 列表归一化丢弃非 dict/str 条目。
"""

import pytest

from src.core.gdm import as_item, as_item_list

# ─── as_item ─────────────────────────────────────────────


def test_dict_passthrough_is_not_copied():
    raw = {"name": "jump", "description": "jump sound"}
    assert as_item(raw) is raw


@pytest.mark.parametrize("raw,expected", [
    ("jump", {"name": "jump"}),
    ("", {"name": ""}),
    ("42", {"name": "42"}),  # 数字也会被 str() 化（audio 旧行为）
])
def test_scalar_becomes_name_dict(raw, expected):
    assert as_item(raw) == expected


def test_description_key_adds_description():
    assert as_item("boom", description_key="description") == {
        "name": "boom",
        "description": "boom",
    }


def test_defaults_fill_missing_keys_only():
    item = as_item("jump", defaults={"description": "", "priority": "medium"})
    assert item == {"name": "jump", "description": "", "priority": "medium"}


def test_defaults_do_not_overwrite_explicit_values():
    # description_key 已写入 description，defaults 里的同键不得覆盖
    item = as_item("jump", description_key="description",
                   defaults={"description": "keep me"})
    assert item["description"] == "jump"


def test_custom_name_key():
    assert as_item("load_save", name_key="module_name") == {"module_name": "load_save"}


# ─── as_item_list ────────────────────────────────────────


def test_list_mixes_dict_and_string():
    out = as_item_list(["a", {"name": "b"}, "c"])
    assert out == [{"name": "a"}, {"name": "b"}, {"name": "c"}]


def test_list_drops_non_dict_str_items():
    assert as_item_list([{"name": "x"}, 7, None, 3.14]) == [{"name": "x"}]


def test_list_none_and_empty():
    assert as_item_list(None) == []
    assert as_item_list([]) == []


def test_list_passes_through_defaults():
    # defaults 只作用在 str 条目上；dict 条目原样返回（与旧实现一致）
    out = as_item_list(["jump", {"name": "run"}],
                       defaults={"role": "environment"})
    assert out == [
        {"name": "jump", "role": "environment"},
        {"name": "run"},
    ]


def test_list_preserves_input_order():
    out = as_item_list(["b", "a", "c"])
    assert [i["name"] for i in out] == ["b", "a", "c"]


# ─── game_designer 形态（回归锚点）────────────────────────


def test_module_style_defaults_match_game_designer():
    out = as_item_list(["combat"], name_key="module_name",
                       defaults={"responsibility": "", "output_files": []})
    assert out == [{"module_name": "combat", "responsibility": "", "output_files": []}]
