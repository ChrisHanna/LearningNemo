SET XACT_ABORT ON;
GO
IF OBJECT_ID(N'control.DatabaseAnalyses', N'U') IS NULL
BEGIN
    CREATE TABLE control.DatabaseAnalyses (
        AnalysisId nvarchar(128) NOT NULL CONSTRAINT PK_DatabaseAnalyses PRIMARY KEY,
        SponsorHash char(64) NOT NULL,
        EvidenceHash char(64) NOT NULL,
        AnalysisJson nvarchar(max) NOT NULL,
        ObservedAt datetime2(0) NOT NULL,
        PlanId nvarchar(128) NULL,
        CONSTRAINT CK_DatabaseAnalyses_Json CHECK (ISJSON(AnalysisJson) = 1)
    );
    ALTER TABLE control.HumanInvestigations DROP CONSTRAINT UQ_HumanInvestigations_Run;
    ALTER TABLE control.HumanInvestigations ALTER COLUMN RunId nvarchar(128) NULL;
    CREATE UNIQUE INDEX UX_HumanInvestigations_Run ON control.HumanInvestigations(RunId) WHERE RunId IS NOT NULL;
    ALTER TABLE control.HumanInvestigations ADD EvidenceKind nvarchar(32) NOT NULL
        CONSTRAINT DF_HumanInvestigations_EvidenceKind DEFAULT N'owned-query-containment';
END;
GO
CREATE OR ALTER PROCEDURE control.usp_record_readonly_analysis
    @sponsor_hash char(64), @analysis_json nvarchar(max)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @id nvarchar(128) = JSON_VALUE(@analysis_json, '$.analysis_id'),
        @hash char(64) = JSON_VALUE(@analysis_json, '$.evidence_hash'),
        @observed datetime2(0) = TRY_CONVERT(datetimeoffset, JSON_VALUE(@analysis_json, '$.observed_at'));
    IF @sponsor_hash IS NULL OR TRY_CONVERT(varbinary(32), @sponsor_hash, 2) IS NULL
       OR ISJSON(@analysis_json) <> 1 OR @id IS NULL OR @id NOT LIKE N'analysis-%'
       OR @observed IS NULL OR @observed < DATEADD(minute, -2, SYSUTCDATETIME()) OR @observed > DATEADD(second, 5, SYSUTCDATETIME())
       OR COALESCE(JSON_VALUE(@analysis_json, '$.source'), N'') <> N'azure-sql-diagnostic-service'
       OR COALESCE(JSON_VALUE(@analysis_json, '$.workload_changed'), N'') <> N'false'
       OR COALESCE(JSON_VALUE(@analysis_json, '$.queries_cancelled'), N'') <> N'false'
       OR @hash IS NULL OR @hash <> LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varchar(max), JSON_QUERY(@analysis_json, '$.snapshot'))), 2))
        THROW 51700, 'readonly_analysis_invalid', 1;
    INSERT control.DatabaseAnalyses (AnalysisId, SponsorHash, EvidenceHash, AnalysisJson, ObservedAt)
    VALUES (@id, @sponsor_hash, @hash, @analysis_json, @observed);
    SELECT @id AS analysis_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_get_readonly_analysis @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (1) AnalysisJson AS analysis_json FROM control.DatabaseAnalyses
    WHERE SponsorHash = @sponsor_hash ORDER BY ObservedAt DESC, AnalysisId DESC;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_propose_readonly_analysis
    @analysis_id nvarchar(128), @sponsor_hash char(64), @evidence_hash char(64), @plan_json nvarchar(max)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @now datetime2(0) = SYSUTCDATETIME(), @analysis nvarchar(max), @existing nvarchar(128),
        @suffix nvarchar(32) = SUBSTRING(@analysis_id, 10, 32), @plan_hash char(64) = JSON_VALUE(@plan_json, '$.plan_hash');
    DECLARE @plan_id nvarchar(128) = N'plan-' + @suffix, @task nvarchar(128) = N'task-' + @suffix,
        @engagement nvarchar(128) = N'engagement-' + @suffix, @workspace nvarchar(128) = N'workspace-' + @suffix,
        @agent nvarchar(128) = N'analyst-' + @suffix;
    IF LEN(@analysis_id) <> 41 OR @analysis_id NOT LIKE N'analysis-%'
       OR COALESCE(JSON_VALUE(@plan_json, '$.plan_id'), N'') <> @plan_id
       OR COALESCE(JSON_VALUE(@plan_json, '$.created_by_hash'), N'') <> @sponsor_hash
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.task_id'), N'') <> @task
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.engagement_id'), N'') <> @engagement
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.workspace_id'), N'') <> @workspace
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.logical_agent_id'), N'') <> @agent
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.operation_id'), N'') <> N'remediate.activate-cycle-safe-query-v1'
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.target_resource'), N'') <> N'lab.QueryVersions/cycle-safe-v1'
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.safe_query_version'), N'') <> N'cycle-safe-v1'
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.rollback_version'), N'') <> N'cycle-unsafe-v1'
       OR COALESCE(JSON_VALUE(@plan_json, '$.content.parameters.query_version'), N'') <> N'cycle-safe-v1'
       OR @plan_hash IS NULL OR TRY_CONVERT(varbinary(32), @plan_hash, 2) IS NULL
        THROW 51701, 'readonly_proposal_contract_denied', 1;
    BEGIN TRANSACTION;
    BEGIN TRY
        SELECT @analysis = AnalysisJson, @existing = PlanId FROM control.DatabaseAnalyses WITH (UPDLOCK, HOLDLOCK)
        WHERE AnalysisId = @analysis_id AND SponsorHash = @sponsor_hash AND EvidenceHash = @evidence_hash;
        IF @analysis IS NULL THROW 51702, 'analysis_not_owned', 1;
        IF @existing IS NOT NULL
        BEGIN
            IF @existing <> @plan_id THROW 51703, 'proposal_changed', 1;
            COMMIT TRANSACTION;
            SELECT PlanId AS plan_id, PlanHash AS plan_hash FROM control.ResolutionPlans WHERE PlanId = @existing;
            RETURN;
        END;
        IF TRY_CONVERT(datetimeoffset, JSON_VALUE(@analysis, '$.observed_at')) < DATEADD(minute, -15, @now)
           OR COALESCE(JSON_VALUE(@analysis, '$.snapshot.active_query_version'), N'') <> N'cycle-unsafe-v1'
           OR NOT EXISTS (SELECT 1 FROM lab.QueryVersions WITH (UPDLOCK, HOLDLOCK) WHERE QueryVersion = N'cycle-unsafe-v1' AND IsActive = 1)
           OR EXISTS (SELECT 1 FROM lab.QueryRuns WITH (UPDLOCK, HOLDLOCK) WHERE State IN (N'starting', N'running', N'cancel_requested'))
            THROW 51704, 'fresh_analysis_and_no_active_queries_required_no_query_cancelled', 1;
        INSERT control.Tasks (TaskId, Title, State, CreatedAt, UpdatedAt)
        VALUES (@task, N'Read-only database analysis proposal', N'contained', @now, @now);
        INSERT control.Engagements (EngagementId, TaskId, WorkspaceId, SponsorSubjectHash, State, CreatedAt, ExpiresAt)
        VALUES (@engagement, @task, @workspace, @sponsor_hash, N'engaged', @now, DATEADD(minute, 30, @now));
        INSERT control.AgentRegistrations (LogicalAgentId, EngagementId, WorkspaceId, PolicyHash, State, CreatedAt, ExpiresAt)
        VALUES (@agent, @engagement, @workspace, @evidence_hash, N'active', @now, DATEADD(minute, 30, @now));
        INSERT control.ResolutionPlans (PlanId, TaskId, EngagementId, WorkspaceId, LogicalAgentId, PlanHash, OperationId, TargetResource,
            SafeQueryVersion, RollbackVersion, ParametersJson, State, CreatedByHash, CreatedByScheme, CreatedAt)
        VALUES (@plan_id, @task, @engagement, @workspace, @agent, @plan_hash, N'remediate.activate-cycle-safe-query-v1',
            N'lab.QueryVersions/cycle-safe-v1', N'cycle-safe-v1', N'cycle-unsafe-v1', N'{"query_version":"cycle-safe-v1"}',
            N'draft', @sponsor_hash, N'entra-tenant-oid-v1', @now);
        DECLARE @safety varchar(max) = '{"active_queries":0,"queries_cancelled":false,"workload_changed":false}';
        INSERT control.HumanInvestigations (PlanId, RunId, DiagnosisReceiptHash, ContainmentReceiptHash, DiagnosisJson, ContainmentJson, EvidenceKind)
        VALUES (@plan_id, NULL, @evidence_hash, LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', @safety), 2)), @analysis, @safety, N'read-only-analysis');
        UPDATE control.DatabaseAnalyses SET PlanId = @plan_id WHERE AnalysisId = @analysis_id;
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson)
        VALUES (@task, N'proposal', N'readonly_analysis_proposed',
            (SELECT @analysis_id AS analysis_id, @evidence_hash AS evidence_hash, CAST(0 AS bit) AS queries_cancelled FOR JSON PATH, WITHOUT_ARRAY_WRAPPER));
        COMMIT TRANSACTION;
        SELECT @plan_id AS plan_id, @plan_hash AS plan_hash;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO