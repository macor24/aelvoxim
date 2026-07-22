#!/usr/bin/env python3
"""
test_windows_mcp.py — 从 WSL 测试 Windows 本机的 Windows-MCP 服务

用法：
    python3 test_windows_mcp.py

前置条件：
    1. 在 Windows 本机运行 start_test.bat（启动 Windows-MCP）
    2. 确保 WSL 能访问 Windows 的 localhost（通常默认通）

测试内容：
    1. tools/list — 获取工具列表
    2. tools/call — 调用 App 工具打开记事本
    3. tools/call — 调用 Snapshot 截图
"""

import json
import sys
import urllib.request
import urllib.error

# Windows-MCP 的地址
# WSL 中可以用 127.0.0.1 访问 Windows 本机的服务（Windows 11 默认开启）
# 如果不行，改成 Windows 的局域网 IP
WIN_HOST = "127.0.0.1"
MCP_PORT = 8000
MCP_URL = f"http://{WIN_HOST}:{MCP_PORT}"

def mcp_call(method: str, params: dict = None) -> dict:
    """调用 MCP 的 JSON-RPC 接口"""
    body = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1,
    }
    req = urllib.request.Request(
        f"{MCP_URL}/mcp/call",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def test_001_list_tools():
    """测试：获取工具列表"""
    print("\n===== 测试 1: 获取工具列表 =====")
    result = mcp_call("tools/list")
    if "error" in result:
        print(f"  [失败] {result['error']}")
        return False
    tools = result.get("result", {}).get("tools", [])
    print(f"  成功获取 {len(tools)} 个工具:")
    for t in tools[:5]:
        print(f"    - {t['name']}: {t.get('description', '')[:60]}")
    if len(tools) > 5:
        print(f"    ... 还有 {len(tools) - 5} 个")
    return True


def test_002_open_notepad():
    """测试：打开记事本"""
    print("\n===== 测试 2: 打开记事本 =====")
    result = mcp_call("tools/call", {
        "name": "App",
        "arguments": {
            "mode": "launch",
            "name": "notepad"
        }
    })
    if "error" in result:
        print(f"  [失败] {result['error']}")
        return False
    print(f"  [成功] 记事本已打开")
    print(f"  返回: {json.dumps(result['result'], ensure_ascii=False)[:200]}")
    return True


def test_003_snapshot():
    """测试：截图（获取桌面状态）"""
    print("\n===== 测试 3: 截图 =====")
    result = mcp_call("tools/call", {
        "name": "Snapshot",
        "arguments": {
            "use_vision": False
        }
    })
    if "error" in result:
        print(f"  [失败] {result['error']}")
        return False
    data = result.get("result", {})
    content = data.get("content", [{}])
    text = content[0].get("text", "") if content else ""
    print(f"  [成功] 桌面状态获取完成")
    print(f"  内容长度: {len(text)} 字符")
    print(f"  预览: {text[:300]}")
    return True


def test_004_click_type():
    """测试：在记事本中输入文字"""
    print("\n===== 测试 4: 在记事本中输入 =====")
    result = mcp_call("tools/call", {
        "name": "Click",
        "arguments": {
            "mode": "type",
            "text": "Hello from Windows-MCP! 测试成功。\n这是第二行。"
        }
    })
    if "error" in result:
        print(f"  [失败] {result['error']}")
        return False
    print(f"  [成功] 文字已输入")
    return True


def main():
    print("=" * 60)
    print("Windows-MCP 功能测试")
    print("=" * 60)
    print(f"\n目标地址: {MCP_URL}")
    print("请确保 Windows 本机已运行 start_test.bat")
    input("\n按 Enter 开始测试...")

    tests = [
        ("列出工具", test_001_list_tools),
        ("打开记事本", test_002_open_notepad),
        ("截图", test_003_snapshot),
        ("输入文字", test_004_click_type),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            if fn():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  [异常] {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试完成: {passed} 通过, {failed} 失败")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
