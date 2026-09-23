SET XACT_ABORT ON;
GO

IF OBJECT_ID(N'control.HumanExecutions', N'U') IS NULL
BEGIN
    CREATE TABLE control.HumanExecutions (
        ExecutionId nvarchar(128) NOT NULL CONSTRAINT PK_HumanExecutions PRIMARY KEY,
        PlanId nvarchar(128) NOT NULL CONSTRAINT UQ_HumanExecutions_Plan UNIQUE,
        ApprovalId nvarchar(128) NOT NULL CONSTRAINT UQ_HumanExecutions_Approval UNIQUE,
        SponsorHash char(64) NOT NULL,
        PlanHash char(64) NOT NULL,
        State nvarchar(32) NOT NULL,
        BrokerJson nvarchar(max) NULL,
        BrokerHash char(64) NULL,
        VerificationJson nvarchar(max) NULL,
        VerificationHash char(64) NULL,
        CreatedAt datetime2(0) NOT NULL CONSTRAINT DF_HumanExecutions_Created DEFAULT SYSUTCDATETIME(),
        UpdatedAt datetime2(0) NOT NULL CONSTRAINT DF_HumanExecutions_Updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_HumanExecutions_Plan FOREIGN KEY (PlanId) REFERENCES control.ResolutionPlans(PlanId),
        CONSTRAINT FK_HumanExecutions_Approval FOREIGN KEY (ApprovalId) REFERENCES control.Approvals(ApprovalId),
        CONSTRAINT CK_HumanExecutions_State CHECK (State IN (N'claimed', N'broker', N'verification', N'completed')),
        CONSTRAINT CK_HumanExecutions_Broker CHECK (BrokerJson IS NULL OR ISJSON(BrokerJson) = 1),
        CONSTRAINT CK_HumanExecutions_Verification CHECK (VerificationJson IS NULL OR ISJSON(VerificationJson) = 1)
    );
END;
GO

CREATE OR ALTER PROCEDURE control.usp_claim_human_execution
    @plan_id nvarchar(128), @expected_plan_hash char(64), @expected_plan_version int,
    @sponsor_hash char(64), @execution_id nvarchar(128)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @now datetime2(0) = SYSUTCDATETIME(), @approval_id nvarchar(128), @approval_json nvarchar(max);
    IF @execution_id IS NULL OR @execution_id NOT LIKE N'execution-%'
       OR @sponsor_hash IS NULL OR LEN(@sponsor_hash) <> 64 OR TRY_CONVERT(varbinary(32), @sponsor_hash, 2) IS NULL
        THROW 51500, 'execution_identity_required', 1;
    BEGIN TRANSACTION;
    BEGIN TRY
        SELECT @approval_id = approval.ApprovalId,
            @approval_json = (
                SELECT approval.ApprovalId AS approval_id, approval.PlanId AS plan_id, approval.TaskId AS task_id,
                    approval.EngagementId AS engagement_id, approval.WorkspaceId AS workspace_id,
                    approval.LogicalAgentId AS logical_agent_id, approval.PlanHash AS plan_hash,
                    approval.OperationId AS operation_id, approval.TargetResource AS target_resource,
                    approval.SafeQueryVersion AS safe_query_version, approval.ApprovedByHash AS approved_by_hash,
                    CONVERT(varchar(19), approval.ApprovedAt, 126) + 'Z' AS approved_at,
                    CONVERT(varchar(19), approval.ExpiresAt, 126) + 'Z' AS expires_at,
                    approval.OneTimeId AS one_time_id, NULL AS consumed_at,
                    approval.Version AS version, approval.ReceiptHash AS receipt_hash
                FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER
            )
        FROM control.ResolutionPlans AS plan_record WITH (UPDLOCK, HOLDLOCK)
        INNER JOIN control.HumanInvestigations AS investigation WITH (UPDLOCK, HOLDLOCK) ON investigation.PlanId = plan_record.PlanId
        INNER JOIN control.Approvals AS approval WITH (UPDLOCK, HOLDLOCK) ON approval.PlanId = plan_record.PlanId
        INNER JOIN control.Tasks AS task WITH (UPDLOCK, HOLDLOCK) ON task.TaskId = plan_record.TaskId
        INNER JOIN control.Engagements AS engagement WITH (UPDLOCK, HOLDLOCK) ON engagement.EngagementId = plan_record.EngagementId
        INNER JOIN control.AgentRegistrations AS agent WITH (UPDLOCK, HOLDLOCK) ON agent.LogicalAgentId = plan_record.LogicalAgentId
        WHERE plan_record.PlanId = @plan_id AND plan_record.PlanHash = @expected_plan_hash
          AND plan_record.Version = @expected_plan_version AND plan_record.State = N'approved'
          AND plan_record.CreatedByHash = @sponsor_hash AND plan_record.CreatedByScheme = N'entra-tenant-oid-v1'
          AND engagement.SponsorSubjectHash = @sponsor_hash AND engagement.TaskId = task.TaskId
          AND engagement.WorkspaceId = plan_record.WorkspaceId AND engagement.State = N'engaged' AND engagement.ExpiresAt > @now
          AND agent.EngagementId = engagement.EngagementId AND agent.WorkspaceId = plan_record.WorkspaceId
          AND agent.State = N'active' AND agent.ExpiresAt > @now AND task.State = N'contained'
          AND approval.State = N'approved' AND approval.ConsumedAt IS NULL AND approval.ExpiresAt > @now
          AND approval.ApprovedByHash <> @sponsor_hash AND approval.PlanHash = plan_record.PlanHash
          AND approval.TaskId = task.TaskId AND approval.EngagementId = engagement.EngagementId
          AND approval.WorkspaceId = plan_record.WorkspaceId AND approval.LogicalAgentId = agent.LogicalAgentId
          AND approval.OperationId = N'remediate.activate-cycle-safe-query-v1'
          AND approval.TargetResource = N'lab.QueryVersions/cycle-safe-v1' AND approval.SafeQueryVersion = N'cycle-safe-v1';
        IF @approval_id IS NULL THROW 51501, 'execution_plan_not_owned_approved_or_live', 1;
        IF EXISTS (SELECT 1 FROM control.HumanExecutions WITH (UPDLOCK, HOLDLOCK) WHERE PlanId = @plan_id OR ExecutionId = @execution_id)
            THROW 51502, 'execution_already_claimed_reconcile_before_retry', 1;
        INSERT control.HumanExecutions (ExecutionId, PlanId, ApprovalId, SponsorHash, PlanHash, State)
        VALUES (@execution_id, @plan_id, @approval_id, @sponsor_hash, @expected_plan_hash, N'claimed');
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson)
        SELECT TaskId, N'execution', N'human_execution_claimed',
            (SELECT @execution_id AS execution_id, @expected_plan_hash AS plan_hash FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)
        FROM control.ResolutionPlans WHERE PlanId = @plan_id;
        COMMIT TRANSACTION;
        SELECT @approval_json AS approval_json;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_record_human_execution_stage
    @execution_id nvarchar(128), @sponsor_hash char(64), @plan_hash char(64),
    @stage nvarchar(32), @receipt_json nvarchar(max), @receipt_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @stage IS NULL OR @stage NOT IN (N'broker', N'verification') OR @receipt_json IS NULL OR ISJSON(@receipt_json) <> 1
       OR @receipt_hash IS NULL OR LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varchar(max), @receipt_json)), 2)) <> @receipt_hash
        THROW 51510, 'execution_receipt_invalid', 1;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @task_id nvarchar(128);
        SELECT @task_id = plan_record.TaskId
        FROM control.HumanExecutions AS execution_record WITH (UPDLOCK, HOLDLOCK)
        INNER JOIN control.ResolutionPlans AS plan_record WITH (UPDLOCK, HOLDLOCK) ON plan_record.PlanId = execution_record.PlanId
        INNER JOIN control.Approvals AS approval WITH (UPDLOCK, HOLDLOCK) ON approval.ApprovalId = execution_record.ApprovalId
        INNER JOIN control.Tasks AS task WITH (UPDLOCK, HOLDLOCK) ON task.TaskId = plan_record.TaskId
        WHERE execution_record.ExecutionId = @execution_id AND execution_record.SponsorHash = @sponsor_hash
          AND execution_record.PlanHash = @plan_hash AND plan_record.PlanHash = @plan_hash
          AND execution_record.State = CASE WHEN @stage = N'broker' THEN N'claimed' ELSE N'broker' END
          AND approval.PlanId = plan_record.PlanId AND approval.PlanHash = @plan_hash
          AND approval.State = N'consumed' AND plan_record.State = N'consumed' AND task.State = N'remediated';
        IF @task_id IS NULL THROW 51511, 'execution_receipt_binding_or_state_denied', 1;
        IF @stage = N'broker'
        BEGIN
            IF COALESCE(JSON_VALUE(@receipt_json, '$.operation_id'), N'') <> N'remediate.activate-cycle-safe-query-v1'
               OR COALESCE(JSON_VALUE(@receipt_json, '$.result_code'), N'') <> N'safe_query_activated'
               OR COALESCE(JSON_VALUE(@receipt_json, '$.result.safe_query_version'), N'') <> N'cycle-safe-v1'
                THROW 51512, 'broker_result_mismatch', 1;
            UPDATE control.HumanExecutions SET BrokerJson = @receipt_json, BrokerHash = @receipt_hash,
                State = N'broker', UpdatedAt = SYSUTCDATETIME() WHERE ExecutionId = @execution_id;
        END
        ELSE
        BEGIN
            IF COALESCE(JSON_VALUE(@receipt_json, '$.execution_id'), N'') <> @execution_id
               OR COALESCE(JSON_VALUE(@receipt_json, '$.plan_hash'), N'') <> @plan_hash
               OR COALESCE(JSON_VALUE(@receipt_json, '$.safe_query_version'), N'') <> N'cycle-safe-v1'
               OR COALESCE(JSON_VALUE(@receipt_json, '$.checks.safe_query_version_active'), N'') <> N'true'
               OR COALESCE(JSON_VALUE(@receipt_json, '$.checks.no_owned_query_running'), N'') <> N'true'
               OR COALESCE(JSON_VALUE(@receipt_json, '$.checks.deterministic_result'), N'') <> N'true'
               OR (SELECT COUNT(*) FROM OPENJSON(@receipt_json, '$.checks')) <> 3
                THROW 51513, 'independent_verification_mismatch', 1;
            UPDATE control.HumanExecutions SET VerificationJson = @receipt_json, VerificationHash = @receipt_hash,
                State = N'verification', UpdatedAt = SYSUTCDATETIME() WHERE ExecutionId = @execution_id;
            UPDATE control.Tasks SET State = N'verified', Version = Version + 1, UpdatedAt = SYSUTCDATETIME() WHERE TaskId = @task_id;
        END;
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson)
        VALUES (@task_id, N'execution', @stage,
            (SELECT @execution_id AS execution_id, @plan_hash AS plan_hash, @receipt_hash AS receipt_hash FOR JSON PATH, WITHOUT_ARRAY_WRAPPER));
        COMMIT TRANSACTION;
        SELECT @execution_id AS execution_id, @stage AS stage;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_complete_human_execution
    @execution_id nvarchar(128), @sponsor_hash char(64), @plan_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @task_id nvarchar(128);
        SELECT @task_id = plan_record.TaskId
        FROM control.HumanExecutions AS execution_record WITH (UPDLOCK, HOLDLOCK)
        INNER JOIN control.ResolutionPlans AS plan_record WITH (UPDLOCK, HOLDLOCK) ON plan_record.PlanId = execution_record.PlanId
        INNER JOIN control.Tasks AS task WITH (UPDLOCK, HOLDLOCK) ON task.TaskId = plan_record.TaskId
        WHERE execution_record.ExecutionId = @execution_id AND execution_record.SponsorHash = @sponsor_hash
          AND execution_record.PlanHash = @plan_hash AND plan_record.PlanHash = @plan_hash
          AND execution_record.State = N'verification' AND task.State = N'verified'
          AND execution_record.VerificationHash IS NOT NULL;
        IF @task_id IS NULL THROW 51520, 'verified_execution_required_for_completion', 1;
        UPDATE control.Tasks SET State = N'completed', Version = Version + 1, UpdatedAt = SYSUTCDATETIME() WHERE TaskId = @task_id;
        UPDATE control.HumanExecutions SET State = N'completed', UpdatedAt = SYSUTCDATETIME() WHERE ExecutionId = @execution_id;
        INSERT control.TaskEvents (TaskId, EventType, ReasonCode, DataJson)
        VALUES (@task_id, N'completion', N'operator_confirmed',
            (SELECT @execution_id AS execution_id, @plan_hash AS plan_hash FOR JSON PATH, WITHOUT_ARRAY_WRAPPER));
        COMMIT TRANSACTION;
        SELECT @execution_id AS execution_id, N'completed' AS state;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO