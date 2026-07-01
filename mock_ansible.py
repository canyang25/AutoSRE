from flask import Flask, request, jsonify
import time
import sys

app = Flask(__name__)
execution_logs = []

# 包含正常路由和全能兜底路由，无视请求方法和奇怪路径
@app.route("/api/v1/execute", methods=["GET", "POST", "PUT"], strict_slashes=False)
@app.route('/', defaults={'path': ''}, methods=["GET", "POST", "PUT"])
@app.route('/<path:path>', methods=["GET", "POST", "PUT"])
def execute_playbook(path=""):
    print(f"[DEBUG] Ansible 收到请求路径: /{path}", file=sys.stderr)
    
    # 极度宽容地提取数据：不管是 JSON 还是 URL 参数，全接住
    data = {}
    if request.is_json:
        data = request.get_json(force=True, silent=True) or {}
    else:
        data = request.form.to_dict() or request.args.to_dict()

    # 提取大模型编造的剧本名称（转成小写方便匹配）
    playbook_raw = str(data.get("playbook", "")).lower()
    hosts = data.get("hosts", ["localhost"])

    print(f"[DEBUG] 大模型请求执行剧本: {playbook_raw}", file=sys.stderr)

    # 核心防翻车逻辑：模糊匹配大模型的意图
    playbook = "unknown"
    if "db" in playbook_raw or "pool" in playbook_raw or "connection" in playbook_raw:
        playbook = "restore_db_pool.yml"
    elif "disk" in playbook_raw or "clean" in playbook_raw or "space" in playbook_raw:
        playbook = "clean_disk_space.yml"
    elif "restart" in playbook_raw or "service" in playbook_raw:
        playbook = "restart_service.yml"

    log_entry = {
        "id": len(execution_logs) + 1,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "playbook": playbook,
        "raw_playbook_from_llm": playbook_raw,
        "hosts": hosts,
        "status": "executing",
    }
    execution_logs.append(log_entry)

    # 模拟执行耗时
    time.sleep(1)

    results = {
        "restore_db_pool.yml": {
            "status": "success",
            "message": "Database connection pool restored from 50 to 200.",
            "changes": {"max_connections": "50 -> 200"}
        },
        "clean_disk_space.yml": {
            "status": "success",
            "message": "Cleaned 15GB temp files, /data usage from 98% to 73%.",
            "changes": {"freed_space": "15GB"}
        },
        "restart_service.yml": {
            "status": "success",
            "message": "payment-service restarted, network connection recovered.",
            "changes": {"service_state": "restarted"}
        }
    }

    result = results.get(playbook, {
        "status": "unknown",
        "message": f"Executed unknown playbook: {playbook_raw}"
    })

    log_entry["status"] = result["status"]
    log_entry["result"] = result

    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9092, debug=False)