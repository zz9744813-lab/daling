"""pytest 配置 — Phase 1 测试基础设施。

使用 pytest-asyncio 的 asyncio mode。
所有测试通过 mock LLM 调用来验证 Agent 的错误处理行为。
"""


# 设置 asyncio mode
def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as async")
