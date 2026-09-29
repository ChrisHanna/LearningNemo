SET XACT_ABORT ON;
GO
IF OBJECT_ID(N'control.InvoiceGuestLaunches', N'U') IS NULL
BEGIN
    CREATE TABLE control.InvoiceGuestLaunches (
        JobId char(32) NOT NULL PRIMARY KEY,
        GuestHash char(64) NOT NULL,
        CreatedAt datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME()
    );
    CREATE INDEX IX_InvoiceGuestLaunches_GuestCreated
        ON control.InvoiceGuestLaunches(GuestHash, CreatedAt);
    CREATE INDEX IX_InvoiceGuestLaunches_Created
        ON control.InvoiceGuestLaunches(CreatedAt);
END;
GO
CREATE OR ALTER PROCEDURE control.usp_admit_invoice_guest_launch
    @job_id char(32), @guest_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @job_id IS NULL OR LEN(@job_id) <> 32 OR @guest_hash IS NULL OR LEN(@guest_hash) <> 64
        THROW 52200, 'invoice_guest_launch_invalid', 1;
    DECLARE @now datetime2(3) = SYSUTCDATETIME(), @visitor int, @global int,
        @admitted bit = 1, @reason varchar(16) = NULL;
    BEGIN TRANSACTION;
    BEGIN TRY
        IF EXISTS (SELECT 1 FROM control.InvoiceGuestLaunches WITH (UPDLOCK, HOLDLOCK) WHERE JobId = @job_id)
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM control.InvoiceGuestLaunches WHERE JobId = @job_id AND GuestHash = @guest_hash)
                THROW 52201, 'invoice_guest_launch_conflict', 1;
        END
        ELSE
        BEGIN
            SELECT @visitor = COUNT(*) FROM control.InvoiceGuestLaunches WITH (UPDLOCK, HOLDLOCK)
            WHERE GuestHash = @guest_hash AND CreatedAt > DATEADD(hour, -1, @now);
            SELECT @global = COUNT(*) FROM control.InvoiceGuestLaunches WITH (UPDLOCK, HOLDLOCK)
            WHERE CreatedAt >= CONVERT(date, @now);
            IF @visitor >= 3 OR @global >= 25
            BEGIN
                SET @admitted = 0;
                SET @reason = CASE WHEN @visitor >= 3 THEN 'hourly' ELSE 'daily' END;
            END
            ELSE
            BEGIN
                INSERT control.InvoiceGuestLaunches (JobId, GuestHash, CreatedAt)
                VALUES (@job_id, @guest_hash, @now);
            END;
        END;
        SELECT @visitor = COUNT(*) FROM control.InvoiceGuestLaunches
        WHERE GuestHash = @guest_hash AND CreatedAt > DATEADD(hour, -1, @now);
        SELECT @global = COUNT(*) FROM control.InvoiceGuestLaunches
        WHERE CreatedAt >= CONVERT(date, @now);
        COMMIT TRANSACTION;
        SELECT @admitted AS admitted, @reason AS reason,
            @visitor AS visitor_launches_last_hour, @global AS global_launches_today,
            3 AS visitor_limit, 25 AS global_limit;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_review_guest_invoice_plans
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (100) invoice_plan.PlanJson AS plan_json, invoice_plan.PlanHash AS plan_hash, invoice_plan.State AS state
    FROM control.InvoicePlans AS invoice_plan
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
CREATE OR ALTER PROCEDURE control.usp_decide_guest_invoice_plan
    @plan_id char(32), @plan_hash char(64), @reviewer_hash char(64), @decision varchar(16)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @decision IS NULL OR @decision NOT IN ('approve', 'reject') OR @reviewer_hash IS NULL
        THROW 52204, 'invoice_guest_review_denied', 1;
    UPDATE control.InvoicePlans SET State = CASE WHEN @decision = 'approve' THEN 'approved' ELSE 'rejected' END,
        ReviewerHash = @reviewer_hash, ApprovedAt = SYSUTCDATETIME(),
        ApprovalExpiresAt = DATEADD(minute, 30, SYSUTCDATETIME())
    WHERE PlanId = @plan_id AND PlanHash = @plan_hash
        AND SponsorHash <> @reviewer_hash AND State = 'submitted'
        AND ReviewExpiresAt > SYSUTCDATETIME()
        AND EXISTS (
            SELECT 1
            FROM control.InvoiceGuestLaunches AS guest
            JOIN control.InvoiceJobs AS job ON job.JobId = guest.JobId
            WHERE job.Kind = 'planning' AND job.TargetId = control.InvoicePlans.ScenarioId
                AND job.SponsorHash = control.InvoicePlans.SponsorHash
        );
    IF @@ROWCOUNT <> 1 THROW 52204, 'invoice_guest_review_denied', 1;
    SELECT @plan_id AS plan_id;
END;
GO
