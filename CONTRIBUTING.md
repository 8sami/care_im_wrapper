# Contributing

Contributions are welcome, and they are greatly appreciated! Every little bit helps, and credit will always be given.

You can contribute in many ways:

## Types of Contributions

### Report Bugs

Report bugs at https://github.com/8sami/care_im_wrapper/issues.

If you are reporting a bug, please include:

- Your operating system name and version.
- Any details about your local setup that might be helpful in troubleshooting.
- Detailed steps to reproduce the bug.

### Fix Bugs

Look through the GitHub issues for bugs. Anything tagged with "bug" and "help wanted" is open to whoever wants to implement it.

### Implement Features

Look through the GitHub issues for features. Anything tagged with "enhancement" and "help wanted" is open to whoever wants to implement it.

### Write Documentation

care_im_wrapper could always use more documentation, whether as part of the official docs, in docstrings, or even on the web in blog posts, articles, and such.

### Submit Feedback

The best way to send feedback is to file an issue at https://github.com/8sami/care_im_wrapper/issues.

If you are proposing a feature:

- Explain in detail how it would work.
- Keep the scope as narrow as possible, to make it easier to implement.
- Remember that this is a volunteer-driven project, and that contributions are welcome :)

## Get Started!

Ready to contribute? Here's how to set up `care_im_wrapper` for local development.

The plugin imports `care`, so it cannot run on its own. You need a working CARE checkout with
its Docker stack, and the plugin registered in that instance's `plug_config.py`. The full
procedure is in [docs/installation.md](docs/installation.md) — follow the *Local development*
section first, then come back here.

1. Fork the repo on GitHub and clone your fork **inside** your care checkout:

   ```sh
   cd care
   git clone git@github.com:your_name_here/care_im_wrapper.git
   ```

2. Register it in `plug_config.py` and install it editable, per
   [docs/installation.md](docs/installation.md). Then bring the stack up from the care root:

   ```sh
   make re-build
   make up
   ```

3. Create a virtualenv **in the plugin directory** for the linter and type checker. These run
   on the host rather than in the container, so they need their own environment:

   ```sh
   cd care_im_wrapper
   python -m venv .venv
   .venv/bin/pip install ruff basedpyright
   ```

4. Create a branch for local development:

   ```sh
   git checkout -b name-of-your-bugfix-or-feature
   ```

   Now you can make your changes locally.

5. When you're done, run the checks. They split across two environments, and the `Makefile`
   handles the difference for you:

   ```sh
   make lint       # ruff check + ruff format --check, from .venv
   make typecheck  # basedpyright, from .venv
   make test       # the full suite, inside the backend container
   ```

   Use `make lint-fix` to apply the fixable style issues automatically.

6. Commit your changes and push your branch to GitHub:

   ```sh
   git add .
   git commit -m "Your detailed description of your changes."
   git push origin name-of-your-bugfix-or-feature
   ```

7. Submit a pull request through the GitHub website.

## Where things run

The plugin's Python code targets 3.13, matching CARE core. Anything that imports `care` — the
tests, the docs build, coverage, the OpenAPI schema — must run inside the backend container,
which is where CARE and its dependencies are installed. The `Makefile` targets already point
at the right place:

| Target | Runs in | Notes |
| --- | --- | --- |
| `make lint`, `make lint-fix` | host `.venv` | ruff |
| `make typecheck` | host `.venv` | basedpyright |
| `make test` | backend container | the whole suite |
| `make test-one T=tests.test_api_notifications` | backend container | one module |
| `make coverage`, `make coverage-html` | backend container | |
| `make docs`, `make docs-open` | backend container | autodoc imports the package |
| `make schema` | backend container | writes `schema.yaml` |

> [!IMPORTANT]
> Two Makefiles are in play, and which directory you are in decides which one you get.
>
> - **`make up`, `make down`, `make re-build`** are CARE's, and run from the **care root**.
> - **Everything in the table above** is the plugin's, and runs from the **plugin directory**.
>
> The plugin's targets reach the stack through `docker compose -f ../docker-compose.yaml`, so
> that `..` only resolves correctly from `care/care_im_wrapper/`. The care root Makefile has
> no `docs`, `lint`, `typecheck` or `coverage` target at all, and its `test` runs CARE's own
> suite rather than the plugin's — so a mistaken directory fails loudly rather than doing the
> wrong thing quietly.

## Pull Request Guidelines

Before you submit a pull request, check that it meets these guidelines:

1. The pull request should include tests.
2. If the pull request adds functionality, update the relevant page under `docs/` and give the
   new code a docstring — the API reference is generated from docstrings by autodoc.
3. `make lint`, `make typecheck` and `make test` should all pass. There is no CI on this repo
   yet, so nothing will catch a failure for you.

## Tips

To run a single test module:

```sh
make test-one T=tests.test_api_notifications
```

To run one test case, extend the dotted path:

```sh
make test-one T=tests.test_api_notifications.TestNotificationEventCreate
```

## Releasing

The plugin is not published to PyPI. It is consumed directly from git by the CARE instance
that runs it, through `plug_config.py`:

```python
package_name="git+https://github.com/8sami/care_im_wrapper.git",
version="@v1.2.3",
```

So a release is a tag. Make sure your changes are committed, including an entry in
HISTORY.md, then:

```sh
git tag v1.2.3
git push --tags
```

Deployments pin `version` to that tag and rebuild the CARE image to pick it up.

## Code of Conduct

Please note that this project is released with a [Contributor Code of Conduct](CODE_OF_CONDUCT.md). By participating in this project you agree to abide by its terms.
