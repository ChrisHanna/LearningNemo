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
        UPDATE lab.QueryRuns WITH (UPDLOCK, ROWLOCK)
        SET
            State = N'failed',
            FailureCode = COALESCE(FailureCode, N'watchdog_expired'),
            CompletedAt = COALESCE(CompletedAt, @now_utc)
        WHERE State IN (N'starting', N'running', N'cancel_requested')
          AND WatchdogDeadline <= @now_utc;

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

GRANT EXECUTE ON OBJECT::control.usp_initialize_cycle_demo TO learningnemo_control;
GO