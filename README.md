# AutoSRE

> An autonomous, LLM-powered Site Reliability Engineer. Give it a production alert and it pulls the metrics and logs, diagnoses the root cause, runs the fix, and writes the incident report — **no human in the loop**.

![Python](https://img.shields.io/badge/python-3.10+-blue)
![Agent](https://img.shields.io/badge/agent-LLM%20tool--use-d97757)
![Runs free](https://img.shields.io/badge/runs%20free-Groq%20%7C%20Ollama-2ea44f)
![License](https://img.shields.io/badge/license-MIT-green)

This is a reference implementation of a **closed-loop AIOps agent**. A fault alert fires; the agent investigates like an on-call SRE would — check the dashboards, read the logs, form a hypothesis, apply a fix, verify — and hands back a written report. The agent is a self-contained Python tool-use loop (Anthropic / Claude); observability backends (Prometheus, Elasticsearch/ELK, Ansible) are provided as lightweight mocks so the whole thing runs on a laptop.

## How it works

```mermaid
flowchart LR
    ALERT([Fault alert]) --> AGENT

    subgraph AGENT [AIOps Agent · Python + Claude tool-use]
        direction TB
        D1[1 · Gather signals]
        D2[2 · Diagnose root cause]
        D3[3 · Remediate]
        D4[4 · Report]
        D1 --> D2 --> D3 --> D4
    end

    D1 -->|query metrics| PROM[(Mock Prometheus<br/>:9091)]
    D1 -->|search logs| ELK[(Mock ELK<br/>:9093)]
    D3 -->|run playbook| ANS[(Mock Ansible<br/>:9092)]

    D4 --> REPORT([Incident report])
```

The agent runs a four-step loop:

1. **Gather signals** — query time-series metrics (Prometheus) and error logs (ELK) for the affected service.
2. **Diagnose** — the LLM correlates the signals into a root-cause hypothesis.
3. **Remediate** — pick and execute the matching Ansible playbook (restore DB pool, clean disk, restart service).
4. **Verify & report** — confirm recovery and emit a structured incident report.

## Scenarios

Three faults ship with the demo, each with a known ground-truth root cause so you can check the agent's reasoning:

| Scenario  | Service           | Symptom                              | Root cause                          | Remediation             |
| --------- | ----------------- | ------------------------------------ | ----------------------------------- | ----------------------- |
| `db`      | order-service     | API latency 200ms → 1.5s             | DB connection pool misconfigured    | `restore_db_pool.yml`   |
| `disk`    | file-service      | `/data` partition at 98%             | Disk space exhausted                | `clean_disk_space.yml`  |
| `network` | payment-service   | Rising payment failure rate          | Network partition                   | `restart_service.yml`   |

## Quickstart

### 1. See the loop without any setup

There's an offline mode that walks through exactly what the agent does — no servers, no keys:

```bash
python agent.py db --simulate
python agent.py --list
```

### 2. Run the mock backends

```bash
pip install -r requirements.txt
./deploy.sh          # starts mock Prometheus / ELK / Ansible via docker compose
```

| Service         | URL                     | Role                         |
| --------------- | ----------------------- | ---------------------------- |
| Mock Prometheus | http://localhost:9091   | Time-series metrics          |
| Mock ELK        | http://localhost:9093   | Log search                   |
| Mock Ansible    | http://localhost:9092   | Playbook execution           |

### 3. Run the real agent

Grab a **free** Groq API key (no credit card) at [console.groq.com/keys](https://console.groq.com/keys), then:

```bash
cp .env.example .env      # then add your GROQ_API_KEY
python agent.py db
```

The agent calls the three mock tools, diagnoses the root cause, runs the matching
playbook, and writes a report to `reports/incident-*.md`. Without a key it
automatically falls back to the offline walkthrough, so it never hard-fails.

Any LLM backend works — the agent auto-detects whichever you configure in `.env`:

| Backend       | Cost              | Setup                                     |
| ------------- | ----------------- | ----------------------------------------- |
| **Groq**      | Free (no card)    | `GROQ_API_KEY`                            |
| **Ollama**    | Free, fully local | `LLM_PROVIDER=ollama` (+ install Ollama)  |
| Anthropic     | Paid              | `ANTHROPIC_API_KEY`                       |
| OpenAI / etc. | Paid              | `OPENAI_API_KEY` (+ `OPENAI_BASE_URL`)    |

> **Optional — Dify path:** `trigger_fault.py` sends the same alert to a [Dify](https://dify.ai)
> Workflow app instead of running the loop in Python. Set `DIFY_*` in `.env` and run
> `python trigger_fault.py db`. (No Dify workflow ships with this repo — see Notes.)

## Repo structure

```
.
├── agent.py           # the agent: Claude tool-use loop over the mock tools
├── trigger_fault.py   # optional: send the alert to a Dify workflow (or --simulate)
├── deploy.sh          # spin up the mock observability stack via docker compose
├── tools/
│   ├── mock_prometheus.py   # metrics API  (:9091)
│   ├── mock_elk.py          # log search API (:9093)
│   └── mock_ansible.py      # playbook runner API (:9092)
├── .env.example       # configuration template
└── requirements.txt
```

## Tech stack

- **Agent:** Python LLM tool-use loop — backend-agnostic (Groq, Ollama, Anthropic, OpenAI-compatible)
- **Tools:** Flask mock services standing in for Prometheus, Elasticsearch/ELK, and Ansible
- **Optional orchestration:** [Dify](https://dify.ai) workflow (via `trigger_fault.py`)

## Notes

- **Primary path is `agent.py`** — a self-contained loop that needs only an `ANTHROPIC_API_KEY` and the local mocks. It falls back to an offline walkthrough when no key is set.
- The original Dify Workflow definition (DSL) is **not** included — that hosted instance is gone. `trigger_fault.py` remains for anyone who wants to rebuild a Dify workflow, but it isn't required to run the project.
- The mock services return canned-but-realistic data; they exist to exercise the agent's reasoning, not to be real observability backends.

## License

[MIT](LICENSE)
