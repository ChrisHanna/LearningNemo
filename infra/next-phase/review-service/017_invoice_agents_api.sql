SET XACT_ABORT ON;
GO
IF OBJECT_ID(N'control.InvoiceAgentSessions', N'U') IS NULL
BEGIN
    CREATE TABLE control.InvoiceAgentSessions (
        RunId char(32) NOT NULL PRIMARY KEY REFERENCES control.InvoiceRuns(RunId),
        SponsorHash char(64) NOT NULL,
        SandboxId char(32) NOT NULL UNIQUE,
        SessionId varchar(160) NULL,
        EnvironmentId varchar(160) NULL,
        UserContextJson nvarchar(2000) NOT NULL CHECK (ISJSON(UserContextJson) = 1),
        State varchar(16) NOT NULL CHECK (State IN ('reserved','connecting','running','completed','uncertain')),
        LastEventId varchar(160) NULL,
        UpdatedAt datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME()
    );
    CREATE UNIQUE INDEX UX_InvoiceAgentSessions_Session ON control.InvoiceAgentSessions(SessionId) WHERE SessionId IS NOT NULL;
    CREATE UNIQUE INDEX UX_InvoiceAgentSessions_Environment ON control.InvoiceAgentSessions(EnvironmentId) WHERE EnvironmentId IS NOT NULL;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_reserve_invoice_agent_session
    @run_id char(32), @sponsor_hash char(64), @sandbox_id char(32), @user_context_json nvarchar(2000)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @user_context_json IS NULL OR ISJSON(@user_context_json) <> 1
        THROW 52700, 'invoice_user_context_invalid', 1;
    IF COALESCE(JSON_VALUE(@user_context_json, '$.sponsor_hash'), '') <> @sponsor_hash
        OR COALESCE(JSON_VALUE(@user_context_json, '$.run_id'), '') <> @run_id
        OR COALESCE(JSON_VALUE(@user_context_json, '$.persona'), '') <> 'operator'
        THROW 52700, 'invoice_user_context_invalid', 1;
    INSERT control.InvoiceAgentSessions(RunId, SponsorHash, SandboxId, UserContextJson, State)
    SELECT run.RunId, run.SponsorHash, run.SandboxId, @user_context_json, 'reserved'
    FROM control.InvoiceRuns AS run
    JOIN control.InvoiceJobs AS job ON job.JobId = run.RunId AND job.SponsorHash = run.SponsorHash
    WHERE run.RunId = @run_id AND run.SponsorHash = @sponsor_hash AND run.SandboxId = @sandbox_id
        AND run.RevokedAt IS NULL AND run.ExpiresAt > SYSUTCDATETIME()
        AND job.State = 'running' AND job.ExpiresAt > SYSUTCDATETIME();
    IF @@ROWCOUNT <> 1 THROW 52701, 'invoice_session_reservation_denied', 1;
    SELECT @run_id AS run_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_checkpoint_invoice_agent_session
    @run_id char(32), @sponsor_hash char(64), @state varchar(16),
    @session_id varchar(160) = NULL, @environment_id varchar(160) = NULL, @event_id varchar(160) = NULL
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @state IS NULL OR @state NOT IN ('connecting','running','completed','uncertain')
        OR (@session_id IS NULL AND @environment_id IS NOT NULL)
        OR (@session_id IS NOT NULL AND @environment_id IS NULL)
        THROW 52702, 'invoice_session_checkpoint_invalid', 1;
    UPDATE control.InvoiceAgentSessions
    SET State = @state, SessionId = COALESCE(SessionId, @session_id),
        EnvironmentId = COALESCE(EnvironmentId, @environment_id),
        LastEventId = COALESCE(@event_id, LastEventId), UpdatedAt = SYSUTCDATETIME()
    WHERE RunId = @run_id AND SponsorHash = @sponsor_hash
        AND (@session_id IS NULL OR SessionId IS NULL OR SessionId = @session_id)
        AND (@environment_id IS NULL OR EnvironmentId IS NULL OR EnvironmentId = @environment_id)
        AND ((State = 'reserved' AND @state IN ('connecting','uncertain'))
          OR (State = 'connecting' AND @state IN ('running','uncertain'))
          OR (State = 'running' AND @state IN ('running','completed','uncertain')))
        AND (@state <> 'connecting' OR @session_id IS NOT NULL);
    IF @@ROWCOUNT <> 1 THROW 52703, 'invoice_session_checkpoint_conflict', 1;
    SELECT @run_id AS run_id;
END;
GO
CREATE OR ALTER PROCEDURE control.usp_get_invoice_agent_session @run_id char(32), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT session.SessionId AS session_id, session.EnvironmentId AS environment_id,
        session.SandboxId AS sandbox_id, run.Kind AS kind, session.UserContextJson AS user_context_json,
        CASE WHEN session.State IN ('reserved','connecting','running') AND
            (run.ExpiresAt <= SYSUTCDATETIME() OR run.RevokedAt IS NOT NULL) THEN 'uncertain' ELSE session.State END AS state,
        session.LastEventId AS last_event_id, session.UpdatedAt AS updated_at
    FROM control.InvoiceAgentSessions AS session JOIN control.InvoiceRuns AS run ON run.RunId = session.RunId
    WHERE session.RunId = @run_id AND session.SponsorHash = @sponsor_hash;
END;
GO
