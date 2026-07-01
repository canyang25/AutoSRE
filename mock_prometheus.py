from flask import Flask, request, jsonify
from datetime import datetime
import random
import sys

app = Flask(__name__)

# Simulated metrics data
metrics_data = {
    "order-service": {
        "cpu_usage": [random.uniform(20, 95) for _ in range(60)],
        "memory_usage": [random.uniform(30, 85) for _ in range(60)],
        "db_connections": [random.randint(150, 200) for _ in range(60)],
        "response_time": [random.uniform(50, 1500) for _ in range(60)]
    },
    "file-service": {
        "disk_usage": [random.uniform(85, 99) for _ in range(60)],
        "io_wait": [random.uniform(5, 50) for _ in range(60)]
    },
    "payment-service": {
        "packet_loss": [random.uniform(0, 50) for _ in range(60)],
        "latency": [random.uniform(10, 1200) for _ in range(60)]
    }
}

# 1. 正常路由：关闭严格斜杠检查，兼容 Dify
@app.route("/api/v1/query_range", methods=["GET", "POST"], strict_slashes=False)
def query_range():
    print(f"[DEBUG] 成功匹配正常路由! 参数: {request.args}", file=sys.stderr)
    service = request.args.get("service", "order-service")
    metric = request.args.get("metric", "cpu_usage")

    series = metrics_data.get(service, {}).get(metric, [random.random() * 100])
    data = []
    end_time = int(datetime.now().timestamp())

    for i in range(60):
        timestamp = end_time - (59 - i) * 60
        value = series[i % len(series)]
        data.append([timestamp, value])

    return jsonify({
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"service": service, "__name__": metric},
                    "values": data,
                }
            ],
        },
    })

# 2. 无敌兜底路由：拦截所有未知路径，强制返回数据！
@app.route('/', defaults={'path': ''}, methods=["GET", "POST", "PUT"])
@app.route('/<path:path>', methods=["GET", "POST", "PUT"])
def catch_all(path):
    print(f"[WARN] 触发兜底路由! Dify 实际请求的奇怪路径是: /{path}", file=sys.stderr)
    print(f"[WARN] 携带的参数是: {request.args}", file=sys.stderr)
    return query_range()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9091, debug=False)