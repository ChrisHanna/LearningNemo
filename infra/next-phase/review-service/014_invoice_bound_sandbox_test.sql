SET XACT_ABORT ON;
GO
IF COL_LENGTH(N'control.InvoiceJobs', N'SandboxTestRequested') IS NULL
BEGIN
    ALTER TABLE control.InvoiceJobs ADD
        SandboxTestRequested bit NOT NULL CONSTRAINT DF_InvoiceJobs_TestRequested DEFAULT 0,
        SandboxTestClosed bit NOT NULL CONSTRAINT DF_InvoiceJobs_TestClosed DEFAULT 0,
        SandboxTestReceiptJson nvarchar(max) NULL;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_request_invoice_sandbox_test
    @job_id char(32), @sponsor_hash char(64), @sandbox_id char(32)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE job SET SandboxTestRequested = 1
    FROM control.InvoiceJobs AS job
    JOIN control.InvoiceRuns AS run ON run.RunId = job.JobId AND run.SponsorHash = job.SponsorHash AND run.Kind = job.Kind
    WHERE job.JobId = @job_id AND job.SponsorHash = @sponsor_hash AND run.SandboxId = @sandbox_id
        AND job.State = 'running' AND job.ExpiresAt > SYSUTCDATETIME() AND job.SandboxTestClosed = 0
        AND run.RevokedAt IS NULL AND run.ExpiresAt > SYSUTCDATETIME();
    IF @@ROWCOUNT <> 1 THROW 51940, 'sandbox_test_window_closed_or_not_owned', 1;
    SELECT @job_id AS job_id, @sandbox_id AS sandbox_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_claim_invoice_sandbox_test
    @job_id char(32), @sponsor_hash char(64), @sandbox_id char(32)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE job SET SandboxTestClosed = 1
    OUTPUT inserted.SandboxTestRequested AS test_requested
    FROM control.InvoiceJobs AS job
    JOIN control.InvoiceRuns AS run ON run.RunId = job.JobId AND run.SponsorHash = job.SponsorHash AND run.Kind = job.Kind
    WHERE job.JobId = @job_id AND job.SponsorHash = @sponsor_hash AND run.SandboxId = @sandbox_id
        AND job.State = 'running' AND job.ExpiresAt > SYSUTCDATETIME() AND job.SandboxTestClosed = 0
        AND run.RevokedAt IS NOT NULL;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_record_invoice_sandbox_test
    @job_id char(32), @sponsor_hash char(64), @sandbox_id char(32), @receipt_json nvarchar(max)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @receipt_json IS NULL OR ISJSON(@receipt_json) <> 1
        OR COALESCE(JSON_VALUE(@receipt_json, '$.run_id'), '') <> @job_id
        OR COALESCE(JSON_VALUE(@receipt_json, '$.sandbox_id'), '') <> @sandbox_id
        OR COALESCE(JSON_VALUE(@receipt_json, '$.scope'), '') <> 'same-agent-sandbox'
        OR COALESCE(JSON_VALUE(@receipt_json, '$.agent_authority_revoked'), '') <> 'true'
        OR COALESCE(JSON_VALUE(@receipt_json, '$.probe_capability_issued'), '') <> 'false'
        OR COALESCE(JSON_VALUE(@receipt_json, '$.sandbox_stopped'), '') <> 'true'
        THROW 51941, 'sandbox_test_receipt_invalid', 1;
    UPDATE job SET SandboxTestReceiptJson = @receipt_json
    FROM control.InvoiceJobs AS job
    JOIN control.InvoiceRuns AS run ON run.RunId = job.JobId AND run.SponsorHash = job.SponsorHash
    WHERE job.JobId = @job_id AND job.SponsorHash = @sponsor_hash AND run.SandboxId = @sandbox_id
        AND job.State = 'running' AND job.SandboxTestClosed = 1 AND job.SandboxTestRequested = 1
        AND run.RevokedAt IS NOT NULL AND job.SandboxTestReceiptJson IS NULL;
    IF @@ROWCOUNT <> 1 THROW 51942, 'sandbox_test_receipt_conflict', 1;
    SELECT @job_id AS job_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_get_invoice_job @job_id char(32), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT JobId AS job_id, Kind AS kind, TargetId AS target_id,
        CASE WHEN State IN ('queued','running') AND ExpiresAt <= SYSUTCDATETIME() THEN 'uncertain' ELSE State END AS state,
        ResultJson AS result_json, CreatedAt AS created_at, ExpiresAt AS expires_at,
        SandboxTestRequested AS sandbox_test_requested, SandboxTestClosed AS sandbox_test_closed,
        SandboxTestReceiptJson AS sandbox_test_json
    FROM control.InvoiceJobs WHERE JobId = @job_id AND SponsorHash = @sponsor_hash;
END;
GO