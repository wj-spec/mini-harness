"""W6 v2 对照使用的偏好夹具与任务，供脚本和离线回归共用。"""
from __future__ import annotations

W6_TEST_PREFERENCES = {
    "version": 2,
    "enabled": True,
    "entries": [
        {
            "id": "w6-explain", "kind": "preference", "scope": "workflow",
            "text": "解释代码时：先给结论，再给细节；用中文，注释简洁。",
            "triggers": ["解释", "说明", "怎么", "如何", "why", "how"],
            "source": "user", "status": "confirmed", "confidence": 1.0,
            "created_at": "2026-10-26T00:00:00Z", "updated_at": "2026-10-26T00:00:00Z",
            "last_confirmed_at": "2026-10-26T00:00:00Z",
        },
        {
            "id": "w6-change", "kind": "preference", "scope": "workflow",
            "text": "修改代码前：先说明方案、影响文件和验证方式。",
            "triggers": ["修改", "增加", "新增", "重构", "改造", "edit", "implement"],
            "source": "user", "status": "confirmed", "confidence": 1.0,
            "created_at": "2026-10-26T00:00:00Z", "updated_at": "2026-10-26T00:00:00Z",
            "last_confirmed_at": "2026-10-26T00:00:00Z",
        },
        {
            "id": "w6-test", "kind": "preference", "scope": "workflow",
            "text": "测试/诊断时：先复现失败，再改代码，最后重新运行验证。",
            "triggers": ["测试", "诊断", "验证", "失败", "复现", "bug", "修复"],
            "source": "user", "status": "confirmed", "confidence": 1.0,
            "created_at": "2026-10-26T00:00:00Z", "updated_at": "2026-10-26T00:00:00Z",
            "last_confirmed_at": "2026-10-26T00:00:00Z",
        },
    ],
}
EMPTY_PROFILE = {"version": 2, "enabled": False, "entries": []}

TASKS = {
    "task-a": {
        "prompt": "解释 Ink 的 /about 命令是怎么实现的：数据文件格式、状态过滤、以及注入到模型上下文的时机。不要改文件。",
        "skills": "结论先行 / 中文简洁",
    },
    "task-b": {
        "prompt": "给 /about 增加 /about edit <序号> <新文本> 命令，用于修改某条偏好的文本；补一个测试。只改必要文件。",
        "skills": "改动前先给方案 / 注释简洁",
    },
    "task-c": {
        "prompt": "检查 app/user_profile.py 的 load_user_profile：如果 entries 里有重复 text 或空白 text 条目会怎样？有问题就修复并补测试。",
        "skills": "根因定位 / 验证",
    },
}
