# Account setup for live integration

The application investigates the supplied benchmark data through a restricted TigerGraph MCP client. The Savanna graph schema, loaders, installed queries, reconciled counts, and case write/read-back path have been verified for the submitted workspace. The hosted OpenRouter assessment path is optional; policy decisions remain deterministic and a deterministic explanation fallback is used when a free router rejects strict structured output.

## Start here

Use the existing local project at `/Users/adnaan/Downloads/HHGOA`. A private `.env` file is the place for credentials; `.env.example` contains the field names. Keep real values out of chat, source files, screenshots, and frontend settings.

If `.env` does not exist, create it without replacing an existing file:

```sh
cd /Users/adnaan/Downloads/HHGOA
(umask 077; set -C; cat .env.example > .env)
```

An existing-file error means the file is already there and should be edited in place.

## OpenRouter model access

1. Sign in to https://openrouter.ai/ and create an API key from the account dashboard.
2. Open the local `.env` file and put the key after `OPENROUTER_API_KEY=`.
3. Keep `HHGOA_MODEL=openrouter/free` for the no-cost router, or set a specific free model slug after checking its structured-output support.
4. OpenRouter’s free tier has usage limits and its model/provider data policies apply. Do not send data you are not authorized to share with that service.

Official references: [OpenRouter quickstart](https://openrouter.ai/docs/quickstart), [structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs), and [free models](https://openrouter.ai/collections/free-models).

## TigerGraph Savanna

1. Sign in at https://savanna.tgcloud.io/. Use any hackathon workspace or credits supplied by the organizer before provisioning your own resources.
2. Create or select a workgroup and workspace. Choose region and capacity appropriate to the supplied dataset and confirm any charges in the portal yourself.
3. Open the workspace connection details and record its HTTPS **database endpoint** as `TG_HOST` in `.env`. Do not use the Savanna portal address as the database endpoint.
4. Create a database secret for the workspace database and put it in `TG_SECRET`. Savanna’s MCP setup uses this secret directly; the admin installer can also use it and mint a short-lived bearer token when needed. A Savanna organization/control-plane API key is different from a database secret. If you already have a bearer token, put it in `TG_API_TOKEN` instead. If you have database username/password access, the official MCP server also supports `TG_USERNAME` and `TG_PASSWORD`; do not put credentials in chat.
5. Keep `TG_TGCLOUD=true` and `TG_GRAPHNAME=HHGOAFraudGraph`. The supplied schema uses this isolated graph name and an `HHGOACard` namespace so it does not collide with sample graphs already present in a workspace.

MCP connection variables and authentication options: https://github.com/tigergraph/tigergraph-mcp

Only three values need to be filled initially:

```dotenv
OPENROUTER_API_KEY=your-openrouter-key
HHGOA_MODEL=openrouter/free
TG_HOST=https://your-workspace-database-host
TG_SECRET=your-database-secret
```

Keep `HHGOA_GRAPH_BACKEND=tigergraph`, `HHGOA_TIGERGRAPH_GRAPH_NAME=HHGOAFraudGraph`, and `HHGOA_READ_ONLY=true` for the live pilot. There is no need to provision AWS resources for this local API plus Savanna workspace setup.

For a durable local API, set `HHGOA_DATABASE_URL` to a PostgreSQL connection string, for example `postgresql+psycopg://hhgoa@localhost:55432/hhgoa`. If it is blank, the API deliberately uses process-local state for tests and demonstrations; revisions will not survive a restart.

## Local connection commands

The project already has its Python environment installed. The following commands use that environment from the project directory. Both the MCP server and application load `.env`; existing shell variables take precedence.

After credentials and the graph are ready, start the MCP server in a separate terminal:

```sh
.venv/bin/tigergraph-mcp --env-file .env \
  --transport streamable-http --host 127.0.0.1 --port 8001 \
  --allowed-tools run_installed_query
```

This serves `http://localhost:8001/mcp/`, matching `HHGOA_TIGERGRAPH_MCP_URL`. The application further restricts query names and arguments. Keep the MCP endpoint on loopback: the generic installed-query tool itself does not enforce the application's query-name allowlist. Do not enable arbitrary GSQL or generic vector tools in the investigation path.

Start the current local application API with:

```sh
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

These command-line options have been checked against the installed packages. They are not evidence of successful cloud authentication. Existing shell variables take precedence in these commands; restart processes after changing configuration.

## Integration work after account setup

The graph assets are in `graph/schema.gsql`, `graph/vectors.gsql`, `graph/loading.gsql`, and `graph/queries.gsql`. The source data has already been normalized and checked locally. Installing schema/loading data requires a separate administrative connection and a review of the intended target workspace; it must not run through the investigation agent.

The live sequence is complete for structured graph evidence: schema and vector attribute compiled, loaders installed, normalized files loaded, approved queries compiled, and Savanna counts reconciled. Document embeddings are not loaded yet, so vector retrieval remains optional until the embedding pipeline is added.

Once the connection values are present, the administrative installer can perform the first three steps without deleting an existing graph:

```sh
.venv/bin/python -m dotenv -f .env run --no-override -- \
  .venv/bin/hhgoa-tigergraph-admin --install

.venv/bin/python -m dotenv -f .env run --no-override -- \
  .venv/bin/hhgoa-tigergraph-admin --load
```

Review the GSQL response after each file. The loader uploads the normalized package in 100,000-row chunks, uses a two-billion-byte client limit, and performs no graph deletion. It also removes only malformed CSV-header artifacts created by the loader. The verified graph counts are recorded in `docs/IMPLEMENTATION_STATUS.md`.

After installation, start the restricted MCP server with `HHGOA_GRAPH_BACKEND=tigergraph`. The application runs the installed time-bounded context queries and verifies every case projection by read-back. A failed graph query fails the investigation rather than being relabeled as local graph evidence.

## What is verified before enabling live mode

- Authentication without logging credentials.
- Graph schema and counts, a time-bounded traversal, and vector retrieval.
- Explicit MCP tool and installed-query allowlists; no arbitrary GSQL exposed to the investigation agent.
- A separate case projection path with write/read-back verification.
- OpenRouter structured output and actual token accounting.

The default assessment model is `openrouter/free`; the application records the selected model and reported token count. Embeddings remain a separate future integration; no model calls, uploads, cloud provisioning, or account changes were performed while preparing this guide.

No customer communication, card control, or regulatory submission will execute in this pilot. Graph ingestion and case persistence are required application operations and remain separate from simulated banking actions.
