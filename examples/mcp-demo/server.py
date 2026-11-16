"""验收用本地 MCP：仅回显输入，不访问网络或用户文件。"""
import json
import os
import sys


def serve():
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion":request.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities":{"tools":{}}, "serverInfo":{"name":"workbench-demo","version":"1.0"}}
            elif method == "tools/list":
                result = {"tools":[{"name":"echo","description":"Local demo echo",
                    "inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}]}
            elif method == "tools/call":
                result = {"content":[{"type":"text","text":os.environ.get("DEMO_LABEL","demo")+": "+
                           str(request.get("params",{}).get("arguments",{}).get("text",""))}]}
            elif method == "ping":
                result = {}
            else:
                print(json.dumps({"jsonrpc":"2.0","id":request["id"],"error":{"code":-32601,"message":"Unknown method"}}),flush=True)
                continue
            print(json.dumps({"jsonrpc":"2.0","id":request["id"],"result":result}),flush=True)
        except (ValueError, TypeError):
            continue


if __name__ == "__main__":
    serve()
