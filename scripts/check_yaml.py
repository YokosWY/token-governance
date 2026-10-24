#!/usr/bin/env python3
"""校验 DSH 补丁 YAML：把 !!js 等自定义标签当作普通字符串，避免误报。

用法：
  python scripts/check_yaml.py <file.yml>

退出码：0 通过 / 1 文件有问题 / 2 环境缺依赖（pyyaml）或用法错误。
"""
import sys

from tg_common import utf8_stdio

utf8_stdio()


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def main():
    if len(sys.argv) < 2:
        die("用法: python scripts/check_yaml.py <file.yml>", 2)
    try:
        import yaml
    except ImportError:
        die("缺少 pyyaml：pip install pyyaml（校验补丁必须用它）", 2)

    class L(yaml.SafeLoader):
        pass

    def _any(loader, suffix, node):
        return f"<{suffix}>"

    L.add_multi_constructor("tag:yaml.org,2002:js", _any)
    L.add_multi_constructor("!", _any)

    p = sys.argv[1]
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.load(f, Loader=L)
    except FileNotFoundError:
        die(f"文件不存在: {p}")
    except UnicodeDecodeError as e:
        die(f"{p} 不是 UTF-8（{e}）。\nWindows 记事本可能存成 GBK/ANSI：另存为 UTF-8 再试。")
    except yaml.YAMLError as e:
        die(f"{p} 不是合法 YAML：{str(e).splitlines()[0]}")

    if not isinstance(data, list):
        die(f"顶层应为数组，实际是 {type(data).__name__}"
            "（DSH 的 cordis.patch.yml 是 `- id: …` 这种列表）")
    ids = [d.get("id") for d in data if isinstance(d, dict)]
    print(f"OK  {p}\n    条目 {len(data)} 个\n    ids: {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
