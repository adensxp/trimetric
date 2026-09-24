#!/usr/bin/env python3
"""config.setup_logging 单测：文件落盘 + 幂等 + 降级安全"""
import os
import pathlib
import sys

os.environ["TRIMETRIC_LOG_DIR"] = "/tmp/gaoliang_test_logs"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import config  # noqa: E402


def test_setup_logging_writes_file():
    logger = config.setup_logging("test_config_smoke")
    logger.info("日志文件写入测试")
    for h in logger.handlers:
        h.flush()
    logs = sorted(pathlib.Path("/tmp/gaoliang_test_logs").glob("test_config_smoke_*.log"))
    assert logs, "未产生日志文件"
    content = logs[-1].read_text(encoding="utf-8")
    assert "日志文件写入测试" in content, content


def test_setup_logging_idempotent():
    logger = config.setup_logging("test_config_smoke")
    n = len(logger.handlers)
    again = config.setup_logging("test_config_smoke")
    assert again is logger and len(logger.handlers) == n, "重复调用应幂等"


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS  " + name)
        except AssertionError as e:
            failed += 1
            print("FAIL  %s: %s" % (name, e))
    print()
    print("%d/%d 通过" % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
