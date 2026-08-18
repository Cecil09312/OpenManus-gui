# Python 版本检查：3.11-3.13
import sys


if sys.version_info < (3, 11) or sys.version_info > (3, 13):
    print(
        "警告：不支持的 Python 版本 {ver}，请使用 3.11-3.13".format(
            ver=".".join(map(str, sys.version_info))
        )
    )
