"""通用工具函数

Phase 2 限定范围：
- load_dotenv: 加载项目根目录 .env 到 os.environ（零依赖实现）
- expand_env_vars: 环境变量展开（从 config/settings.py 提取）
- safe_load_yaml: YAML 安全加载封装

零外部依赖，不 import dotClaw 其他模块。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# .env 是否已加载过（幂等保护，避免重复读盘）
_dotenv_loaded = False


def load_dotenv(path: str | Path | None = None, override: bool = False) -> bool:
    """加载 .env 文件到 os.environ（零依赖实现）。

    解析规则（兼容常见 .env 写法）：
    - 跳过空行和以 # 开头的注释行
    - 支持 `KEY=VALUE` 和 `export KEY=VALUE`
    - 去除值两端的成对单/双引号
    - 行内 # 注释仅在「未被引号包裹」时才剥离
    - 默认不覆盖已存在的环境变量（override=False），系统环境变量优先级更高

    Args:
        path: .env 路径。None = 项目根目录 / .env
        override: 是否用 .env 的值覆盖已存在的环境变量

    Returns:
        是否成功加载了文件（文件不存在返回 False）
    """
    global _dotenv_loaded

    if path is None:
        # 项目根目录 = 本文件上溯三层：common/ -> dotclaw/ -> src/ -> 根
        path = Path(__file__).resolve().parents[3] / ".env"
    path = Path(path)

    if not path.exists():
        return False

    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()

            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue

            value = value.strip()
            # 去成对引号；引号内的 # 不当注释
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            else:
                # 未加引号时剥离行内 # 注释
                hash_idx = value.find(" #")
                if hash_idx != -1:
                    value = value[:hash_idx].rstrip()

            if override or key not in os.environ:
                os.environ[key] = value

        _dotenv_loaded = True
        return True
    except Exception as e:
        logger.warning(".env 加载失败（已忽略）: %s", e)
        return False


def expand_env_vars(value: Any) -> Any:
    """递归替换 ${ENV_VAR} 为环境变量值。未解析的变量写入 warning 日志。"""
    if isinstance(value, str):
        pattern = re.compile(r'\$\{([^}]+)\}')

        def replacer(m: re.Match) -> str:
            var_name = m.group(1)
            env_value = os.environ.get(var_name)
            if env_value is None:
                logger.warning(
                    f"环境变量 ${var_name} 未设置，保留原始占位符。"
                    f" 请设置该环境变量或在配置文件中替换为实际值。"
                )
                return m.group(0)
            return env_value

        return pattern.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    return value


def safe_load_yaml(path: Path) -> dict:
    """安全加载 YAML 文件，文件不存在时返回空 dict"""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
