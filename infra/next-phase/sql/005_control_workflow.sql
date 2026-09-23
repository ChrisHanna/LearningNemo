SET XACT_ABORT ON;
GO

CREATE OR ALTER PROCEDURE control.usp_initialize_cycle_demo
    @task_id nvarchar(128),
    @title nvarchar(256),
    @engagement_id nvarchar(128),
    @workspace_id nvarchar(128),
    @sponsor_subject_hash char(64),
    @logical_agent_id nvarchar(128),
    @policy_hash char(64),
    @expires_at datetime2(0),
    @now_utc datetime2(0)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @task_id NOT LIKE N'task-%'
       OR @engagement_id NOT LIKE N'engagement-%'
       OR @workspace_id NOT LIKE N'workspace-%'
       OR @logical_agent_id NOT LIKE N'agent-%'
       OR LEN(@title) = 0
       OR TRY_CONVERT(varbinary(32), @sponsor_subject_hash, 2) IS NULL
       OR TRY_CONVERT(varbinary(32), @policy_hash, 2) IS NULL
       OR @expires_at <= @now_utc
       OR @expires_at > DATEADD(hour, 1, @now_utc)
        THROW 51100, 'cycle_demo_initialization_denied', 1;

    BEGIN TRANSACTION;
    BEGIN TRY
        IF EXISTS (SELECT 1 FROM control.Tasks WITH (UPDLOCK, HOLDLOCK) WHERE TaskId = @task_id)
           OR EXISTS (SELECT 1 FROM control.Engagements WITH (UPDLOCK, HOLDLOCK) WHERE EngagementId = @engagement_id)
           OR EXISTS (SELECT 1 FROM control.AgentRegistrations WITH (UPDLOCK, HOLDLOCK) WHERE LogicalAgentId = @logical_agent_id)
           OR EXISTS (SELECT 1 FROM lab.QueryRuns WITH (UPDLOCK, HOLDLOCK) WHERE State IN (N'starting', N'running', N'cancel_requested'))
            THROW 51101, 'cycle_demo_context_exists', 1;

        UPDATE lab.QueryVersions SET IsActive = 0 WHERE IsActive = 1;
        UPDATE lab.QueryVersions
        SET IsActive = 1
        WHERE QueryVersion = N'cycle-unsafe-v1' AND QueryKind = N'unsafe';
        IF @@ROWCOUNT <> 1 THROW 51102, 'cycle_demo_unsafe_version_missing', 1;

        INSERT control.Tasks (TaskId, Title, State, CreatedAt, UpdatedAt)
        VALUES (@task_id, @title, N'open', @now_utc, @now_utc);
        INSERT control.Engagements (
            EngagementId, TaskId, WorkspaceId, SponsorSubjectHash, State, CreatedAt, ExpiresAt
        ) VALUES (
            @engagement_id, @task_id, @workspace_id, @sponsor_subject_hash, N'engaged', @now_utc, @expires_at
        );
        INSERT control.AgentRegistrations (
            LogicalAgentId, EngagementId, WorkspaceId, PolicyHash, State, CreatedAt, ExpiresAt
        ) VALUES (
            @logical_agent_id, @engagement_id, @workspace_id, @policy_hash, N'active', @now_utc, @expires_at
        );
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson, CreatedAt)
        VALUES (
            @task_id,
            N'workflow',
            N'cycle_demo_initialized',
            (SELECT @engagement_id AS engagement_id, @workspace_id AS workspace_id FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
            @now_utc
        );
        COMMIT TRANSACTION;
        SELECT N'cycle_demo_initialized' AS result_code, N'open' AS task_state;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_record_cycle_containment_plan
    @task_id nvarchar(128),
    @engagement_id nvarchar(128),
    @workspace_id nvarchar(128),
    @logical_agent_id nvarchar(128),
    @run_id nvarchar(128),
    @plan_id nvarchar(128),
    @plan_hash char(64),
    @operation_id nvarchar(128),
    @target_resource nvarchar(256),
    @safe_query_version nvarchar(64),
    @rollback_version nvarchar(64),
    @parameters_json nvarchar(max),
    @created_by_hash char(64),
    @now_utc datetime2(0)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @run_id NOT LIKE N'run-%'
       OR @plan_id NOT LIKE N'plan-%'
       OR @operation_id <> N'remediate.activate-cycle-safe-query-v1'
       OR @safe_query_version <> N'cycle-safe-v1'
       OR @rollback_version <> N'cycle-unsafe-v1'
       OR TRY_CONVERT(varbinary(32), @plan_hash, 2) IS NULL
       OR TRY_CONVERT(varbinary(32), @created_by_hash, 2) IS NULL
       OR ISJSON(@parameters_json) <> 1
       OR JSON_VALUE(@parameters_json, '$.query_version') <> @safe_query_version
        THROW 51110, 'cycle_demo_plan_policy_denied', 1;

    BEGIN TRANSACTION;
    BEGIN TRY
        IF NOT EXISTS (
            SELECT 1
            FROM control.Tasks AS task WITH (UPDLOCK, HOLDLOCK)
            INNER JOIN control.Engagements AS engagement ON engagement.TaskId = task.TaskId
            INNER JOIN control.AgentRegistrations AS agent ON agent.EngagementId = engagement.EngagementId
            WHERE task.TaskId = @task_id
              AND task.State = N'open'
              AND engagement.EngagementId = @engagement_id
              AND engagement.WorkspaceId = @workspace_id
              AND engagement.State = N'engaged'
              AND engagement.ExpiresAt > @now_utc
              AND agent.LogicalAgentId = @logical_agent_id
              AND agent.WorkspaceId = @workspace_id
              AND agent.State = N'active'
              AND agent.ExpiresAt > @now_utc
        ) THROW 51111, 'cycle_demo_context_mismatch', 1;
        IF NOT EXISTS (
            SELECT 1 FROM lab.QueryRuns WITH (UPDLOCK, HOLDLOCK)
            WHERE RunId = @run_id AND State = N'cancelled'
        ) THROW 51112, 'cycle_demo_query_not_contained', 1;
        IF NOT EXISTS (
            SELECT 1 FROM lab.QueryVersions WITH (UPDLOCK, HOLDLOCK)
            WHERE QueryVersion = N'cycle-unsafe-v1' AND IsActive = 1
        ) THROW 51113, 'cycle_demo_diagnosis_changed', 1;

        UPDATE control.Tasks
        SET State = N'contained', Version = Version + 1, UpdatedAt = @now_utc
        WHERE TaskId = @task_id AND State = N'open';
        IF @@ROWCOUNT <> 1 THROW 51114, 'cycle_demo_containment_race', 1;
        INSERT control.ResolutionPlans (
            PlanId, TaskId, EngagementId, WorkspaceId, LogicalAgentId, PlanHash,
            OperationId, TargetResource, SafeQueryVersion, RollbackVersion,
            ParametersJson, State, CreatedByHash, CreatedAt
        ) VALUES (
            @plan_id, @task_id, @engagement_id, @workspace_id, @logical_agent_id, @plan_hash,
            @operation_id, @target_resource, @safe_query_version, @rollback_version,
            @parameters_json, N'awaiting_approval', @created_by_hash, @now_utc
        );
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson, CreatedAt)
        VALUES
        (
            @task_id,
            N'diagnosis',
            N'cycle_detected',
            (SELECT @run_id AS run_id, N'cycle-unsafe-v1' AS active_query_version FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
            @now_utc
        ),
        (
            @task_id,
            N'containment',
            N'owned_query_cancelled',
            (SELECT @run_id AS run_id FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
            @now_utc
        ),
        (
            @task_id,
            N'plan',
            N'plan_awaiting_approval',
            (SELECT @plan_id AS plan_id, @plan_hash AS plan_hash FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
            @now_utc
        );
        COMMIT TRANSACTION;
        SELECT N'cycle_demo_contained' AS result_code, N'contained' AS task_state, N'awaiting_approval' AS plan_state;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_record_cycle_execution
    @execution_id nvarchar(128),
    @task_id nvarchar(128),
    @plan_id nvarchar(128),
    @approval_id nvarchar(128),
    @plan_hash char(64),
    @safe_query_version nvarchar(64),
    @workspace_id nvarchar(128),
    @triggered_by_hash char(64),
    @result_code nvarchar(128),
    @receipt_hash char(64),
    @started_at datetime2(0),
    @completed_at datetime2(0)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @execution_id NOT LIKE N'execution-%'
       OR @safe_query_version <> N'cycle-safe-v1'
       OR @result_code <> N'safe_query_activated'
       OR TRY_CONVERT(varbinary(32), @plan_hash, 2) IS NULL
       OR TRY_CONVERT(varbinary(32), @triggered_by_hash, 2) IS NULL
       OR TRY_CONVERT(varbinary(32), @receipt_hash, 2) IS NULL
       OR @completed_at < @started_at
        THROW 51120, 'cycle_demo_execution_policy_denied', 1;

    BEGIN TRANSACTION;
    BEGIN TRY
        IF NOT EXISTS (
            SELECT 1
            FROM control.Tasks AS task WITH (UPDLOCK, HOLDLOCK)
                        INNER JOIN control.ResolutionPlans AS plan_record ON plan_record.TaskId = task.TaskId
                        INNER JOIN control.Approvals AS approval_record ON approval_record.PlanId = plan_record.PlanId
            WHERE task.TaskId = @task_id
              AND task.State = N'remediated'
                            AND plan_record.PlanId = @plan_id
                            AND plan_record.PlanHash = @plan_hash
                            AND plan_record.SafeQueryVersion = @safe_query_version
                            AND plan_record.WorkspaceId = @workspace_id
                            AND plan_record.State = N'consumed'
                            AND approval_record.ApprovalId = @approval_id
                            AND approval_record.State = N'consumed'
        ) THROW 51121, 'cycle_demo_execution_binding_mismatch', 1;

        INSERT control.Executions (
            ExecutionId, TaskId, PlanId, ApprovalId, PlanHash, SafeQueryVersion,
            WorkspaceId, TriggeredByHash, State, ResultCode, ReceiptHash, StartedAt, CompletedAt
        ) VALUES (
            @execution_id, @task_id, @plan_id, @approval_id, @plan_hash, @safe_query_version,
            @workspace_id, @triggered_by_hash, N'succeeded', @result_code, @receipt_hash, @started_at, @completed_at
        );
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson, CreatedAt)
        VALUES (
            @task_id,
            N'execution',
            N'remediation_succeeded',
            (SELECT @execution_id AS execution_id, @receipt_hash AS receipt_hash FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
            @completed_at
        );
        COMMIT TRANSACTION;
        SELECT N'cycle_demo_execution_recorded' AS result_code, N'succeeded' AS execution_state;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_record_cycle_verification
    @verification_id nvarchar(128),
    @task_id nvarchar(128),
    @execution_id nvarchar(128),
    @plan_hash char(64),
    @safe_query_version nvarchar(64),
    @verification_profile nvarchar(128),
    @workspace_id nvarchar(128),
    @checks_json nvarchar(max),
    @receipt_hash char(64),
    @verified_at datetime2(0)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @verification_id NOT LIKE N'verification-%'
       OR @safe_query_version <> N'cycle-safe-v1'
       OR @verification_profile <> N'cycle-recovery-v1'
       OR ISJSON(@checks_json) <> 1
       OR JSON_VALUE(@checks_json, '$.safe_query_version_active') <> N'true'
       OR JSON_VALUE(@checks_json, '$.no_owned_query_running') <> N'true'
       OR JSON_VALUE(@checks_json, '$.deterministic_result') <> N'true'
       OR TRY_CONVERT(varbinary(32), @receipt_hash, 2) IS NULL
        THROW 51130, 'cycle_demo_verification_policy_denied', 1;

    BEGIN TRANSACTION;
    BEGIN TRY
        IF NOT EXISTS (
            SELECT 1
                        FROM control.Executions AS execution_record WITH (UPDLOCK, HOLDLOCK)
                        INNER JOIN control.Tasks AS task ON task.TaskId = execution_record.TaskId
                        WHERE execution_record.ExecutionId = @execution_id
                            AND execution_record.TaskId = @task_id
                            AND execution_record.PlanHash = @plan_hash
                            AND execution_record.SafeQueryVersion = @safe_query_version
                            AND execution_record.WorkspaceId = @workspace_id
                            AND execution_record.State = N'succeeded'
              AND task.State = N'remediated'
        ) THROW 51131, 'cycle_demo_verification_binding_mismatch', 1;

        INSERT control.Verifications (
            VerificationId, TaskId, ExecutionId, PlanHash, SafeQueryVersion,
            VerificationProfile, WorkspaceId, State, ChecksJson, ReceiptHash, VerifiedAt
        ) VALUES (
            @verification_id, @task_id, @execution_id, @plan_hash, @safe_query_version,
            @verification_profile, @workspace_id, N'passed', @checks_json, @receipt_hash, @verified_at
        );
        UPDATE control.Tasks
        SET State = N'completed', Version = Version + 1, UpdatedAt = @verified_at
        WHERE TaskId = @task_id AND State = N'remediated';
        IF @@ROWCOUNT <> 1 THROW 51132, 'cycle_demo_completion_race', 1;
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson, CreatedAt)
        VALUES (
            @task_id,
            N'verification',
            N'cycle_recovery_verified',
            (SELECT @verification_id AS verification_id, @receipt_hash AS receipt_hash FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
            @verified_at
        );
        COMMIT TRANSACTION;
        SELECT N'cycle_demo_completed' AS result_code, N'completed' AS task_state, N'passed' AS verification_state;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_get_cycle_demo_summary
    @task_id nvarchar(128),
    @run_id nvarchar(128)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        task.State AS task_state,
        plan_record.State AS plan_state,
        approval_record.State AS approval_state,
        execution_record.State AS execution_state,
        verification_record.State AS verification_state,
        query_run.State AS query_run_state,
        active_version.QueryVersion AS active_query_version
    FROM control.Tasks AS task
    INNER JOIN control.ResolutionPlans AS plan_record ON plan_record.TaskId = task.TaskId
    INNER JOIN control.Approvals AS approval_record ON approval_record.PlanId = plan_record.PlanId
    INNER JOIN control.Executions AS execution_record ON execution_record.ApprovalId = approval_record.ApprovalId
    INNER JOIN control.Verifications AS verification_record ON verification_record.ExecutionId = execution_record.ExecutionId
    INNER JOIN lab.QueryRuns AS query_run ON query_run.RunId = @run_id
    CROSS APPLY (
        SELECT QueryVersion FROM lab.QueryVersions WHERE IsActive = 1
    ) AS active_version
    WHERE task.TaskId = @task_id;
END;
GO

GRANT EXECUTE ON OBJECT::control.usp_initialize_cycle_demo TO learningnemo_control;
GRANT EXECUTE ON OBJECT::control.usp_record_cycle_containment_plan TO learningnemo_control;
GRANT EXECUTE ON OBJECT::control.usp_record_cycle_execution TO learningnemo_control;
GRANT EXECUTE ON OBJECT::control.usp_record_cycle_verification TO learningnemo_control;
GRANT EXECUTE ON OBJECT::control.usp_get_cycle_demo_summary TO learningnemo_control;
GO