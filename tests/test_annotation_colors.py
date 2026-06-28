from pathlib import Path
import json
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def run_color_script(script: str) -> list[int]:
    """annotation_colors.js の割り当て結果を Node.js で取得する"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def extract_color_array(name: str) -> list[str]:
    """app.js から COLORS 配列を取得する"""
    app_js = (ROOT / "static" / "app.js").read_text()
    match = re.search(rf"{name}:\s*\[(.*?)\]", app_js, re.DOTALL)
    assert match, f"{name} not found"
    return re.findall(r"'([^']+)'", match.group(1))


def test_confirmed_palette_alternates_warm_and_cool_colors() -> None:
    confirmed = extract_color_array("confirmed")
    confirmed_stroke = extract_color_array("confirmedStroke")

    assert confirmed == [
        "rgba(197, 48, 48, 0.2)",
        "rgba(49, 130, 206, 0.2)",
        "rgba(214, 158, 46, 0.2)",
        "rgba(0, 139, 139, 0.2)",
        "rgba(183, 63, 151, 0.2)",
        "rgba(74, 124, 89, 0.2)",
    ]
    assert confirmed_stroke == [
        color.replace("0.2", "0.8") for color in confirmed
    ]


def test_nearby_annotations_do_not_reuse_color_until_palette_is_exhausted() -> None:
    script = """
const { assignColorIndices } = require('./static/annotation_colors.js');
const items = Array.from({ length: 6 }, (_, index) => ({
  box: { minX: index * 12, minY: 0, maxX: index * 12 + 10, maxY: 40 },
}));
console.log(JSON.stringify(assignColorIndices(items, 6, { nearGapRatio: 1 })));
"""
    assigned = run_color_script(script)

    assert len(set(assigned)) == 6
    assert all(left != right for left, right in zip(assigned, assigned[1:]))


def test_spatially_near_annotation_does_not_reuse_same_color_after_wraparound() -> None:
    script = """
const { assignColorIndices } = require('./static/annotation_colors.js');
const items = [
  { box: { minX: 0, minY: 0, maxX: 10, maxY: 40 } },
  { box: { minX: 80, minY: 0, maxX: 90, maxY: 40 } },
  { box: { minX: 160, minY: 0, maxX: 170, maxY: 40 } },
  { box: { minX: 240, minY: 0, maxX: 250, maxY: 40 } },
  { box: { minX: 320, minY: 0, maxX: 330, maxY: 40 } },
  { box: { minX: 400, minY: 0, maxX: 410, maxY: 40 } },
  { box: { minX: 480, minY: 0, maxX: 490, maxY: 40 } },
  { box: { minX: 560, minY: 0, maxX: 570, maxY: 40 } },
  { box: { minX: 6, minY: 0, maxX: 16, maxY: 40 } },
];
console.log(JSON.stringify(assignColorIndices(items, 8, { nearGapRatio: 1 })));
"""
    assigned = run_color_script(script)

    assert assigned[8] != assigned[0]


def test_nearby_annotations_avoid_same_color_family() -> None:
    script = """
const { assignColorIndices } = require('./static/annotation_colors.js');
const items = [
  { box: { minX: 0, minY: 0, maxX: 10, maxY: 40 } },
  { box: { minX: 12, minY: 0, maxX: 22, maxY: 40 } },
];
const familyIds = [0, 0, 1, 2];
console.log(JSON.stringify(assignColorIndices(items, 4, { nearGapRatio: 1, familyIds })));
"""
    assigned = run_color_script(script)

    assert assigned[0] in {0, 1}
    assert assigned[1] not in {0, 1}


def test_recent_three_annotations_do_not_repeat_colors() -> None:
    script = """
const { assignColorIndices } = require('./static/annotation_colors.js');
const items = Array.from({ length: 9 }, (_, index) => ({
  box: { minX: index * 100, minY: 0, maxX: index * 100 + 10, maxY: 40 },
}));
console.log(JSON.stringify(assignColorIndices(items, 6, { recentWindow: 2 })));
"""
    assigned = run_color_script(script)

    for index in range(2, len(assigned)):
        assert assigned[index] != assigned[index - 1]
        assert assigned[index] != assigned[index - 2]
