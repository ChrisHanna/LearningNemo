SET XACT_ABORT ON;
GO
CREATE OR ALTER PROCEDURE control.usp_read_invoice_retention
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (1000) (
        SELECT job.JobId AS run_id, job.Kind AS kind, job.State AS state,
            job.SponsorHash AS sponsor_hash,
            run.SandboxId AS sandbox_id, run.ScenarioId AS scenario_id,
            CONVERT(varchar(30), run.RevokedAt, 126) + 'Z' AS revoked_at,
            CONVERT(varchar(30), stopped.ObservedAt, 126) + 'Z' AS stopped_at,
            JSON_QUERY(job.ResultJson) AS result, JSON_QUERY(approved_plan.PlanJson) AS [plan],
            approved_plan.PlanHash AS plan_hash, approved_plan.State AS plan_state,
            JSON_QUERY(approved_plan.VerificationJson) AS verification,
            JSON_QUERY((SELECT activity.Sequence AS sequence,
                CONVERT(varchar(30), activity.ObservedAt, 126) + 'Z' AS observed_at,
                JSON_QUERY(activity.EventJson) AS event
                FROM control.InvoiceActivity AS activity WHERE activity.JobId = job.JobId
                ORDER BY activity.Sequence FOR JSON PATH)) AS events,
            JSON_QUERY(COALESCE((SELECT '[' + STRING_AGG(CONVERT(nvarchar(max), receipt.ReceiptJson), ',')
                WITHIN GROUP (ORDER BY receipt.StepId) + ']'
                FROM control.InvoiceStepReceipts AS receipt WHERE receipt.RunId = job.JobId), '[]')) AS receipts
        FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER
    ) AS record_json
    FROM control.InvoiceJobs AS job
    JOIN control.InvoiceRuns AS run ON run.RunId = job.JobId AND run.SponsorHash = job.SponsorHash AND run.Kind = job.Kind
    CROSS APPLY (SELECT MAX(activity.ObservedAt) AS ObservedAt FROM control.InvoiceActivity AS activity
        WHERE activity.JobId = job.JobId AND JSON_VALUE(activity.EventJson, '$.source') = 'workspace-controller'
            AND JSON_VALUE(activity.EventJson, '$.event_type') = 'sandbox-stopped'
            AND JSON_VALUE(activity.EventJson, '$.sandbox_id') = run.SandboxId) AS stopped
    LEFT JOIN control.InvoicePlans AS approved_plan ON
        (job.Kind = 'planning' AND approved_plan.PlanningRunId = job.JobId)
        OR (job.Kind = 'execution' AND approved_plan.ExecutionRunId = job.JobId)
    WHERE job.State = 'finished' AND run.RevokedAt IS NOT NULL AND stopped.ObservedAt IS NOT NULL
        AND (job.Kind = 'planning' AND JSON_VALUE(job.ResultJson, '$.outcome') IN ('proposal', 'no-change')
            OR job.Kind = 'execution' AND approved_plan.State IN ('verified', 'completed'))
    ORDER BY stopped.ObservedAt ASC, job.JobId;
END;
GO