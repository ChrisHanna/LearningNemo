SET XACT_ABORT ON;
GO
CREATE OR ALTER PROCEDURE control.usp_review_guest_invoice_plans
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (100) invoice_plan.PlanJson AS plan_json, invoice_plan.PlanHash AS plan_hash,
        invoice_plan.State AS state,
        CONVERT(varchar(30), invoice_plan.ReviewExpiresAt, 126) + 'Z' AS review_expires_at,
        evidence.EvidenceJson AS evidence_json
    FROM control.InvoicePlans AS invoice_plan
    JOIN control.InvoiceEvidence AS evidence ON evidence.RunId = invoice_plan.PlanningRunId
        AND evidence.EvidenceHash = JSON_VALUE(invoice_plan.PlanJson, '$.evidence_hash')
    WHERE invoice_plan.State = 'submitted' AND invoice_plan.ReviewExpiresAt > SYSUTCDATETIME()
        AND EXISTS (
            SELECT 1
            FROM control.InvoiceGuestLaunches AS guest
            JOIN control.InvoiceJobs AS job ON job.JobId = guest.JobId
            WHERE job.Kind = 'planning' AND job.TargetId = invoice_plan.ScenarioId
                AND job.SponsorHash = invoice_plan.SponsorHash
        )
    ORDER BY invoice_plan.CreatedAt;
END;
GO
