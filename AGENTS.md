# AGENTS.md

## Why this file looks the way it does

Generic style guides ("write clean code", "follow PEP 8") don't prevent the failures that actually
happen in practice: deleting a function still used elsewhere, silently dropping a branch of logic
while "cleaning up" a function, leaving your own uncertainty typed into the file as comments, or
claiming something is done without having checked. This file is written around preventing those
specific failure modes, not around restating Python style conventions you already know.

## Non-negotiable workflow rules

These apply to every change, no exceptions, regardless of how small the task seems.

### 1. Edit, don't regenerate.
When asked to change part of a function, produce a targeted diff against the existing code. Do not
rewrite the whole function from your own mental model of what it should look like — that is how
branches of logic silently disappear (a `NoDataError` raise, an edge-case check, an import) without
you ever deciding to remove them. If a function needs substantial restructuring, say so explicitly
and show what specifically changes and why, rather than quietly replacing it wholesale.

### 2. Grep before you delete or rename anything.
Before removing a function, class, setting key, or changing its name/signature: search the entire
repo for every usage. If you don't do this, you will delete something another file still imports and
find out from a linter later — or worse, not find out at all. This includes settings keys: check
`DEFAULTS`/`REQUIRED_SETTINGS` for the exact existing spelling before referencing or renaming one.
Never introduce a "new" setting name without confirming it doesn't already exist under a slightly
different spelling.

### 3. Never leave your own reasoning in the committed code.
If you're unsure how to solve part of a problem, resolve it before writing the file — don't write
sentences like "we need to figure out how to handle X" or "however, the instructions say Y, so let's
try Z" into comments and ship it. A comment should explain a non-obvious decision to a future reader,
never narrate your uncertainty while solving the task. If you genuinely cannot resolve something,
stop and say so in your response to the user — do not paper over it with a stub, a `pass`, a
placeholder return value, or a comment promising to finish it "next".

### 4. No stub code, ever, in a "done" response.
Placeholder function bodies, TODO-and-return-early paths for logic you were asked to implement, and
"minimal version for now" shortcuts are not acceptable substitutes for the actual implementation. If
a task is too large to finish, say which parts are incomplete explicitly — don't disguise incomplete
work as complete work.

### 5. Verify before claiming done — don't just read the code back.
After making changes, actually run the project's linter and type checker and read their full output,
not just the parts related to what you just touched — full-repo checks catch collateral damage.
Where the task's correctness depends on runtime behavior (a query behaving as expected, an exception
firing under a specific condition, a message being non-empty), trace that specific scenario
concretely — don't infer it's correct from having read the code once. If you claim a test/check
passed, you must have actually run it in this session, not assumed it from code review.

### 6. Proofread identifiers you type from memory.
Setting names, imported symbol names, log message strings — type them, then check them against the
actual source, not from memory. A single dropped letter (`TASK_RETR_DELAY_SECONDS`) or wrong word
(`pyint` instead of `pyright`) silently breaks things in ways that don't always show up as errors.

### 7. Report failures honestly, per item, not as a general "done".
When asked to verify a list of things, go through the list and state pass/fail for each one
individually, with how you checked it. "This should now work" is not a verification. If you could
not verify something (e.g. no way to run the app in this environment), say that explicitly instead of
implying it was checked.

## Pre-submit checklist (run through this before ending your turn on any code change)

- [ ] Did I grep for every usage of anything I deleted, renamed, or changed the signature of?
- [ ] Does every function I modified still contain every conditional branch the original had, unless
      I was explicitly asked to remove one (and if so, did I say so)?
- [ ] Is there any comment in my diff that narrates confusion, uncertainty, or a plan to finish later,
      rather than explaining a decision?
- [ ] Did I run the linter/type-checker on the full repo, not just the files I touched, and read the
      complete output?
- [ ] For any claim I'm about to make ("this now returns X", "this is cached", "this raises Y under
      Z condition") — did I actually trace or test that, or am I inferring it from having written the
      code?
- [ ] Did I check every identifier (setting key, imported name, exception class) against its actual
      definition rather than typing it from memory?

If any box is unchecked, do that step before responding — don't respond and mention it should be done
later.

---

## Project conventions (Django/Python)

### Core principles
- Django-native and Pythonic: `timezone.localtime()`, `select_related()`/`prefetch_related()`,
  `TextChoices`, Django's ORM — prefer these over hand-rolled equivalents.
- DRY: before writing new logic, search for whether it already exists elsewhere in the repo. Reuse
  it. Do not implement the same helper twice in two files (this has happened — watch for it
  specifically).
- Modular structure: keep app boundaries intact; don't reach across module boundaries to avoid a
  proper abstraction (e.g. don't import a whole app's internals to avoid a two-line shared helper).

### Views, models, forms
- Prefer function-based views for simple logic, class-based views for complex/reusable behavior.
- Business logic belongs in models/services, not views — views handle request/response, nothing more.
- Use Django's form/model-form validation rather than hand-rolled validation in views.
- Strict MVT separation.

### Error handling
- Handle errors at the view/handler level using Django's mechanisms; use signals to decouple
  cross-cutting error handling/logging from core business logic where appropriate.
- Customize error pages/responses for user-facing paths.
- When adding a new exception type, check whether an existing one already covers the case (see DRY
  above) before introducing another.

### Performance
- Use `select_related`/`prefetch_related` to avoid N+1 queries — when a data-fetching function is
  changed, re-check whether the query pattern still matches what downstream code actually accesses
  (e.g. adding a new field access in a loop without adding it to `select_related` reintroduces N+1 —
  this has happened, watch for it specifically when extending existing fetch functions).
- Use Django's cache framework (Redis/Memcached-backed) for expensive/frequently-repeated fetches.
  When changing what a cached function's result depends on (e.g. adding pagination), the cache key
  must be updated to include that new dependency, or you will silently serve stale/wrong-page data.
- Background/long-running work goes through Celery, not inline in request handling.

### Testing
- Django's `unittest`, run through `manage.py test` inside the backend container. Use the
  `Makefile`: `make test`, or `make test-one T=tests.test_api_notifications` for one module.
  The plugin imports `care`, so tests cannot run in the host `.venv` — that environment is
  Python 3.12 and exists only for `make lint` and `make typecheck`.
- Do not modify tests unless specifically asked to — cleanup and test-writing are separate passes.

### Security
- CSRF protection, ORM parameterization (avoid raw SQL unless justified), XSS prevention — Django's
  defaults handle most of this; don't bypass them without a stated reason.
- Never guess at authorization/permission logic. If a permission check's correctness is unclear,
  investigate the actual authorization model in use (read the relevant source) rather than inventing
  a plausible-sounding check — getting this wrong is a security bug, not a style issue.

### Dependencies
Django, Django REST Framework (APIs), Celery (background tasks), Redis (cache/queues),
PostgreSQL (CARE's database — there is no MySQL support to preserve).
