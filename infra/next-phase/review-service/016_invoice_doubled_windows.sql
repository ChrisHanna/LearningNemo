SET XACT_ABORT ON;
GO
ALTER TABLE control.InvoicePlans DROP CONSTRAINT DF_InvoicePlans_ReviewExpiresAt;
GO
ALTER TABLE control.InvoicePlans ADD CONSTRAINT DF_InvoicePlans_ReviewExpiresAt
    DEFAULT DATEADD(minute, 180, SYSUTCDATETIME()) FOR ReviewExpiresAt;
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
        ReviewerHash = @reviewer_hash, ApprovedAt = SYSUTCDATETIME(), ApprovalExpiresAt = DATEADD(minute, 30, SYSUTCDATETIME())
    WHERE PlanId = @plan_id AND PlanHash = @plan_hash AND SponsorHash <> @reviewer_hash AND State = 'submitted'
        AND ReviewExpiresAt > SYSUTCDATETIME();
    IF @@ROWCOUNT <> 1 THROW 51906, 'invoice_review_denied', 1;
    SELECT @plan_id AS plan_id;
END;
GO