CREATE OR ALTER PROCEDURE control.usp_reconcile_human_broker
    @plan_id nvarchar(128), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @execution_id nvarchar(128), @state nvarchar(32);
        SELECT @execution_id = execution_record.ExecutionId, @state = execution_record.State
        FROM control.HumanExecutions AS execution_record WITH (UPDLOCK, HOLDLOCK)
        INNER JOIN control.ResolutionPlans AS plan_record ON plan_record.PlanId = execution_record.PlanId
        INNER JOIN control.Approvals AS approval ON approval.ApprovalId = execution_record.ApprovalId
        INNER JOIN control.Tasks AS task ON task.TaskId = plan_record.TaskId
        WHERE execution_record.PlanId = @plan_id AND execution_record.SponsorHash = @sponsor_hash
            AND plan_record.CreatedByHash = @sponsor_hash AND plan_record.CreatedByScheme = N'entra-tenant-oid-v1'
            AND plan_record.PlanHash = execution_record.PlanHash AND approval.PlanHash = execution_record.PlanHash
            AND plan_record.State = N'consumed' AND approval.State = N'consumed' AND approval.ConsumedAt IS NOT NULL
            AND approval.ApprovedByHash <> @sponsor_hash
            AND task.State IN (N'remediated', N'verified', N'completed')
            AND EXISTS (SELECT 1 FROM control.TaskEvents AS event_record
                WHERE event_record.TaskId = task.TaskId AND event_record.EventType = N'remediation'
                    AND event_record.ReasonCode = N'safe_query_activated'
                    AND JSON_VALUE(event_record.DataJson, '$.approval_id') = approval.ApprovalId
                    AND JSON_VALUE(event_record.DataJson, '$.plan_hash') = execution_record.PlanHash
                    AND JSON_VALUE(event_record.DataJson, '$.safe_query_version') = N'cycle-safe-v1');
        IF @execution_id IS NULL THROW 51610, 'broker_commit_not_confirmed_no_replay_permitted', 1;
        IF @state = N'claimed'
        BEGIN
            DECLARE @receipt varchar(max) = '{"operation_id":"remediate.activate-cycle-safe-query-v1","result":{"safe_query_version":"cycle-safe-v1"},"result_code":"safe_query_activated"}';
            UPDATE control.HumanExecutions SET State = N'broker', BrokerJson = @receipt,
                BrokerHash = LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', @receipt), 2)), UpdatedAt = SYSUTCDATETIME()
            WHERE ExecutionId = @execution_id AND State = N'claimed';
        END;
        COMMIT TRANSACTION;
        SELECT @execution_id AS execution_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO