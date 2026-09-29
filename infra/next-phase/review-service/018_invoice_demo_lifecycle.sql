SET XACT_ABORT ON;
GO
IF OBJECT_ID(N'control.InvoiceDemoSessions',N'U') IS NULL
BEGIN
 CREATE TABLE control.InvoiceDemoSessions (
  SessionId char(32) NOT NULL PRIMARY KEY, OwnerHash char(64) NOT NULL,
  State varchar(16) NOT NULL CHECK(State IN ('active','ending','idle')),
  ExpiresAt datetime2(3) NOT NULL, Cleanup varchar(16) NULL,
  CleanupClaim char(32) NULL, CleanupClaimUntil datetime2(3) NULL,
  UpdatedAt datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME()
 );
 CREATE TABLE control.InvoiceDemoLeases (
  LeaseId char(32) NOT NULL PRIMARY KEY,
  SessionId char(32) NOT NULL REFERENCES control.InvoiceDemoSessions(SessionId),
  OwnerHash char(64) NOT NULL, Released bit NOT NULL DEFAULT 0,
  ExpiresAt datetime2(3) NOT NULL
 );
END;
GO
CREATE OR ALTER PROCEDURE control.usp_invoice_demo_transition
 @action varchar(24), @identifier char(32)=NULL, @owner char(64)=NULL
WITH EXECUTE AS OWNER
AS
BEGIN
 SET NOCOUNT ON; SET XACT_ABORT ON;
 IF @action NOT IN ('status','start','end','acquire','release','claim','finish','unclaim')
  THROW 52100,'invalid_demo_action',1;
 DECLARE @now datetime2(3)=SYSUTCDATETIME(),@session char(32),@lock int,@claimed bit=0;
 BEGIN TRANSACTION;
 BEGIN TRY
  EXEC @lock=sys.sp_getapplock @Resource=N'invoice-demo-lifecycle',@LockMode=N'Exclusive',@LockOwner=N'Transaction',@LockTimeout=15000;
  IF @lock<0 THROW 52101,'demo_lifecycle_busy',1;
  UPDATE control.InvoiceDemoSessions SET State='ending',UpdatedAt=@now WHERE State='active' AND ExpiresAt<=@now;
  SELECT TOP(1) @session=SessionId FROM control.InvoiceDemoSessions ORDER BY UpdatedAt DESC,SessionId;
  IF @action='start'
  BEGIN
   IF @identifier IS NULL OR @owner IS NULL THROW 52102,'demo_identity_required',1;
   IF EXISTS(SELECT 1 FROM control.InvoiceDemoSessions WHERE SessionId=@identifier)
   BEGIN
    IF NOT EXISTS(SELECT 1 FROM control.InvoiceDemoSessions WHERE SessionId=@identifier AND OwnerHash=@owner AND SessionId=@session)
     THROW 52103,'demo_request_conflict',1;
   END
   ELSE
   BEGIN
    IF EXISTS(SELECT 1 FROM control.InvoiceDemoSessions WHERE State<>'idle') THROW 52104,'demo_still_active',1;
    IF EXISTS(SELECT 1 FROM control.InvoiceJobs WHERE State IN ('queued','running') AND ExpiresAt>@now)
      OR EXISTS(SELECT 1 FROM control.InvoiceRuns WHERE RevokedAt IS NULL AND ExpiresAt>@now)
     THROW 52110,'previous_authority_still_active',1;
    INSERT control.InvoiceDemoSessions(SessionId,OwnerHash,State,ExpiresAt) VALUES(@identifier,@owner,'active',DATEADD(hour,4,@now));
    SET @session=@identifier;
   END
  END;
  IF @action='end'
  BEGIN
   IF NOT EXISTS(SELECT 1 FROM control.InvoiceDemoSessions WHERE SessionId=@identifier AND OwnerHash=@owner AND SessionId=@session)
    THROW 52105,'owned_demo_required',1;
   UPDATE control.InvoiceDemoSessions SET State='ending',UpdatedAt=@now WHERE SessionId=@session AND State='active';
  END;
  IF @action='acquire'
  BEGIN
   IF @identifier IS NULL OR @owner IS NULL OR NOT EXISTS(SELECT 1 FROM control.InvoiceDemoSessions WHERE SessionId=@session AND State='active')
    THROW 52106,'active_demo_required',1;
   IF EXISTS(SELECT 1 FROM control.InvoiceDemoLeases WHERE LeaseId=@identifier) THROW 52107,'demo_admission_replay',1;
   INSERT control.InvoiceDemoLeases(LeaseId,SessionId,OwnerHash,ExpiresAt) VALUES(@identifier,@session,@owner,DATEADD(minute,20,@now));
  END;
  IF @action='release'
  BEGIN
   IF NOT EXISTS(SELECT 1 FROM control.InvoiceDemoLeases WHERE LeaseId=@identifier AND OwnerHash=@owner) THROW 52108,'owned_lease_required',1;
   UPDATE control.InvoiceDemoLeases SET Released=1 WHERE LeaseId=@identifier;
  END;
  -- Expired leases remain visible while any unexpired job or capability can act.
  IF NOT EXISTS(SELECT 1 FROM control.InvoiceJobs WHERE State IN ('queued','running') AND ExpiresAt>@now)
   AND NOT EXISTS(SELECT 1 FROM control.InvoiceRuns WHERE RevokedAt IS NULL AND ExpiresAt>@now)
   UPDATE control.InvoiceDemoLeases SET Released=1 WHERE Released=0 AND ExpiresAt<=@now;
  IF @action='claim' AND @identifier IS NOT NULL
  BEGIN
   UPDATE control.InvoiceDemoSessions SET CleanupClaim=@identifier,CleanupClaimUntil=DATEADD(minute,15,@now),UpdatedAt=@now
    WHERE SessionId=@session AND State='ending' AND (CleanupClaim IS NULL OR CleanupClaimUntil<=@now)
    AND NOT EXISTS(SELECT 1 FROM control.InvoiceDemoLeases WHERE SessionId=@session AND Released=0)
    AND NOT EXISTS(SELECT 1 FROM control.InvoiceRuns WHERE RevokedAt IS NULL AND ExpiresAt>@now)
    AND NOT EXISTS(SELECT 1 FROM control.InvoiceJobs WHERE State IN ('queued','running') AND ExpiresAt>@now);
   IF @@ROWCOUNT=1 SET @claimed=1;
  END;
  IF @action IN ('finish','unclaim')
  BEGIN
   IF NOT EXISTS(SELECT 1 FROM control.InvoiceDemoSessions WHERE SessionId=@session AND CleanupClaim=@identifier AND State='ending')
    THROW 52109,'owned_cleanup_claim_required',1;
   UPDATE control.InvoiceDemoSessions SET State=CASE WHEN @action='finish' THEN 'idle' ELSE State END,
    Cleanup=CASE WHEN @action='finish' THEN 'confirmed' ELSE 'unconfirmed' END,
    CleanupClaim=NULL,CleanupClaimUntil=NULL,UpdatedAt=@now WHERE SessionId=@session;
  END;
  COMMIT TRANSACTION;
  SELECT 'invoice-demo-session' AS source,@session AS session_id,
   COALESCE((SELECT State FROM control.InvoiceDemoSessions WHERE SessionId=@session),'idle') AS state,
   (SELECT ExpiresAt FROM control.InvoiceDemoSessions WHERE SessionId=@session) AS expires_at,
   (SELECT OwnerHash FROM control.InvoiceDemoSessions WHERE SessionId=@session) AS owner_hash,
   (SELECT Cleanup FROM control.InvoiceDemoSessions WHERE SessionId=@session) AS cleanup,
   (SELECT UpdatedAt FROM control.InvoiceDemoSessions WHERE SessionId=@session) AS updated_at,
   (SELECT COUNT(*) FROM control.InvoiceDemoLeases WHERE SessionId=@session AND Released=0) AS inflight,
   (SELECT MIN(ExpiresAt) FROM control.InvoiceDemoLeases WHERE SessionId=@session AND Released=0) AS recovery_after,
   @claimed AS claimed;
 END TRY
 BEGIN CATCH
  IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;
  THROW;
 END CATCH
END;
GO
