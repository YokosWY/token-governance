#!/usr/bin/env python3
"""数字单一真相：用 data/metrics.json 生成/校验文档里的表格块。

用法：
  python gen_metrics.py --write     # 用 json 重写文档里 <!-- metrics:begin --> 到 <!-- metrics:end --> 之间的内容
  python gen_metrics.py --check     # 只校验，不一致退出码 1（audit_pack 会调用）

原则：表格数字只改 data/metrics.json，再 --write；手改文档会被 --check 抓出来。
"""
import argparse, json, os, sys

from tg_common import utf8_stdio

utf8_stdio()

BEGIN = "<!-- metrics:begin -->"
END = "<!-- metrics:end -->"
TARGETS = ["README.md", os.path.join("docs", "benchmark.md")]


def render(data):
    cols = data["columns"]
    out = [BEGIN, ""]
    out.append("| " + " | ".join(cols) + " |")
    out.append("|" + "|".join(["---"] + ["---:"] * (len(cols) - 1)) + "|")
    for row in data["rows"]:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    out.append("")
    out.append(f"> 数字来源 `data/metrics.json`（更新 {data['updated']}）。{data['scope']}")
    out.append("")
    out.append(END)
    return "\n".join(out)


def replace_block(text, block):
    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < 0 or j < i:
        return None
    return text[:i] + block + text[j + len(END):]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    root = a.root
    data = json.load(open(os.path.join(root, "data", "metrics.json"), encoding="utf-8"))
    block = render(data)
    bad, changed = [], []

    for rel in TARGETS:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            bad.append(f"{rel}: 文件不存在")
            continue
        txt = open(p, encoding="utf-8").read()
        new = replace_block(txt, block)
        if new is None:
            bad.append(f"{rel}: 缺少 {BEGIN} / {END} 标记")
            continue
        if new == txt:
            continue
        if a.write:
            open(p, "w", encoding="utf-8", newline="\n").write(new)
            changed.append(rel)
        else:
            bad.append(f"{rel}: 表格与 data/metrics.json 不一致")

    if a.write:
        print("已更新: " + (", ".join(changed) if changed else "无需改动"))
        return
    if bad:
        for b in bad:
            print("[FAIL] " + b, file=sys.stderr)
        print("修法: python scripts/gen_metrics.py --write", file=sys.stderr)
        sys.exit(1)
    print("OK  数字与 data/metrics.json 一致（%d 个文件）" % len(TARGETS))


if __name__ == "__main__":
    main()
