import hashlib
import json
import sys
from pathlib import Path

import pytest

from app import swebench_data as data


def test_default_manifest_pins_public_dev_source_and_ten_tasks():
    manifest = data.load_manifest()
    assert manifest["dataset"] == "princeton-nlp/SWE-bench_Lite"
    assert manifest["revision"] == "6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2"
    assert manifest["parquet_sha256"] == "8312f321838051849d2fa7c6ca071244733a3e90bb517ff6ca8186f722199b5c"
    assert manifest["parquet_size"] == 119551
    assert manifest["split"] == "dev"
    assert len(manifest["instance_ids"]) == 10
    assert len(set(manifest["instance_ids"])) == 10
    assert {"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"}.isdisjoint(manifest)


@pytest.fixture
def source(tmp_path, monkeypatch):
    payload = b"test parquet bytes"
    monkeypatch.setattr(data, "PARQUET_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(data, "PARQUET_SIZE", len(payload))
    manifest = {
        "dataset": data.DATASET_NAME,
        "revision": data.REVISION,
        "split": "dev",
        "parquet_path": data.PARQUET_PATH,
        "parquet_sha256": data.PARQUET_SHA256,
        "parquet_size": data.PARQUET_SIZE,
        "instance_ids": ["pvlib__pvlib-python-1072", "sqlfluff__sqlfluff-1625"],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    rows = [
        {"instance_id": key, "repo": key.split("__")[0] + "/repo", "base_commit": "a" * 40,
         "problem_statement": "fix issue", "patch": "gold", "test_patch": "tests",
         "version": "1", "FAIL_TO_PASS": "[]", "PASS_TO_PASS": "[]"}
        for key in reversed(manifest["instance_ids"])
    ]
    monkeypatch.setattr(data, "_read_parquet", lambda path: rows)
    return payload, manifest, path, rows


@pytest.mark.parametrize("field,value", [
    ("dataset", "other/dataset"), ("revision", "main"), ("split", "test"),
    ("parquet_path", "../secret"), ("parquet_sha256", "f" * 64),
    ("parquet_size", 1), ("instance_ids", []),
    ("instance_ids", ["../../escape"]),
    ("instance_ids", ["sqlfluff__sqlfluff-1625"] * 2),
])
def test_manifest_rejects_unpinned_or_invalid_source(source, field, value):
    _, manifest, path, _ = source
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        data.load_manifest(path)


def test_prepare_downloads_verified_source_selects_manifest_order_and_caches(source, tmp_path, monkeypatch):
    payload, manifest, path, _ = source
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return __import__("httpx").Response(200, content=payload, request=__import__("httpx").Request("GET", url))

    monkeypatch.setattr(data.httpx, "get", get)
    cache = tmp_path / ".vibe" / "benchmarks" / "cache"
    rows = data.prepare_dataset(cache, path)
    assert [row["instance_id"] for row in rows] == manifest["instance_ids"]
    assert rows[0]["patch"] == "gold"
    assert calls == [(data.DATASET_URL, {"follow_redirects": True, "timeout": 30})]
    assert json.loads((cache / data.DATASET_JSON_NAME).read_text()) == rows
    assert (cache / Path(data.PARQUET_PATH).name).read_bytes() == payload
    assert data.prepare_dataset(cache, path) == rows
    assert len(calls) == 1


def test_prepare_rejects_changed_download_before_cache_or_read(source, tmp_path, monkeypatch):
    _, _, path, _ = source
    httpx = __import__("httpx")
    monkeypatch.setattr(data.httpx, "get", lambda url, **kwargs: httpx.Response(
        200, content=b"wrong", request=httpx.Request("GET", url)))
    monkeypatch.setattr(data, "_read_parquet", lambda path: pytest.fail("must verify before parsing"))
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="校验"):
        data.prepare_dataset(cache, path)
    assert not (cache / data.DATASET_JSON_NAME).exists()
    assert not (cache / Path(data.PARQUET_PATH).name).exists()


def test_prepare_rejects_tampered_cached_parquet(source, tmp_path, monkeypatch):
    _, _, path, _ = source
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / Path(data.PARQUET_PATH).name).write_bytes(b"tampered")
    monkeypatch.setattr(data.httpx, "get", lambda *args, **kwargs: pytest.fail("do not silently replace tampered cache"))
    with pytest.raises(ValueError, match="校验"):
        data.prepare_dataset(cache, path)


def test_prepare_fails_if_selected_id_is_absent(source, tmp_path, monkeypatch):
    payload, _, path, rows = source
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / Path(data.PARQUET_PATH).name).write_bytes(payload)
    monkeypatch.setattr(data, "_read_parquet", lambda path: rows[:1])
    with pytest.raises(ValueError, match="缺少"):
        data.prepare_dataset(cache, path)
    assert not (cache / data.DATASET_JSON_NAME).exists()


def test_parquet_dependency_error_explains_optional_install(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    with pytest.raises(RuntimeError, match="--extra eval"):
        data._read_parquet(tmp_path / "tasks.parquet")


def test_image_name_matches_official_v4_contract():
    assert data.image_name("pylint-dev__astroid-1196") == (
        "swebench/sweb.eval.x86_64.pylint-dev_1776_astroid-1196:latest")


def test_official_grade_args_use_local_pinned_rows_and_named_subset(tmp_path):
    tasks = tmp_path / "tasks.json"
    predictions = tmp_path / "predictions.jsonl"
    reports = tmp_path / "reports"
    ids = ["pvlib__pvlib-python-1072", "sqlfluff__sqlfluff-1625"]
    args = data.official_grade_args(tasks, predictions, "trial-1", reports, ids)
    assert args[:3] == [sys.executable, "-m", "swebench.harness.run_evaluation"]
    assert args[args.index("--dataset_name") + 1] == str(tasks.resolve())
    assert args[args.index("--split") + 1] == "dev"
    assert args[args.index("--predictions_path") + 1] == str(predictions.resolve())
    assert args[args.index("--instance_ids") + 1:args.index("--max_workers")] == ids
    assert args[args.index("--max_workers") + 1] == "1"
    assert args[args.index("--namespace") + 1] == "swebench"
    assert args[args.index("--cache_level") + 1] == "instance"
    assert args[args.index("--report_dir") + 1] == str(reports.resolve())
    assert "--arch" not in args


@pytest.mark.parametrize("instance_id", ["../escape", "ok__repo-1;uname", "", "repo-1"])
def test_image_name_rejects_invalid_identifiers(instance_id):
    with pytest.raises(ValueError):
        data.image_name(instance_id)


def test_grade_rejects_empty_subset_or_path_run_id(tmp_path):
    for ids, run_id in [([], "run"), (["sqlfluff__sqlfluff-1625"], "../run")]:
        with pytest.raises(ValueError):
            data.official_grade_args(tmp_path / "tasks.json", tmp_path / "preds.jsonl", run_id, tmp_path, ids)
