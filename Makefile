# NoiseREP developer shortcuts. Uses the project venv; override with:
#   make check PYTHON=/path/to/python
PYTHON ?= ./venv/bin/python

.PHONY: check test gui sync clean

check:            ## compile, tests, and CLI/decode/BLE/GUI smoke checks
	$(PYTHON) tools/check.py

test:             ## run the pytest suite
	$(PYTHON) -m pytest -q

gui:              ## launch the desktop app
	$(PYTHON) -m noiserep.cli gui

sync:             ## fetch from the band; e.g. make sync ARGS="--since 2026-10-01"
	$(PYTHON) -m noiserep.cli sync $(ARGS)

clean:            ## remove bytecode and pytest caches (not venv or data)
	rm -rf __pycache__ .pytest_cache
	find noiserep tests tools proto -type d -name __pycache__ -prune -exec rm -rf {} +
