SET XACT_ABORT ON;
GO
IF COL_LENGTH(N'control.InvoicePlans', N'ReviewExpiresAt') IS NULL
    ALTER TABLE control.InvoicePlans ADD ReviewExpiresAt datetime2(3) NULL;
GO
UPDATE control.InvoicePlans SET ReviewExpiresAt = DATEADD(minute, 30, CreatedAt)
WHERE ReviewExpiresAt IS NULL;
GO
ALTER TABLE control.InvoicePlans ALTER COLUMN ReviewExpiresAt datetime2(3) NOT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE parent_object_id = OBJECT_ID(N'control.InvoicePlans')
    AND name = N'DF_InvoicePlans_ReviewExpiresAt')
    ALTER TABLE control.InvoicePlans ADD CONSTRAINT DF_InvoicePlans_ReviewExpiresAt
        DEFAULT DATEADD(minute, 90, SYSUTCDATETIME()) FOR ReviewExpiresAt;
GO
CREATE OR ALTER PROCEDURE control.usp_submit_invoice_plan @plan_id char(32), @plan_hash char(64), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE control.InvoicePlans SET State = 'submitted' WHERE PlanId = @plan_id AND PlanHash = @plan_hash
        AND SponsorHash = @sponsor_hash AND State = 'draft' AND ReviewExpiresAt > SYSUTCDATETIME();
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
        AND ReviewExpiresAt > SYSUTCDATETIME();
    IF @@ROWCOUNT <> 1 THROW 51906, 'invoice_review_denied', 1;
    SELECT @plan_id AS plan_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_read_invoice_plans @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (100) approved_plan.PlanJson AS plan_json, approved_plan.PlanHash AS plan_hash, approved_plan.State AS state,
        approved_plan.ApprovalExpiresAt AS approval_expires_at, approved_plan.ExecutionRunId AS execution_run_id,
        approved_plan.NextStep AS next_step, approved_plan.VerificationJson AS verification_json,
        CONVERT(varchar(30), approved_plan.ReviewExpiresAt, 126) + 'Z' AS review_expires_at,
        CONVERT(varchar(30), scenario.ExpiresAt, 126) + 'Z' AS scenario_expires_at,
        CONVERT(varchar(30), DATEADD(minute, -2, CASE WHEN approved_plan.ApprovalExpiresAt < scenario.ExpiresAt
            THEN approved_plan.ApprovalExpiresAt ELSE scenario.ExpiresAt END), 126) + 'Z' AS execution_before
    FROM control.InvoicePlans AS approved_plan JOIN lab.InvoiceScenarios AS scenario ON scenario.ScenarioId = approved_plan.ScenarioId
    WHERE approved_plan.SponsorHash = @sponsor_hash ORDER BY approved_plan.CreatedAt DESC, approved_plan.PlanId;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_review_invoice_plans
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (100) approved_plan.PlanJson AS plan_json, approved_plan.PlanHash AS plan_hash, approved_plan.State AS state,
        CONVERT(varchar(30), approved_plan.ReviewExpiresAt, 126) + 'Z' AS review_expires_at,
        evidence.EvidenceJson AS evidence_json
    FROM control.InvoicePlans AS approved_plan
    JOIN control.InvoiceEvidence AS evidence ON evidence.RunId = approved_plan.PlanningRunId
        AND evidence.EvidenceHash = JSON_VALUE(approved_plan.PlanJson, '$.evidence_hash')
    WHERE approved_plan.State = 'submitted' AND approved_plan.ReviewExpiresAt > SYSUTCDATETIME()
    ORDER BY approved_plan.CreatedAt;
END;
GO