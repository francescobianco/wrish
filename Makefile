
PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
USER_SITE := $(shell $(PYTHON) -c "import site; print(site.getusersitepackages())")
USER_BIN := $(shell $(PYTHON) -c "import site; print(site.getuserbase() + '/bin')")

.PHONY: install develop check clean push

install:
	install -d "$(USER_SITE)" "$(USER_BIN)"
	rm -rf "$(USER_SITE)/wrish"
	cp -R wrish "$(USER_SITE)/wrish"
	printf '%s\n' '#!/usr/bin/env sh' 'exec "$(PYTHON)" -m wrish.cli "$$@"' > "$(USER_BIN)/wrish"
	chmod +x "$(USER_BIN)/wrish"

develop:
	$(PIP) install --user -e .

check:
	$(PYTHON) -m py_compile $$(find wrish -name '*.py' | sort)

clean:
	rm -rf build dist ./*.egg-info

push:
	@git config credential.helper 'cache --timeout=3600'
	@git add .
	@git commit -am "New release!" || true
	@git push
