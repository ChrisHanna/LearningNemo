SET XACT_ABORT ON;
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
        @admitted bit = 1, @reason varchar(16) = NULL,
        @demo_until datetime2(3) = CONVERT(datetime2(3), '2026-10-27T04:00:00.000', 126),
        @visitor_limit int = 3, @global_limit int = 25;
    -- Inclusive of October 26 in the demo owner's current UTC-04 timezone.
    -- NULL means no per-guest hourly cap; the shared daily cap still applies.
    IF @now < @demo_until
    BEGIN
        SET @visitor_limit = NULL;
        SET @global_limit = 100;
    END;
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
            IF (@visitor_limit IS NOT NULL AND @visitor >= @visitor_limit) OR @global >= @global_limit
            BEGIN
                SET @admitted = 0;
                SET @reason = CASE WHEN @visitor_limit IS NOT NULL AND @visitor >= @visitor_limit THEN 'hourly' ELSE 'daily' END;
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
            @visitor_limit AS visitor_limit, @global_limit AS global_limit;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO