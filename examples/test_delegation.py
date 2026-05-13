import requests
import json
import sys
import time

def test_delegation():
    url = "http://localhost:8088/mcp"
    
    print("\n" + "="*60)
    print("  MCP Proxy Gateway - Bidirectional Delegation Test")
    print("="*60)
    
    # 1. Check Registry
    print("\n1. Fetching Unified Tool Registry...")
    try:
        reg_resp = requests.get("http://localhost:8088/registry")
        reg_data = reg_resp.json()
        total = reg_data.get("total_tools", 0)
        print(f"   [OK] Found {total} tools in the registry.")
        
        tools = []
        by_ds = reg_data.get("by_downstream", {})
        for ds, t_names in by_ds.items():
            for name in t_names:
                tools.append(f"{name} (from {ds})")
        
        for t in tools:
            print(f"    - {t}")
    except Exception as e:
        print(f"   [ERROR] Could not connect to registry: {e}")
        return

    # TEST CASE A: MCP1 -> Proxy -> MCP2
    print("\nTEST A: Calling 'delegate_ai_search' on MCP1...")
    print("        (Flow: Client -> Proxy -> MCP1 -> Proxy -> MCP2)")
    
    payload_a = {
        "jsonrpc": "2.0",
        "id": "test-a",
        "method": "tools/call",
        "params": {
            "name": "delegate_ai_search",
            "arguments": {
                "query": "How does bidirectional delegation work?"
            }
        }
    }
    
    try:
        resp_a = requests.post(url, json=payload_a, timeout=15)
        res_a = resp_a.json()
        error = res_a.get("error")
        if error:
            print(f"   [FAIL] Tool returned error: {error}")
        else:
            text = res_a.get("result", {}).get("content", [{}])[0].get("text", "No result")
            print(f"\n   [SUCCESS] Result from MCP2 via MCP1:\n   {text[:200]}...")
    except Exception as e:
        print(f"   [ERROR] Request A failed: {e}")

    # TEST CASE B: MCP2 -> Proxy -> MCP1
    print("\nTEST B: Calling 'summarize_via_mcp1' on MCP2...")
    print("        (Flow: Client -> Proxy -> MCP2 -> Proxy -> MCP1)")
    
    payload_b = {
        "jsonrpc": "2.0",
        "id": "test-b",
        "method": "tools/call",
        "params": {
            "name": "summarize_via_mcp1",
            "arguments": {
                "text": "The Model Context Protocol (MCP) is a protocol for connecting AI models to external tools and data. It supports various transport layers including WebSocket and SSE."
            }
        }
    }
    
    try:
        resp_b = requests.post(url, json=payload_b, timeout=15)
        res_b = resp_b.json()
        error = res_b.get("error")
        if error:
            print(f"   [FAIL] Tool returned error: {error}")
        else:
            text = res_b.get("result", {}).get("content", [{}])[0].get("text", "No result")
            print(f"\n   [SUCCESS] Result from MCP1 via MCP2:\n   {text}")
    except Exception as e:
        print(f"   [ERROR] Request B failed: {e}")

if __name__ == "__main__":
    test_delegation()
