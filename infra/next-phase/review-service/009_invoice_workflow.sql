SET XACT_ABORT ON;
GO
IF OBJECT_ID(N'control.InvoiceRuns', N'U') IS NULL
BEGIN
    CREATE TABLE control.InvoiceRuns (
        RunId char(32) NOT NULL PRIMARY KEY,
        ScenarioId char(32) NOT NULL REFERENCES lab.InvoiceScenarios(ScenarioId),
        SponsorHash char(64) NOT NULL,
        SandboxId char(32) NOT NULL UNIQUE,
        PolicyHash char(64) NOT NULL,
        CapabilityHash char(64) NOT NULL UNIQUE,
        Kind varchar(16) NOT NULL CHECK (Kind IN ('planning', 'execution')),
        ExpiresAt datetime2(3) NOT NULL,
        RevokedAt datetime2(3) NULL,
        Calls int NOT NULL DEFAULT 0,
        CallLimit int NOT NULL CHECK (CallLimit BETWEEN 1 AND 40)
    );
    CREATE TABLE control.InvoiceEvidence (
        RunId char(32) NOT NULL REFERENCES control.InvoiceRuns(RunId),
        EvidenceHash char(64) NOT NULL,
        EvidenceJson nvarchar(max) NOT NULL CHECK (ISJSON(EvidenceJson) = 1),
        CreatedAt datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
        PRIMARY KEY (RunId, EvidenceHash)
    );
    CREATE TABLE control.InvoicePlans (
        PlanId char(32) NOT NULL PRIMARY KEY,
        PlanHash char(64) NOT NULL UNIQUE,
        PlanJson nvarchar(max) NOT NULL CHECK (ISJSON(PlanJson) = 1),
        ScenarioId char(32) NOT NULL REFERENCES lab.InvoiceScenarios(ScenarioId),
        SponsorHash char(64) NOT NULL,
        PlanningRunId char(32) NOT NULL UNIQUE REFERENCES control.InvoiceRuns(RunId),
        State varchar(24) NOT NULL CHECK (State IN ('draft','submitted','approved','rejected','executing','verified','completed')),
        ReviewerHash char(64) NULL,
        ApprovedAt datetime2(3) NULL,
        ApprovalExpiresAt datetime2(3) NULL,
        ExecutionRunId char(32) NULL,
        NextStep int NOT NULL DEFAULT 1,
        VerificationJson nvarchar(max) NULL,
        CreatedAt datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME()
    );
    CREATE UNIQUE INDEX UX_InvoicePlans_ExecutionRun ON control.InvoicePlans(ExecutionRunId) WHERE ExecutionRunId IS NOT NULL;
    CREATE TABLE control.InvoiceStepReceipts (
        RunId char(32) NOT NULL REFERENCES control.InvoiceRuns(RunId),
        StepId int NOT NULL,
        StepHash char(64) NOT NULL,
        ReceiptJson nvarchar(max) NOT NULL CHECK (ISJSON(ReceiptJson) = 1),
        CreatedAt datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
        PRIMARY KEY (RunId, StepId)
    );
END;
GO
CREATE OR ALTER PROCEDURE control.usp_register_invoice_planning
    @run_id char(32), @scenario_id char(32), @sponsor_hash char(64), @sandbox_id char(32),
    @policy_hash char(64), @capability_hash char(64), @expires_at datetime2(3), @call_limit int
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @expires_at <= SYSUTCDATETIME() OR @expires_at > DATEADD(minute, 15, SYSUTCDATETIME())
        OR @call_limit NOT BETWEEN 1 AND 40 OR @sponsor_hash IS NULL OR @capability_hash IS NULL
        OR NOT EXISTS (SELECT 1 FROM lab.InvoiceScenarios WHERE ScenarioId = @scenario_id AND ExpiresAt >= @expires_at)
        THROW 51900, 'invoice_run_admission_denied', 1;
    INSERT control.InvoiceRuns (RunId, ScenarioId, SponsorHash, SandboxId, PolicyHash, CapabilityHash, Kind, ExpiresAt, CallLimit)
    VALUES (@run_id, @scenario_id, @sponsor_hash, @sandbox_id, @policy_hash, @capability_hash, 'planning', @expires_at, @call_limit);
    SELECT @run_id AS run_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_admit_invoice_tool
    @run_id char(32), @capability_hash char(64), @tool varchar(32)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE control.InvoiceRuns SET Calls = Calls + 1
    OUTPUT inserted.ScenarioId AS scenario_id, inserted.Kind AS kind, inserted.SandboxId AS sandbox_id
    WHERE RunId = @run_id AND CapabilityHash = @capability_hash AND RevokedAt IS NULL
        AND ExpiresAt > SYSUTCDATETIME() AND Calls < CallLimit
        AND (@tool = 'inference' OR (Kind = 'planning' AND @tool IN ('invoice_summary', 'invoice_batches'))
            OR (Kind = 'execution' AND @tool = 'execution_status'));
END;
GO
CREATE OR ALTER PROCEDURE control.usp_record_invoice_evidence
    @run_id char(32), @evidence_hash char(64), @evidence_json nvarchar(max)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @evidence_hash IS NULL OR @evidence_hash <> LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varchar(max), @evidence_json)), 2))
        OR NOT EXISTS (SELECT 1 FROM control.InvoiceRuns WHERE RunId = @run_id AND Kind = 'planning'
            AND RevokedAt IS NULL AND ExpiresAt > SYSUTCDATETIME()
            AND ScenarioId = JSON_VALUE(@evidence_json, '$.scenario_id'))
        THROW 51901, 'invoice_evidence_denied', 1;
    IF NOT EXISTS (SELECT 1 FROM control.InvoiceEvidence WHERE RunId = @run_id AND EvidenceHash = @evidence_hash)
        INSERT control.InvoiceEvidence (RunId, EvidenceHash, EvidenceJson) VALUES (@run_id, @evidence_hash, @evidence_json);
    SELECT @evidence_hash AS evidence_hash;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_record_invoice_plan @plan_json nvarchar(max), @plan_hash char(64), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @run char(32) = JSON_VALUE(@plan_json, '$.planning_run_id'), @plan char(32) = JSON_VALUE(@plan_json, '$.plan_id'),
        @scenario char(32) = JSON_VALUE(@plan_json, '$.scenario_id'), @evidence nvarchar(max), @revision int;
    IF @plan_hash IS NULL OR @plan_hash <> LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varchar(max), @plan_json)), 2))
        OR COALESCE(JSON_VALUE(@plan_json, '$.schema_version'), '') <> '2'
        OR COALESCE(JSON_VALUE(@plan_json, '$.sponsor_hash'), '') <> @sponsor_hash
        OR COALESCE(JSON_VALUE(@plan_json, '$.failure_policy'), '') <> 'stop-and-reconcile'
        OR COALESCE(JSON_VALUE(@plan_json, '$.verification_profile'), '') <> 'invoice-integrity-and-replay.v1'
        THROW 51902, 'invoice_plan_contract_denied', 1;
    SELECT @evidence = evidence.EvidenceJson FROM control.InvoiceEvidence AS evidence
    JOIN control.InvoiceRuns AS run ON run.RunId = evidence.RunId
    WHERE run.RunId = @run AND run.ScenarioId = @scenario AND run.SponsorHash = @sponsor_hash AND run.Kind = 'planning'
        AND run.SandboxId = JSON_VALUE(@plan_json, '$.planning_sandbox_id') AND run.RevokedAt IS NULL
        AND run.ExpiresAt > SYSUTCDATETIME() AND evidence.CreatedAt > DATEADD(minute, -15, SYSUTCDATETIME())
        AND evidence.EvidenceHash = JSON_VALUE(@plan_json, '$.evidence_hash');
    IF @evidence IS NULL THROW 51903, 'invoice_plan_evidence_not_owned', 1;
    SET @revision = TRY_CONVERT(int, JSON_VALUE(@evidence, '$.revision'));
    IF (SELECT COUNT(*) FROM OPENJSON(@plan_json, '$.steps')) NOT BETWEEN 1 AND 3 OR @revision IS NULL
        THROW 51904, 'invoice_plan_steps_invalid', 1;
    IF EXISTS (SELECT 1 FROM OPENJSON(@plan_json, '$.steps') AS step WHERE
        COALESCE(JSON_VALUE(step.value, '$.target'), '') <> @scenario
        OR COALESCE(TRY_CONVERT(int, JSON_VALUE(step.value, '$.step_id')), 0) <> CONVERT(int, step.[key]) + 1
        OR COALESCE(TRY_CONVERT(int, JSON_VALUE(step.value, '$.expected_revision')), 0) <> @revision + CONVERT(int, step.[key])
        OR COALESCE(JSON_VALUE(step.value, '$.operation'), '') NOT IN ('invoice.quarantine-duplicates.v1','invoice.rebuild-total.v1','invoice.activate-idempotent-import.v1')
        OR (JSON_VALUE(step.value, '$.operation') = 'invoice.quarantine-duplicates.v1' AND
            COALESCE(JSON_VALUE(step.value, '$.duplicate_set_hash'), '') <> JSON_VALUE(@evidence, '$.duplicate_set_hash')))
        THROW 51904, 'invoice_plan_steps_invalid', 1;
    INSERT control.InvoicePlans (PlanId, PlanHash, PlanJson, ScenarioId, SponsorHash, PlanningRunId, State)
    VALUES (@plan, @plan_hash, @plan_json, @scenario, @sponsor_hash, @run, 'draft');
    SELECT @plan AS plan_id, @plan_hash AS plan_hash;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_submit_invoice_plan @plan_id char(32), @plan_hash char(64), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE control.InvoicePlans SET State = 'submitted' WHERE PlanId = @plan_id AND PlanHash = @plan_hash
        AND SponsorHash = @sponsor_hash AND State = 'draft' AND CreatedAt > DATEADD(minute, -30, SYSUTCDATETIME());
    IF @@ROWCOUNT <> 1 THROW 51905, 'invoice_submission_denied', 1;
    SELECT @plan_id AS plan_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_decide_invoice_plan
    @plan_id char(32), @plan_hash char(64), @reviewer_hash char(64), @decision varchar(16)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @decision IS NULL OR @decision NOT IN ('approve', 'reject') OR @reviewer_hash IS NULL
        THROW 51906, 'invoice_review_denied', 1;
    UPDATE control.InvoicePlans SET State = CASE WHEN @decision = 'approve' THEN 'approved' ELSE 'rejected' END,
        ReviewerHash = @reviewer_hash, ApprovedAt = SYSUTCDATETIME(), ApprovalExpiresAt = DATEADD(minute, 15, SYSUTCDATETIME())
    WHERE PlanId = @plan_id AND PlanHash = @plan_hash AND SponsorHash <> @reviewer_hash AND State = 'submitted'
        AND CreatedAt > DATEADD(minute, -30, SYSUTCDATETIME());
    IF @@ROWCOUNT <> 1 THROW 51906, 'invoice_review_denied', 1;
    SELECT @plan_id AS plan_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_claim_invoice_execution
    @plan_id char(32), @plan_hash char(64), @sponsor_hash char(64), @run_id char(32),
    @sandbox_id char(32), @policy_hash char(64), @capability_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @scenario char(32), @expiry datetime2(3);
        SELECT @scenario = ScenarioId, @expiry = ApprovalExpiresAt FROM control.InvoicePlans WITH (UPDLOCK, HOLDLOCK)
        WHERE PlanId = @plan_id AND PlanHash = @plan_hash AND SponsorHash = @sponsor_hash
            AND ReviewerHash <> @sponsor_hash AND State = 'approved' AND ExecutionRunId IS NULL AND ApprovalExpiresAt > SYSUTCDATETIME();
        IF @scenario IS NULL OR EXISTS (SELECT 1 FROM control.InvoiceRuns WHERE SandboxId = @sandbox_id)
            OR NOT EXISTS (SELECT 1 FROM lab.InvoiceScenarios WHERE ScenarioId = @scenario AND ExpiresAt > SYSUTCDATETIME())
            THROW 51907, 'invoice_execution_claim_denied', 1;
        INSERT control.InvoiceRuns (RunId, ScenarioId, SponsorHash, SandboxId, PolicyHash, CapabilityHash, Kind, ExpiresAt, CallLimit)
        VALUES (@run_id, @scenario, @sponsor_hash, @sandbox_id, @policy_hash, @capability_hash, 'execution', @expiry, 20);
        UPDATE control.InvoicePlans SET State = 'executing', ExecutionRunId = @run_id WHERE PlanId = @plan_id;
        COMMIT TRANSACTION;
        SELECT PlanJson AS plan_json FROM control.InvoicePlans WHERE PlanId = @plan_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE ops.usp_execute_invoice_step
    @run_id char(32), @capability_hash char(64), @step_json nvarchar(max)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @scenario char(32), @plan nvarchar(max), @next int, @expected nvarchar(max), @step_hash char(64);
        SELECT @scenario = approved_plan.ScenarioId, @plan = approved_plan.PlanJson, @next = approved_plan.NextStep
        FROM control.InvoicePlans AS approved_plan WITH (UPDLOCK, HOLDLOCK)
        JOIN control.InvoiceRuns AS run WITH (UPDLOCK, HOLDLOCK) ON run.RunId = approved_plan.ExecutionRunId
        WHERE run.RunId = @run_id AND run.CapabilityHash = @capability_hash AND run.Kind = 'execution'
            AND run.RevokedAt IS NULL AND run.ExpiresAt > SYSUTCDATETIME() AND run.Calls < run.CallLimit
            AND approved_plan.State = 'executing' AND approved_plan.ApprovalExpiresAt > SYSUTCDATETIME();
        IF @scenario IS NULL THROW 51908, 'invoice_execution_authority_denied', 1;
        SELECT @expected = value FROM OPENJSON(@plan, '$.steps') WHERE CONVERT(int, [key]) = @next - 1;
        IF @expected IS NULL OR HASHBYTES('SHA2_256', @step_json) <> HASHBYTES('SHA2_256', @expected)
            OR @step_json IS NULL THROW 51909, 'invoice_step_not_exactly_approved', 1;
        SET @step_hash = LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', CONVERT(varchar(max), @step_json)), 2));
        DECLARE @revision int = CONVERT(int, JSON_VALUE(@step_json, '$.expected_revision')),
            @operation varchar(64) = JSON_VALUE(@step_json, '$.operation'), @duplicates char(64) = JSON_VALUE(@step_json, '$.duplicate_set_hash');
        EXEC lab.usp_apply_invoice_operation @scenario_id = @scenario, @expected_revision = @revision,
            @operation = @operation, @duplicate_set_hash = @duplicates;
        DECLARE @receipt nvarchar(max) = (SELECT @run_id AS run_id, @next AS step_id, @step_hash AS step_hash,
            @scenario AS scenario_id, @revision + 1 AS revision, @operation AS operation FOR JSON PATH, WITHOUT_ARRAY_WRAPPER);
        INSERT control.InvoiceStepReceipts (RunId, StepId, StepHash, ReceiptJson) VALUES (@run_id, @next, @step_hash, @receipt);
        UPDATE control.InvoicePlans SET NextStep = NextStep + 1 WHERE ExecutionRunId = @run_id;
        UPDATE control.InvoiceRuns SET Calls = Calls + 1 WHERE RunId = @run_id;
        COMMIT TRANSACTION;
        SELECT @receipt AS receipt_json;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_read_invoice_plans @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (100) PlanJson AS plan_json, PlanHash AS plan_hash, State AS state,
        ApprovalExpiresAt AS approval_expires_at, ExecutionRunId AS execution_run_id, NextStep AS next_step, VerificationJson AS verification_json
    FROM control.InvoicePlans WHERE SponsorHash = @sponsor_hash ORDER BY CreatedAt DESC, PlanId;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_review_invoice_plans
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (100) PlanJson AS plan_json, PlanHash AS plan_hash, State AS state
    FROM control.InvoicePlans WHERE State = 'submitted' AND CreatedAt > DATEADD(minute, -30, SYSUTCDATETIME()) ORDER BY CreatedAt;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_revoke_invoice_run @run_id char(32), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE control.InvoiceRuns SET RevokedAt = SYSUTCDATETIME() WHERE RunId = @run_id AND SponsorHash = @sponsor_hash AND RevokedAt IS NULL;
    IF @@ROWCOUNT <> 1 THROW 51910, 'invoice_revocation_denied', 1;
    SELECT @run_id AS run_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_get_invoice_execution @run_id char(32), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT receipt.ReceiptJson AS receipt_json FROM control.InvoiceStepReceipts AS receipt
    JOIN control.InvoiceRuns AS run ON run.RunId = receipt.RunId
    WHERE run.RunId = @run_id AND run.SponsorHash = @sponsor_hash ORDER BY receipt.StepId;
END;
GO
CREATE OR ALTER PROCEDURE ops.usp_verify_invoice_run @run_id char(32)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @scenario char(32);
        DECLARE @checks TABLE (
            no_duplicates bit, legitimate_invoices_preserved bit, total_reconciles bit, idempotent_version_active bit);
        SELECT @scenario = ScenarioId FROM control.InvoicePlans WITH (UPDLOCK, HOLDLOCK)
        WHERE ExecutionRunId = @run_id AND State IN ('executing', 'verified')
            AND NextStep = (SELECT COUNT(*) + 1 FROM OPENJSON(PlanJson, '$.steps'));
        IF @scenario IS NULL THROW 51911, 'invoice_verification_prerequisite_missing', 1;
        DECLARE @locked_revision int;
        SELECT @locked_revision = Revision FROM lab.InvoiceScenarios WITH (UPDLOCK, HOLDLOCK)
        WHERE ScenarioId = @scenario AND ExpiresAt > SYSUTCDATETIME();
        IF @locked_revision IS NULL THROW 51911, 'invoice_verification_prerequisite_missing', 1;
        INSERT @checks EXEC ops.usp_verify_invoice_integrity @scenario_id = @scenario;
        DECLARE @before bigint = (SELECT COUNT_BIG(*) FROM lab.InvoiceRows WHERE ScenarioId = @scenario), @after bigint;
        SAVE TRANSACTION invoice_replay_check;
        EXEC lab.usp_import_invoice_batch @scenario_id = @scenario, @attempt = 3;
        SET @after = (SELECT COUNT_BIG(*) FROM lab.InvoiceRows WHERE ScenarioId = @scenario);
        ROLLBACK TRANSACTION invoice_replay_check;
        DECLARE @result nvarchar(max) = (SELECT no_duplicates, legitimate_invoices_preserved, total_reconciles,
            idempotent_version_active, CAST(CASE WHEN @before = @after THEN 1 ELSE 0 END AS bit) AS replay_created_no_invoices
            FROM @checks FOR JSON PATH, WITHOUT_ARRAY_WRAPPER);
        IF (SELECT COUNT(*) FROM @checks) <> 1 OR EXISTS (SELECT 1 FROM @checks WHERE no_duplicates = 0
            OR legitimate_invoices_preserved = 0 OR total_reconciles = 0 OR idempotent_version_active = 0) OR @before <> @after
            THROW 51912, 'invoice_independent_verification_failed', 1;
        UPDATE control.InvoicePlans SET State = 'verified', VerificationJson = @result WHERE ExecutionRunId = @run_id;
        COMMIT TRANSACTION;
        SELECT @result AS verification_json;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_complete_invoice_plan @plan_id char(32), @plan_hash char(64), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE control.InvoicePlans SET State = 'completed' WHERE PlanId = @plan_id AND PlanHash = @plan_hash
        AND SponsorHash = @sponsor_hash AND State = 'verified' AND VerificationJson IS NOT NULL;
    IF @@ROWCOUNT <> 1 THROW 51913, 'invoice_completion_denied', 1;
    SELECT @plan_id AS plan_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_get_invoice_evidence
    @run_id char(32), @sponsor_hash char(64), @evidence_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT evidence.EvidenceJson AS evidence_json
    FROM control.InvoiceEvidence AS evidence
    JOIN control.InvoiceRuns AS run ON run.RunId = evidence.RunId
    WHERE run.RunId = @run_id AND run.SponsorHash = @sponsor_hash AND run.Kind = 'planning'
        AND evidence.EvidenceHash = @evidence_hash;
END;
GO