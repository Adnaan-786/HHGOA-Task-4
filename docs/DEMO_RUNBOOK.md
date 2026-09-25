# Five-minute demo runbook

1. Double-click `run_hhgoa_demo.command`. Keep the Terminal window open. It starts the restricted TigerGraph MCP server, the FastAPI service, and the React dashboard, then opens the dashboard at `http://127.0.0.1:5173`.
2. Start screen recording only after the page shows the **Case queue**. This proves the application has loaded through the API rather than showing a static mockup.
3. Select `HHG-004` and click **Start investigation**. Show the fraud probability, evidence map, evidence citations, exposure, graph case ID, and read-only/simulated banner.
4. In **Controlled evidence request**, click **Customer denies**. Show the new revision, the changed recommendation, the preserved initial recommendation, and the revision trail.
5. Select an approval button for a recommended action. Show that the UI records approval while stating that execution remains simulated.
6. Select `HHG-014`, click **Start investigation**, and show a second case with related graph evidence. This demonstrates that the workflow handles an analyst-request trigger as well as a customer report.
7. End by showing the case queue and the `READ-ONLY PILOT · SIMULATED CONTROLS` banner. Stop the services with Ctrl-C in the launcher Terminal.

The recording should be 3–5 minutes, use the browser view and a readable zoom level, and avoid showing `.env`, terminal credentials, or raw source files. Upload it as an unlisted or public video and paste that URL into the submission form's Demo video field.

If the launcher says that the Savanna workspace is stopped, open the TigerGraph Savanna portal, start the workspace, wait for its status to become running, and launch the `.command` file again. Auto-start is disabled for this workspace, so the launcher deliberately stops with that message instead of opening a UI that cannot investigate cases.
