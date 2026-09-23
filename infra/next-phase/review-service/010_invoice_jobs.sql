SET XACT_ABORT ON;
GO
IF OBJECT_ID(N'control.InvoiceJobs', N'U') IS NULL
BEGIN
    CREATE TABLE control.InvoiceJobs (
        JobId char(32) NOT NULL PRIMARY KEY,
        SponsorHash char(64) NOT NULL,
        Kind varchar(16) NOT NULL CHECK (Kind IN ('planning', 'execution')),
        TargetId char(32) NOT NULL,
        PlanHash char(64) NULL,
        State varchar(16) NOT NULL DEFAULT 'queued' CHECK (State IN ('queued', 'running', 'finished', 'uncertain')),
        ResultJson nvarchar(max) NULL CHECK (ResultJson IS NULL OR ISJSON(ResultJson) = 1),
        CreatedAt datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
        ExpiresAt datetime2(3) NOT NULL DEFAULT DATEADD(minute, 15, SYSUTCDATETIME())
    );
    CREATE TABLE control.InvoiceActivity (
        Sequence bigint IDENTITY PRIMARY KEY,
        JobId char(32) NOT NULL REFERENCES control.InvoiceJobs(JobId),
        EventJson nvarchar(2000) NOT NULL CHECK (ISJSON(EventJson) = 1),
        ObservedAt datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME()
    );
END;
GO
CREATE OR ALTER PROCEDURE control.usp_admit_invoice_job
    @job_id char(32), @sponsor_hash char(64), @kind varchar(16), @target_id char(32), @plan_hash char(64) = NULL
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @kind IS NULL OR @kind NOT IN ('planning', 'execution') OR @sponsor_hash IS NULL
        OR @target_id IS NULL OR @job_id IS NULL OR LEN(@job_id) <> 32
        OR (@kind = 'execution' AND @plan_hash IS NULL) OR (@kind = 'planning' AND @plan_hash IS NOT NULL)
        THROW 52000, 'invoice_job_invalid', 1;
    BEGIN TRANSACTION;
    BEGIN TRY
        IF EXISTS (SELECT 1 FROM control.InvoiceJobs WITH (UPDLOCK, HOLDLOCK) WHERE JobId = @job_id)
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM control.InvoiceJobs WHERE JobId = @job_id AND SponsorHash = @sponsor_hash
                AND Kind = @kind AND TargetId = @target_id AND COALESCE(PlanHash, '') = COALESCE(@plan_hash, ''))
                THROW 52001, 'invoice_job_conflict', 1;
        END
        ELSE
        BEGIN
            IF EXISTS (SELECT 1 FROM control.InvoiceJobs WITH (UPDLOCK, HOLDLOCK)
                WHERE State IN ('queued','running') AND ExpiresAt > SYSUTCDATETIME())
                THROW 52002, 'invoice_runtime_busy', 1;
            INSERT control.InvoiceJobs (JobId, SponsorHash, Kind, TargetId, PlanHash)
            VALUES (@job_id, @sponsor_hash, @kind, @target_id, @plan_hash);
        END;
        COMMIT TRANSACTION;
        SELECT @job_id AS job_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_claim_invoice_job @job_id char(32), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE control.InvoiceJobs SET State = 'running'
    OUTPUT inserted.Kind AS kind, inserted.TargetId AS target_id, inserted.PlanHash AS plan_hash
    WHERE JobId = @job_id AND SponsorHash = @sponsor_hash AND State = 'queued' AND ExpiresAt > SYSUTCDATETIME();
END;
GO
CREATE OR ALTER PROCEDURE control.usp_finish_invoice_job
    @job_id char(32), @sponsor_hash char(64), @state varchar(16), @result_json nvarchar(max)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @state IS NULL OR @state NOT IN ('finished','uncertain') OR ISJSON(@result_json) <> 1 OR @result_json IS NULL
        THROW 52003, 'invoice_job_result_invalid', 1;
    UPDATE control.InvoiceJobs SET State = @state, ResultJson = @result_json
    WHERE JobId = @job_id AND SponsorHash = @sponsor_hash AND State = 'running';
    IF @@ROWCOUNT <> 1 THROW 52004, 'invoice_job_completion_conflict', 1;
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
        ResultJson AS result_json, CreatedAt AS created_at, ExpiresAt AS expires_at
    FROM control.InvoiceJobs WHERE JobId = @job_id AND SponsorHash = @sponsor_hash;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_append_invoice_activity
    @job_id char(32), @sponsor_hash char(64), @event_json nvarchar(2000)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM control.InvoiceJobs WHERE JobId = @job_id AND SponsorHash = @sponsor_hash)
        OR @event_json IS NULL OR ISJSON(@event_json) <> 1
        OR (SELECT COUNT(*) FROM control.InvoiceActivity WHERE JobId = @job_id) >= 200
        THROW 52005, 'invoice_activity_denied', 1;
    INSERT control.InvoiceActivity(JobId, EventJson) OUTPUT inserted.Sequence AS sequence VALUES (@job_id, @event_json);
END;
GO
CREATE OR ALTER PROCEDURE control.usp_read_invoice_activity @job_id char(32), @sponsor_hash char(64), @after bigint
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (100) activity.Sequence AS sequence, activity.ObservedAt AS observed_at, activity.EventJson AS event_json
    FROM control.InvoiceActivity AS activity JOIN control.InvoiceJobs AS job ON job.JobId = activity.JobId
    WHERE job.JobId = @job_id AND job.SponsorHash = @sponsor_hash AND activity.Sequence > @after ORDER BY activity.Sequence;
END;
GO