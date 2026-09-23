SET XACT_ABORT ON;
GO

CREATE OR ALTER PROCEDURE control.usp_issue_approval
    @approval_id nvarchar(128),
    @plan_id nvarchar(128),
    @task_id nvarchar(128),
    @engagement_id nvarchar(128),
    @workspace_id nvarchar(128),
    @logical_agent_id nvarchar(128),
    @plan_hash char(64),
    @operation_id nvarchar(128),
    @target_resource nvarchar(256),
    @safe_query_version nvarchar(64),
    @approved_by_hash char(64),
    @approved_at datetime2(0),
    @expires_at datetime2(0),
    @one_time_id nvarchar(128),
    @receipt_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @operation_id <> N'remediate.activate-cycle-safe-query-v1' OR @expires_at <= @approved_at
        THROW 51000, 'approval_policy_denied', 1;

    BEGIN TRANSACTION;
    BEGIN TRY
        IF NOT EXISTS (
            SELECT 1
            FROM control.ResolutionPlans WITH (UPDLOCK, HOLDLOCK)
            WHERE PlanId = @plan_id
              AND TaskId = @task_id
              AND EngagementId = @engagement_id
              AND WorkspaceId = @workspace_id
              AND LogicalAgentId = @logical_agent_id
              AND PlanHash = @plan_hash
              AND OperationId = @operation_id
              AND TargetResource = @target_resource
              AND SafeQueryVersion = @safe_query_version
              AND State = N'awaiting_approval'
        ) THROW 51001, 'approval_plan_binding_mismatch', 1;

        INSERT control.Approvals (
            ApprovalId, PlanId, TaskId, EngagementId, WorkspaceId, LogicalAgentId,
            PlanHash, OperationId, TargetResource, SafeQueryVersion, ApprovedByHash,
            ApprovedAt, ExpiresAt, OneTimeId, State, ReceiptHash
        ) VALUES (
            @approval_id, @plan_id, @task_id, @engagement_id, @workspace_id, @logical_agent_id,
            @plan_hash, @operation_id, @target_resource, @safe_query_version, @approved_by_hash,
            @approved_at, @expires_at, @one_time_id, N'approved', @receipt_hash
        );
        UPDATE control.ResolutionPlans
        SET State = N'approved', Version = Version + 1
        WHERE PlanId = @plan_id AND State = N'awaiting_approval';
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson)
        VALUES (
            @task_id,
            N'approval',
            N'approval_issued',
            (SELECT @approval_id AS approval_id, @plan_hash AS plan_hash FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)
        );
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_get_diagnostic_snapshot
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        COALESCE((
            SELECT RunId AS run_id, State AS state
            FROM lab.QueryRuns
            ORDER BY StartedAt, RunId
            FOR JSON PATH
        ), N'[]') AS query_run_states_json,
        COALESCE((
            SELECT TOP (1) QueryVersion
            FROM lab.QueryVersions
            WHERE IsActive = 1
        ), N'') AS active_query_version;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_register_query_run
    @run_id nvarchar(128),
    @lease_id nvarchar(128),
    @owner_instance nvarchar(128),
    @watchdog_deadline datetime2(0)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @run_id NOT LIKE N'run-%' OR @lease_id NOT LIKE N'lease-%' OR @watchdog_deadline <= SYSUTCDATETIME()
        THROW 51010, 'query_run_registration_denied', 1;
    INSERT lab.QueryRuns (RunId, QueryVersion, LeaseId, OwnerInstance, State, WatchdogDeadline)
    VALUES (@run_id, N'cycle-unsafe-v1', @lease_id, @owner_instance, N'starting', @watchdog_deadline);
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_mark_query_running
    @run_id nvarchar(128),
    @lease_id nvarchar(128)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE lab.QueryRuns
    SET State = N'running'
    WHERE RunId = @run_id AND LeaseId = @lease_id AND State = N'starting';
    IF @@ROWCOUNT <> 1 THROW 51011, 'query_run_lease_mismatch', 1;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_request_owned_query_cancel
    @run_id nvarchar(128)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE lab.QueryRuns WITH (UPDLOCK, ROWLOCK)
    SET State = N'cancel_requested', CancelRequestedAt = SYSUTCDATETIME()
    WHERE RunId = @run_id AND State = N'running';
    IF @@ROWCOUNT <> 1 THROW 51012, 'owned_query_not_running', 1;
    SELECT RunId AS run_id, LeaseId AS lease_id, State AS state
    FROM lab.QueryRuns WHERE RunId = @run_id;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_confirm_owned_query_cancelled
    @run_id nvarchar(128),
    @lease_id nvarchar(128)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE lab.QueryRuns WITH (UPDLOCK, ROWLOCK)
    SET State = N'cancelled', CompletedAt = SYSUTCDATETIME()
    WHERE RunId = @run_id AND LeaseId = @lease_id AND State = N'cancel_requested';
    IF @@ROWCOUNT <> 1 THROW 51013, 'query_cancel_confirmation_denied', 1;
    SELECT N'owned_query_cancelled' AS result_code, RunId AS run_id, State AS state
    FROM lab.QueryRuns WHERE RunId = @run_id;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_mark_query_failed
    @run_id nvarchar(128),
    @lease_id nvarchar(128),
    @failure_code nvarchar(128)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE lab.QueryRuns
    SET State = N'failed', FailureCode = @failure_code, CompletedAt = SYSUTCDATETIME()
    WHERE RunId = @run_id AND LeaseId = @lease_id AND State IN (N'starting', N'running', N'cancel_requested');
    IF @@ROWCOUNT <> 1 THROW 51014, 'query_failure_confirmation_denied', 1;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_expire_query_leases
    @now_utc datetime2(0)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE lab.QueryRuns WITH (UPDLOCK, ROWLOCK)
    SET State = N'cancel_requested', CancelRequestedAt = @now_utc, FailureCode = N'watchdog_expired'
    WHERE State IN (N'starting', N'running') AND WatchdogDeadline <= @now_utc;
    SELECT RunId AS run_id, LeaseId AS lease_id, OwnerInstance AS owner_instance
    FROM lab.QueryRuns
    WHERE State = N'cancel_requested' AND FailureCode = N'watchdog_expired' AND CompletedAt IS NULL;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_run_controlled_unsafe_query
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    ;WITH hierarchy AS (
        SELECT ParentNode, ChildNode, CAST(1 AS int) AS depth
        FROM lab.HierarchyEdges
        WHERE ParentNode = N'A'
        UNION ALL
        SELECT edge.ParentNode, edge.ChildNode, hierarchy.depth + 1
        FROM hierarchy
        INNER JOIN lab.HierarchyEdges AS edge ON edge.ParentNode = hierarchy.ChildNode
    )
    SELECT COUNT_BIG(*) AS unreachable_count
    FROM hierarchy
    OPTION (MAXRECURSION 0, MAXDOP 1);
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_run_cycle_safe_query
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    ;WITH hierarchy AS (
        SELECT
            ParentNode,
            ChildNode,
            CAST(N'/' + ParentNode + N'/' + ChildNode + N'/' AS nvarchar(4000)) AS visited_path,
            CAST(1 AS int) AS depth
        FROM lab.HierarchyEdges
        WHERE ParentNode = N'A'
        UNION ALL
        SELECT
            edge.ParentNode,
            edge.ChildNode,
            CAST(hierarchy.visited_path + edge.ChildNode + N'/' AS nvarchar(4000)),
            hierarchy.depth + 1
        FROM hierarchy
        INNER JOIN lab.HierarchyEdges AS edge ON edge.ParentNode = hierarchy.ChildNode
        WHERE hierarchy.depth < 16
          AND CHARINDEX(N'/' + edge.ChildNode + N'/', hierarchy.visited_path) = 0
    )
    SELECT ParentNode AS parent_node, ChildNode AS child_node, depth
    FROM hierarchy
    ORDER BY depth, ParentNode, ChildNode
    OPTION (MAXRECURSION 16, MAXDOP 1);
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_activate_cycle_safe_query
    @approval_id nvarchar(128),
    @plan_id nvarchar(128),
    @task_id nvarchar(128),
    @engagement_id nvarchar(128),
    @workspace_id nvarchar(128),
    @logical_agent_id nvarchar(128),
    @plan_hash char(64),
    @operation_id nvarchar(128),
    @target_resource nvarchar(256),
    @safe_query_version nvarchar(64),
    @approved_by_hash char(64),
    @approved_at datetime2(0),
    @expires_at datetime2(0),
    @one_time_id nvarchar(128),
    @expected_approval_version int,
    @receipt_hash char(64),
    @now_utc datetime2(0)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @operation_id <> N'remediate.activate-cycle-safe-query-v1' OR @safe_query_version <> N'cycle-safe-v1'
        THROW 51020, 'remediation_operation_denied', 1;

    BEGIN TRANSACTION;
    BEGIN TRY
        IF NOT EXISTS (
            SELECT 1
            FROM control.Approvals WITH (UPDLOCK, HOLDLOCK)
            WHERE ApprovalId = @approval_id
              AND PlanId = @plan_id
              AND TaskId = @task_id
              AND EngagementId = @engagement_id
              AND WorkspaceId = @workspace_id
              AND LogicalAgentId = @logical_agent_id
              AND PlanHash = @plan_hash
              AND OperationId = @operation_id
              AND TargetResource = @target_resource
              AND SafeQueryVersion = @safe_query_version
              AND ApprovedByHash = @approved_by_hash
              AND ApprovedAt = @approved_at
              AND ExpiresAt = @expires_at
              AND OneTimeId = @one_time_id
              AND Version = @expected_approval_version
              AND ReceiptHash = @receipt_hash
              AND State = N'approved'
              AND ConsumedAt IS NULL
              AND ExpiresAt > @now_utc
        ) THROW 51021, 'approval_binding_mismatch', 1;

        IF NOT EXISTS (
            SELECT 1 FROM lab.QueryVersions WITH (UPDLOCK, HOLDLOCK)
            WHERE QueryVersion = @safe_query_version AND QueryKind = N'safe'
        ) THROW 51022, 'safe_query_version_not_registered', 1;

        UPDATE lab.QueryVersions SET IsActive = 0 WHERE IsActive = 1;
        UPDATE lab.QueryVersions SET IsActive = 1 WHERE QueryVersion = @safe_query_version;
        UPDATE control.Approvals
        SET State = N'consumed', ConsumedAt = @now_utc, Version = Version + 1
        WHERE ApprovalId = @approval_id AND State = N'approved' AND ConsumedAt IS NULL;
        IF @@ROWCOUNT <> 1 THROW 51023, 'approval_replay_denied', 1;
        UPDATE control.ResolutionPlans
        SET State = N'consumed', Version = Version + 1
        WHERE PlanId = @plan_id AND State = N'approved';
        IF @@ROWCOUNT <> 1 THROW 51024, 'plan_state_mismatch', 1;
        UPDATE control.Tasks
        SET State = N'remediated', Version = Version + 1, UpdatedAt = @now_utc
        WHERE TaskId = @task_id AND State = N'contained';
        IF @@ROWCOUNT <> 1 THROW 51025, 'task_state_mismatch', 1;
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson, CreatedAt)
        VALUES (
            @task_id,
            N'remediation',
            N'safe_query_activated',
            (
                SELECT
                    @approval_id AS approval_id,
                    @plan_hash AS plan_hash,
                    @safe_query_version AS safe_query_version
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
            ),
            @now_utc
        );
        COMMIT TRANSACTION;
        SELECT
            N'safe_query_activated' AS result_code,
            @operation_id AS operation_id,
            @safe_query_version AS safe_query_version;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE ops.usp_verify_cycle_recovery
    @safe_query_version nvarchar(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @result_count int;
    ;WITH hierarchy AS (
        SELECT
            ParentNode,
            ChildNode,
            CAST(N'/' + ParentNode + N'/' + ChildNode + N'/' AS nvarchar(4000)) AS visited_path,
            CAST(1 AS int) AS depth
        FROM lab.HierarchyEdges
        WHERE ParentNode = N'A'
        UNION ALL
        SELECT
            edge.ParentNode,
            edge.ChildNode,
            CAST(hierarchy.visited_path + edge.ChildNode + N'/' AS nvarchar(4000)),
            hierarchy.depth + 1
        FROM hierarchy
        INNER JOIN lab.HierarchyEdges AS edge ON edge.ParentNode = hierarchy.ChildNode
        WHERE hierarchy.depth < 16
          AND CHARINDEX(N'/' + edge.ChildNode + N'/', hierarchy.visited_path) = 0
    )
    SELECT @result_count = COUNT(*) FROM hierarchy OPTION (MAXRECURSION 16, MAXDOP 1);

    SELECT
        CAST(CASE WHEN EXISTS (
            SELECT 1 FROM lab.QueryVersions WHERE QueryVersion = @safe_query_version AND QueryKind = N'safe' AND IsActive = 1
        ) THEN 1 ELSE 0 END AS bit) AS safe_query_version_active,
        CAST(CASE WHEN NOT EXISTS (
            SELECT 1 FROM lab.QueryRuns WHERE State IN (N'starting', N'running', N'cancel_requested')
        ) THEN 1 ELSE 0 END AS bit) AS no_owned_query_running,
        CAST(CASE WHEN @result_count = 3 THEN 1 ELSE 0 END AS bit) AS deterministic_result;
END;
GO