"""由 .env.sample/.env.example 生成 .env 的工具。

本腳本用途：
- 將範本配置檔複製為 `.env`
- 可透過 `--set KEY=VALUE` 覆寫指定配置
- 預設會自動搜尋 `.env.sample`，若不存在則使用 `.env.example`
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
INTERACTIVE_REQUIRED_FIELDS = [
    ("API_KEY", "Ankr API_KEY"),
    ("TARGET_CHAIN", "目標鏈名稱"),
    ("ETH_RPC_URL", "Ethereum RPC URL"),
    ("TARGET_CHAIN_RPC_URL", "Target Chain RPC URL"),
    ("TARGET_CHAIN_EXPLORER_BASE_URL", "Target Chain Explorer Base URL"),
]


def detect_source(cwd: Path) -> Path:
    """自動選擇來源範本檔。"""
    for name in (".env.sample", ".env.example"):
        candidate = cwd / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError("找不到 .env.sample 或 .env.example")


def apply_overrides(lines: list[str], overrides: dict[str, str]) -> list[str]:
    """將 KEY=VALUE 覆寫到既有內容，不存在時追加到檔尾。"""
    output: list[str] = []
    seen_keys: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue

        key, _ = line.split("=", 1)
        key = key.strip()
        if key in overrides:
            output.append(f"{key}={overrides[key]}\n")
            seen_keys.add(key)
        else:
            output.append(line)

    for key, value in overrides.items():
        if key not in seen_keys:
            output.append(f"{key}={value}\n")
    return output


def parse_set_items(items: list[str]) -> dict[str, str]:
    """解析 --set KEY=VALUE 參數。"""
    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"無效的 --set 參數：{item}（需要 KEY=VALUE）")
        key, value = item.split("=", 1)
        parsed[key.strip()] = value
    return parsed


def parse_key_values(lines: list[str]) -> dict[str, str]:
    """從範本內容解析 KEY=VALUE 映射。"""
    parsed: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def resolve_placeholders(raw_value: str, values: dict[str, str]) -> str:
    """使用目前已知值替換 ${KEY} 佔位符。"""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return values.get(key, match.group(0))

    return PLACEHOLDER_PATTERN.sub(replace, raw_value)


def prompt_value(label: str, key: str, default: str) -> str:
    """互動式詢問單個配置值。"""
    prompt_suffix = f" [{default}]" if default else ""
    while True:
        entered = input(f"{label} ({key}){prompt_suffix}: ").strip()
        value = entered or default
        if value:
            return value
        print(f"{key} 為必填，請輸入值。")


def collect_interactive_overrides(template_values: dict[str, str], preset: dict[str, str]) -> dict[str, str]:
    """互動式收集必填配置覆寫。"""
    if not sys.stdin.isatty():
        raise RuntimeError("目前為非互動環境，請改用 --set KEY=VALUE 傳入必填項。")

    answers = dict(preset)
    print("請依序填入必填配置；直接 Enter 可接受方括號內的默認值。")
    for key, label in INTERACTIVE_REQUIRED_FIELDS:
        if answers.get(key):
            continue
        template_default = resolve_placeholders(template_values.get(key, ""), answers)
        answers[key] = prompt_value(label, key, template_default)
    return {key: value for key, value in answers.items() if key not in preset or preset[key] != value}


def main() -> None:
    """命令列入口。"""
    parser = argparse.ArgumentParser(description="Generate .env from sample/example file.")
    parser.add_argument("--source", default="", help="來源檔案，預設自動偵測 .env.sample/.env.example")
    parser.add_argument("--output", default=".env", help="輸出檔案，預設 .env")
    parser.add_argument("--set", dest="sets", action="append", default=[], help="覆寫配置，格式 KEY=VALUE")
    parser.add_argument("--force", action="store_true", help="若輸出檔已存在，允許覆蓋")
    args = parser.parse_args()

    cwd = Path.cwd()
    source = Path(args.source) if args.source else detect_source(cwd)
    output = Path(args.output)
    if not source.is_absolute():
        source = cwd / source
    if not output.is_absolute():
        output = cwd / output

    if output.exists() and not args.force:
        raise FileExistsError(f"{output} 已存在，請加 --force 覆蓋")

    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    overrides = parse_set_items(args.sets)
    template_values = parse_key_values(lines)
    interactive_overrides = collect_interactive_overrides(template_values, overrides)
    overrides.update(interactive_overrides)
    final_lines = apply_overrides(lines, overrides)
    output.write_text("".join(final_lines), encoding="utf-8")
    print(f"Generated {output} from {source}")


if __name__ == "__main__":
    main()
