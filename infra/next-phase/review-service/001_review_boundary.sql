SET XACT_ABORT ON;
GO

IF COL_LENGTH(N'control.ResolutionPlans', N'CreatedByScheme') IS NULL
    ALTER TABLE control.ResolutionPlans ADD CreatedByScheme nvarchar(64) NULL;
GO

CREATE OR ALTER PROCEDURE control.usp_list_review_plans
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (100)
        (
            SELECT plan_record.PlanId AS plan_id,
                JSON_QUERY((
                    SELECT plan_record.TaskId AS task_id, plan_record.EngagementId AS engagement_id,
                        plan_record.WorkspaceId AS workspace_id, plan_record.LogicalAgentId AS logical_agent_id,
                        plan_record.OperationId AS operation_id, plan_record.TargetResource AS target_resource,
                        plan_record.SafeQueryVersion AS safe_query_version, plan_record.RollbackVersion AS rollback_version,
                        JSON_QUERY(plan_record.ParametersJson) AS parameters
                    FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
                )) AS content,
                plan_record.PlanHash AS plan_hash, plan_record.State AS state,
                plan_record.Version AS version, plan_record.CreatedByHash AS created_by_hash,
                CONVERT(varchar(19), plan_record.CreatedAt, 126) + 'Z' AS created_at
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
        ) AS plan_json,
        plan_record.CreatedByScheme AS author_identity_scheme,
        CONVERT(varchar(19), CASE WHEN engagement.ExpiresAt < agent.ExpiresAt
            THEN engagement.ExpiresAt ELSE agent.ExpiresAt END, 126) + 'Z' AS expires_at
    FROM control.ResolutionPlans AS plan_record
    INNER JOIN control.Engagements AS engagement ON engagement.EngagementId = plan_record.EngagementId
    INNER JOIN control.AgentRegistrations AS agent ON agent.LogicalAgentId = plan_record.LogicalAgentId
    WHERE plan_record.OperationId = N'remediate.activate-cycle-safe-query-v1'
            AND plan_record.CreatedByScheme = N'entra-tenant-oid-v1'
      AND plan_record.State IN (N'awaiting_approval', N'approved', N'rejected', N'consumed')
      AND plan_record.TargetResource = N'lab.QueryVersions/cycle-safe-v1'
      AND engagement.State = N'engaged' AND engagement.ExpiresAt > SYSUTCDATETIME()
      AND agent.State = N'active' AND agent.ExpiresAt > SYSUTCDATETIME()
    ORDER BY plan_record.CreatedAt DESC, plan_record.PlanId;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_decide_review_plan
    @plan_id nvarchar(128),
    @expected_plan_hash char(64),
    @expected_plan_version int,
    @reviewer_hash char(64),
    @decision nvarchar(16),
    @receipt_json nvarchar(max)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @now_utc datetime2(0) = SYSUTCDATETIME();
    IF @decision NOT IN (N'approve', N'reject')
       OR @reviewer_hash IS NULL OR LEN(@reviewer_hash) <> 64
       OR TRY_CONVERT(varbinary(32), @reviewer_hash, 2) IS NULL
       OR @expected_plan_version IS NULL OR @expected_plan_version < 1
       OR @expected_plan_hash IS NULL
        THROW 51200, 'review_request_denied', 1;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @task_id nvarchar(128), @engagement_id nvarchar(128), @workspace_id nvarchar(128),
            @logical_agent_id nvarchar(128), @operation_id nvarchar(128), @target_resource nvarchar(256),
            @safe_query_version nvarchar(64), @context_expiry datetime2(0);
        SELECT @task_id = plan_record.TaskId, @engagement_id = plan_record.EngagementId,
            @workspace_id = plan_record.WorkspaceId, @logical_agent_id = plan_record.LogicalAgentId,
            @operation_id = plan_record.OperationId, @target_resource = plan_record.TargetResource,
            @safe_query_version = plan_record.SafeQueryVersion,
            @context_expiry = CASE WHEN engagement.ExpiresAt < agent.ExpiresAt
                THEN engagement.ExpiresAt ELSE agent.ExpiresAt END
        FROM control.ResolutionPlans AS plan_record WITH (UPDLOCK, HOLDLOCK)
        INNER JOIN control.Tasks AS task WITH (UPDLOCK, HOLDLOCK) ON task.TaskId = plan_record.TaskId
        INNER JOIN control.Engagements AS engagement WITH (UPDLOCK, HOLDLOCK) ON engagement.EngagementId = plan_record.EngagementId
        INNER JOIN control.AgentRegistrations AS agent WITH (UPDLOCK, HOLDLOCK) ON agent.LogicalAgentId = plan_record.LogicalAgentId
        WHERE plan_record.PlanId = @plan_id AND plan_record.PlanHash = @expected_plan_hash
                    AND plan_record.CreatedByScheme = N'entra-tenant-oid-v1'
          AND plan_record.Version = @expected_plan_version AND plan_record.State = N'awaiting_approval'
          AND plan_record.CreatedByHash <> @reviewer_hash
          AND task.State = N'contained'
          AND engagement.TaskId = task.TaskId AND engagement.WorkspaceId = plan_record.WorkspaceId
          AND engagement.State = N'engaged' AND engagement.ExpiresAt > @now_utc
          AND agent.EngagementId = engagement.EngagementId AND agent.WorkspaceId = plan_record.WorkspaceId
          AND agent.State = N'active' AND agent.ExpiresAt > @now_utc
          AND plan_record.OperationId = N'remediate.activate-cycle-safe-query-v1'
          AND plan_record.TargetResource = N'lab.QueryVersions/cycle-safe-v1'
          AND plan_record.SafeQueryVersion = N'cycle-safe-v1'
          AND plan_record.RollbackVersion = N'cycle-unsafe-v1';
        IF @task_id IS NULL THROW 51201, 'review_plan_changed_expired_or_self_review', 1;

        IF @decision = N'reject'
        BEGIN
            IF @receipt_json IS NOT NULL THROW 51202, 'rejection_cannot_issue_receipt', 1;
            UPDATE control.ResolutionPlans SET State = N'rejected', Version = Version + 1 WHERE PlanId = @plan_id;
        END
        ELSE
        BEGIN
            IF @receipt_json IS NULL OR ISJSON(@receipt_json) <> 1 THROW 51203, 'review_receipt_invalid', 1;
            DECLARE @approval_id nvarchar(128) = JSON_VALUE(@receipt_json, '$.approval_id'),
                @one_time_id nvarchar(128) = JSON_VALUE(@receipt_json, '$.one_time_id'),
                @receipt_hash char(64) = JSON_VALUE(@receipt_json, '$.receipt_hash'),
                @approved_at datetime2(0) = TRY_CONVERT(datetime2(0), JSON_VALUE(@receipt_json, '$.approved_at'), 127),
                @expires_at datetime2(0) = TRY_CONVERT(datetime2(0), JSON_VALUE(@receipt_json, '$.expires_at'), 127);
            IF @approval_id IS NULL OR @approval_id NOT LIKE N'approval-%'
               OR @one_time_id IS NULL OR @one_time_id NOT LIKE N'grant-%'
               OR @receipt_hash IS NULL OR TRY_CONVERT(varbinary(32), @receipt_hash, 2) IS NULL
               OR @approved_at IS NULL OR @expires_at IS NULL
               OR @approved_at < DATEADD(second, -60, @now_utc) OR @approved_at > DATEADD(second, 5, @now_utc)
               OR @expires_at <= @now_utc OR @expires_at > DATEADD(second, 900, @approved_at)
               OR @expires_at > @context_expiry
               OR COALESCE(JSON_VALUE(@receipt_json, '$.plan_id'), N'') <> @plan_id
               OR COALESCE(JSON_VALUE(@receipt_json, '$.task_id'), N'') <> @task_id
               OR COALESCE(JSON_VALUE(@receipt_json, '$.engagement_id'), N'') <> @engagement_id
               OR COALESCE(JSON_VALUE(@receipt_json, '$.workspace_id'), N'') <> @workspace_id
               OR COALESCE(JSON_VALUE(@receipt_json, '$.logical_agent_id'), N'') <> @logical_agent_id
               OR COALESCE(JSON_VALUE(@receipt_json, '$.plan_hash'), N'') <> @expected_plan_hash
               OR COALESCE(JSON_VALUE(@receipt_json, '$.approved_by_hash'), N'') <> @reviewer_hash
               OR COALESCE(JSON_VALUE(@receipt_json, '$.operation_id'), N'') <> @operation_id
               OR COALESCE(JSON_VALUE(@receipt_json, '$.target_resource'), N'') <> @target_resource
               OR COALESCE(JSON_VALUE(@receipt_json, '$.safe_query_version'), N'') <> @safe_query_version
               OR COALESCE(JSON_VALUE(@receipt_json, '$.version'), N'') <> N'1'
               OR JSON_VALUE(@receipt_json, '$.consumed_at') IS NOT NULL
                THROW 51204, 'review_receipt_binding_denied', 1;
            EXEC control.usp_issue_approval
                @approval_id = @approval_id, @plan_id = @plan_id, @task_id = @task_id,
                @engagement_id = @engagement_id, @workspace_id = @workspace_id,
                @logical_agent_id = @logical_agent_id, @plan_hash = @expected_plan_hash,
                @operation_id = @operation_id, @target_resource = @target_resource,
                @safe_query_version = @safe_query_version, @approved_by_hash = @reviewer_hash,
                @approved_at = @approved_at, @expires_at = @expires_at,
                @one_time_id = @one_time_id, @receipt_hash = @receipt_hash;
        END;
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson, CreatedAt)
        VALUES (@task_id, N'human_review', @decision,
            (SELECT @plan_id AS plan_id, @expected_plan_hash AS plan_hash,
                @expected_plan_version AS reviewed_version, @reviewer_hash AS reviewer_hash
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER), @now_utc);
        COMMIT TRANSACTION;
        SELECT @plan_id AS plan_id, CASE WHEN @decision = N'approve' THEN N'approved' ELSE N'rejected' END AS state;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO