"""固定 SWE-bench Lite 开发集；调用方审批后写入评测缓存。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import httpx


DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
REVISION = "6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2"
PARQUET_PATH = "data/dev-00000-of-00001.parquet"
PARQUET_SHA256 = "8312f321838051849d2fa7c6ca071244733a3e90bb517ff6ca8186f722199b5c"
PARQUET_SIZE = 119551
DATASET_URL = f"https://huggingface.co/datasets/{DATASET_NAME}/resolve/{REVISION}/{PARQUET_PATH}"
DATASET_JSON_NAME = "swebench-lite-dev.json"
DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "evals" / DATASET_JSON_NAME
_INSTANCE_ID = re.compile(r"[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-[0-9]+\Z")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _validate_ids(instance_ids: object) -> list[str]:
    if not isinstance(instance_ids, list) or not instance_ids:
        raise ValueError("instance_ids 必须是非空列表")
    if any(not isinstance(value, str) or not _INSTANCE_ID.fullmatch(value) for value in instance_ids):
        raise ValueError("instance_ids 含有无效的题目 ID")
    if len(instance_ids) != len(set(instance_ids)):
        raise ValueError("instance_ids 不能重复")
    return instance_ids


def load_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> dict:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("评测清单必须是 JSON 对象")
    expected = {
        "dataset": DATASET_NAME,
        "revision": REVISION,
        "split": "dev",
        "parquet_path": PARQUET_PATH,
        "parquet_sha256": PARQUET_SHA256,
        "parquet_size": PARQUET_SIZE,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"评测清单 {key} 与固定官方数据源不一致")
    _validate_ids(manifest.get("instance_ids"))
    return manifest


def _read_parquet(path: Path) -> list[dict]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:
        raise RuntimeError("缺少评测依赖，请运行 uv sync --extra test --extra eval") from error
    return parquet.read_table(path).to_pylist()


def _verify_parquet(payload: bytes, manifest: dict) -> None:
    if (len(payload) != manifest["parquet_size"]
            or hashlib.sha256(payload).hexdigest() != manifest["parquet_sha256"]):
        raise ValueError("SWE-bench 数据校验失败：文件大小或 SHA-256 不符，请检查下载或缓存")


def prepare_dataset(cache_root: Path, manifest_path: Path = DEFAULT_MANIFEST) -> list[dict]:
    """完整评分字段只写 cache_root，不能将该目录暴露给受测 Agent。"""
    manifest = load_manifest(manifest_path)
    cache_root = Path(cache_root)
    parquet_path = cache_root / Path(PARQUET_PATH).name
    if parquet_path.exists():
        payload = parquet_path.read_bytes()
        _verify_parquet(payload, manifest)
    else:
        response = httpx.get(DATASET_URL, follow_redirects=True, timeout=30)
        response.raise_for_status()
        payload = response.content
        _verify_parquet(payload, manifest)
        cache_root.mkdir(parents=True, exist_ok=True)
        temporary = parquet_path.with_suffix(".parquet.tmp")
        temporary.write_bytes(payload)
        temporary.replace(parquet_path)

    rows = _read_parquet(parquet_path)
    by_id = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("instance_id"), str):
            raise ValueError("官方数据包含无效任务")
        if row["instance_id"] in by_id:
            raise ValueError("官方数据包含重复任务")
        by_id[row["instance_id"]] = row
    missing = set(manifest["instance_ids"]) - by_id.keys()
    if missing:
        raise ValueError(f"官方数据缺少清单任务：{', '.join(sorted(missing))}")
    selected = [by_id[instance_id] for instance_id in manifest["instance_ids"]]
    dataset_path = cache_root / DATASET_JSON_NAME
    temporary = dataset_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(dataset_path)
    return selected


def image_name(instance_id: str) -> str:
    _validate_ids([instance_id])
    normalized = instance_id.lower().replace("__", "_1776_")
    return f"swebench/sweb.eval.x86_64.{normalized}:latest"


def official_grade_args(
    dataset_json: Path,
    predictions: Path,
    run_id: str,
    report_dir: Path,
    instance_ids: list[str],
) -> list[str]:
    """官方 swebench==4.1.0 契约；调用方须将 cwd 设为 report_dir。"""
    _validate_ids(instance_ids)
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id 仅支持字母、数字、点、下划线和连字符")
    return [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", str(Path(dataset_json).resolve()),
        "--split", "dev",
        "--predictions_path", str(Path(predictions).resolve()),
        "--instance_ids", *instance_ids,
        "--max_workers", "1",
        "--namespace", "swebench",
        "--cache_level", "instance",
        "--clean", "false",
        "--run_id", run_id,
        "--report_dir", str(Path(report_dir).resolve()),
    ]
