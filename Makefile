# Dev tasks for the Cairn harness packages.
# Requires `uv` (https://docs.astral.sh/uv/).

VENV := .venv
PY := $(VENV)/bin/python
WORKSPACE ?= .

.PHONY: setup test serve mcp clean

setup:
	uv venv --python 3.11 $(VENV)
	uv pip install --python $(VENV) \
		-e "./core[convert,embeddings]" \
		-e ./mcp-server \
		-e ./cli \
		-e ./web \
		"mcp>=1.6.0" pytest
	@echo "Ready. Try:  make test   |   make serve WORKSPACE=~/Documents/notes"

test:
	$(PY) -m pytest core/tests cli/tests -q

serve:
	$(VENV)/bin/cairn --workspace $(WORKSPACE) serve

mcp:
	$(VENV)/bin/cairn --workspace $(WORKSPACE) mcp

clean:
	rm -rf $(VENV) **/__pycache__ **/*.egg-info .pytest_cache
