.PHONY: check release test validate

OMARCHY ?= omarchy
PYTHON ?= python3

check: validate test

validate:
	@$(PYTHON) -m json.tool manifest.json >/dev/null
	@$(OMARCHY) plugin validate .

test:
	@$(PYTHON) -m py_compile recorder.py mix_audio.py
	@$(PYTHON) -m unittest discover -s tests -v
	@bash -n scripts/release.sh

release:
	@scripts/release.sh
