"""Sphinx configuration for care_im_wrapper.

Mirrors CARE core's own ``docs/conf.py`` (myst-parser + furo + autodoc/apidoc) so the
plugin's docs read as part of the same set. autodoc imports the package, which pulls in
``care``, so this must be built where CARE is installed -- see docs/README for the
one-line docker command.
"""

import os
import sys
from datetime import date
from pathlib import Path

import django

DOCS_DIR = Path(__file__).parent.resolve()
PACKAGE_ROOT = DOCS_DIR.parent
# The plugin is installed editable, but keep src/ first so a checkout wins over the venv.
sys.path.insert(0, str(PACKAGE_ROOT / "src"))
# CARE core itself, for `config.settings` below, when building from inside the care tree.
sys.path.insert(0, str(PACKAGE_ROOT.parent))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")
django.setup()

project = "care_im_wrapper"
copyright = f"{date.today().year}, Open Healthcare Network"  # noqa: A001, DTZ011
author = "ohcnetwork"

extensions = [
    "myst_parser",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.extlinks",
    "sphinx.ext.autodoc",
    "sphinx.ext.apidoc",
    "sphinx.ext.viewcode",
]

apidoc_modules = [
    {
        "path": "../src/care_im_wrapper",
        "destination": "reference/generated",
        "exclude_patterns": ["**/migrations*", "**/tests*"],
        "max_depth": 4,
        "follow_links": False,
        "separate_modules": True,
        "include_private": False,
        "no_headings": False,
        "module_first": True,
        "implicit_namespaces": False,
    },
]

autodoc_inherit_docstrings = False
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "undoc-members": False,
    "show-inheritance": True,
    "inherited-members": False,
    # models/__init__ re-exports its classes through __all__. Honouring that would
    # document each one twice -- once in the package, once in the defining module -- and
    # every :class:`NotificationTemplate` reference would then be ambiguous.
    "ignore-module-all": True,
}

extlinks = {
    "source": ("https://github.com/8sami/care_im_wrapper/blob/main/%s", "%s"),
    "issue": ("https://github.com/8sami/care_im_wrapper/issues/%s", "#%s"),
    "care-source": ("https://github.com/ohcnetwork/care/blob/develop/%s", "%s"),
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "**/tests*", "**/migrations*"]

add_function_parentheses = True
add_module_names = False
pygments_style = "trac"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "django": ("https://docs.djangoproject.com/en/stable/", None),
    "celery": ("https://docs.celeryq.dev/en/stable/", None),
}

myst_enable_extensions = ["colon_fence", "deflist"]
# Lets a heading be cross-referenced by name from another page.
myst_heading_anchors = 3

html_theme = "furo"
html_static_path = ["_static"]
html_title = "care_im_wrapper"
