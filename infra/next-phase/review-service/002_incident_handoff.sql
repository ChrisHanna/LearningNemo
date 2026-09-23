SET XACT_ABORT ON;
GO

IF OBJECT_ID(N'control.HumanInvestigations', N'U') IS NULL
BEGIN
    CREATE TABLE control.HumanInvestigations (
        PlanId nvarchar(128) NOT NULL CONSTRAINT PK_HumanInvestigations PRIMARY KEY,
        RunId nvarchar(128) NOT NULL CONSTRAINT UQ_HumanInvestigations_Run UNIQUE,
        DiagnosisReceiptHash char(64) NOT NULL,
        ContainmentReceiptHash char(64) NOT NULL,
        DiagnosisJson nvarchar(max) NOT NULL,
        ContainmentJson nvarchar(max) NOT NULL,
        Version int NOT NULL CONSTRAINT DF_HumanInvestigations_Version DEFAULT 1,
        RecordedAt datetime2(0) NOT NULL CONSTRAINT DF_HumanInvestigations_RecordedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_HumanInvestigations_Plan FOREIGN KEY (PlanId) REFERENCES control.ResolutionPlans(PlanId),
        CONSTRAINT FK_HumanInvestigations_Run FOREIGN KEY (RunId) REFERENCES lab.QueryRuns(RunId),
        CONSTRAINT CK_HumanInvestigations_Version CHECK (Version > 0),
        CONSTRAINT CK_HumanInvestigations_Diagnosis CHECK (ISJSON(DiagnosisJson) = 1),
        CONSTRAINT CK_HumanInvestigations_Containment CHECK (ISJSON(ContainmentJson) = 1)
    );
END;
GO

CREATE OR ALTER PROCEDURE control.usp_record_human_investigation
    @plan_json nvarchar(max),
    @run_id nvarchar(128),
    @expected_task_version int,
    @diagnosis_receipt_hash char(64),
    @containment_receipt_hash char(64),
    @diagnosis_json nvarchar(max),
    @containment_json nvarchar(max)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @plan_json IS NULL OR ISJSON(@plan_json) <> 1
       OR @expected_task_version IS NULL OR @expected_task_version < 1
       OR @diagnosis_receipt_hash IS NULL OR LEN(@diagnosis_receipt_hash) <> 64
       OR TRY_CONVERT(varbinary(32), @diagnosis_receipt_hash, 2) IS NULL
       OR @containment_receipt_hash IS NULL OR LEN(@containment_receipt_hash) <> 64
    OR TRY_CONVERT(varbinary(32), @containment_receipt_hash, 2) IS NULL
    OR @diagnosis_json IS NULL OR ISJSON(@diagnosis_json) <> 1
    OR @containment_json IS NULL OR ISJSON(@containment_json) <> 1
        THROW 51300, 'trusted_investigation_required', 1;

    DECLARE @plan_id nvarchar(128) = JSON_VALUE(@plan_json, '$.plan_id'),
        @task_id nvarchar(128) = JSON_VALUE(@plan_json, '$.content.task_id'),
        @engagement_id nvarchar(128) = JSON_VALUE(@plan_json, '$.content.engagement_id'),
        @workspace_id nvarchar(128) = JSON_VALUE(@plan_json, '$.content.workspace_id'),
        @logical_agent_id nvarchar(128) = JSON_VALUE(@plan_json, '$.content.logical_agent_id'),
        @plan_hash char(64) = JSON_VALUE(@plan_json, '$.plan_hash'),
        @sponsor_hash char(64) = JSON_VALUE(@plan_json, '$.created_by_hash'),
        @now_utc datetime2(0) = SYSUTCDATETIME();
    IF @plan_id IS NULL OR @plan_id NOT LIKE N'plan-%'
       OR @plan_hash IS NULL OR LEN(@plan_hash) <> 64
       OR TRY_CONVERT(varbinary(32), @plan_hash, 2) IS NULL
       OR @sponsor_hash IS NULL OR LEN(@sponsor_hash) <> 64
       OR TRY_CONVERT(varbinary(32), @sponsor_hash, 2) IS NULL
       OR COALESCE(JSON_VALUE(@plan_json, '$.state'), N'') <> N'draft'
       OR COALESCE(JSON_VALUE(@plan_json, '$.version'), N'') <> N'1'
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.operation_id'), N'') <> N'remediate.activate-cycle-safe-query-v1'
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.target_resource'), N'') <> N'lab.QueryVersions/cycle-safe-v1'
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.safe_query_version'), N'') <> N'cycle-safe-v1'
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.rollback_version'), N'') <> N'cycle-unsafe-v1'
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.parameters.query_version'), N'') <> N'cycle-safe-v1'
       OR (SELECT COUNT(*) FROM OPENJSON(@plan_json, '$.content.parameters')) <> 1
        THROW 51301, 'investigation_plan_denied', 1;

    IF COALESCE(JSON_VALUE(@diagnosis_json, '$.task_id'), N'') <> @task_id
       OR COALESCE(JSON_VALUE(@diagnosis_json, '$.engagement_id'), N'') <> @engagement_id
       OR COALESCE(JSON_VALUE(@diagnosis_json, '$.workspace_id'), N'') <> @workspace_id
       OR COALESCE(JSON_VALUE(@diagnosis_json, '$.logical_agent_id'), N'') <> @logical_agent_id
       OR COALESCE(JSON_VALUE(@containment_json, '$.operation_id'), N'') <> N'contain.cancel-owned-query-v1'
       OR COALESCE(JSON_VALUE(@containment_json, '$.result_code'), N'') <> N'owned_query_cancelled'
       OR COALESCE(JSON_VALUE(@containment_json, '$.result.run_id'), N'') <> @run_id
       OR COALESCE(JSON_VALUE(@containment_json, '$.result.state'), N'') <> N'cancelled'
       OR LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varchar(max), @diagnosis_json)), 2)) <> @diagnosis_receipt_hash
       OR LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varchar(max), @containment_json)), 2)) <> @containment_receipt_hash
        THROW 51306, 'investigation_receipt_mismatch', 1;

    BEGIN TRANSACTION;
    BEGIN TRY
        IF NOT EXISTS (
            SELECT 1 FROM control.Tasks AS task WITH (UPDLOCK, HOLDLOCK)
            INNER JOIN control.Engagements AS engagement WITH (UPDLOCK, HOLDLOCK) ON engagement.TaskId = task.TaskId
            INNER JOIN control.AgentRegistrations AS agent WITH (UPDLOCK, HOLDLOCK) ON agent.EngagementId = engagement.EngagementId
            WHERE task.TaskId = @task_id AND task.State = N'open' AND task.Version = @expected_task_version
              AND engagement.EngagementId = @engagement_id AND engagement.WorkspaceId = @workspace_id
              AND engagement.SponsorSubjectHash = @sponsor_hash
              AND engagement.State = N'engaged' AND engagement.ExpiresAt > @now_utc
              AND agent.LogicalAgentId = @logical_agent_id AND agent.WorkspaceId = @workspace_id
              AND agent.State = N'active' AND agent.ExpiresAt > @now_utc
        ) THROW 51302, 'investigation_context_changed', 1;
        IF NOT EXISTS (SELECT 1 FROM lab.QueryRuns WITH (UPDLOCK, HOLDLOCK) WHERE RunId = @run_id AND State = N'cancelled')
           OR EXISTS (SELECT 1 FROM control.HumanInvestigations WITH (UPDLOCK, HOLDLOCK) WHERE RunId = @run_id)
            THROW 51303, 'investigation_run_unavailable', 1;

        IF NOT EXISTS (SELECT 1 FROM lab.QueryVersions WITH (UPDLOCK, HOLDLOCK)
            WHERE QueryVersion = N'cycle-unsafe-v1' AND IsActive = 1)
            THROW 51304, 'investigation_diagnosis_changed', 1;
        UPDATE control.Tasks SET State = N'contained', Version = Version + 1, UpdatedAt = @now_utc
            WHERE TaskId = @task_id AND State = N'open' AND Version = @expected_task_version;
        IF @@ROWCOUNT <> 1 THROW 51305, 'investigation_task_race', 1;
        INSERT control.ResolutionPlans (
            PlanId, TaskId, EngagementId, WorkspaceId, LogicalAgentId, PlanHash,
            OperationId, TargetResource, SafeQueryVersion, RollbackVersion, ParametersJson,
            State, CreatedByHash, CreatedByScheme, CreatedAt
        ) VALUES (
            @plan_id, @task_id, @engagement_id, @workspace_id, @logical_agent_id, @plan_hash,
            N'remediate.activate-cycle-safe-query-v1', N'lab.QueryVersions/cycle-safe-v1',
            N'cycle-safe-v1', N'cycle-unsafe-v1', N'{"query_version":"cycle-safe-v1"}',
            N'draft', @sponsor_hash, N'entra-tenant-oid-v1', @now_utc
        );
        INSERT control.HumanInvestigations (PlanId, RunId, DiagnosisReceiptHash, ContainmentReceiptHash, DiagnosisJson, ContainmentJson)
        VALUES (@plan_id, @run_id, @diagnosis_receipt_hash, @containment_receipt_hash, @diagnosis_json, @containment_json);
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson)
        VALUES (@task_id, N'investigation', N'human_plan_drafted',
            (SELECT @plan_id AS plan_id, @plan_hash AS plan_hash,
                @diagnosis_receipt_hash AS diagnosis_receipt_hash,
                @containment_receipt_hash AS containment_receipt_hash FOR JSON PATH, WITHOUT_ARRAY_WRAPPER));
        COMMIT TRANSACTION;
        SELECT @plan_id AS plan_id, @plan_hash AS plan_hash, N'draft' AS state;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_list_human_incidents
    @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @sponsor_hash IS NULL OR LEN(@sponsor_hash) <> 64 OR TRY_CONVERT(varbinary(32), @sponsor_hash, 2) IS NULL
        THROW 51310, 'sponsor_identity_required', 1;
    SELECT TOP (100) (
        SELECT JSON_QUERY((
            SELECT plan_record.PlanId AS plan_id,
                JSON_QUERY((SELECT plan_record.TaskId AS task_id, plan_record.EngagementId AS engagement_id,
                    plan_record.WorkspaceId AS workspace_id, plan_record.LogicalAgentId AS logical_agent_id,
                    plan_record.OperationId AS operation_id, plan_record.TargetResource AS target_resource,
                    plan_record.SafeQueryVersion AS safe_query_version, plan_record.RollbackVersion AS rollback_version,
                    JSON_QUERY(plan_record.ParametersJson) AS parameters FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)) AS content,
                plan_record.PlanHash AS plan_hash, plan_record.State AS state, plan_record.Version AS version,
                plan_record.CreatedByHash AS created_by_hash,
                CONVERT(varchar(19), plan_record.CreatedAt, 126) + 'Z' AS created_at
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)) AS [plan],
            investigation.Version AS investigation_version,
            investigation.DiagnosisReceiptHash AS diagnosis_receipt_hash,
            investigation.ContainmentReceiptHash AS containment_receipt_hash,
            CONVERT(varchar(19), CASE WHEN engagement.ExpiresAt < agent.ExpiresAt THEN engagement.ExpiresAt ELSE agent.ExpiresAt END, 126) + 'Z' AS expires_at,
            plan_record.CreatedByScheme AS author_identity_scheme,
            CONVERT(bit, CASE WHEN plan_record.State = N'draft' AND task.State = N'contained'
                AND engagement.State = N'engaged' AND agent.State = N'active'
                AND engagement.ExpiresAt > SYSUTCDATETIME() AND agent.ExpiresAt > SYSUTCDATETIME()
                THEN 1 ELSE 0 END) AS canSubmit
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
    ) AS proposal_json
    FROM control.HumanInvestigations AS investigation
    INNER JOIN control.ResolutionPlans AS plan_record ON plan_record.PlanId = investigation.PlanId
    INNER JOIN control.Tasks AS task ON task.TaskId = plan_record.TaskId
    INNER JOIN control.Engagements AS engagement ON engagement.EngagementId = plan_record.EngagementId AND engagement.TaskId = task.TaskId AND engagement.WorkspaceId = plan_record.WorkspaceId
    INNER JOIN control.AgentRegistrations AS agent ON agent.LogicalAgentId = plan_record.LogicalAgentId AND agent.EngagementId = engagement.EngagementId AND agent.WorkspaceId = plan_record.WorkspaceId
    WHERE plan_record.CreatedByHash = @sponsor_hash AND engagement.SponsorSubjectHash = @sponsor_hash
        AND plan_record.CreatedByScheme = N'entra-tenant-oid-v1'
        AND plan_record.State IN (N'draft', N'awaiting_approval', N'approved', N'rejected', N'consumed')
    ORDER BY investigation.RecordedAt DESC, investigation.PlanId;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_submit_human_plan
    @plan_id nvarchar(128),
    @sponsor_hash char(64),
    @expected_plan_hash char(64),
    @expected_investigation_version int
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @now_utc datetime2(0) = SYSUTCDATETIME(), @task_id nvarchar(128);
    BEGIN TRANSACTION;
    BEGIN TRY
        SELECT @task_id = plan_record.TaskId
        FROM control.ResolutionPlans AS plan_record WITH (UPDLOCK, HOLDLOCK)
        INNER JOIN control.HumanInvestigations AS investigation WITH (UPDLOCK, HOLDLOCK) ON investigation.PlanId = plan_record.PlanId
        INNER JOIN control.Tasks AS task WITH (UPDLOCK, HOLDLOCK) ON task.TaskId = plan_record.TaskId
        INNER JOIN control.Engagements AS engagement WITH (UPDLOCK, HOLDLOCK) ON engagement.EngagementId = plan_record.EngagementId AND engagement.TaskId = task.TaskId AND engagement.WorkspaceId = plan_record.WorkspaceId
        INNER JOIN control.AgentRegistrations AS agent WITH (UPDLOCK, HOLDLOCK) ON agent.LogicalAgentId = plan_record.LogicalAgentId AND agent.EngagementId = engagement.EngagementId AND agent.WorkspaceId = plan_record.WorkspaceId
        WHERE plan_record.PlanId = @plan_id AND plan_record.State = N'draft'
            AND plan_record.CreatedByScheme = N'entra-tenant-oid-v1'
            AND plan_record.CreatedByHash = @sponsor_hash AND engagement.SponsorSubjectHash = @sponsor_hash
            AND plan_record.PlanHash = @expected_plan_hash AND investigation.Version = @expected_investigation_version
            AND task.State = N'contained' AND engagement.State = N'engaged' AND agent.State = N'active'
            AND engagement.ExpiresAt > @now_utc AND agent.ExpiresAt > @now_utc;
        IF @task_id IS NULL THROW 51311, 'incident_changed_expired_or_not_owned', 1;
        UPDATE control.ResolutionPlans SET State = N'awaiting_approval', Version = Version + 1 WHERE PlanId = @plan_id;
        UPDATE control.HumanInvestigations SET Version = Version + 1 WHERE PlanId = @plan_id;
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson)
        VALUES (@task_id, N'plan', N'human_plan_submitted',
            (SELECT @plan_id AS plan_id, @expected_plan_hash AS plan_hash, @sponsor_hash AS sponsor_hash FOR JSON PATH, WITHOUT_ARRAY_WRAPPER));
        COMMIT TRANSACTION;
        SELECT @plan_id AS plan_id, @expected_plan_hash AS plan_hash, N'awaiting_approval' AS state;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO