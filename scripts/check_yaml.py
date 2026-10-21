#!/usr/bin/env python3
"""校验 DSH 补丁 YAML：把 !!js 等自定义标签当作普通字符串，避免误报。"""
import sys, yaml

class L(yaml.SafeLoader):
    pass

def _any(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return f"<{suffix}>"
    return f"<{suffix}>"

L.add_multi_constructor("tag:yaml.org,2002:js", _any)
L.add_multi_constructor("!", _any)

def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python check_yaml.py <file.yml>")
    p = sys.argv[1]
    data = yaml.load(open(p, encoding="utf-8"), Loader=L)
    if not isinstance(data, list):
        sys.exit(f"顶层应为数组，实际是 {type(data).__name__}")
    ids = [d.get("id") for d in data if isinstance(d, dict)]
    print(f"OK  {p}\n    条目 {len(data)} 个\n    ids: {ids}")

if __name__ == "__main__":
    main()
