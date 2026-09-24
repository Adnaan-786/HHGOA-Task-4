# TigerGraph integration

`schema.gsql` defines the proposed graph shape. `vectors.gsql` adds the document
vector attribute. `loading.gsql` and `loading-map.json` map normalized source files
to loading jobs. `queries.gsql` defines the approved query boundary, including
vector retrieval through an installed query. These assets are compiled and
verified on the configured Savanna workspace as `HHGOAFraudGraph`. Document
vectors have not been populated yet.

The local backend uses `app.graph.LocalGraphStore` so the API and policy workflow
can be exercised without TigerGraph credentials. Set
`HHGOA_GRAPH_BACKEND=tigergraph` for the live pilot. The restricted MCP client
and versioned case projection adapter passed live traversal and exact read-back
verification; local calculations remain labeled separately from graph evidence.

Account configuration and the restricted MCP launch command are documented in
[`docs/SETUP.md`](../docs/SETUP.md). Schema installation and data loading belong
to a separate administrative connection, not the investigation agent.
