SET XACT_ABORT ON;
GO
IF OBJECT_ID(N'control.InvoiceScenarioRequests', N'U') IS NULL
BEGIN
    CREATE TABLE control.InvoiceScenarioRequests (
        ScenarioId char(32) NOT NULL PRIMARY KEY REFERENCES lab.InvoiceScenarios(ScenarioId),
        SponsorHash char(64) NOT NULL,
        Variant varchar(32) NOT NULL CHECK (Variant IN ('healthy', 'lost-acknowledgement'))
    );
END;
GO
CREATE OR ALTER PROCEDURE control.usp_create_owned_invoice_scenario
    @scenario_id char(32), @variant varchar(32), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @sponsor_hash IS NULL OR LEN(@sponsor_hash) <> 64 OR @sponsor_hash COLLATE Latin1_General_100_BIN2 LIKE '%[^0-9a-f]%'
        THROW 51930, 'invoice_scenario_owner_required', 1;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @created TABLE (scenario_id char(32));
        INSERT @created EXEC control.usp_create_invoice_scenario @scenario_id = @scenario_id, @variant = @variant;
        INSERT control.InvoiceScenarioRequests (ScenarioId, SponsorHash, Variant) VALUES (@scenario_id, @sponsor_hash, @variant);
        COMMIT TRANSACTION;
        SELECT ScenarioId AS scenario_id, @variant AS variant,
            CONVERT(varchar(30), CreatedAt, 126) + 'Z' AS created_at,
            CONVERT(varchar(30), ExpiresAt, 126) + 'Z' AS expires_at,
            CONVERT(varchar(30), DATEADD(minute, -10, ExpiresAt), 126) + 'Z' AS planning_before
        FROM lab.InvoiceScenarios WHERE ScenarioId = @scenario_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_read_invoice_scenario
    @scenario_id char(32), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT scenario.ScenarioId AS scenario_id, request.Variant AS variant,
        CONVERT(varchar(30), scenario.CreatedAt, 126) + 'Z' AS created_at,
        CONVERT(varchar(30), scenario.ExpiresAt, 126) + 'Z' AS expires_at,
        CONVERT(varchar(30), DATEADD(minute, -10, scenario.ExpiresAt), 126) + 'Z' AS planning_before
    FROM lab.InvoiceScenarios AS scenario
    LEFT JOIN control.InvoiceScenarioRequests AS request ON request.ScenarioId = scenario.ScenarioId
    WHERE scenario.ScenarioId = @scenario_id AND (request.SponsorHash = @sponsor_hash OR
        (request.SponsorHash IS NULL AND EXISTS (SELECT 1 FROM control.InvoiceRuns AS run
            WHERE run.ScenarioId = scenario.ScenarioId AND run.SponsorHash = @sponsor_hash)));
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
        CONVERT(varchar(30), DATEADD(minute, 30, approved_plan.CreatedAt), 126) + 'Z' AS review_expires_at,
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
        CONVERT(varchar(30), DATEADD(minute, 30, approved_plan.CreatedAt), 126) + 'Z' AS review_expires_at,
        evidence.EvidenceJson AS evidence_json
    FROM control.InvoicePlans AS approved_plan
    JOIN control.InvoiceEvidence AS evidence ON evidence.RunId = approved_plan.PlanningRunId
        AND evidence.EvidenceHash = JSON_VALUE(approved_plan.PlanJson, '$.evidence_hash')
    WHERE approved_plan.State = 'submitted' AND approved_plan.CreatedAt > DATEADD(minute, -30, SYSUTCDATETIME())
    ORDER BY approved_plan.CreatedAt;
END;
GO
IF DATABASE_PRINCIPAL_ID(N'id-learningnemo-invoice-simulator-dev') IS NOT NULL
    REVOKE EXECUTE ON OBJECT::control.usp_create_invoice_scenario FROM [id-learningnemo-invoice-simulator-dev];
GO