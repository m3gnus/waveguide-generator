// Generated from docs/reference/openapi.v1.json.
// Run npm --prefix frontend run generate:api-types to update.
// Do not edit by hand.

export interface paths {
    "/api/acl-repair/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Acl Repair Status */
        get: operations["acl_repair_status_api_acl_repair_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cad-workspace/capture-document": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cad Workspace Capture Document */
        post: operations["cad_workspace_capture_document_api_cad_workspace_capture_document_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cad-workspace/open": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cad Workspace Open */
        post: operations["cad_workspace_open_api_cad_workspace_open_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cad-workspace/path": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Cad Workspace Path */
        get: operations["cad_workspace_path_api_cad_workspace_path_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cad-workspace/select": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cad Workspace Select */
        post: operations["cad_workspace_select_api_cad_workspace_select_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/delivery": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Delivery Status
         * @description The request consumer: running or not, why its last pass started nothing,
         *     when a pass last completed, and recent refusals of taken files (C4).
         */
        get: operations["get_delivery_status_api_cadlink_delivery_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/designs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Designs
         * @description Expose recent CAD-linked projects as a local project picker.
         *
         *     Each lineage contributes only its newest head and reports the archive
         *     folder its runs and captured CAD documents share, so a project can be
         *     opened, revealed and counted from one listing rather than from three that
         *     could disagree.
         */
        get: operations["list_designs_api_cadlink_designs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/designs/{design_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Design
         * @description Return the registry's exact current snapshot for one linked design.
         */
        get: operations["get_design_api_cadlink_designs__design_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/domain-interpretation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Domain Interpretation
         * @description How this snapshot's domain was read, the readings Change offers, and any pending Change.
         */
        get: operations["get_domain_interpretation_api_cadlink_domain_interpretation_get"];
        /**
         * Put Domain Interpretation
         * @description Change: remember the user's reading of this model's domain for its lineage.
         */
        put: operations["put_domain_interpretation_api_cadlink_domain_interpretation_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/fusion-status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Fusion Status
         * @description Report whether the design on screen is open and current in Fusion.
         */
        post: operations["fusion_status_api_cadlink_fusion_status_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/ingest": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Post Ingest */
        post: operations["post_ingest_api_cadlink_ingest_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/ingest/{ingest_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Ingest */
        get: operations["get_ingest_api_cadlink_ingest__ingest_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/ingest/{ingest_id}/mesh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Ingest Mesh
         * @description Serve the exact ingested solve mesh for diagnostics and fallback.
         */
        get: operations["get_ingest_mesh_api_cadlink_ingest__ingest_id__mesh_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/ingest/{ingest_id}/viewport-mesh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Ingest Viewport Mesh
         * @description Serve the independently tessellated full-domain CAD display artifact.
         */
        get: operations["get_ingest_viewport_mesh_api_cadlink_ingest__ingest_id__viewport_mesh_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/install-addin": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Install Addin
         * @description Record the explicit choice even if activation must wait or cannot install.
         */
        post: operations["post_install_addin_api_cadlink_install_addin_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/deliveries": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Live Delivery
         * @description Accept or recover one delivered operation once its snapshot is retained.
         */
        post: operations["post_live_delivery_api_cadlink_live_deliveries_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/endpoint": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Live Endpoint Hello
         * @description Which WG start answers here. Never the secret.
         */
        get: operations["live_endpoint_hello_api_cadlink_live_endpoint_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/heartbeat": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Live Heartbeat
         * @description The add-in's heartbeat for its live session; 204 when recorded.
         */
        post: operations["post_live_heartbeat_api_cadlink_live_heartbeat_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Poll Fusion Requests
         * @description The Fusion-bound requests this session may claim; waits for one when none.
         */
        get: operations["poll_fusion_requests_api_cadlink_live_requests_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/requests/{operationId}/claim": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Claim Fusion Request
         * @description Take one offered request; the first claim by either transport wins.
         */
        post: operations["claim_fusion_request_api_cadlink_live_requests__operationId__claim_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/requests/{operationId}/complete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Complete Fusion Request
         * @description Record the claimed request's outcome, mapped as the heartbeat maps it.
         */
        post: operations["complete_fusion_request_api_cadlink_live_requests__operationId__complete_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/requests/{operationId}/progress": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Fusion Request Progress
         * @description Record that Fusion queued, then began executing, the claimed request.
         */
        post: operations["fusion_request_progress_api_cadlink_live_requests__operationId__progress_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Register Live Session
         * @description Register the add-in after it proves it read this start's secret.
         */
        post: operations["register_live_session_api_cadlink_live_sessions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/sessions/current": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * End Live Session
         * @description End the session at once; the add-in continues with file delivery.
         */
        delete: operations["end_live_session_api_cadlink_live_sessions_current_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/live/sessions/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Refresh Live Session
         * @description A new token for the same session; the old one stays valid for 30 s.
         *
         *     Only the current token refreshes; a token in its grace window is refused.
         */
        post: operations["refresh_live_session_api_cadlink_live_sessions_refresh_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/onshape/connection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Connection
         * @description Identify the account the key pair authenticates as, and its plan.
         *
         *     Cached, because this is the only route that spends rate limit on something
         *     the user did not explicitly ask for.
         */
        get: operations["connection_api_cadlink_onshape_connection_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/onshape/return": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Return To Wg
         * @description Export the linked Part Studio, create wgreturn 1.1, and ingest it.
         */
        post: operations["return_to_wg_api_cadlink_onshape_return_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/onshape/send": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Send
         * @description Export the design as a wglink bundle and materialise it in Onshape.
         */
        post: operations["send_api_cadlink_onshape_send_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/onshape/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Status
         * @description Decide linked / current / stale for the design on screen. No network.
         */
        post: operations["status_api_cadlink_onshape_status_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/onshape/unlink": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Unlink
         * @description Forget a link. The Onshape document is left exactly as it is.
         */
        post: operations["unlink_api_cadlink_onshape_unlink_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/operations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Cad Operations
         * @description Unfinished operations, deriving solve state from their authoritative jobs.
         */
        get: operations["list_cad_operations_api_cadlink_operations_get"];
        put?: never;
        /**
         * Post Cad Operation
         * @description Create or recover a manual solve from an immutable retained CAD ingest.
         */
        post: operations["post_cad_operation_api_cadlink_operations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/operations/{operation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Cad Operation */
        get: operations["get_cad_operation_api_cadlink_operations__operation_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/operations/{operation_id}/approvals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Cad Operation Approvals
         * @description Acknowledge blocking findings of one preparation. A new preparation needs its own.
         *
         *     The next preparation of the same snapshot and setup revision resumes that
         *     preparation, so these approvals apply to it.
         */
        post: operations["post_cad_operation_approvals_api_cadlink_operations__operation_id__approvals_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/operations/{operation_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Cancel Cad Operation
         * @description Dismiss an operation: at once when idle, at the next step when running.
         *
         *     A solve is reconciled with the jobs store first: one whose job already
         *     exists follows the job, and one that may have a job is not dismissed
         *     while the jobs store cannot be read (409).
         */
        post: operations["post_cancel_cad_operation_api_cadlink_operations__operation_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/operations/{operation_id}/prepare": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Prepare Cad Operation
         * @description Compatibility Solve: create the first intent, or continue its refused job.
         */
        post: operations["post_prepare_cad_operation_api_cadlink_operations__operation_id__prepare_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/operations/{operation_id}/reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Reconcile Cad Operation
         * @description Re-read Fusion evidence for an unsettled WG-produced request.
         */
        post: operations["post_reconcile_cad_operation_api_cadlink_operations__operation_id__reconcile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/project-setups": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Put Project Setup
         * @description Record a project's solve settings for its sources (CAD-OPERATIONS.md, "Project setups").
         *
         *     A solve Fusion sends for that project is prepared from them, whatever
         *     project the editor has open. The latest recording is the project's.
         */
        put: operations["put_project_setup_api_cadlink_project_setups_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/projects/{lineage_id}/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Project Documents
         * @description The captured CAD documents this project's runs were solved from.
         */
        get: operations["list_project_documents_api_cadlink_projects__lineage_id__documents_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/projects/{lineage_id}/documents/{return_state_hash}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Download Project Document
         * @description Hand back the Fusion document one geometry version was captured from.
         */
        get: operations["download_project_document_api_cadlink_projects__lineage_id__documents__return_state_hash__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/projects/{lineage_id}/reveal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reveal Project Folder
         * @description Open this project's archive folder in the desktop file manager.
         */
        post: operations["reveal_project_folder_api_cadlink_projects__lineage_id__reveal_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/request-fusion-return": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Request Fusion Return
         * @description Ask the connected add-in to export the active Fusion document to WG.
         */
        post: operations["request_fusion_return_api_cadlink_request_fusion_return_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/returns": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Returns */
        get: operations["list_returns_api_cadlink_returns_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/runs/archive-document": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Archive Run Document
         * @description File a run's CAD document beside the run, when the mode asks for it.
         *
         *     Advisory throughout: the run archive is already written by the time this is
         *     called, and a missing convenience copy must never make a good run look
         *     failed. But it must not go missing quietly either -- the answer says why a
         *     copy is absent and whether asking again can still produce it, and the
         *     caller reports what it could not file.
         */
        post: operations["archive_run_document_api_cadlink_runs_archive_document_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/setup-revisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Setup Revision
         * @description Store an immutable setup revision; identical content is one revision.
         */
        post: operations["post_setup_revision_api_cadlink_setup_revisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/setup-revisions/{revision_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Setup Revision */
        get: operations["get_setup_revision_api_cadlink_setup_revisions__revision_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/solve-command": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Solve Command
         * @description Nothing pending, always: the backend is the one consumer of solve commands.
         *
         *     A page from a build before the backend owned solves (v0.3.2,
         *     v0.3.3-rc.1) polls this route and acts on what it hands out, and it can
         *     still be open in a browser tab across an update restart. This answer is
         *     the one it reads as "nothing pending": the route claims no delivery,
         *     records no outcome and hands out no command such a page could start a
         *     solve from (docs/architecture/CAD-OPERATIONS.md, "Solve-command
         *     compatibility").
         */
        get: operations["get_solve_command_api_cadlink_solve_command_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/solve-command/outcome": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Post Solve Command Outcome
         * @description Answered and ignored: a solve command's outcome is the backend's to record.
         *
         *     The same older page reports here after its own Solve or Dismiss. A 2xx
         *     answer lets it drop its copy; an error would leave its Dismiss card stuck,
         *     or report a failure after a job that exists. Nothing is recorded: a job
         *     such a page submitted under ``cad-solve:<commandId>`` is the operation's
         *     outcome, and the backend finds it through that key.
         */
        post: operations["post_solve_command_outcome_api_cadlink_solve_command_outcome_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/solver-frame": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Solver Frame
         * @description Every solver frame axis's matrix for this snapshot, and what its project confirmed.
         */
        get: operations["get_solver_frame_api_cadlink_solver_frame_get"];
        /**
         * Put Solver Frame
         * @description Confirm the axis this snapshot's project radiates along.
         */
        put: operations["put_solver_frame_api_cadlink_solver_frame_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cadlink/solver-selection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Put Solver Selection
         * @description Record WG's shared solve choice for backend-prepared CAD operations.
         */
        put: operations["put_solver_selection_api_cadlink_solver_selection_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/capabilities": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Capabilities */
        get: operations["capabilities_api_capabilities_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/design/import-report": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Import Report Endpoint */
        post: operations["import_report_endpoint_api_design_import_report_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/design/open": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Endpoint */
        post: operations["open_endpoint_api_design_open_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/design/save": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Save Endpoint */
        post: operations["save_endpoint_api_design_save_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/design/serialize": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Serialize Endpoint */
        post: operations["serialize_endpoint_api_design_serialize_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/design/symmetry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Design Symmetry */
        post: operations["design_symmetry_api_design_symmetry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/diagnostics/bundle": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Download Bundle */
        get: operations["download_bundle_api_diagnostics_bundle_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/diagnostics/client-log": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Record Client Error */
        post: operations["record_client_error_api_diagnostics_client_log_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/diagnostics/open-logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Logs */
        post: operations["open_logs_api_diagnostics_open_logs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/diagnostics/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Summary */
        get: operations["read_summary_api_diagnostics_summary_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/drivers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Search Drivers */
        get: operations["search_drivers_api_drivers_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/drivers/library": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Library Info */
        get: operations["library_info_api_drivers_library_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/drivers/library/rescan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Library Rescan */
        post: operations["library_rescan_api_drivers_library_rescan_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/drivers/{driver_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Driver */
        get: operations["get_driver_api_drivers__driver_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/export/profiles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Export Profiles */
        post: operations["export_profiles_api_export_profiles_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/export/step": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Export Step
         * @description Export STEP. ``solid`` is the manufacturable part; ``surface`` the bore.
         */
        post: operations["export_step_api_export_step_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/export/stl": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Export Stl */
        post: operations["export_stl_api_export_stl_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/export/wglink": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Export Wglink
         * @description Write an identity-bearing CAD-link bundle into the selected workspace.
         */
        post: operations["export_wglink_api_export_wglink_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/integration/v1/design-schema": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Design Schema */
        get: operations["design_schema_api_integration_v1_design_schema_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/integration/v1/parameters": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Parameter Catalog */
        get: operations["parameter_catalog_api_integration_v1_parameters_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Jobs */
        get: operations["list_jobs_api_jobs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/cad-solve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Cad Solve
         * @description Accept the displayed CAD Solve press, or recover its original job.
         */
        post: operations["cad_solve_api_jobs_cad_solve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/clear-failed": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Clear Failed */
        delete: operations["clear_failed_api_jobs_clear_failed_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Job */
        delete: operations["delete_job_api_jobs__job_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{job_id}/approvals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Cad Approvals
         * @description Record blocking findings on this refused job's exact preparation.
         */
        post: operations["cad_approvals_api_jobs__job_id__approvals_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{job_id}/archive-snapshot": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Job Archive Snapshot
         * @description Return one retention-consistent set of permanent-archive inputs.
         */
        get: operations["job_archive_snapshot_api_jobs__job_id__archive_snapshot_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{job_id}/dismiss": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Dismiss Cad Job
         * @description Dismiss a refused CAD solve with its refused ancestors, as one change.
         *
         *     Deleting the jobs one by one could leave a parent a reconnect revives.
         */
        post: operations["dismiss_cad_job_api_jobs__job_id__dismiss_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{job_id}/log": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Job Log */
        get: operations["job_log_api_jobs__job_id__log_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{job_id}/metadata": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Patch Job Metadata */
        patch: operations["patch_job_metadata_api_jobs__job_id__metadata_patch"];
        trace?: never;
    };
    "/api/jobs/{job_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Job */
        post: operations["retry_job_api_jobs__job_id__retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{job_id}/solve-again": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Solve Again
         * @description Capture a waiting first press, or continue a refused CAD preparation.
         */
        post: operations["solve_again_api_jobs__job_id__solve_again_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/mesh-artifact/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Mesh Artifact */
        get: operations["mesh_artifact_api_mesh_artifact__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/partial-results/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Partial Job Results
         * @description Process-local correctness path for a dropped live-result delta.
         */
        get: operations["partial_job_results_api_partial_results__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/pressure-basis/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Pressure Basis Artifact */
        get: operations["pressure_basis_artifact_api_pressure_basis__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/radiation-impedance/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Radiation Impedance Artifact */
        get: operations["radiation_impedance_artifact_api_radiation_impedance__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/radiation-impedance/{job_id}/presentation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Radiation Impedance Presentation */
        get: operations["radiation_impedance_presentation_api_radiation_impedance__job_id__presentation_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/render-charts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Render Charts */
        post: operations["render_charts_api_render_charts_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/render-directivity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Render Directivity */
        post: operations["render_directivity_api_render_directivity_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/results/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Job Results */
        get: operations["job_results_api_results__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/results/{job_id}/combine": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Recombine Job Results
         * @description Recompute the combined channel from stored bases without re-solving.
         */
        post: operations["recombine_job_results_api_results__job_id__combine_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/results/{job_id}/field-plane": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Field Plane
         * @description Evaluate one exterior complex-pressure grid from retained traces.
         */
        post: operations["field_plane_api_results__job_id__field_plane_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Settings */
        get: operations["read_settings_api_settings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/settings/{namespace}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Write Namespace */
        put: operations["write_namespace_api_settings__namespace__put"];
        post?: never;
        /** Delete Namespace */
        delete: operations["delete_namespace_api_settings__namespace__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/solve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Submit Solve */
        post: operations["submit_solve_api_solve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/solve/imported-plan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Plan Imported Solve
         * @description Every engine's verdict on one ingested CAD return, without a job.
         *
         *     The solver selector reads this for imported geometry: the same
         *     per-engine capability ``POST /api/solve`` resolves with, keyed by the
         *     request's ``ingest_id``. A request the ingestion record itself refuses
         *     (unknown ingest, mismatched hashes, an unsupported cut set) answers 422.
         */
        post: operations["plan_imported_solve_api_solve_imported_plan_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/solve/plan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Plan Solve
         * @description Resolve the submitted design without allocating or persisting a job.
         */
        post: operations["plan_solve_api_solve_plan_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/solver-mesh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Solver Mesh */
        post: operations["solver_mesh_api_solver_mesh_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/status/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Job Status */
        get: operations["job_status_api_status__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/stop/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Stop Job */
        post: operations["stop_job_api_stop__job_id__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/theme-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Theme Preview */
        get: operations["theme_preview_api_theme_preview_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/themes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Themes */
        get: operations["themes_api_themes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/channel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Update Channel */
        get: operations["update_channel_api_updates_channel_get"];
        /** Choose Update Channel */
        put: operations["choose_update_channel_api_updates_channel_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/diagnostics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Update Diagnostics */
        get: operations["update_diagnostics_api_updates_diagnostics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/install": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Install Update */
        post: operations["install_update_api_updates_install_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/reset": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reset Installer Download */
        post: operations["reset_installer_download_api_updates_reset_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Held Back Build */
        post: operations["retry_held_back_build_api_updates_retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Update Status */
        get: operations["update_status_api_updates_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspace/export-destination": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Export Destination */
        get: operations["export_destination_api_workspace_export_destination_get"];
        put?: never;
        /**
         * Choose Export Destination
         * @description Ask the user where this export goes, without moving the workspace.
         *
         *     Cancelling answers ``selected: false`` and writes nothing -- neither a
         *     file nor the remembered folder, which only a completed export moves.
         */
        post: operations["choose_export_destination_api_workspace_export_destination_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspace/open": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Workspace Open */
        post: operations["workspace_open_api_workspace_open_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspace/path": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Workspace Path */
        get: operations["workspace_path_api_workspace_path_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspace/select": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Workspace Select */
        post: operations["workspace_select_api_workspace_select_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workspace/write-export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Workspace Write Export */
        post: operations["workspace_write_export_api_workspace_write_export_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * ArchiveRunDocumentRequest
         * @description Where the caller wrote the run folder this document belongs beside.
         *
         *     The run-folder naming rule lives in the frontend, which has just used it to
         *     write the folder; asking the server to re-derive it would be a third
         *     implementation of one name rule and a third chance for them to disagree.
         */
        ArchiveRunDocumentRequest: {
            /** Archivestem */
            archiveStem: string;
            /** Returnstatehash */
            returnStateHash: string;
            /** Runstem */
            runStem: string;
            /** Subdirectory */
            subdirectory: string;
        };
        /** Body_choose_update_channel_api_updates_channel_put */
        Body_choose_update_channel_api_updates_channel_put: {
            /** Channel */
            channel: string;
        };
        /** CadApprovalsRequest */
        CadApprovalsRequest: {
            /** Finding Ids */
            finding_ids: string[];
            /** Preparation Id */
            preparation_id: string;
        };
        /**
         * CadDriveChannelIdentity
         * @description The submitted channel address resolved against immutable return sources.
         */
        CadDriveChannelIdentity: {
            /** Drive Channel Id */
            drive_channel_id: string;
            /** Instance Ids */
            instance_ids?: string[];
            /** Source Ids */
            source_ids: string[];
        };
        /**
         * CadIdentityInstance
         * @description CAD-authored addresses for one linked placement in an imported run.
         */
        CadIdentityInstance: {
            /** Assembly From Link */
            assembly_from_link: number[][];
            /** Body Object Ids */
            body_object_ids?: string[];
            /** Default Drive Channel Ids */
            default_drive_channel_ids?: string[];
            /** Design Id */
            design_id?: string | null;
            /** Instance Id */
            instance_id: string;
            /** Source Ids */
            source_ids?: string[];
        };
        /**
         * CadIdentityProvenance
         * @description Versioned CAD placement/body/source/drive graph retained with a run.
         */
        CadIdentityProvenance: {
            /** Drive Channels */
            drive_channels: components["schemas"]["CadDriveChannelIdentity"][];
            /** Ingest Id */
            ingest_id: string;
            /** Instances */
            instances?: components["schemas"]["CadIdentityInstance"][];
            /**
             * Schema Version
             * @constant
             */
            schema_version: 1;
            /** Selected Instance Id */
            selected_instance_id?: string | null;
            /** Solver Anchor Instance Id */
            solver_anchor_instance_id?: string | null;
        };
        /** CadOperationSnapshotSummary */
        CadOperationSnapshotSummary: {
            /** Bundlepath */
            bundlePath?: string | null;
            /** Documentname */
            documentName: string | null;
            /** Manifestsha256 */
            manifestSha256: string | null;
            /** Projectlineageid */
            projectLineageId: string | null;
        };
        /** CadOperationSummary */
        CadOperationSummary: {
            /** Acceptedseq */
            acceptedSeq: number | null;
            /** Attemptgeneration */
            attemptGeneration: number;
            /** Createdat */
            createdAt: string | null;
            /** Frameaxisautomatic */
            frameAxisAutomatic?: string | null;
            /** Jobid */
            jobId: string | null;
            /** Kind */
            kind: string;
            /** Legacy */
            legacy: boolean;
            /** Message */
            message: string | null;
            /** Operationid */
            operationId: string;
            /** Preparationid */
            preparationId: string | null;
            /** Reason */
            reason: string | null;
            /**
             * Setupdefaults
             * @default false
             */
            setupDefaults?: boolean;
            /** Setuprevisionid */
            setupRevisionId: string | null;
            snapshot: components["schemas"]["CadOperationSnapshotSummary"] | null;
            /** Stage */
            stage: string | null;
            /** State */
            state: string;
            /** Updatedat */
            updatedAt: string | null;
        };
        /** CadReturnIngestRequest */
        CadReturnIngestRequest: {
            /** Areadriftoverrides */
            areaDriftOverrides?: string[];
            /**
             * Bundleorigin
             * @default wglink
             * @enum {string}
             */
            bundleOrigin?: "wglink" | "onshape";
            /** Bundlepath */
            bundlePath: string;
            /** Expecteddesignid */
            expectedDesignId?: string | null;
            /** Expectedinstanceid */
            expectedInstanceId?: string | null;
            mesh: components["schemas"]["ImportedMeshRequest"];
            /** Skippedsourceids */
            skippedSourceIds?: string[];
            /** Surfacedeviationmm */
            surfaceDeviationMm?: number | null;
            /**
             * Symmetrymode
             * @default auto
             * @enum {string}
             */
            symmetryMode?: "auto" | "full";
        };
        /** CadSolveAgainRequest */
        CadSolveAgainRequest: {
            approvals?: components["schemas"]["CadApprovalsRequest"] | null;
            /** Frame Axis */
            frame_axis?: string | null;
            /** Setup Revision Id */
            setup_revision_id?: string | null;
            /**
             * Submit
             * @default true
             */
            submit?: boolean;
        };
        /** CadSolveRequest */
        CadSolveRequest: {
            approvals?: components["schemas"]["CadApprovalsRequest"] | null;
            /**
             * Client Request Id
             * @description Stable manual Solve id; an exact ingest replay recovers its original job.
             */
            client_request_id: string;
            /** Frame Axis */
            frame_axis?: string | null;
            /** Ingest Id */
            ingest_id: string;
            /** Label */
            label?: string | null;
            /** Setup Revision Id */
            setup_revision_id?: string | null;
            /**
             * Submit
             * @default true
             */
            submit?: boolean;
        };
        /**
         * CadSource
         * @description Where an imported run came from, kept for the run archive.
         *
         *     A CAD run used to be traceable only through the ingestion record, which is
         *     addressed by content and says nothing about which document a person opened.
         */
        CadSource: {
            /** Archive Stem */
            archive_stem: string | null;
            /** Design Id */
            design_id: string | null;
            /** Document Name */
            document_name: string | null;
            /** Domain Decision */
            domain_decision?: {
                [key: string]: unknown;
            } | null;
            identity?: components["schemas"]["CadIdentityProvenance"] | null;
            /** Ingest Id */
            ingest_id: string | null;
            /** Lineage Id */
            lineage_id: string | null;
            /** Manifest Sha256 */
            manifest_sha256: string | null;
            /** Return State Hash */
            return_state_hash: string | null;
            /** Solve Model Sha256 */
            solve_model_sha256?: string | null;
            /** Transformed Geometry Hash */
            transformed_geometry_hash?: string | null;
        };
        /**
         * CadState
         * @description The CAD solve's state, derived entirely from this job's record.
         */
        CadState: {
            /** Approvals */
            approvals: components["schemas"]["CadStateApproval"][];
            /** Frame Axis Automatic */
            frame_axis_automatic: string | null;
            /** Job Id */
            job_id: string | null;
            /** Message */
            message: string | null;
            /** Operation Id */
            operation_id: string | null;
            preparation: components["schemas"]["CadStatePreparation"] | null;
            /** Reason */
            reason: string | null;
            /** Received At */
            received_at: string | null;
            /** Setup Defaults */
            setup_defaults: boolean;
            snapshot: components["schemas"]["CadStateSnapshot"] | null;
            /** Stage */
            stage: string | null;
            /** State */
            state: string;
            /** Updated At */
            updated_at: string | null;
        };
        /** CadStateApproval */
        CadStateApproval: {
            /** Finding Id */
            finding_id: string;
            /** Preparation Id */
            preparation_id: string;
        };
        /** CadStatePreparation */
        CadStatePreparation: {
            /** Blocking Finding Ids */
            blocking_finding_ids: string[];
            /** Ingest Id */
            ingest_id: string | null;
            /** Preparation Id */
            preparation_id: string;
            /** Report Sha256 */
            report_sha256: string | null;
        };
        /** CadStateSnapshot */
        CadStateSnapshot: {
            /** Artifact Sha256 */
            artifact_sha256: string | null;
            /** Document Name */
            document_name: string | null;
            /** Manifest Sha256 */
            manifest_sha256: string | null;
            /** Project Lineage Id */
            project_lineage_id: string | null;
        };
        /** CaptureDocumentRequest */
        CaptureDocumentRequest: {
            /** Enabled */
            enabled?: boolean | null;
            /** Mode */
            mode?: ("off" | "project" | "run") | null;
        };
        /** CatalogCondition */
        CatalogCondition: {
            /** Conditions */
            conditions?: components["schemas"]["CatalogCondition"][];
            /** Operator */
            operator: string;
            /** Path */
            path?: string | null;
            value?: components["schemas"]["JsonValue"] | null;
        };
        /** CatalogEditorBounds */
        CatalogEditorBounds: {
            /** Maximum */
            maximum: number;
            /** Minimum */
            minimum: number;
        };
        /** CatalogOption */
        CatalogOption: {
            /** Degraded Label */
            degraded_label: string | null;
            /** Degraded Without */
            degraded_without: string | null;
            /** Label */
            label: string;
            /** Requires Feature */
            requires_feature: string | null;
            value: components["schemas"]["JsonValue"];
        };
        /** CatalogValidationAuthority */
        CatalogValidationAuthority: {
            /** Design Schema */
            design_schema: string;
            /** Note */
            note: string;
            /** Request Schema */
            request_schema: string;
            /** Validate Cli */
            validate_cli: string;
        };
        /**
         * ChannelCombineSpec
         * @description Filtered time-aligned sum of drive-channel bases.
         *
         *     ``members`` is the chain in band order, lowest first. Two spec forms are
         *     accepted (CADLINK-CROSSOVER-DRIVERS.md §2): the per-channel ``channels``
         *     map, and the legacy ``crossovers_hz``/``level_match``/``align`` triple,
         *     which means an LR4 chain with auto gain and auto delay. ``resolved()`` is
         *     the single place that turns the legacy form into the other one, so the
         *     solver only ever sees one shape. Structural defects refuse at submission;
         *     solve-time observations become metadata warnings, never silent skips.
         */
        ChannelCombineSpec: {
            /**
             * Align
             * @default true
             */
            align?: boolean;
            /** Channels */
            channels?: {
                [key: string]: components["schemas"]["ChannelFilterSpec"];
            } | null;
            /** Crossovers Hz */
            crossovers_hz?: number[] | null;
            /**
             * Id
             * @default combined
             */
            id?: string;
            /**
             * Level Match
             * @default true
             */
            level_match?: boolean;
            /** Members */
            members: string[];
            /** Reference */
            reference?: string | null;
        };
        /**
         * ChannelFilterSpec
         * @description Everything one member of the chain owns: band, level, delay, polarity.
         */
        ChannelFilterSpec: {
            delay?: components["schemas"]["DelaySpec"];
            gain?: components["schemas"]["GainSpec"];
            hp?: components["schemas"]["FilterSpec"] | null;
            /** Invert */
            invert?: boolean | null;
            lp?: components["schemas"]["FilterSpec"] | null;
        };
        /**
         * ChannelSolveExecution
         * @description Execution identity emitted for each solved channel.
         */
        ChannelSolveExecution: {
            /**
             * Accuracy
             * @enum {string}
             */
            accuracy: "fast" | "accurate";
            /** Engine */
            engine: string;
            /** Formulation */
            formulation: string | null;
        };
        /** ChartsReferencePayload */
        ChartsReferencePayload: {
            /** Beam Shape */
            beam_shape?: {
                [key: string]: unknown;
            } | null;
            /** Di */
            di?: (number | null)[] | {
                [key: string]: unknown;
            };
            /** Di Frequencies */
            di_frequencies?: number[];
            /** Frequencies */
            frequencies?: number[];
            /** Impedance Frequencies */
            impedance_frequencies?: number[];
            /** Impedance Imaginary */
            impedance_imaginary?: (number | null)[];
            /** Impedance Normalization */
            impedance_normalization?: string | null;
            /** Impedance Real */
            impedance_real?: (number | null)[];
            /** Impedance Units */
            impedance_units?: string | null;
            /** Label */
            label?: string | null;
            /** Sound Speed M Per S */
            sound_speed_m_per_s?: number | null;
            /** Spl */
            spl?: (number | null)[];
        };
        /** ChartsRenderRequest */
        ChartsRenderRequest: {
            /** Beam Shape */
            beam_shape?: {
                [key: string]: unknown;
            } | null;
            /** Di */
            di?: (number | null)[] | {
                [key: string]: unknown;
            };
            /** Di Frequencies */
            di_frequencies?: number[];
            /** Directivity */
            directivity?: {
                [key: string]: unknown;
            };
            /** Frequencies */
            frequencies?: number[];
            /** Impedance Frequencies */
            impedance_frequencies?: number[];
            /** Impedance Imaginary */
            impedance_imaginary?: (number | null)[];
            /** Impedance Normalization */
            impedance_normalization?: string | null;
            /** Impedance Real */
            impedance_real?: (number | null)[];
            /** Impedance Units */
            impedance_units?: string | null;
            /** Phase Degrees */
            phase_degrees?: (number | null)[];
            /** Phase Reference Distance M */
            phase_reference_distance_m?: number | null;
            /** Phase Time Convention */
            phase_time_convention?: string | null;
            reference?: components["schemas"]["ChartsReferencePayload"] | null;
            /** Sound Speed M Per S */
            sound_speed_m_per_s?: number | null;
            /** Spl */
            spl?: (number | null)[];
            /**
             * Theme
             * @default console
             */
            theme?: string;
        };
        /**
         * ChooseExportDestinationRequest
         * @description A folder typed instead of chosen from the native picker.
         *
         *     Same reasoning as ``SelectWorkspaceRequest``: the picker runs on the machine
         *     hosting the server, so a browser on another machine needs a way to name a
         *     folder that does not open a dialog nobody is sitting in front of.
         */
        ChooseExportDestinationRequest: {
            /** Path */
            path: string;
        };
        /** ClaimRequest */
        ClaimRequest: {
            /** Attemptgeneration */
            attemptGeneration: number;
            /** Claimid */
            claimId: string;
        };
        /** ClearFailedResponse */
        ClearFailedResponse: {
            /** Deleted */
            deleted: boolean;
            /** Deleted Count */
            deleted_count: number;
            /** Deleted Ids */
            deleted_ids: string[];
        };
        /**
         * CompletionEvidence
         * @description The link the document carries: this operation and its export.
         */
        CompletionEvidence: {
            /** Exportid */
            exportId: string;
            /** Operationid */
            operationId: string;
        };
        /** CompletionRequest */
        CompletionRequest: {
            /** Attemptgeneration */
            attemptGeneration: number;
            evidence?: components["schemas"]["CompletionEvidence"] | null;
            /** Message */
            message?: string | null;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "applied" | "reconciled" | "refused" | "superseded" | "discarded" | "recoveryRequired" | "failed";
        };
        /**
         * ConfigBlock
         * @description An unrecognized v1 block retained as ordered rows and item strings.
         */
        ConfigBlock: {
            /** Comments */
            comments?: string[];
            /** Entries */
            entries?: string[];
            /** Items */
            items?: {
                [key: string]: string;
            };
            /** Lines */
            lines?: string[];
        };
        /**
         * CornerGrid
         * @description Optional per-ring corner samples used by advanced FREEFORM payloads.
         */
        CornerGrid: {
            t: components["schemas"]["Expr"];
            /** Values */
            values: components["schemas"]["Expr"][][];
        };
        /** CrossSectionStation */
        CrossSectionStation: {
            /** Corner Grid */
            corner_grid?: components["schemas"]["Expr"][][] | null;
            corner_radius_mm?: components["schemas"]["Expr"] | null;
            exponent?: components["schemas"]["Expr"] | null;
            /**
             * Shape
             * @enum {string}
             */
            shape: "ellipse" | "superellipse" | "rounded_rectangle";
            t: components["schemas"]["Expr"];
        };
        /**
         * DelaySpec
         * @description A channel's delay: aligned automatically, or stated in ms.
         */
        DelaySpec: {
            /**
             * Mode
             * @default auto
             * @enum {string}
             */
            mode?: "auto" | "manual";
            /** Ms */
            ms?: number | null;
        };
        /** DeleteResponse */
        DeleteResponse: {
            /**
             * Deleted
             * @constant
             */
            deleted: true;
            /** Job Id */
            job_id: string;
        };
        /**
         * DeliveryRequest
         * @description One outbox item. Transport fields (tokens, attempts, claims) are refused.
         */
        DeliveryRequest: {
            /** Bundlepath */
            bundlePath: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "prepare_and_solve" | "receive_snapshot";
            /** Manifestsha256 */
            manifestSha256: string;
            /** Operationid */
            operationId: string;
            /** Requestedat */
            requestedAt: string;
            /** Returnid */
            returnId?: string | null;
        };
        /**
         * DesignAvailability
         * @description Whether a job's stored design can be reopened, and if not, why not.
         *
         *     A job that cannot be reopened must say what is wrong in words the user can
         *     act on, because "Rerun is greyed out" is not a diagnosis.
         */
        DesignAvailability: {
            /** Note */
            note: string | null;
            /** Reason */
            reason: string | null;
            /**
             * Reason Code
             * @enum {string}
             */
            reason_code: "ok" | "imported_geometry" | "no_stored_design" | "unreadable_design";
            /** Reopenable */
            reopenable: boolean;
            /**
             * Source
             * @enum {string}
             */
            source: "v2-snapshot" | "cad-import" | "none";
        };
        /**
         * DesignConfig
         * @description Root discriminated design union, serialized as a flat API object.
         */
        DesignConfig: components["schemas"]["OSSEConfig"] | components["schemas"]["ROSSEConfig"] | components["schemas"]["ICWConfig"] | components["schemas"]["FreeformConfig"];
        /**
         * DesignSchemaDocument
         * @description Discoverable root of WG's generated JSON Schema document.
         */
        DesignSchemaDocument: {
            /** $Defs */
            $defs: {
                [key: string]: {
                    [key: string]: unknown;
                };
            };
            /**
             * $Schema
             * @constant
             */
            $schema: "https://json-schema.org/draft/2020-12/schema";
            /** Description */
            description: string;
            discriminator: components["schemas"]["JsonSchemaDiscriminator"];
            /** Oneof */
            oneOf: components["schemas"]["JsonSchemaReference"][];
            /** Title */
            title: string;
            /**
             * X-Wg-Schema-Version
             * @constant
             */
            "x-wg-schema-version": 1;
        };
        /** DesignSnapshot */
        DesignSnapshot: {
            design: components["schemas"]["DesignConfig"];
            /**
             * Version
             * @default 1
             * @constant
             */
            version?: 1;
        };
        /** DirectivityRenderRequest */
        DirectivityRenderRequest: {
            /**
             * Angle Guide Step
             * @default 10
             */
            angle_guide_step?: number;
            /** Directivity */
            directivity: {
                [key: string]: unknown;
            };
            /** Frequencies */
            frequencies: number[];
            /** Reference Directivity */
            reference_directivity?: {
                [key: string]: unknown;
            } | null;
            /** Reference Frequencies */
            reference_frequencies?: number[] | null;
            /** Reference Label */
            reference_label?: string | null;
            /**
             * Reference Level
             * @default -6
             */
            reference_level?: number;
            /**
             * Theme
             * @default console
             */
            theme?: string;
        };
        /** DomainReadingRequest */
        DomainReadingRequest: {
            /** Ingestid */
            ingestId?: string | null;
            /** Operationid */
            operationId?: string | null;
            /** Planes */
            planes?: ("x0" | "y0")[];
            /**
             * Reading
             * @enum {string}
             */
            reading: "as-shown" | "reduced";
        };
        /** DriveChannel */
        DriveChannel: {
            driver?: components["schemas"]["DriverSpec"] | null;
            exterior_transducer?: components["schemas"]["ExteriorTransducerSpec"] | null;
            /** Id */
            id: string;
            /**
             * Motion
             * @default normal
             * @enum {string}
             */
            motion?: "normal" | "axial";
            /** Source Ids */
            source_ids: string[];
        };
        /** DriverDetail */
        DriverDetail: {
            /** Brand */
            brand: string;
            /**
             * Completeness
             * @enum {string}
             */
            completeness: "full" | "partial" | "catalogue";
            display: components["schemas"]["DriverDisplay"];
            /** Extras */
            extras: {
                [key: string]: string;
            };
            /** Fields */
            fields: {
                [key: string]: number | string | null;
            };
            /** Id */
            id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "lf" | "cd" | "unknown";
            /** Model */
            model: string;
            /** Size */
            size?: string | null;
            source: components["schemas"]["DriverSource"];
            /** Spec */
            spec: {
                [key: string]: number;
            };
            /** Variants */
            variants: components["schemas"]["DriverVariantSummary"][];
            /** Xo Min Hz */
            xo_min_hz?: number | null;
            /** Z Ohm */
            z_ohm?: number | null;
        };
        /** DriverDisplay */
        DriverDisplay: {
            /** Bl T M */
            bl_t_m?: number | null;
            /** Fs Hz */
            fs_hz?: number | null;
            /** Power W */
            power_w?: number | null;
            /** Price Eur */
            price_eur?: number | null;
            /** Sd Cm2 */
            sd_cm2?: number | null;
            /** Sensitivity Db */
            sensitivity_db?: number | null;
            /** Xmax Mm */
            xmax_mm?: number | null;
        };
        /** DriverHit */
        DriverHit: {
            /** Brand */
            brand: string;
            /**
             * Completeness
             * @enum {string}
             */
            completeness: "full" | "partial" | "catalogue";
            display: components["schemas"]["DriverDisplay"];
            /** Id */
            id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "lf" | "cd" | "unknown";
            /** Model */
            model: string;
            /** Size */
            size?: string | null;
            source: components["schemas"]["DriverSource"];
            /** Spec */
            spec: {
                [key: string]: number;
            };
            /** Variants */
            variants: components["schemas"]["DriverVariantSummary"][];
            /** Xo Min Hz */
            xo_min_hz?: number | null;
            /** Z Ohm */
            z_ohm?: number | null;
        };
        /**
         * DriverKindCount
         * @description What the library holds of one driver type.
         */
        DriverKindCount: {
            /**
             * Complete
             * @default 0
             */
            complete?: number;
            /** Kind */
            kind: string;
            /** Total */
            total: number;
        };
        /** DriverLibraryFile */
        DriverLibraryFile: {
            /**
             * Bundled
             * @default false
             */
            bundled?: boolean;
            /** Name */
            name: string;
            /** Rows */
            rows: number;
        };
        /** DriverLibraryInfo */
        DriverLibraryInfo: {
            /**
             * Complete Drivers
             * @default 0
             */
            complete_drivers?: number;
            /** Files */
            files: components["schemas"]["DriverLibraryFile"][];
            /** Folder */
            folder: string;
            /**
             * Kinds
             * @default []
             */
            kinds?: components["schemas"]["DriverKindCount"][];
            /** Last Scan */
            last_scan?: string | null;
            /** Total Drivers */
            total_drivers: number;
        };
        /** DriverSearchResponse */
        DriverSearchResponse: {
            /**
             * Hidden Incomplete
             * @default 0
             */
            hidden_incomplete?: number;
            /** Items */
            items: components["schemas"]["DriverHit"][];
            /**
             * Matches By Kind
             * @default {}
             */
            matches_by_kind?: {
                [key: string]: number;
            };
            /** Total */
            total: number;
        };
        /** DriverSource */
        DriverSource: {
            /**
             * Bundled
             * @default false
             */
            bundled?: boolean;
            /** File */
            file: string;
            /** Price Eur */
            price_eur?: number | null;
            /** Source Url */
            source_url?: string | null;
        };
        /**
         * DriverSpec
         * @description Thiele-Small driver model for one drive channel, Hornresp units.
         *
         *     The wire keeps Hornresp's units (Sd cm², Le/Le2 mH, Mmd/Mms g, Cms m/N,
         *     Vas L, Rms kg/s, Xmax mm) exactly as the Fusion add-in documents them;
         *     conversion to SI happens once, in ``server/solver/driver_lem.py``.
         */
        DriverSpec: {
            /** Bl T M */
            bl_t_m: number;
            /** Cms M Per N */
            cms_m_per_n?: number | null;
            /**
             * Count
             * @default 1
             */
            count?: number;
            /** Fs Hz */
            fs_hz?: number | null;
            /** Label */
            label?: string | null;
            /** Le2 Mh */
            le2_mh?: number | null;
            /**
             * Le Mh
             * @default 0
             */
            le_mh?: number;
            /** Mmd G */
            mmd_g?: number | null;
            /** Mms G */
            mms_g?: number | null;
            /** Power W */
            power_w?: number | null;
            /** Qms */
            qms?: number | null;
            /** Re2 Ohm */
            re2_ohm?: number | null;
            /** Re Ohm */
            re_ohm: number;
            /** Rear Volume L */
            rear_volume_l?: number | null;
            /** Rms Kg Per S */
            rms_kg_per_s?: number | null;
            /** Sd Cm2 */
            sd_cm2: number;
            /** Vas L */
            vas_l?: number | null;
            /** Xmax Mm */
            xmax_mm?: number | null;
            /** Z Nom Ohm */
            z_nom_ohm?: number | null;
        };
        /** DriverVariantSummary */
        DriverVariantSummary: {
            /** Id */
            id: string;
            /** Z Ohm */
            z_ohm?: number | null;
        };
        /** EnclosureConfig */
        EnclosureConfig: {
            /** Back Resolution */
            back_resolution?: components["schemas"]["Expr"] | [
                components["schemas"]["Expr"],
                components["schemas"]["Expr"],
                components["schemas"]["Expr"],
                components["schemas"]["Expr"]
            ] | null;
            depth?: components["schemas"]["Expr"] | null;
            edge_radius?: components["schemas"]["Expr"] | null;
            edge_type?: components["schemas"]["Expr"] | null;
            /** Front Resolution */
            front_resolution?: components["schemas"]["Expr"] | [
                components["schemas"]["Expr"],
                components["schemas"]["Expr"],
                components["schemas"]["Expr"],
                components["schemas"]["Expr"]
            ] | null;
            space_b?: components["schemas"]["Expr"] | null;
            space_l?: components["schemas"]["Expr"] | null;
            space_r?: components["schemas"]["Expr"] | null;
            space_t?: components["schemas"]["Expr"] | null;
        };
        /**
         * EngineSubstitution
         * @description The engine the request asked for, and the one standing in for it.
         *
         *     Present only when a stored selection named an engine this host cannot run.
         *     `requested` is left exactly as the caller sent it, because the selection is
         *     host-local UI state that should re-engage untouched the day that engine
         *     becomes available here.
         */
        EngineSubstitution: {
            /** Reason */
            reason: string;
            /** Requested */
            requested: string;
            /** Resolved */
            resolved: string;
        };
        /**
         * ErrorDetail
         * @description Stable refusal/failure detail while the legacy ``detail`` string remains.
         */
        ErrorDetail: {
            /** Client Request Id */
            client_request_id?: string | null;
            /** Code */
            code: string;
            /** Details */
            details?: {
                [key: string]: unknown;
            };
            /** Message */
            message: string;
            /**
             * Retryable
             * @default false
             */
            retryable?: boolean;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version?: 1;
            /** Stage */
            stage: string;
        };
        /**
         * ErrorEnvelope
         * @description Backward-compatible HTTP/CLI error body.
         */
        ErrorEnvelope: {
            /** Detail */
            detail: string;
            error: components["schemas"]["ErrorDetail"];
        };
        /** ExportRequest */
        ExportRequest: {
            /**
             * Basename
             * @default waveguide
             */
            baseName?: string;
            design: components["schemas"]["DesignConfig"];
            /** Designrevision */
            designRevision: number;
            /**
             * Modelname
             * @default MWG Horn
             */
            modelName?: string;
        };
        /**
         * Expr
         * @description A numeric value and its exact v1 source spelling.
         *
         *     V1 retains strings in ``src/config/index.js:319-333``.  A value is ``None``
         *     when the expression depends on the angular variable ``p`` (or is otherwise
         *     not a scalar); ``raw`` is always sufficient for lossless serialization.
         */
        Expr: {
            /** Raw */
            raw?: string | null;
            /** Value */
            value?: number | null;
        };
        /**
         * ExteriorTransducerSpec
         * @description Opt-in BEAT bare-driver coordinate; SI scalars, global axis, RMS volts.
         */
        ExteriorTransducerSpec: {
            /** Bl N Per A */
            bl_n_per_a: number;
            /** Cms M Per N */
            cms_m_per_n: number;
            /** Le H */
            le_h: number;
            /** Mmd Kg */
            mmd_kg: number;
            /** Motion Axis */
            motion_axis: [
                number,
                number,
                number
            ];
            /** Re Ohm */
            re_ohm: number;
            /** Rms N S Per M */
            rms_n_s_per_m: number;
            /**
             * Version
             * @default 1
             * @constant
             */
            version?: 1;
        };
        /** FieldPlaneRequest */
        FieldPlaneRequest: {
            /** Frequency Index */
            frequency_index: number;
            plane: components["schemas"]["FieldPlaneSpec"];
            /** Request Id */
            request_id: string;
            response: components["schemas"]["FieldPlaneResponseSpec"];
            /**
             * Version
             * @constant
             */
            version: 1;
        };
        /** FieldPlaneResponseSpec */
        FieldPlaneResponseSpec: {
            /** Id */
            id: string;
        };
        /**
         * FieldPlaneSpec
         * @description A centred, orthonormal sampling plane in solver metres.
         *
         *     Samples are generated as v-major rows with u varying fastest within each
         *     row. Both axes point in their positive grid directions.
         */
        FieldPlaneSpec: {
            /** Axis U */
            axis_u: [
                number,
                number,
                number
            ];
            /** Axis V */
            axis_v: [
                number,
                number,
                number
            ];
            /** Height M */
            height_m: number;
            /** Nx */
            nx: number;
            /** Ny */
            ny: number;
            /** Origin M */
            origin_m: [
                number,
                number,
                number
            ];
            /** Width M */
            width_m: number;
        };
        /** FieldPlaneStringErrorResponse */
        FieldPlaneStringErrorResponse: {
            /** Detail */
            detail: string;
        };
        /**
         * FieldPlaneUnavailableResponse
         * @description Backward-compatible, versioned remedy for an unsupported field plane.
         */
        FieldPlaneUnavailableResponse: {
            /**
             * Code
             * @enum {string}
             */
            code: "unsupported_axisymmetric_formulation" | "unsupported_coupled_infinite_baffle" | "unsupported_per_band_mesh_ladder" | "unsupported_ground_plane";
            /** Detail */
            detail: string;
            /**
             * Error Contract Version
             * @default 1
             * @constant
             */
            error_contract_version?: 1;
            /** Message */
            message: string;
            /** Remedy */
            remedy: string;
        };
        /** FieldPlaneValidationErrorResponse */
        FieldPlaneValidationErrorResponse: {
            /** Detail */
            detail: {
                [key: string]: unknown;
            }[];
        };
        /**
         * FilterSpec
         * @description One high-pass or low-pass section of a drive channel.
         */
        FilterSpec: {
            /**
             * Family
             * @enum {string}
             */
            family: "lr" | "butterworth" | "bessel" | "linear_phase";
            /** Fc Hz */
            fc_hz: number;
            /** Order */
            order: number;
        };
        /** FreeformConfig */
        FreeformConfig: {
            /** Corner Grids */
            corner_grids?: components["schemas"]["CornerGrid"][];
            /** Coverage Mode */
            coverage_mode?: string | null;
            /** Cross Sections */
            cross_sections: components["schemas"]["CrossSectionStation"][];
            enclosure?: components["schemas"]["EnclosureConfig"] | null;
            /** Extra Blocks */
            extra_blocks?: {
                [key: string]: components["schemas"]["ConfigBlock"];
            };
            /** Extra Keys */
            extra_keys?: {
                [key: string]: string;
            };
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            formula: "FREEFORM";
            /** Inflection Policy */
            inflection_policy?: ("reject" | "warn") | null;
            length: components["schemas"]["Expr"];
            /** Length Mode */
            length_mode?: ("profile" | "total") | null;
            mesh?: components["schemas"]["MeshConfig"];
            morph?: components["schemas"]["MorphConfig"];
            output?: components["schemas"]["OutputConfig"];
            profile_h: components["schemas"]["FreeformProfile"];
            profile_v: components["schemas"]["FreeformProfile"];
            scale?: components["schemas"]["Expr"] | null;
            simulation?: components["schemas"]["SimulationConfig"];
            slot_length?: components["schemas"]["Expr"] | null;
            source?: components["schemas"]["SourceConfig"];
            throat_ext_angle?: components["schemas"]["Expr"] | null;
            throat_ext_length?: components["schemas"]["Expr"] | null;
        };
        /** FreeformPoint */
        FreeformPoint: {
            angle_deg?: components["schemas"]["Expr"] | null;
            r: components["schemas"]["Expr"];
            t: components["schemas"]["Expr"];
        };
        /** FreeformProfile */
        FreeformProfile: {
            mouth_angle_deg?: components["schemas"]["Expr"] | null;
            /** Points */
            points: components["schemas"]["FreeformPoint"][];
            throat_angle_deg?: components["schemas"]["Expr"] | null;
        };
        /** FusionReturnRequest */
        FusionReturnRequest: {
            /** Designid */
            designId: string;
            /** Documentid */
            documentId: string;
            /** Expectedreturnstatehash */
            expectedReturnStateHash?: string | null;
            /** Instanceid */
            instanceId: string;
        };
        /** FusionStatusRequest */
        FusionStatusRequest: {
            design: components["schemas"]["DesignConfig"];
            identity?: components["schemas"]["SaveIdentity"] | null;
            /** Instanceid */
            instanceId?: string | null;
            /** Returnbundlepath */
            returnBundlePath?: string | null;
        };
        /**
         * GainSpec
         * @description A channel's level: matched automatically, stated in dB, or driven to
         *     the driver's own ceiling.
         *
         *     ``max`` is resolved by the solver, not here: it means "the loudest this
         *     channel's driver can be run before excursion, rated power or the amplifier
         *     binds", which needs the solved excursion and impedance and so cannot be a
         *     number on the wire. A channel whose members carry no driver model has no
         *     ceiling to find, and the solver reports that as a warning and falls back to
         *     0 dB rather than inventing one.
         */
        GainSpec: {
            /** Db */
            db?: number | null;
            /**
             * Mode
             * @default auto
             * @enum {string}
             */
            mode?: "auto" | "manual" | "max";
        };
        /**
         * GroundPlaneConfig
         * @description A rigid reflecting half space the model stands above.
         *
         *     Kept in solve options rather than in the design because it describes the
         *     room the horn is placed in, not the horn: ATH's text format has no key for
         *     it, so a design-level field would either break round-trip or invent a
         *     WG-only dialect. It is also the setting a user changes between solves of
         *     the *same* design, which is what solve options are for.
         *
         *     ``axis`` names the coordinate the half space bounds -- never an axis-pair
         *     token; see ``server/solver/ground_plane.py`` for why that distinction is
         *     load-bearing. WG's frame has z along the horn axis and y vertical, so the
         *     floor is ``axis="y"``, a side wall is ``"x"``, and ``"z"`` is a rigid wall
         *     behind the throat.
         *
         *     ``height_m`` is the height of the model's own origin above the plane,
         *     matching Boundary Lab's per-source ``positionHeightM``. The default 1.0 m
         *     is a listening-axis height, not a physical constant: it is a starting
         *     value, and the solve refuses rather than guesses if the model would cross
         *     the plane at it.
         */
        GroundPlaneConfig: {
            /**
             * Axis
             * @default y
             * @enum {string}
             */
            axis?: "x" | "y" | "z";
            /**
             * Enabled
             * @default false
             */
            enabled?: boolean;
            /**
             * Height M
             * @default 1
             */
            height_m?: number;
        };
        /** GuidingCurveConfig */
        GuidingCurveConfig: {
            aspect_ratio?: components["schemas"]["Expr"] | null;
            curve_type?: components["schemas"]["Expr"] | null;
            distance?: components["schemas"]["Expr"] | null;
            rotation?: components["schemas"]["Expr"] | null;
            sf_a?: components["schemas"]["Expr"] | null;
            sf_b?: components["schemas"]["Expr"] | null;
            sf_m1?: components["schemas"]["Expr"] | null;
            sf_m2?: components["schemas"]["Expr"] | null;
            sf_n1?: components["schemas"]["Expr"] | null;
            sf_n2?: components["schemas"]["Expr"] | null;
            sf_n3?: components["schemas"]["Expr"] | null;
            superellipse_n?: components["schemas"]["Expr"] | null;
            superformula?: components["schemas"]["Expr"] | null;
            width?: components["schemas"]["Expr"] | null;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * HeldBackBuild
         * @description A build the completion record holds back, by contract §2.3's interim identity.
         */
        HeldBackBuild: {
            /** Commit */
            commit?: string | null;
            /** Runtimeid */
            runtimeId?: string | null;
            /** Version */
            version: string;
        };
        /** ICWConfig */
        ICWConfig: {
            L?: components["schemas"]["Expr"] | null;
            R?: components["schemas"]["Expr"] | null;
            a?: components["schemas"]["Expr"] | null;
            a0?: components["schemas"]["Expr"] | null;
            coverage_angle?: components["schemas"]["Expr"] | null;
            /** Coverage Mode */
            coverage_mode?: string | null;
            curl?: components["schemas"]["Expr"] | null;
            depth?: components["schemas"]["Expr"] | null;
            enclosure?: components["schemas"]["EnclosureConfig"] | null;
            /** Extra Blocks */
            extra_blocks?: {
                [key: string]: components["schemas"]["ConfigBlock"];
            };
            /** Extra Keys */
            extra_keys?: {
                [key: string]: string;
            };
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            formula: "ICW";
            hold_end?: components["schemas"]["Expr"] | null;
            hold_start?: components["schemas"]["Expr"] | null;
            k?: components["schemas"]["Expr"] | null;
            /** Length Mode */
            length_mode?: ("profile" | "total") | null;
            mesh?: components["schemas"]["MeshConfig"];
            morph?: components["schemas"]["MorphConfig"];
            n_coeff?: components["schemas"]["Expr"] | null;
            output?: components["schemas"]["OutputConfig"];
            q?: components["schemas"]["Expr"] | null;
            r0?: components["schemas"]["Expr"] | null;
            scale?: components["schemas"]["Expr"] | null;
            simulation?: components["schemas"]["SimulationConfig"];
            slot_length?: components["schemas"]["Expr"] | null;
            source?: components["schemas"]["SourceConfig"];
            /** Termination */
            termination?: ("flat_baffle" | "rollback") | null;
            theta1_deg?: components["schemas"]["Expr"] | null;
            throat_ext_angle?: components["schemas"]["Expr"] | null;
            throat_ext_length?: components["schemas"]["Expr"] | null;
        };
        /**
         * ImportedEngineVerdictResponse
         * @description Whether one engine can solve one ingested CAD return here, and why not.
         */
        ImportedEngineVerdictResponse: {
            /** Code */
            code?: string | null;
            /** Label */
            label: string;
            /** Name */
            name: string;
            /** Reason */
            reason?: string | null;
            /** Solves */
            solves: boolean;
            /** Stage */
            stage?: string | null;
        };
        /** ImportedGeometrySource */
        ImportedGeometrySource: {
            /** Acknowledged Findings */
            acknowledged_findings?: string[];
            /** Artifact Sha256 */
            artifact_sha256: string;
            /** Bem Port Area M2 */
            bem_port_area_m2?: number | null;
            combine?: components["schemas"]["ChannelCombineSpec"] | null;
            /** Drive Channels */
            drive_channels: components["schemas"]["DriveChannel"][];
            /**
             * Drive Voltage V
             * @default 2.83
             */
            drive_voltage_v?: number;
            /**
             * Exterior Only
             * @default false
             */
            exterior_only?: boolean;
            /** Ingest Id */
            ingest_id: string;
            /** Manifest Sha256 */
            manifest_sha256: string;
            /** Max Drive Voltage V */
            max_drive_voltage_v?: number | null;
            mesh: components["schemas"]["ImportedMeshSizes"];
            /** Model Port Area M2 */
            model_port_area_m2?: number | null;
            /**
             * Passive Cardioid Coupled
             * @default false
             */
            passive_cardioid_coupled?: boolean;
            /** Passive Cardioid Foam Resistance Pa S M3 */
            passive_cardioid_foam_resistance_pa_s_m3?: number | null;
            /**
             * Passive Cardioid Invert Port
             * @default true
             */
            passive_cardioid_invert_port?: boolean;
            /** Passive Cardioid Port Length Mm */
            passive_cardioid_port_length_mm?: number | null;
            /** Passive Cardioid Rear Volume L */
            passive_cardioid_rear_volume_l?: number | null;
            /** Port Area Source */
            port_area_source?: ("user" | "bem_aperture") | null;
            /**
             * Rg Ohm
             * @default 0
             */
            rg_ohm?: number;
            /** Skipped Source Ids */
            skipped_source_ids?: string[];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "imported";
        };
        /** ImportedMeshRequest */
        ImportedMeshRequest: {
            /** Rigidsizemm */
            rigidSizeMm: number;
            /** Sourcesizemm */
            sourceSizeMm: {
                [key: string]: number;
            };
            /** Transitionmm */
            transitionMm: number;
        };
        /** ImportedMeshSizes */
        ImportedMeshSizes: {
            /** Rigid Size Mm */
            rigid_size_mm: number;
            /** Source Size Mm */
            source_size_mm: {
                [key: string]: number;
            };
            /** Transition Mm */
            transition_mm: number;
        };
        /**
         * ImportedSolvePlanResponse
         * @description Every engine's verdict on one imported request, and where it resolves.
         *
         *     ``engine`` is what submitting this request would run on; it is null, with
         *     ``code`` and ``reason``, when the request would be refused or no capable
         *     engine is available.
         */
        ImportedSolvePlanResponse: {
            /** Code */
            code?: string | null;
            /** Domain */
            domain?: string | null;
            /** Domain Decision */
            domain_decision?: {
                [key: string]: unknown;
            } | null;
            /** Engine */
            engine: string | null;
            /** Engines */
            engines: components["schemas"]["ImportedEngineVerdictResponse"][];
            /** Ingest Id */
            ingest_id: string;
            /** Reason */
            reason: string;
            /** Requested */
            requested: string;
        };
        /** JobItem */
        JobItem: {
            /** Archived At */
            archived_at?: string | null;
            /** Auto Export Completed At */
            auto_export_completed_at: string | null;
            /** Auto Export Formats */
            auto_export_formats: {
                [key: string]: unknown;
            };
            /** Axisymmetric Eligibility Reasons */
            axisymmetric_eligibility_reasons?: string[];
            /** Cad Intent */
            cad_intent?: {
                [key: string]: unknown;
            } | null;
            /** Cad Provenance */
            cad_provenance?: {
                [key: string]: unknown;
            } | null;
            /** Cad Setup */
            cad_setup?: {
                [key: string]: unknown;
            } | null;
            cad_source?: components["schemas"]["CadSource"] | null;
            cad_state?: components["schemas"]["CadState"] | null;
            /** Cancellation Requested */
            cancellation_requested: boolean;
            /** Channel Solve Executions */
            channel_solve_executions: {
                [key: string]: components["schemas"]["ChannelSolveExecution"];
            };
            /** Client Metadata */
            client_metadata: {
                [key: string]: unknown;
            };
            /** Client Request Id */
            client_request_id?: string | null;
            /** Completed At */
            completed_at: string | null;
            /** Config Summary */
            config_summary: {
                [key: string]: unknown;
            };
            /** Created At */
            created_at: string;
            design_availability: components["schemas"]["DesignAvailability"];
            /** Design Revision */
            design_revision: number;
            /** Error Message */
            error_message: string | null;
            /** Exported Files */
            exported_files: string[];
            /**
             * Field Plane Available
             * @default false
             */
            field_plane_available?: boolean;
            /** Field Trace Bytes */
            field_trace_bytes?: number | null;
            /** Has Mesh Artifact */
            has_mesh_artifact: boolean;
            /**
             * Has Pressure Basis Artifact
             * @default false
             */
            has_pressure_basis_artifact?: boolean;
            /**
             * Has Radiation Impedance Artifact
             * @default false
             */
            has_radiation_impedance_artifact?: boolean;
            /** Has Results */
            has_results: boolean;
            /** Id */
            id: string;
            /** Label */
            label: string | null;
            /** Log Tail */
            log_tail: string[];
            /** Mesh Artifact File */
            mesh_artifact_file: string | null;
            /** Mesh Discarded At */
            mesh_discarded_at?: string | null;
            /** Mesh Stats */
            mesh_stats: {
                [key: string]: unknown;
            } | null;
            /** Parent Job Id */
            parent_job_id: string | null;
            /** Persistence Warnings */
            persistence_warnings?: string[];
            /** Polar Grid */
            polar_grid: {
                [key: string]: unknown;
            };
            /** Pressure Basis Artifact Bytes */
            pressure_basis_artifact_bytes?: number | null;
            /** Progress */
            progress: number;
            /** Queued At */
            queued_at: string;
            /** Radiation Impedance Artifact Bytes */
            radiation_impedance_artifact_bytes?: number | null;
            /** Rating */
            rating: number | null;
            /** Raw Results File */
            raw_results_file: string | null;
            /** Results Discarded At */
            results_discarded_at?: string | null;
            /** Run Number */
            run_number: number | null;
            /** Script Snapshot */
            script_snapshot: {
                [key: string]: unknown;
            } | null;
            /**
             * Solve Accuracy
             * @default fast
             * @enum {string}
             */
            solve_accuracy?: "fast" | "accurate";
            solve_execution: components["schemas"]["SolveExecution"] | null;
            solve_options: components["schemas"]["SolveOptionsResponse"];
            /** Solve Path */
            solve_path?: ("full-3d" | "axisymmetric-meridian") | null;
            /** Solve Wall Time Seconds */
            solve_wall_time_seconds?: number | null;
            /** Stage */
            stage: string | null;
            /** Stage Message */
            stage_message: string | null;
            /** Started At */
            started_at: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "preparing" | "queued" | "running" | "complete" | "error" | "cancelled";
            /** Symmetry */
            symmetry?: {
                [key: string]: unknown;
            };
            /** Unavailable Reason */
            unavailable_reason?: string | null;
        };
        /** JobListResponse */
        JobListResponse: {
            /** Items */
            items: components["schemas"]["JobItem"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /**
         * JobMetadataPatch
         * @description V1 task metadata retained by ``server/api/routes_simulation.py:253-277``.
         */
        JobMetadataPatch: {
            /** Archived At */
            archived_at?: string | null;
            /** Auto Export Completed At */
            auto_export_completed_at?: string | null;
            /** Auto Export Formats */
            auto_export_formats?: {
                [key: string]: unknown;
            } | null;
            /** Exported Files */
            exported_files?: string[] | null;
            /** Label */
            label?: string | null;
            /** Mesh Artifact File */
            mesh_artifact_file?: string | null;
            /** Rating */
            rating?: number | null;
            /** Raw Results File */
            raw_results_file?: string | null;
            /** Script Snapshot */
            script_snapshot?: {
                [key: string]: unknown;
            } | null;
        };
        /** JobStatusResponse */
        JobStatusResponse: {
            /** Archived At */
            archived_at?: string | null;
            /** Auto Export Completed At */
            auto_export_completed_at: string | null;
            /** Auto Export Formats */
            auto_export_formats: {
                [key: string]: unknown;
            };
            /** Axisymmetric Eligibility Reasons */
            axisymmetric_eligibility_reasons?: string[];
            /** Cad Intent */
            cad_intent?: {
                [key: string]: unknown;
            } | null;
            /** Cad Provenance */
            cad_provenance?: {
                [key: string]: unknown;
            } | null;
            /** Cad Setup */
            cad_setup?: {
                [key: string]: unknown;
            } | null;
            cad_source?: components["schemas"]["CadSource"] | null;
            cad_state?: components["schemas"]["CadState"] | null;
            /** Cancellation Requested */
            cancellation_requested: boolean;
            /** Channel Solve Executions */
            channel_solve_executions: {
                [key: string]: components["schemas"]["ChannelSolveExecution"];
            };
            /** Client Metadata */
            client_metadata: {
                [key: string]: unknown;
            };
            /** Client Request Id */
            client_request_id?: string | null;
            /** Completed At */
            completed_at: string | null;
            /** Config Summary */
            config_summary: {
                [key: string]: unknown;
            };
            /** Created At */
            created_at: string;
            design_availability: components["schemas"]["DesignAvailability"];
            /** Design Revision */
            design_revision: number;
            /** Error Message */
            error_message: string | null;
            /** Exported Files */
            exported_files: string[];
            /**
             * Field Plane Available
             * @default false
             */
            field_plane_available?: boolean;
            /** Field Trace Bytes */
            field_trace_bytes?: number | null;
            /** Has Mesh Artifact */
            has_mesh_artifact: boolean;
            /**
             * Has Pressure Basis Artifact
             * @default false
             */
            has_pressure_basis_artifact?: boolean;
            /**
             * Has Radiation Impedance Artifact
             * @default false
             */
            has_radiation_impedance_artifact?: boolean;
            /** Has Results */
            has_results: boolean;
            /** Id */
            id: string;
            /** Label */
            label: string | null;
            /** Log Tail */
            log_tail: string[];
            /** Mesh Artifact File */
            mesh_artifact_file: string | null;
            /** Mesh Discarded At */
            mesh_discarded_at?: string | null;
            /** Mesh Stats */
            mesh_stats: {
                [key: string]: unknown;
            } | null;
            /** Message */
            message?: string | null;
            /** Parent Job Id */
            parent_job_id: string | null;
            /** Persistence Warnings */
            persistence_warnings?: string[];
            /** Polar Grid */
            polar_grid: {
                [key: string]: unknown;
            };
            /** Pressure Basis Artifact Bytes */
            pressure_basis_artifact_bytes?: number | null;
            /** Progress */
            progress: number;
            /** Queued At */
            queued_at: string;
            /** Radiation Impedance Artifact Bytes */
            radiation_impedance_artifact_bytes?: number | null;
            /** Rating */
            rating: number | null;
            /** Raw Results File */
            raw_results_file: string | null;
            /** Results Discarded At */
            results_discarded_at?: string | null;
            /** Run Number */
            run_number: number | null;
            /** Script Snapshot */
            script_snapshot: {
                [key: string]: unknown;
            } | null;
            /**
             * Solve Accuracy
             * @default fast
             * @enum {string}
             */
            solve_accuracy?: "fast" | "accurate";
            solve_execution: components["schemas"]["SolveExecution"] | null;
            solve_options: components["schemas"]["SolveOptionsResponse"];
            /** Solve Path */
            solve_path?: ("full-3d" | "axisymmetric-meridian") | null;
            /** Solve Wall Time Seconds */
            solve_wall_time_seconds?: number | null;
            /** Stage */
            stage: string | null;
            /** Stage Message */
            stage_message: string | null;
            /** Started At */
            started_at: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "preparing" | "queued" | "running" | "complete" | "error" | "cancelled";
            /** Symmetry */
            symmetry?: {
                [key: string]: unknown;
            };
            /** Unavailable Reason */
            unavailable_reason?: string | null;
            /** Updated At */
            updated_at: string;
        };
        /** JsonSchemaDiscriminator */
        JsonSchemaDiscriminator: {
            /** Mapping */
            mapping: {
                [key: string]: string;
            };
            /** Propertyname */
            propertyName: string;
        };
        /** JsonSchemaReference */
        JsonSchemaReference: {
            /** $Ref */
            $ref: string;
        };
        JsonValue: unknown;
        /**
         * LoadedIdentity
         * @description The build WGLink captured when it loaded.
         */
        LoadedIdentity: {
            /** Addinversion */
            addinVersion: string | null;
            /** Loadedat */
            loadedAt: string;
            /** Managedby */
            managedBy: string | null;
            /**
             * Source
             * @enum {string}
             */
            source: "managed" | "devSync" | "unmanaged";
            /** Sourcecommit */
            sourceCommit: string | null;
            /** Waveguidegeneratorroot */
            waveguideGeneratorRoot: string | null;
        };
        /** ManualSolveOperationRequest */
        ManualSolveOperationRequest: {
            /** Ingestid */
            ingestId: string;
            /** Operationid */
            operationId: string;
        };
        /** ManualSolveOperationResponse */
        ManualSolveOperationResponse: {
            operation: components["schemas"]["CadOperationSummary"];
        };
        /** MeshConfig */
        MeshConfig: {
            allow_large_mesh?: components["schemas"]["Expr"] | null;
            angular_segments?: components["schemas"]["Expr"] | null;
            aperture_resolution_scale?: components["schemas"]["Expr"] | null;
            corner_segments?: components["schemas"]["Expr"] | null;
            length_segments?: components["schemas"]["Expr"] | null;
            max_edge?: components["schemas"]["Expr"] | null;
            max_triangles?: components["schemas"]["Expr"] | null;
            mouth_resolution?: components["schemas"]["Expr"] | null;
            quadrants?: components["schemas"]["Expr"] | null;
            rear_resolution?: components["schemas"]["Expr"] | null;
            /** Sampling Mode */
            sampling_mode?: string | null;
            throat_resolution?: components["schemas"]["Expr"] | null;
            throat_segments?: components["schemas"]["Expr"] | null;
            throat_slice_density?: components["schemas"]["Expr"] | null;
            vertical_offset?: components["schemas"]["Expr"] | null;
            wall_thickness?: components["schemas"]["Expr"] | null;
            /** Z Map Points */
            z_map_points?: string | null;
        };
        /** MetadataResponse */
        MetadataResponse: {
            /**
             * Status
             * @constant
             */
            status: "ok";
        };
        /** MorphConfig */
        MorphConfig: {
            allow_shrinkage?: components["schemas"]["Expr"] | null;
            corner_radius?: components["schemas"]["Expr"] | null;
            fixed_part?: components["schemas"]["Expr"] | null;
            rate?: components["schemas"]["Expr"] | null;
            target_exponent?: components["schemas"]["Expr"] | null;
            target_height?: components["schemas"]["Expr"] | null;
            target_shape?: components["schemas"]["Expr"] | null;
            target_width?: components["schemas"]["Expr"] | null;
        };
        /**
         * MultiChannelResultEnvelope
         * @description One solve, one channel per drive channel, addressed by ``channel_order``.
         *
         *     Every channel is a parametric-shaped result whose ``metadata`` names its
         *     drive address: ``drive_channel_id``, the ``source_ids`` it drives, ``role``
         *     (the driver band ``HF``/``MF``/``LF``, null when the sources carry no band
         *     role) and, when the ingestion record names its sources, ``source_labels``
         *     parallel to ``source_ids``. A combined channel adds ``derived_from_channels``
         *     and a ``combine`` payload whose ``members`` and ``member_roles`` are parallel
         *     lists, so a client can label a crossover pair without the ingestion record.
         */
        MultiChannelResultEnvelope: {
            /** Channel Order */
            channel_order: string[];
            /** Channels */
            channels: {
                [key: string]: {
                    [key: string]: unknown;
                };
            };
            /** Client Metadata */
            client_metadata: {
                [key: string]: components["schemas"]["JsonValue"];
            };
            /** Client Request Id */
            client_request_id: string | null;
            /** Frequencies */
            frequencies?: number[] | null;
            /** Frequency Status */
            frequency_status?: ("solved" | "interpolated")[] | null;
            /** Metadata */
            metadata: {
                [key: string]: unknown;
            };
            provenance: components["schemas"]["ResultProvenance"];
            /**
             * Result Contract Version
             * @constant
             */
            result_contract_version: 2;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            result_kind: "multi_channel";
        } & {
            [key: string]: unknown;
        };
        /** OSSEConfig */
        OSSEConfig: {
            L?: components["schemas"]["Expr"] | null;
            a?: components["schemas"]["Expr"] | null;
            a0?: components["schemas"]["Expr"] | null;
            circ_arc_radius?: components["schemas"]["Expr"] | null;
            circ_arc_term_angle?: components["schemas"]["Expr"] | null;
            /** Coverage Mode */
            coverage_mode?: string | null;
            enclosure?: components["schemas"]["EnclosureConfig"] | null;
            /** Extra Blocks */
            extra_blocks?: {
                [key: string]: components["schemas"]["ConfigBlock"];
            };
            /** Extra Keys */
            extra_keys?: {
                [key: string]: string;
            };
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            formula: "OSSE";
            guiding_curve?: components["schemas"]["GuidingCurveConfig"];
            h?: components["schemas"]["Expr"] | null;
            k?: components["schemas"]["Expr"] | null;
            /** Length Mode */
            length_mode?: ("profile" | "total") | null;
            mesh?: components["schemas"]["MeshConfig"];
            morph?: components["schemas"]["MorphConfig"];
            n?: components["schemas"]["Expr"] | null;
            output?: components["schemas"]["OutputConfig"];
            q?: components["schemas"]["Expr"] | null;
            r0?: components["schemas"]["Expr"] | null;
            rotation?: components["schemas"]["Expr"] | null;
            s?: components["schemas"]["Expr"] | null;
            /** S1 */
            s1?: number | null;
            /** S2 */
            s2?: number | null;
            scale?: components["schemas"]["Expr"] | null;
            simulation?: components["schemas"]["SimulationConfig"];
            slot_length?: components["schemas"]["Expr"] | null;
            source?: components["schemas"]["SourceConfig"];
            throat_ext_angle?: components["schemas"]["Expr"] | null;
            throat_ext_length?: components["schemas"]["Expr"] | null;
            throat_profile?: components["schemas"]["Expr"] | null;
        };
        /** OnshapeReturnRequest */
        OnshapeReturnRequest: {
            /** Designid */
            designId: string;
            /** Instanceid */
            instanceId?: string | null;
        };
        /** OnshapeSendRequest */
        OnshapeSendRequest: {
            /**
             * Allowpublic
             * @default false
             */
            allowPublic?: boolean;
            /**
             * Basename
             * @default waveguide
             */
            baseName?: string;
            /**
             * Buildmode
             * @default import
             * @enum {string}
             */
            buildMode?: "import" | "native";
            design: components["schemas"]["DesignConfig"];
            /** Designrevision */
            designRevision: number;
            identity?: components["schemas"]["SaveIdentity"] | null;
            /** Instanceid */
            instanceId?: string | null;
        };
        /** OnshapeStatusRequest */
        OnshapeStatusRequest: {
            design: components["schemas"]["DesignConfig"];
            identity?: components["schemas"]["SaveIdentity"] | null;
            /** Instanceid */
            instanceId?: string | null;
        };
        /** OnshapeUnlinkRequest */
        OnshapeUnlinkRequest: {
            /** Designid */
            designId: string;
            /** Instanceid */
            instanceId?: string | null;
        };
        /** OperationApprovalsRequest */
        OperationApprovalsRequest: {
            /** Findingids */
            findingIds: string[];
            /** Preparationid */
            preparationId: string;
        };
        /** OutputConfig */
        OutputConfig: {
            msh?: components["schemas"]["Expr"] | null;
            stl?: components["schemas"]["Expr"] | null;
        };
        /** ParameterCatalog */
        ParameterCatalog: {
            /**
             * Catalog Version
             * @constant
             */
            catalog_version: 1;
            /** Design Families */
            design_families: ("R-OSSE" | "OSSE" | "ICW" | "FREEFORM")[];
            /** Parameters */
            parameters: components["schemas"]["ParameterDescriptor"][];
            /**
             * Schema Version
             * @constant
             */
            schema_version: 1;
            validation_authority: components["schemas"]["CatalogValidationAuthority"];
        };
        /** ParameterDescriptor */
        ParameterDescriptor: {
            /** Accepts Expression */
            accepts_expression: boolean;
            /** Default By Family */
            default_by_family: {
                [key: string]: components["schemas"]["JsonValue"];
            };
            /** Description */
            description: string;
            /** Disabled Reason */
            disabled_reason?: string | null;
            disabled_when?: components["schemas"]["CatalogCondition"] | null;
            editor_bounds: components["schemas"]["CatalogEditorBounds"] | null;
            /** Families */
            families: ("R-OSSE" | "OSSE" | "ICW" | "FREEFORM")[];
            /** Id */
            id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "number" | "select" | "indicator" | "table" | "text";
            /** Label */
            label: string;
            /** Legacy Key */
            legacy_key: string;
            /** Mirror Paths */
            mirror_paths: string[];
            /** Options */
            options: components["schemas"]["CatalogOption"][];
            /** Path */
            path: string | null;
            /** Precision */
            precision: number | null;
            /** Section */
            section: string;
            /** Step */
            step: number | null;
            /** Symbol */
            symbol: string | null;
            /** Unit */
            unit: string | null;
            visible_when?: components["schemas"]["CatalogCondition"] | null;
            /** Writable */
            writable: boolean;
        };
        /** ParametricGeometrySource */
        ParametricGeometrySource: {
            design: components["schemas"]["DesignConfig"];
            /**
             * Design Revision
             * @default 0
             */
            design_revision?: number;
            design_snapshot?: components["schemas"]["DesignSnapshot"] | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "parametric";
        };
        /** ParametricResultEnvelope */
        ParametricResultEnvelope: {
            /** Client Metadata */
            client_metadata: {
                [key: string]: components["schemas"]["JsonValue"];
            };
            /** Client Request Id */
            client_request_id: string | null;
            /** Frequencies */
            frequencies?: number[] | null;
            /** Frequency Status */
            frequency_status?: ("solved" | "interpolated")[] | null;
            /** Metadata */
            metadata: {
                [key: string]: unknown;
            };
            provenance: components["schemas"]["ResultProvenance"];
            /**
             * Result Contract Version
             * @constant
             */
            result_contract_version: 1;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            result_kind: "parametric";
        } & {
            [key: string]: unknown;
        };
        /**
         * PlanAdjustment
         * @description A change the runtime makes to the submitted design before solving it.
         *
         *     `bempp_wall_default` is the one kind today: a free-standing BEMPP solve
         *     whose wall thickness was left unset (`omitted`) or set to 0 mm, a bare
         *     shell (`explicit_zero`), is solved with an `effective_mm` closed wall.
         *     The explicit case is an override of what the user asked for, which is why
         *     the two are reported apart. `policy_version` names the rule that applied,
         *     so a run stays attributable if that rule later changes.
         */
        PlanAdjustment: {
            /** Effective Mm */
            effective_mm: number;
            /**
             * Kind
             * @constant
             */
            kind: "bempp_wall_default";
            /** Policy Version */
            policy_version: number;
            /** Reason Code */
            reason_code: string;
            /**
             * Requested
             * @enum {string}
             */
            requested: "omitted" | "explicit_zero";
        };
        /**
         * PolarConfig
         * @description Directivity observation contract shared by every solve engine.
         */
        PolarConfig: {
            /**
             * Angle Range
             * @default [
             *       0,
             *       180,
             *       37
             *     ]
             */
            angle_range?: [
                number,
                number,
                number
            ];
            /** Angle Step */
            angle_step?: number | null;
            /**
             * Distance
             * @default 2
             */
            distance?: number;
            /** Enabled Axes */
            enabled_axes?: ("horizontal" | "vertical" | "diagonal")[];
            /**
             * Field Plane
             * @default true
             */
            field_plane?: boolean;
            /**
             * Inclination
             * @default 45
             */
            inclination?: number;
            /**
             * Norm Angle
             * @default 5
             */
            norm_angle?: number;
            /**
             * Observation Origin
             * @default mouth
             * @enum {string}
             */
            observation_origin?: "mouth" | "throat";
            /**
             * Spherical Phi Count
             * @default 72
             */
            spherical_phi_count?: number;
            /**
             * Spherical Sampling
             * @default false
             */
            spherical_sampling?: boolean;
            /**
             * Spherical Theta Count
             * @default 37
             */
            spherical_theta_count?: number;
        };
        /** PrepareOperationRequest */
        PrepareOperationRequest: {
            approvals?: components["schemas"]["OperationApprovalsRequest"] | null;
            /** Frameaxis */
            frameAxis?: string | null;
            /** Setuprevisionid */
            setupRevisionId?: string | null;
            /**
             * Submit
             * @default true
             */
            submit?: boolean;
            /**
             * Wait
             * @default false
             */
            wait?: boolean;
        };
        /** ProgressRequest */
        ProgressRequest: {
            /** Attemptgeneration */
            attemptGeneration: number;
            /**
             * Stage
             * @enum {string}
             */
            stage: "queuedForFusion" | "executing";
        };
        /** ProjectSetupRequest */
        ProjectSetupRequest: {
            /** Inventory */
            inventory: components["schemas"]["SourceInventoryItem"][];
            /** Lineageid */
            lineageId: string;
            /** Setup */
            setup: {
                [key: string]: unknown;
            };
        };
        /** ROSSEConfig */
        ROSSEConfig: {
            R?: components["schemas"]["Expr"] | null;
            a?: components["schemas"]["Expr"] | null;
            a0?: components["schemas"]["Expr"] | null;
            b?: components["schemas"]["Expr"] | null;
            /** Coverage Mode */
            coverage_mode?: string | null;
            enclosure?: components["schemas"]["EnclosureConfig"] | null;
            /** Extra Blocks */
            extra_blocks?: {
                [key: string]: components["schemas"]["ConfigBlock"];
            };
            /** Extra Keys */
            extra_keys?: {
                [key: string]: string;
            };
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            formula: "R-OSSE";
            k?: components["schemas"]["Expr"] | null;
            /** Length Mode */
            length_mode?: ("profile" | "total") | null;
            m?: components["schemas"]["Expr"] | null;
            mesh?: components["schemas"]["MeshConfig"];
            morph?: components["schemas"]["MorphConfig"];
            output?: components["schemas"]["OutputConfig"];
            q?: components["schemas"]["Expr"] | null;
            r?: components["schemas"]["Expr"] | null;
            r0?: components["schemas"]["Expr"] | null;
            /** S1 */
            s1?: number | null;
            /** S2 */
            s2?: number | null;
            scale?: components["schemas"]["Expr"] | null;
            simulation?: components["schemas"]["SimulationConfig"];
            slot_length?: components["schemas"]["Expr"] | null;
            source?: components["schemas"]["SourceConfig"];
            throat_ext_angle?: components["schemas"]["Expr"] | null;
            throat_ext_length?: components["schemas"]["Expr"] | null;
            tmax?: components["schemas"]["Expr"] | null;
        };
        /** RadiationImpedanceAperture */
        RadiationImpedanceAperture: {
            /** Area M2 */
            area_m2: number;
            /** Name */
            name: string;
            /** Tag */
            tag: number;
        };
        /** RadiationImpedanceMatrix */
        RadiationImpedanceMatrix: {
            /** Imaginary */
            imaginary: number[][][];
            /** Real */
            real: number[][][];
        };
        /**
         * RadiationImpedancePresentation
         * @description Plot/export view of the lossless NPZ, always in engineering convention.
         */
        RadiationImpedancePresentation: {
            /** Apertures */
            apertures: components["schemas"]["RadiationImpedanceAperture"][];
            engineering_matrix: components["schemas"]["RadiationImpedanceMatrix"];
            /** Frequencies Hz */
            frequencies_hz: number[];
            in_phase_termination: components["schemas"]["RadiationImpedanceTermination"];
            /**
             * Phase Time Convention
             * @constant
             */
            phase_time_convention: "engineering_exp_plus_jwt";
            /**
             * Quantity
             * @constant
             */
            quantity: "average_aperture_pressure_per_volume_velocity";
            /**
             * Schema Version
             * @constant
             */
            schema_version: 1;
            /**
             * Units
             * @constant
             */
            units: "Pa*s/m^3";
        };
        /** RadiationImpedanceTermination */
        RadiationImpedanceTermination: {
            /** Aperture Names */
            aperture_names: string[];
            /** Imaginary */
            imaginary: number[][];
            /** Real */
            real: number[][];
        };
        /** RegistrationRequest */
        RegistrationRequest: {
            /** Adaptersessionid */
            adapterSessionId: string;
            /** Adapterversion */
            adapterVersion: string;
            /**
             * Cadapplication
             * @constant
             */
            cadApplication: "fusion360";
            /** Clientnonce */
            clientNonce: string;
            /** Clientproof */
            clientProof: string;
            /** Deliveryversion */
            deliveryVersion: number;
            /** Installationid */
            installationId: string;
            /** Liveprotocol */
            liveProtocol: number;
            loadedIdentity: components["schemas"]["LoadedIdentity"];
        };
        /** ResultProvenance */
        ResultProvenance: {
            cad_identity?: components["schemas"]["CadIdentityProvenance"] | null;
            /** Dependency Drift */
            dependency_drift?: string[] | null;
            /** Dependency Shas */
            dependency_shas: {
                [key: string]: string;
            };
            /** Effective Geometry Sha256 */
            effective_geometry_sha256: string;
            /** Effective Request Sha256 */
            effective_request_sha256: string;
            /** Effective Solve Options Sha256 */
            effective_solve_options_sha256: string;
            /** Execution Geometry Sha256 */
            execution_geometry_sha256: string;
            /** Execution Request Sha256 */
            execution_request_sha256: string;
            /** Execution Solve Options Sha256 */
            execution_solve_options_sha256: string;
            /** Geometry Sha256 */
            geometry_sha256: string;
            /** Installed Dependency Shas */
            installed_dependency_shas?: {
                [key: string]: string | null;
            } | null;
            /**
             * Request Identity
             * @constant
             */
            request_identity: "execution";
            /** Request Sha256 */
            request_sha256: string;
            /** Resolved Engine */
            resolved_engine: string;
            /**
             * Schema Version
             * @constant
             */
            schema_version: 1;
            /** Solve Options Sha256 */
            solve_options_sha256: string;
            /** Wg Version */
            wg_version: string;
        } & {
            [key: string]: unknown;
        };
        /**
         * SaveIdentity
         * @description The optimistic-concurrency token sent back by an open editor.
         */
        SaveIdentity: {
            /** Baseeditversion */
            baseEditVersion: number;
            /** Designid */
            designId: string;
            /** Lineageid */
            lineageId: string;
        };
        /** SelectCadWorkspaceRequest */
        SelectCadWorkspaceRequest: {
            /** Path */
            path: string;
        };
        /**
         * SelectWorkspaceRequest
         * @description A folder typed instead of chosen from the native picker.
         *
         *     The picker runs on the machine hosting the server, which is the right
         *     behaviour for the desktop launcher and useless when WG is reached from a
         *     browser on another machine. Accepting a path keeps that case workable
         *     without asking the browser for a directory handle only Chromium grants.
         */
        SelectWorkspaceRequest: {
            /** Path */
            path: string;
        };
        /** SetupRevisionRequest */
        SetupRevisionRequest: {
            /** Setup */
            setup: {
                [key: string]: unknown;
            };
        };
        /** SimulationConfig */
        SimulationConfig: {
            f1?: components["schemas"]["Expr"] | null;
            f2?: components["schemas"]["Expr"] | null;
            num_frequencies?: components["schemas"]["Expr"] | null;
            /** Sim Type */
            sim_type?: ("freestanding" | "infinite-baffle") | null;
            /** Solver Mode */
            solver_mode?: ("auto" | "full_3d" | "circsym") | null;
        };
        /** SolveAccepted */
        SolveAccepted: {
            /** Client Request Id */
            client_request_id?: string | null;
            /** Job Id */
            job_id: string;
        };
        /** SolveCommandOutcome */
        SolveCommandOutcome: {
            /** Commandid */
            commandId: string;
            /** Jobid */
            jobId?: string | null;
            /** Reason */
            reason?: string | null;
            /** State */
            state: string;
        };
        /** SolveExecution */
        SolveExecution: {
            /**
             * Accuracy
             * @enum {string}
             */
            accuracy: "fast" | "accurate";
            /** Engine */
            engine: string;
            /** Fallback Reason */
            fallback_reason?: string | null;
            /** Formulation */
            formulation: string | null;
        };
        /**
         * SolveOptions
         * @description Execution choices kept separate from the authoritative v2 design.
         */
        SolveOptions: {
            /**
             * Accuracy
             * @default fast
             * @enum {string}
             */
            accuracy?: "fast" | "accurate";
            /**
             * Adaptive Frequency Sampling
             * @default false
             */
            adaptive_frequency_sampling?: boolean;
            /**
             * Engine
             * @default auto
             */
            engine?: string;
            /** Frequencies Hz */
            frequencies_hz?: number[] | null;
            /** Frequency Range */
            frequency_range?: number[] | null;
            /**
             * Frequency Spacing
             * @default log
             * @enum {string}
             */
            frequency_spacing?: "log" | "linear";
            ground_plane?: components["schemas"]["GroundPlaneConfig"];
            /**
             * Mesh Ladder
             * @default off
             * @enum {string}
             */
            mesh_ladder?: "off" | "auto";
            /**
             * Mesh Validation Mode
             * @default warn
             * @enum {string}
             */
            mesh_validation_mode?: "warn" | "strict" | "off";
            /** Num Frequencies */
            num_frequencies?: number | null;
            polar_config?: components["schemas"]["PolarConfig"];
            /**
             * Solver Mode
             * @default full_3d
             * @enum {string}
             */
            solver_mode?: "auto" | "full_3d" | "circsym";
            /**
             * Stage Delay Ms
             * @default 30
             */
            stage_delay_ms?: number;
            /**
             * Symmetry
             * @default auto
             */
            symmetry?: string;
            /**
             * Verbose
             * @default false
             */
            verbose?: boolean;
        };
        /**
         * SolveOptionsResponse
         * @description Stored options are fully dumped; submission defaults remain optional.
         */
        SolveOptionsResponse: {
            /**
             * Accuracy
             * @enum {string}
             */
            accuracy: "fast" | "accurate";
            /**
             * Adaptive Frequency Sampling
             * @default false
             */
            adaptive_frequency_sampling?: boolean;
            /** Engine */
            engine: string;
            /** Frequencies Hz */
            frequencies_hz: number[] | null;
            /** Frequency Range */
            frequency_range: number[] | null;
            /**
             * Frequency Spacing
             * @enum {string}
             */
            frequency_spacing: "log" | "linear";
            ground_plane: components["schemas"]["GroundPlaneConfig"];
            /**
             * Mesh Ladder
             * @enum {string}
             */
            mesh_ladder: "off" | "auto";
            /**
             * Mesh Validation Mode
             * @enum {string}
             */
            mesh_validation_mode: "warn" | "strict" | "off";
            /** Num Frequencies */
            num_frequencies: number | null;
            polar_config: components["schemas"]["PolarConfig"];
            /**
             * Solver Mode
             * @enum {string}
             */
            solver_mode: "auto" | "full_3d" | "circsym";
            /** Stage Delay Ms */
            stage_delay_ms: number;
            /** Symmetry */
            symmetry: string;
            /** Verbose */
            verbose: boolean;
        };
        /**
         * SolvePlanResponse
         * @description The request-specific engine/formulation selected by the runtime.
         */
        SolvePlanResponse: {
            /**
             * Adjustments
             * @default []
             */
            adjustments?: components["schemas"]["PlanAdjustment"][];
            /** Eligibility Reasons */
            eligibility_reasons: string[];
            /** Engine */
            engine: string;
            engine_substitution?: components["schemas"]["EngineSubstitution"] | null;
            /**
             * Formulation
             * @constant
             */
            formulation: "full-3d";
            /** Reason */
            reason: string;
        };
        /** SolveRequest */
        SolveRequest: {
            /** Client Metadata */
            client_metadata?: {
                [key: string]: unknown;
            };
            /**
             * Client Request Id
             * @description Durable submission key: an identical replay returns the original job; the same key with a different normalized request is refused.
             */
            client_request_id?: string | null;
            /** Geometry */
            geometry: components["schemas"]["ParametricGeometrySource"] | components["schemas"]["ImportedGeometrySource"];
            /** Label */
            label?: string | null;
            options?: components["schemas"]["SolveOptions"];
            /** Parent Job Id */
            parent_job_id?: string | null;
        };
        /** SolverFrameConfirmationRequest */
        SolverFrameConfirmationRequest: {
            /** Axis */
            axis: string;
            /** Ingestid */
            ingestId?: string | null;
            /** Operationid */
            operationId?: string | null;
        };
        /**
         * SolverMeshRequest
         * @description The design exactly as a solve submission would carry it.
         */
        SolverMeshRequest: {
            design: components["schemas"]["DesignConfig"];
            /**
             * Symmetry
             * @default auto
             * @enum {string}
             */
            symmetry?: "auto" | "full" | "half_xz" | "half_yz" | "quarter";
        };
        /** SolverSelectionRequest */
        SolverSelectionRequest: {
            /**
             * Accuracy
             * @default fast
             * @enum {string}
             */
            accuracy?: "fast" | "accurate";
            /** Engine */
            engine: string;
        };
        /** SourceConfig */
        SourceConfig: {
            /** Contours */
            contours?: string | null;
            curvature?: components["schemas"]["Expr"] | null;
            radius?: components["schemas"]["Expr"] | null;
            shape?: components["schemas"]["Expr"] | null;
            velocity?: components["schemas"]["Expr"] | null;
            /** Velocity Convention */
            velocity_convention?: ("normal" | "axial" | "legacy") | null;
        };
        /**
         * SourceInventoryItem
         * @description One source of a return, as its manifest states it.
         */
        SourceInventoryItem: {
            /** Id */
            id: string;
            /** Required */
            required: boolean;
            /** Role */
            role: string;
        };
        /** StopResponse */
        StopResponse: {
            /** Message */
            message: string;
            /**
             * Status
             * @enum {string}
             */
            status: "cancelled" | "cancelling";
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** WgLinkExportRequest */
        WgLinkExportRequest: {
            /**
             * Basename
             * @default waveguide
             */
            baseName?: string;
            design: components["schemas"]["DesignConfig"];
            /** Designrevision */
            designRevision: number;
            /** Expectedfusiondocumentid */
            expectedFusionDocumentId?: string | null;
            /** Expectedfusioninstanceid */
            expectedFusionInstanceId?: string | null;
            /** Expectedfusionreturnstatehash */
            expectedFusionReturnStateHash?: string | null;
            identity?: components["schemas"]["SaveIdentity"] | null;
            /**
             * Modelname
             * @default MWG Horn
             */
            modelName?: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    acl_repair_status_api_acl_repair_status_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    cad_workspace_capture_document_api_cad_workspace_capture_document_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaptureDocumentRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cad_workspace_open_api_cad_workspace_open_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    cad_workspace_path_api_cad_workspace_path_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    cad_workspace_select_api_cad_workspace_select_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["SelectCadWorkspaceRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_delivery_status_api_cadlink_delivery_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    list_designs_api_cadlink_designs_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    get_design_api_cadlink_designs__design_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                design_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_domain_interpretation_api_cadlink_domain_interpretation_get: {
        parameters: {
            query?: {
                operationId?: string | null;
                ingestId?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_domain_interpretation_api_cadlink_domain_interpretation_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DomainReadingRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fusion_status_api_cadlink_fusion_status_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FusionStatusRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_ingest_api_cadlink_ingest_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CadReturnIngestRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_ingest_api_cadlink_ingest__ingest_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                ingest_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_ingest_mesh_api_cadlink_ingest__ingest_id__mesh_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                ingest_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/plain": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_ingest_viewport_mesh_api_cadlink_ingest__ingest_id__viewport_mesh_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                ingest_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/plain": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_install_addin_api_cadlink_install_addin_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    post_live_delivery_api_cadlink_live_deliveries_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeliveryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description snapshot_not_readable or store_busy: retry after Retry-After */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    live_endpoint_hello_api_cadlink_live_endpoint_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description store_busy: the live session service is not running */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    post_live_heartbeat_api_cadlink_live_heartbeat_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description store_busy: the live session service is not running */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    poll_fusion_requests_api_cadlink_live_requests_get: {
        parameters: {
            query?: {
                waitSeconds?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description store_busy: the live session service is not running */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    claim_fusion_request_api_cadlink_live_requests__operationId__claim_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operationId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClaimRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description store_busy: the live session service is not running */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    complete_fusion_request_api_cadlink_live_requests__operationId__complete_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operationId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CompletionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description store_busy: the live session service is not running */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    fusion_request_progress_api_cadlink_live_requests__operationId__progress_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operationId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProgressRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description store_busy: the live session service is not running */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    register_live_session_api_cadlink_live_sessions_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RegistrationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description store_busy: the live session service is not running */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    end_live_session_api_cadlink_live_sessions_current_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description store_busy: the live session service is not running */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    refresh_live_session_api_cadlink_live_sessions_refresh_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description invalid_request (no input echoed) or installation_mismatch */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description store_busy: the live session service is not running */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Refused; error.code names why (never 422) */
            "4XX": {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    connection_api_cadlink_onshape_connection_get: {
        parameters: {
            query?: {
                refresh?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    return_to_wg_api_cadlink_onshape_return_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OnshapeReturnRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    send_api_cadlink_onshape_send_post: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OnshapeSendRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    status_api_cadlink_onshape_status_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OnshapeStatusRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    unlink_api_cadlink_onshape_unlink_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OnshapeUnlinkRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_cad_operations_api_cadlink_operations_get: {
        parameters: {
            query?: {
                pending?: boolean;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_cad_operation_api_cadlink_operations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ManualSolveOperationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ManualSolveOperationResponse"];
                };
            };
            /** @description CAD ingest not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Update restart pending, operation id conflict, or retained snapshot unavailable */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_cad_operation_api_cadlink_operations__operation_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_cad_operation_approvals_api_cadlink_operations__operation_id__approvals_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OperationApprovalsRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_cancel_cad_operation_api_cadlink_operations__operation_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_prepare_cad_operation_api_cadlink_operations__operation_id__prepare_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrepareOperationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_reconcile_cad_operation_api_cadlink_operations__operation_id__reconcile_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ManualSolveOperationResponse"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_project_setup_api_cadlink_project_setups_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProjectSetupRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_project_documents_api_cadlink_projects__lineage_id__documents_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                lineage_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    download_project_document_api_cadlink_projects__lineage_id__documents__return_state_hash__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                lineage_id: string;
                return_state_hash: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reveal_project_folder_api_cadlink_projects__lineage_id__reveal_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                lineage_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_fusion_return_api_cadlink_request_fusion_return_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FusionReturnRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_returns_api_cadlink_returns_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    archive_run_document_api_cadlink_runs_archive_document_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ArchiveRunDocumentRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_setup_revision_api_cadlink_setup_revisions_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SetupRevisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_setup_revision_api_cadlink_setup_revisions__revision_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                revision_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_solve_command_api_cadlink_solve_command_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    post_solve_command_outcome_api_cadlink_solve_command_outcome_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SolveCommandOutcome"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_solver_frame_api_cadlink_solver_frame_get: {
        parameters: {
            query?: {
                operationId?: string | null;
                ingestId?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_solver_frame_api_cadlink_solver_frame_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SolverFrameConfirmationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_solver_selection_api_cadlink_solver_selection_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SolverSelectionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    capabilities_api_capabilities_get: {
        parameters: {
            query?: {
                refresh?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    import_report_endpoint_api_design_import_report_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "text/plain": string;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    open_endpoint_api_design_open_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "text/plain": string;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_endpoint_api_design_save_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    serialize_endpoint_api_design_serialize_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    design_symmetry_api_design_symmetry_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DesignConfig"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    download_bundle_api_diagnostics_bundle_get: {
        parameters: {
            query?: {
                job?: string | null;
                design?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    record_client_error_api_diagnostics_client_log_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": unknown;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    open_logs_api_diagnostics_open_logs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    read_summary_api_diagnostics_summary_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    search_drivers_api_drivers_get: {
        parameters: {
            query?: {
                q?: string;
                kind?: "lf" | "cd" | "all";
                z?: number | null;
                limit?: number;
                complete?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DriverSearchResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    library_info_api_drivers_library_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DriverLibraryInfo"];
                };
            };
        };
    };
    library_rescan_api_drivers_library_rescan_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DriverLibraryInfo"];
                };
            };
        };
    };
    get_driver_api_drivers__driver_id__get: {
        parameters: {
            query?: {
                complete?: boolean;
            };
            header?: never;
            path: {
                driver_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DriverDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_profiles_api_export_profiles_post: {
        parameters: {
            query?: {
                kind?: "profiles" | "slices";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExportRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_step_api_export_step_post: {
        parameters: {
            query?: {
                body?: "solid" | "surface";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExportRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_stl_api_export_stl_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExportRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_wglink_api_export_wglink_post: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WgLinkExportRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    design_schema_api_integration_v1_design_schema_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/schema+json": components["schemas"]["DesignSchemaDocument"];
                };
            };
        };
    };
    parameter_catalog_api_integration_v1_parameters_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ParameterCatalog"];
                };
            };
        };
    };
    list_jobs_api_jobs_get: {
        parameters: {
            query?: {
                status?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobListResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cad_solve_api_jobs_cad_solve_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CadSolveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SolveAccepted"];
                };
            };
            /** @description Unknown ingest or setup revision */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Restart pending, conflicting ingest or snapshot unavailable */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    clear_failed_api_jobs_clear_failed_delete: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClearFailedResponse"];
                };
            };
        };
    };
    delete_job_api_jobs__job_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeleteResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cad_approvals_api_jobs__job_id__approvals_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CadApprovalsRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobStatusResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    job_archive_snapshot_api_jobs__job_id__archive_snapshot_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    dismiss_cad_job_api_jobs__job_id__dismiss_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeleteResponse"];
                };
            };
            /** @description Not a refused CAD solve, or still being prepared */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    job_log_api_jobs__job_id__log_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/plain": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    patch_job_metadata_api_jobs__job_id__metadata_patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["JobMetadataPatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MetadataResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retry_job_api_jobs__job_id__retry_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SolveAccepted"];
                };
            };
            /** @description An update restart is pending (error envelope), or the job has no solve request to replay (`detail`), for example one still being prepared */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    solve_again_api_jobs__job_id__solve_again_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CadSolveAgainRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SolveAccepted"];
                };
            };
            /** @description Restart pending or job cannot be continued */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    mesh_artifact_api_mesh_artifact__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/plain": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    partial_job_results_api_partial_results__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pressure_basis_artifact_api_pressure_basis__job_id__get: {
        parameters: {
            query?: {
                channel_id?: string | null;
            };
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    radiation_impedance_artifact_api_radiation_impedance__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    radiation_impedance_presentation_api_radiation_impedance__job_id__presentation_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RadiationImpedancePresentation"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    render_charts_api_render_charts_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChartsRenderRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    render_directivity_api_render_directivity_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DirectivityRenderRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    job_results_api_results__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Versioned solver result */
            200: {
                headers: {
                    /** @description SHA-256 identity of the served bytes: the stored bytes, or an archived record with read-time power-qualification flags added */
                    ETag?: string;
                    /** @description Hex SHA-256 of the served bytes */
                    "X-WG-Results-SHA256"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ParametricResultEnvelope"] | components["schemas"]["MultiChannelResultEnvelope"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    recombine_job_results_api_results__job_id__combine_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChannelCombineSpec"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    field_plane_api_results__job_id__field_plane_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FieldPlaneRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Invalid field-plane selection, unsupported solve with an actionable remedy, or invalid request body */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FieldPlaneUnavailableResponse"] | components["schemas"]["FieldPlaneStringErrorResponse"] | components["schemas"]["FieldPlaneValidationErrorResponse"];
                };
            };
        };
    };
    read_settings_api_settings_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    write_namespace_api_settings__namespace__put: {
        parameters: {
            query?: never;
            header?: {
                "X-WG-Settings-Writer"?: string | null;
                "X-WG-Settings-Seq"?: number | null;
            };
            path: {
                namespace: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": unknown;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description A later write from the same writer is already stored */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_namespace_api_settings__namespace__delete: {
        parameters: {
            query?: never;
            header?: {
                "X-WG-Settings-Writer"?: string | null;
                "X-WG-Settings-Seq"?: number | null;
            };
            path: {
                namespace: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description A later write from the same writer is already stored */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    submit_solve_api_solve_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SolveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SolveAccepted"];
                };
            };
            /** @description Submission key conflict, or an update restart is pending */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Solve request refused */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Engine unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    plan_imported_solve_api_solve_imported_plan_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SolveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportedSolvePlanResponse"];
                };
            };
            /** @description Imported request refused */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    plan_solve_api_solve_plan_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SolveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SolvePlanResponse"];
                };
            };
            /** @description Solve request refused */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Engine unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    solver_mesh_api_solver_mesh_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SolverMeshRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    job_status_api_status__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobStatusResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stop_job_api_stop__job_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StopResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    theme_preview_api_theme_preview_get: {
        parameters: {
            query?: {
                theme?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    themes_api_themes_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    update_channel_api_updates_channel_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    choose_update_channel_api_updates_channel_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Body_choose_update_channel_api_updates_channel_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_diagnostics_api_updates_diagnostics_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    install_update_api_updates_install_post: {
        parameters: {
            query?: never;
            header?: {
                "X-WG-Update"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reset_installer_download_api_updates_reset_post: {
        parameters: {
            query?: never;
            header?: {
                "X-WG-Update"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retry_held_back_build_api_updates_retry_post: {
        parameters: {
            query?: never;
            header?: {
                "X-WG-Update"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["HeldBackBuild"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_status_api_updates_status_get: {
        parameters: {
            query?: {
                refresh?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_destination_api_workspace_export_destination_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    choose_export_destination_api_workspace_export_destination_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ChooseExportDestinationRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    workspace_open_api_workspace_open_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    workspace_path_api_workspace_path_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    workspace_select_api_workspace_select_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["SelectWorkspaceRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    workspace_write_export_api_workspace_write_export_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    /** Destination */
                    destination?: string | null;
                    /**
                     * Existing
                     * @default reject
                     * @enum {string}
                     */
                    existing?: "reject" | "merge_identical" | "overwrite" | "confirm";
                    /** Members */
                    members: {
                        /** Content Base64 */
                        content_base64?: string | null;
                        /** Relative Path */
                        relative_path: string;
                        /** Text */
                        text?: string | null;
                    }[];
                    /**
                     * Subdirectory
                     * @default
                     */
                    subdirectory?: string;
                };
                "multipart/form-data": {
                    /** @description Handle from POST /api/workspace/export-destination. Absent writes into the workspace. */
                    destination?: string;
                    /**
                     * @default reject
                     * @enum {string}
                     */
                    existing?: "reject" | "merge_identical" | "overwrite" | "confirm";
                    file: string[];
                    relative_path: string[];
                    subdirectory: string;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
}
