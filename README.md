# Unity Gateway model services, auto-upgraded by pull request

A Declarative Automation Bundle that defines one Unity Gateway model service per model class (`opus`, `sonnet`,
`haiku`, `luna`, `terra`, `sol`), each pinned to a model in `system.ai`, plus two GitHub workflows that keep the pins
current:

1. **`check-models`** (daily) looks for a newer servable version of each pinned model. If it finds any, it opens a PR
   that changes only the `model:` lines in `resources/model_services.yml`, or updates the PR it already opened.
   CODEOWNERS are asked to review, which is how the owner hears about it.
2. **`deploy`** runs on that PR (and any other): tests, `bundle deploy -t staging`, then a smoke test that sends a
   16-token request through every model service. On the bot's PRs, it starts once you click **Approve workflows to
   run**. Merging to `main` does the same against `prod`.

"Servable" matters because `system.ai` lists models that aren't served in every region. The gateway only rejects them
at request time ("not available in your region"). Before proposing a model, `check-models` sends it a test request
through a throwaway model service in the staging schema and deletes the service afterward. If the newest model fails,
it tries the next-newest.

## Configuration

The workspace isn't committed. It comes from `.env` when you run locally and from GitHub environment secrets in CI.
They're secrets rather than variables because GitHub prints variables in run logs, and run logs are public on a
public repo.

| What | Where |
|---|---|
| Which model classes exist | The blocks in `resources/model_services.yml`. To add one, copy a block, change the key and `model_service_id`, and pin any model in the family. |
| Workspace | `DATABRICKS_CONFIG_PROFILE` in `.env` locally. The `DATABRICKS_HOST` secret on each GitHub environment. |
| Destination catalog and schema | The `catalog` and `schema` variables per target in `databricks.yml`. The schema must already exist. |
| Who gets `EXECUTE` | The `grants` variable per target in `databricks.yml`. Staging and prod grant `account users`. It's the **complete** list: deploys remove grants made outside the bundle. Use account-level principals; workspace-local groups can't be used in Unity Catalog grants. |
| Who is notified | `.github/CODEOWNERS`. |
| How often to check | The cron in `.github/workflows/check-models.yml`. |

### How versions are detected

There's no list of model classes in code. The version is the one run of dash-separated numbers in the pinned name,
and the rest of the name is the family: `databricks-claude-opus-[5-5]`, `databricks-gpt-[6-1]-sol`,
`databricks-gpt-[5-4]-mini`. Versions compare part by part as numbers, so `6-1` > `6` > `5-6`. That handles
Anthropic's naming (version last) and OpenAI's (version before the tier) without special cases.

`check-models` leaves a service alone and lists it under "Not tracked" in its output when:

- its name has zero or several runs of numbers (e.g. `databricks-inkling`)
- it routes to anything other than exactly one pay-per-token `system.ai` model (e.g. a traffic split)

It never downgrades.

### Targets

- **dev**: a personal sandbox (`mode: development`). Model service names are *not* prefixed in development mode, so
  developers who share the dev schema overwrite each other's services. To use your own, set `BUNDLE_VAR_schema` (and
  `BUNDLE_VAR_catalog` if needed) in `.env`.
- **staging**: where PRs deploy, and where `check-models` creates its probes.
- **prod**: where merges to `main` deploy.

In staging and prod, `prevent_destroy` makes a deploy fail rather than delete a model service its callers depend on.

## One-time setup

1. **Schemas and grants.** Create the schemas. Set `catalog`, `schema`, and `grants` for each target in
   `databricks.yml`.
2. **Service principal.** Create one for CI and give it:
   - `USE_CATALOG` on the catalogs
   - `USE_SCHEMA` and `CREATE_SERVICE` on the staging and prod schemas
   - `EXECUTE` on the `system.ai` models (account users have this by default)

   It owns the model services it deploys. Pull requests deploy to staging with the same credentials, so anyone who
   can push a branch could also reach prod. If that matters, give prod its own service principal; only prod's
   secrets change.
3. **OAuth secret.** The workflows sign in with OAuth machine-to-machine; no personal access tokens. A workspace admin
   can create a secret for the service principal:

   ```bash
   databricks service-principal-secrets-proxy create <service-principal-id>
   ```

   Secrets expire (see `expire_time` in the output), so create a new one and update GitHub before then. If you're an
   account admin, you can drop secrets entirely: add a service principal federation policy for
   `repo:<owner>/<repo>:environment:<env>`, then set `DATABRICKS_AUTH_TYPE: github-oidc` and `id-token: write` in the
   workflows.
4. **GitHub environments `staging` and `prod`.** On each, set these secrets:
   - `DATABRICKS_HOST`
   - `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET`: the service principal

   Limit `prod` to the `main` branch (Deployment branches), so only merges deploy there. Add required reviewers on
   `prod` if you want a second gate after merge. Pull requests from forks get no secrets, so
   they only run the tests.
5. **Let the bot open PRs.** Turn on "Allow GitHub Actions to create and approve pull requests" (Settings > Actions >
   General). The bot uses the workflow's own `GITHUB_TOKEN`, so there's no extra credential. The trade-off: GitHub
   holds the checks on PRs opened that way until someone with write access clicks **Approve workflows to run** on the
   PR. To make them start on their own, open the PR with a GitHub App token instead.
6. **CODEOWNERS and branch protection.** List who reviews upgrade PRs in `.github/CODEOWNERS`. Protect `main`,
   requiring the `deploy` checks and code owner review.

### Adopting model services that already exist

If services with these names already exist in a target's schema, bind them before the first deploy. Otherwise the
deploy fails with "already exists":

```bash
databricks bundle deployment bind opus <catalog>.<schema>.opus -t <target>
```

## Develop

Local settings live in `.env`, which is gitignored. Copy the example and fill in your Databricks CLI profile:

```bash
cp .env.example .env
```

`uv run --env-file .env` loads it for any command, including the Databricks CLI:

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .

uv run --env-file .env databricks bundle deploy
uv run --env-file .env databricks bundle summary -o json > /tmp/summary.json
uv run --env-file .env smoke-test /tmp/summary.json

# What the daily workflow does, with probes in your dev schema. It edits resources/model_services.yml in place.
uv run --env-file .env check-models
```

These are the same commands the workflows run, so they're also how to test a workflow change without a GitHub runner.
Only opening the PR needs GitHub.

## Known limitations

- Closing an upgrade PR without merging doesn't stick: the next run proposes the same upgrade again. To hold a
  service back, stop tracking it, for example by giving it a second destination.
- Each check sends one 16-token request per newer candidate model. Once the pins are current, there are no candidates
  and no requests.

## Layout

- `databricks.yml`: targets and variables
- `.env.example`: local settings to copy into `.env`
- `resources/model_services.yml`: the model services; the bot edits only the `model:` lines
- `src/model_upgrades/versions.py`: reads the version and family out of a model name
- `src/model_upgrades/gateway.py`: test requests and throwaway probe services
- `src/model_upgrades/check.py`: the `check-models` command
- `src/model_upgrades/smoke_test.py`: the `smoke-test` command
- `.github/workflows/`: `check-models.yml` (daily PR) and `deploy.yml` (test, deploy, smoke test)
