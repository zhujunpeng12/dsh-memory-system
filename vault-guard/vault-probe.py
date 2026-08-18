"""vault-probe — Vault 文件修改前字节级预检（只读，不写盘）。

用途：edit/replace 前确认目标片段在文件中的真实字符（char codes），
解决显示层（read/grep）与字节层不一致导致的 old_string not found。
背景：2026-08-18 复盘确认 edit 按显示层构造 old_string 连续失败 2 次，
因为文件被 PowerShell 反引号转义污染（BEL 0x07）或含不可见字符时，
肉眼看到的 ≠ 实际字节。t1（vault-write）管写入侧拦截，本工具管修改前核对。

用法：
  python vault-probe.py <文件> [子串]
  python vault-probe.py <文件> --needle-file <UTF-8文件>   # 子串含反引号/BEL 时用文件传，别走内联
  python vault-probe.py <文件>                              # 无子串：输出全文件不可见字符清单

输出：命中位置（字符偏移/行:列/字节偏移）+ 窗口内逐字符 repr + U+XXXX 具名
（控制字符具名：BEL/TAB/CR/LF/NUL/ESC…）；子串未找到退出 1（edit 大概率也失败，
先看输出的控制字符清单再核对）。编码：UTF-8 严格读取，stdout 重配置 UTF-8。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_CTRL = {
    0x00: "NUL", 0x01: "SOH", 0x02: "STX", 0x03: "ETX", 0x04: "EOT", 0x05: "ENQ",
    0x06: "ACK", 0x07: "BEL", 0x08: "BS", 0x09: "TAB", 0x0A: "LF", 0x0B: "VT",
    0x0C: "FF", 0x0D: "CR", 0x0E: "SO", 0x0F: "SI", 0x10: "DLE", 0x11: "DC1",
    0x12: "DC2", 0x13: "DC3", 0x14: "DC4", 0x15: "NAK", 0x16: "SYN", 0x17: "ETB",
    0x18: "CAN", 0x19: "EM", 0x1A: "SUB", 0x1B: "ESC", 0x1C: "FS", 0x1D: "GS",
    0x1E: "RS", 0x1F: "US", 0x7F: "DEL",
}


def _label(ch: str) -> str:
    code = ord(ch)
    if code in _CTRL:
        return _CTRL[code]
    return f"U+{code:04X}"


def _invisible(ch: str) -> bool:
    """需要示警的不可见字符：C0 控制（排除正常换行 LF）、DEL、非可见非 ASCII。"""
    code = ord(ch)
    if code < 0x20 and code != 0x0A:
        return True
    if code == 0x7F or code == 0xFEFF:  # DEL / BOM
        return True
    return code > 0x7F and not ch.isprintable() and not ch.isspace()


def _line_col(text: str, char_index: int) -> tuple[int, int]:
    line = text.count("\n", 0, char_index) + 1
    last_nl = text.rfind("\n", 0, char_index)
    return line, char_index - last_nl


def _window(text: str, start: int, length: int, heading: str) -> None:
    print(f"    {heading}")
    for idx in range(start, min(start + length, len(text))):
        ch = text[idx]
        print(f"      [{idx}] {ch!r:<6} {_label(ch)}")


def _show_invisible(text: str, limit: int = 200) -> int:
    count = 0
    for idx, ch in enumerate(text):
        if not _invisible(ch):
            continue
        line, col = _line_col(text, idx)
        byte_off = len(text[:idx].encode("utf-8"))
        print(f"      [{idx}] 字节偏移 {byte_off} 行 {line}:列 {col}  {ch!r:<6} {_label(ch)}")
        count += 1
        if count >= limit:
            print(f"      …（已达 {limit} 条上限，共 {sum(1 for c in text if _invisible(c))} 处）")
            break
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Vault 文件修改前字节级预检（只读）")
    parser.add_argument("file", type=Path, help="目标文件（UTF-8）")
    parser.add_argument("needle", nargs="?", help="目标子串；含反引号/控制字符时改用 --needle-file")
    parser.add_argument("--needle-file", type=Path, help="子串从 UTF-8 文件读入（防 PowerShell 内联转义）")
    parser.add_argument("--context", type=int, default=8, help="命中点前后窗口字符数")
    args = parser.parse_args(argv)

    try:
        # newline="" 保留 CRLF：text mode 默认 universal newlines 会把 \r\n 译成 \n，
        # CR(0x0D) 会被吞掉——字节级预检必须看到真实 CR（CRLF 文件正是 edit 失败常见元凶）。
        with open(args.file, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except FileNotFoundError:
        print(f"vault-probe: 文件不存在：{args.file}", file=sys.stderr)
        return 1
    except UnicodeDecodeError as exc:
        print(f"vault-probe: 非 UTF-8 或文件损坏：{args.file}（{exc}）", file=sys.stderr)
        print("提示：含中文的 Vault/配置文件请用 read/grep 工具验证；先修编码再编辑。", file=sys.stderr)
        return 1

    needle = args.needle
    if needle is None and args.needle_file is not None:
        try:
            with open(args.needle_file, "r", encoding="utf-8", newline="") as handle:
                needle = handle.read()
        except Exception as exc:
            print(f"vault-probe: 读 --needle-file 失败：{exc}", file=sys.stderr)
            return 1

    if needle is not None:
        hits: list[int] = []
        start = 0
        while True:
            pos = text.find(needle, start)
            if pos < 0:
                break
            hits.append(pos)
            start = pos + 1  # 允许重叠命中
        if not hits:
            print(f"vault-probe: 未找到子串（{len(needle)} 字符）：{needle[:40]!r}")
            print("  文件中的不可见字符清单（edit old_string 很可能因此失败）：")
            if _show_invisible(text) == 0:
                print("    （无不可见字符；请核对子串本身的文字/标点是否与文件一致）")
            return 1
        print(f"vault-probe: 命中 {len(hits)} 处")
        for i, pos in enumerate(hits, 1):
            line, col = _line_col(text, pos)
            byte_off = len(text[:pos].encode("utf-8"))
            print(f"  ─ 命中 {i} @ 字符偏移 {pos}（字节偏移 {byte_off}）行 {line}:列 {col}")
            wstart = max(0, pos - args.context)
            _window(text, wstart, (pos - wstart) + len(needle) + args.context, "窗口（含命中片段）:")
        return 0

    print(f"vault-probe: {args.file} — 不可见字符清单（{sum(1 for c in text if _invisible(c))} 处）")
    if _show_invisible(text) == 0:
        print("  （无控制字符/不可见字符，文件干净）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())