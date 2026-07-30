# Compose v2 ships as `docker compose`; older installs only have `docker-compose`.
# Detected here so `make` works on either without editing anything.
DC := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
API_URL ?= http://localhost:8080

.PHONY: all stop evaluation fire-alert traffic logs restart status

all:
	$(DC) up --build -d
	@echo ""
	@echo "Stack starting. The API downloads the dataset and trains the model on first boot."
	@echo "  API        $(API_URL)/docs"
	@echo "  Prometheus http://localhost:9090"
	@echo "  Grafana    http://localhost:3000  (admin / admin)"

stop:
	$(DC) down

evaluation:
	$(DC) build evaluation
	$(DC) run --rm evaluation

# Triggers the ModelRMSEHigh and SevereDataDrift alerts by evaluating a
# deliberately corrupted batch. See SOLUTION.md for which alert is tested.
fire-alert:
	@echo "Sending a deliberately drifted batch to $(API_URL)/trigger-drift ..."
	@curl -s -X POST $(API_URL)/trigger-drift
	@echo ""
	@echo "Watch http://localhost:9090/alerts : ModelRMSEHigh and SevereDataDrift"
	@echo "go from inactive to pending to firing within about a minute."

traffic:
	bash scripts/generate_traffic.sh

logs:
	$(DC) logs -f --tail=100

status:
	$(DC) ps

restart: stop all
