from flask import Flask, request, jsonify
import sys

app = Flask(__name__)

# Simulated log database
logs_database = {
    "order-service": [
        {"timestamp": "2025-06-04T13:55:00Z", "level": "INFO", "message": "OrderService started successfully"},
        {"timestamp": "2025-06-04T14:00:00Z", "level": "ERROR", "message": "DB connection timeout (500ms > 200ms)"},
        {"timestamp": "2025-06-04T14:01:00Z", "level": "WARN", "message": "Database connection pool at 98% capacity"},
        {"timestamp": "2025-06-04T14:02:00Z", "level": "ERROR", "message": "DB connection timeout (520ms)"},
        {"timestamp": "2025-06-04T14:03:00Z", "level": "ERROR", "message": "Transaction rollback due to timeout"},
    ],
    "file-service": [
        {"timestamp": "2025-06-04T10:25:00Z", "level": "WARN", "message": "/data partition usage reached 90%"},
        {"timestamp": "2025-06-04T10:28:00Z", "level": "ERROR", "message": "No space left on device"},
        {"timestamp": "2025-06-04T10:30:00Z", "level": "ERROR", "message": "Failed to write log file: disk full"},
    ],
    "payment-service": [
        {"timestamp": "2025-06-04T16:40:00Z", "level": "WARN", "message": "Network latency increased to 800ms"},
        {"timestamp": "2025-06-04T16:45:00Z", "level": "ERROR", "message": "Connection reset by peer"},
        {"timestamp": "2025-06-04T16:46:00Z", "level": "ERROR", "message": "Payment gateway timeout"},
    ],
}

# 同时监听 _search 正常路由，并加上全能兜底路由
@app.route("/_search", methods=["GET", "POST"], strict_slashes=False)
@app.route('/', defaults={'path': ''}, methods=["GET", "POST", "PUT"])
@app.route('/<path:path>', methods=["GET", "POST", "PUT"])
def search_logs(path=""):
    print(f"[DEBUG] ELK 收到请求路径: /{path}", file=sys.stderr)
    
    # 获取 JSON 请求体
    data = request.get_json(force=True, silent=True) or {}
    query_field = data.get("query", {})

    service = None
    level = None

    # 如果大模型乖乖传了 JSON 对象
    if isinstance(query_field, dict):
        service = query_field.get("service")
        level = query_field.get("level")
    # 如果大模型自作聪明传了 Lucene 字符串，直接粗暴匹配！
    elif isinstance(query_field, str):
        if "order-service" in query_field: service = "order-service"
        elif "file-service" in query_field: service = "file-service"
        elif "payment-service" in query_field: service = "payment-service"
        
        if "ERROR" in query_field: level = "ERROR"
        elif "WARN" in query_field: level = "WARN"

    # 如果什么都没匹配到，给个默认值
    if not service:
        service = "order-service"

    print(f"[DEBUG] 最终提取过滤条件: service={service}, level={level}", file=sys.stderr)

    logs = logs_database.get(service, [])
    filtered_logs = []
    for log in logs:
        if level and log["level"] != level:
            continue
        filtered_logs.append(log)

    return jsonify({
        "hits": {
            "total": {"value": len(filtered_logs)},
            "hits": [{"_source": log} for log in filtered_logs[-5:]]
        }
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9093, debug=False)