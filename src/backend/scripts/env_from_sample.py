"""由 .env.sample/.env.example 生成 .env 的工具。

本腳本用途：
- 將範本配置檔複製為 `.env`
- 可透過 `--set KEY=VALUE` 覆寫指定配置
- 預設會自動搜尋 `.env.sample`，若不存在則使用 `.env.example`
"""

from __future__ import annotations

import argparse
from pathlib import Path


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
    final_lines = apply_overrides(lines, overrides)
    output.write_text("".join(final_lines), encoding="utf-8")
    print(f"Generated {output} from {source}")


if __name__ == "__main__":
    main()
