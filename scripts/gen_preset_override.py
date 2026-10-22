#!/usr/bin/env python3
"""从组合配置生成 preset 覆盖片段（升级后重生成用）。

preset 内的 plugins 不可按 id 寻址（tg-ops 坑 2），只能整段覆盖。本脚本把
composed 文件里的 preset 块原样取出，只改阈值相关的数字，输出可直接粘进
cordis.patch.yml 的片段。

用法：
  python gen_preset_override.py --composed ~/.dsh/profiles/desktop/cordis.yml \
      --presets preset-standard,preset-gui,preset-ptc,preset-cordis \
      --pruner-threshold 200000 --compact-threshold 0.8 > preset-blocks.yml

注意：
  - 只在原块已有对应键时改值，不新增键；缺 compaction 配置的 preset 会打印提示，
    表示它走插件默认值。
  - 生成后先跑 scripts/check_yaml.py，再重启验证组合结果。
"""
import argparse
import io
import re
import sys

TOP = re.compile(r'^- id: (\S+)\s*$')


def split_blocks(lines):
    blocks, cur, key = {}, [], None
    for line in lines:
        m = TOP.match(line)
        if m:
            if key:
                blocks[key] = cur
            key, cur = m.group(1), [line]
        elif key:
            cur.append(line)
    if key:
        blocks[key] = cur
    return blocks


def adjust(lines, pruner, compact):
    out, changed = [], {"pruner": 0, "compact": 0}
    for line in lines:
        new = re.sub(r'^(\s*thresholdChars:\s*)\d+\s*$', r'\g<1>' + str(pruner), line)
        if new != line:
            changed["pruner"] += 1
        line2 = re.sub(r'^(\s*thresholdRatio:\s*)[\d.]+\s*$', r'\g<1>' + str(compact), new)
        if line2 != new:
            changed["compact"] += 1
        out.append(line2)
    return out, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--composed", required=True)
    ap.add_argument("--presets", required=True, help="逗号分隔的 preset id")
    ap.add_argument("--pruner-threshold", type=int, default=200000)
    ap.add_argument("--compact-threshold", type=float, default=0.8)
    args = ap.parse_args()

    raw = io.open(args.composed, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    blocks = split_blocks(raw.split(nl))

    out = []
    for pid in [p.strip() for p in args.presets.split(",") if p.strip()]:
        if pid not in blocks:
            print(f"# 跳过 {pid}: composed 里没有这个 preset", file=sys.stderr)
            continue
        body, changed = adjust(blocks[pid], args.pruner_threshold, args.compact_threshold)
        has_compact_cfg = any("thresholdRatio:" in x for x in body)
        print(f"# {pid}: pruner 改 {changed['pruner']} 处, compaction 改 {changed['compact']} 处"
              + ("" if changed["compact"] else "（走插件默认阈值）"), file=sys.stderr)
        if not has_compact_cfg:
            print(f"# 注意 {pid}: 没有 compaction 配置，将使用插件默认值", file=sys.stderr)
        out.append("")
        out.append(f"# ---- {pid} 整段覆盖（由 gen_preset_override.py 生成）----")
        out.extend(body)

    text = nl.join(out).lstrip(nl) + nl
    sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
