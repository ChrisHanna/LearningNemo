CREATE OR ALTER PROCEDURE control.usp_get_human_execution
    @plan_id nvarchar(128), @sponsor_hash char(64)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF @sponsor_hash IS NULL OR TRY_CONVERT(varbinary(32), @sponsor_hash, 2) IS NULL
        THROW 51600, 'execution_sponsor_required', 1;
    SELECT (
        SELECT execution_record.ExecutionId AS execution_id, execution_record.PlanId AS plan_id,
            execution_record.PlanHash AS plan_hash, execution_record.State AS state,
            JSON_QUERY(execution_record.BrokerJson) AS broker, execution_record.BrokerHash AS broker_hash,
            JSON_QUERY(execution_record.VerificationJson) AS verification, execution_record.VerificationHash AS verification_hash
        FOR JSON PATH, INCLUDE_NULL_VALUES, WITHOUT_ARRAY_WRAPPER
    ) AS execution_json
    FROM control.HumanExecutions AS execution_record
    INNER JOIN control.ResolutionPlans AS plan_record ON plan_record.PlanId = execution_record.PlanId
    WHERE execution_record.PlanId = @plan_id AND execution_record.SponsorHash = @sponsor_hash
        AND plan_record.CreatedByHash = @sponsor_hash AND plan_record.CreatedByScheme = N'entra-tenant-oid-v1';
END;
GO