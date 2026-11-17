"""测试包。

显式声明为包，这样 ``from tests.conftest import DIALECT`` 才能正常解析，
而不是依赖 pytest 的 rootdir 推导。
"""
