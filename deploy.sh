#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p "$SCRIPT_DIR/tools"
mkdir -p "$SCRIPT_DIR/logs"

echo "Creating/refreshing docker-compose file for mock services..."
cat > docker-compose-mock.yml << 'EOF'
services:
  mock-prometheus:
    image: python:3.9-slim
    ports:
      - "9091:9091"
    volumes:
      - ./tools/mock_prometheus.py:/app/mock.py
      - ./fixtures:/app/fixtures
    working_dir: /app
    command: sh -c "pip install flask && python mock.py"
    networks:
      - mocknet

  mock-ansible:
    image: python:3.9-slim
    ports:
      - "9092:9092"
    volumes:
      - ./tools/mock_ansible.py:/app/mock.py
      - ./fixtures:/app/fixtures
    working_dir: /app
    command: sh -c "pip install flask && python mock.py"
    environment:
      - PROMETHEUS_URL=http://mock-prometheus:9091
      - ELK_URL=http://mock-elk:9093
    networks:
      - mocknet

  mock-elk:
    image: python:3.9-slim
    ports:
      - "9093:9093"
    volumes:
      - ./tools/mock_elk.py:/app/mock.py
      - ./fixtures:/app/fixtures
    working_dir: /app
    command: sh -c "pip install flask && python mock.py"
    networks:
      - mocknet

networks:
  mocknet:
EOF

echo "Stopping old mock backend services (if any)..."
docker compose -f docker-compose-mock.yml down

echo "Starting mock backend services..."
docker compose -f docker-compose-mock.yml up -d

echo "======================================"
echo "Deployment completed successfully!"
echo "Mock Prometheus:      http://localhost:9091"
echo "Mock Ansible:         http://localhost:9092"
echo "Mock ELK:             http://localhost:9093"
echo "======================================"
