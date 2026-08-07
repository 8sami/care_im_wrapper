# Minimal makefile for Sphinx documentation.
#
# autodoc imports care_im_wrapper, which imports care, so this has to run where CARE is
# installed. From the care repo root:
#
#   docker compose exec -w /app/care_im_wrapper/docs backend make html
#
# Output lands in _build/html.

SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

.PHONY: help Makefile

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
