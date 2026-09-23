SET XACT_ABORT ON;
GO

IF OBJECT_ID(N'control.HumanInitiations', N'U') IS NULL
BEGIN
    CREATE TABLE control.HumanInitiations (
        RequestId uniqueidentifier NOT NULL,
        SponsorHash char(64) NOT NULL,
        PlanId nvarchar(128) NOT NULL CONSTRAINT UQ_HumanInitiations_Plan UNIQUE,
        TaskId nvarchar(128) NOT NULL CONSTRAINT UQ_HumanInitiations_Task UNIQUE,
        RunId nvarchar(128) NOT NULL CONSTRAINT UQ_HumanInitiations_Run UNIQUE,
        ExpiresAt datetime2(0) NOT NULL,
        CreatedAt datetime2(0) NOT NULL,
        CONSTRAINT PK_HumanInitiations PRIMARY KEY (SponsorHash, RequestId)
    );
END;
GO

CREATE OR ALTER PROCEDURE control.usp_begin_human_investigation
    @task_id nvarchar(128),
    @engagement_id nvarchar(128),
    @workspace_id nvarchar(128),
    @logical_agent_id nvarchar(128),
    @request_id uniqueidentifier,
    @sponsor_hash char(64),
    @run_id nvarchar(128),
    @plan_id nvarchar(128),
    @expires_at datetime2(0),
    @now_utc datetime2(0)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET @now_utc = SYSUTCDATETIME();
    IF @request_id IS NULL OR @sponsor_hash IS NULL
       OR LEN(@sponsor_hash) <> 64 OR TRY_CONVERT(varbinary(32), @sponsor_hash, 2) IS NULL
       OR @expires_at IS NULL OR @expires_at < DATEADD(minute, 5, @now_utc)
       OR @expires_at > DATEADD(minute, 31, @now_utc)
       OR @task_id IS NULL OR @task_id NOT LIKE N'task-%'
       OR @engagement_id IS NULL OR @engagement_id NOT LIKE N'engagement-%'
       OR @workspace_id IS NULL OR @workspace_id NOT LIKE N'workspace-%'
       OR @logical_agent_id IS NULL OR @logical_agent_id NOT LIKE N'investigator-%'
       OR @run_id IS NULL OR @run_id NOT LIKE N'run-%'
       OR @plan_id IS NULL OR @plan_id NOT LIKE N'plan-%'
        THROW 51500, 'human_initiation_contract_denied', 1;

    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @lock_result int;
        EXEC @lock_result = sys.sp_getapplock @Resource = N'learningnemo-human-incident',
            @LockMode = N'Exclusive', @LockOwner = N'Transaction', @LockTimeout = 0;
        IF @lock_result < 0 THROW 51501, 'human_investigation_busy', 1;
        IF EXISTS (SELECT 1 FROM control.HumanInitiations WITH (UPDLOCK, HOLDLOCK)
            WHERE SponsorHash = @sponsor_hash AND RequestId = @request_id)
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM control.HumanInitiations WHERE SponsorHash = @sponsor_hash
                AND RequestId = @request_id AND PlanId = @plan_id AND TaskId = @task_id AND RunId = @run_id)
                THROW 51502, 'human_initiation_request_changed', 1;
            DECLARE @state nvarchar(16) = CASE WHEN EXISTS (
                SELECT 1 FROM control.HumanInvestigations WHERE PlanId = @plan_id
            ) THEN N'recorded' ELSE N'running' END;
            COMMIT TRANSACTION;
            SELECT @state AS state, @plan_id AS plan_id;
            RETURN;
        END;
        IF EXISTS (SELECT 1 FROM control.HumanInitiations WITH (UPDLOCK, HOLDLOCK) WHERE ExpiresAt > @now_utc)
           OR EXISTS (SELECT 1 FROM control.Engagements WITH (UPDLOCK, HOLDLOCK) WHERE State = N'engaged' AND ExpiresAt > @now_utc)
           OR EXISTS (SELECT 1 FROM lab.QueryRuns WITH (UPDLOCK, HOLDLOCK)
                WHERE State IN (N'starting', N'running', N'cancel_requested') AND WatchdogDeadline > @now_utc)
            THROW 51501, 'human_investigation_busy', 1;

        UPDATE lab.QueryVersions SET IsActive = 0 WHERE IsActive = 1;
        UPDATE lab.QueryVersions SET IsActive = 1 WHERE QueryVersion = N'cycle-unsafe-v1' AND QueryKind = N'unsafe';
        IF @@ROWCOUNT <> 1 THROW 51503, 'registered_demo_query_missing', 1;
        INSERT control.Tasks (TaskId, Title, State, CreatedAt, UpdatedAt)
        VALUES (@task_id, N'Operator-sponsored controlled cycle investigation', N'open', @now_utc, @now_utc);
        INSERT control.Engagements (EngagementId, TaskId, WorkspaceId, SponsorSubjectHash, State, CreatedAt, ExpiresAt)
        VALUES (@engagement_id, @task_id, @workspace_id, @sponsor_hash, N'engaged', @now_utc, @expires_at);
        INSERT control.AgentRegistrations (LogicalAgentId, EngagementId, WorkspaceId, PolicyHash, State, CreatedAt, ExpiresAt)
        VALUES (@logical_agent_id, @engagement_id, @workspace_id,
            LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', 'trusted-fixed-investigation-v1'), 2)), N'active', @now_utc, @expires_at);
        INSERT control.HumanInitiations (RequestId, SponsorHash, PlanId, TaskId, RunId, ExpiresAt, CreatedAt)
        VALUES (@request_id, @sponsor_hash, @plan_id, @task_id, @run_id, @expires_at, @now_utc);
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson, CreatedAt)
        VALUES (@task_id, N'investigation', N'human_initiation_admitted',
            (SELECT CONVERT(varchar(36), @request_id) AS request_id, @sponsor_hash AS sponsor_hash,
                @run_id AS run_id, N'trusted-fixed-investigation-v1' AS producer FOR JSON PATH, WITHOUT_ARRAY_WRAPPER), @now_utc);
        COMMIT TRANSACTION;
        SELECT N'admitted' AS state, @plan_id AS plan_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO