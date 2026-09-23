SET XACT_ABORT ON;
GO
IF OBJECT_ID(N'lab.InvoiceScenarios', N'U') IS NULL
BEGIN
    CREATE TABLE lab.InvoiceScenarios (
        ScenarioId char(32) NOT NULL PRIMARY KEY,
        Revision int NOT NULL DEFAULT 1,
        ImportVersion varchar(32) NOT NULL DEFAULT 'retry-unsafe-v1',
        ReportedCents bigint NOT NULL DEFAULT 0,
        CreatedAt datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
        ExpiresAt datetime2(3) NOT NULL,
        CONSTRAINT CK_InvoiceScenario_Revision CHECK (Revision > 0),
        CONSTRAINT CK_InvoiceScenario_Import CHECK (ImportVersion IN ('retry-unsafe-v1', 'idempotent-v2'))
    );
    CREATE TABLE lab.InvoiceOrders (
        ScenarioId char(32) NOT NULL REFERENCES lab.InvoiceScenarios(ScenarioId),
        OrderId int NOT NULL,
        CustomerId int NOT NULL,
        AmountCents int NOT NULL CHECK (AmountCents > 0),
        PRIMARY KEY (ScenarioId, OrderId)
    );
    CREATE TABLE lab.InvoiceRows (
        InvoiceId bigint IDENTITY PRIMARY KEY,
        ScenarioId char(32) NOT NULL,
        OrderId int NOT NULL,
        BatchId int NOT NULL,
        Attempt int NOT NULL,
        AmountCents int NOT NULL,
        Quarantined bit NOT NULL DEFAULT 0,
        FOREIGN KEY (ScenarioId, OrderId) REFERENCES lab.InvoiceOrders(ScenarioId, OrderId)
    );
    CREATE INDEX IX_InvoiceRows_Scenario ON lab.InvoiceRows(ScenarioId, OrderId, Quarantined);
END;
GO
CREATE OR ALTER PROCEDURE lab.usp_import_invoice_batch
    @scenario_id char(32), @attempt int
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM lab.InvoiceScenarios WHERE ScenarioId = @scenario_id AND ExpiresAt > SYSUTCDATETIME())
        THROW 51800, 'invoice_scenario_missing_or_expired', 1;
    INSERT lab.InvoiceRows (ScenarioId, OrderId, BatchId, Attempt, AmountCents)
    SELECT orders.ScenarioId, orders.OrderId, 1, @attempt, orders.AmountCents
    FROM lab.InvoiceOrders AS orders
    JOIN lab.InvoiceScenarios AS scenario ON scenario.ScenarioId = orders.ScenarioId
    WHERE orders.ScenarioId = @scenario_id AND (scenario.ImportVersion = 'retry-unsafe-v1' OR NOT EXISTS (
        SELECT 1 FROM lab.InvoiceRows AS invoice
        WHERE invoice.ScenarioId = orders.ScenarioId AND invoice.OrderId = orders.OrderId AND invoice.Quarantined = 0));
END;
GO
CREATE OR ALTER PROCEDURE control.usp_create_invoice_scenario
    @scenario_id char(32), @variant varchar(32)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF LEN(@scenario_id) <> 32 OR @scenario_id COLLATE Latin1_General_100_BIN2 LIKE '%[^0-9a-f]%'
        OR @variant NOT IN ('lost-acknowledgement', 'healthy') OR @variant IS NULL
        THROW 51801, 'invoice_fixture_contract_denied', 1;
    BEGIN TRANSACTION;
    BEGIN TRY
        IF EXISTS (SELECT 1 FROM lab.InvoiceScenarios WITH (UPDLOCK, HOLDLOCK) WHERE ScenarioId = @scenario_id)
            THROW 51802, 'invoice_fixture_already_exists', 1;
        INSERT lab.InvoiceScenarios (ScenarioId, ImportVersion, ExpiresAt)
        VALUES (@scenario_id, CASE WHEN @variant = 'healthy' THEN 'idempotent-v2' ELSE 'retry-unsafe-v1' END, DATEADD(hour, 2, SYSUTCDATETIME()));
        INSERT lab.InvoiceOrders (ScenarioId, OrderId, CustomerId, AmountCents)
        SELECT @scenario_id, seed.OrderId, seed.CustomerId, seed.AmountCents FROM (VALUES
            (1, 101, 12500), (2, 101, 12500), (3, 102, 20000), (4, 103, 4500),
            (5, 104, 7000), (6, 105, 16000), (7, 106, 3300), (8, 107, 25000),
            (9, 108, 8000), (10, 109, 11000), (11, 110, 14000), (12, 110, 14000)
        ) AS seed(OrderId, CustomerId, AmountCents);
        EXEC lab.usp_import_invoice_batch @scenario_id = @scenario_id, @attempt = 1;
        EXEC lab.usp_import_invoice_batch @scenario_id = @scenario_id, @attempt = 2;
        UPDATE lab.InvoiceScenarios SET ReportedCents = (
            SELECT SUM(CONVERT(bigint, AmountCents)) FROM lab.InvoiceRows WHERE ScenarioId = @scenario_id AND Quarantined = 0
        ) WHERE ScenarioId = @scenario_id;
        COMMIT TRANSACTION;
        SELECT @scenario_id AS scenario_id;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER VIEW lab.InvoiceDuplicateRows AS
    SELECT InvoiceId, ScenarioId, OrderId, BatchId, Attempt, AmountCents,
        ROW_NUMBER() OVER (PARTITION BY ScenarioId, OrderId ORDER BY InvoiceId) AS Occurrence
    FROM lab.InvoiceRows WHERE Quarantined = 0;
GO
CREATE OR ALTER PROCEDURE ops.usp_diagnose_invoice_summary @scenario_id char(32)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT scenario.ScenarioId AS scenario_id, scenario.Revision AS revision,
        CONVERT(varchar(30), SYSUTCDATETIME(), 126) + 'Z' AS observed_at,
        'invoice-diagnostic-api' AS source,
        (SELECT COUNT(*) FROM lab.InvoiceOrders WHERE ScenarioId = @scenario_id) AS orders,
        (SELECT COUNT(*) FROM lab.InvoiceRows WHERE ScenarioId = @scenario_id AND Quarantined = 0) AS active_invoices,
        (SELECT COUNT(*) FROM lab.InvoiceDuplicateRows WHERE ScenarioId = @scenario_id AND Occurrence > 1) AS duplicate_invoices,
        LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', COALESCE((
            SELECT STRING_AGG(CONVERT(varchar(max), InvoiceId), ',') WITHIN GROUP (ORDER BY InvoiceId)
            FROM lab.InvoiceDuplicateRows WHERE ScenarioId = @scenario_id AND Occurrence > 1
        ), '')), 2)) AS duplicate_set_hash,
        (SELECT COALESCE(SUM(CONVERT(bigint, AmountCents)), 0) FROM lab.InvoiceOrders WHERE ScenarioId = @scenario_id) AS expected_cents,
        (SELECT COALESCE(SUM(CONVERT(bigint, AmountCents)), 0) FROM lab.InvoiceRows WHERE ScenarioId = @scenario_id AND Quarantined = 0) AS actual_cents,
        scenario.ReportedCents AS reported_cents, scenario.ImportVersion AS import_version
    FROM lab.InvoiceScenarios AS scenario WHERE ScenarioId = @scenario_id AND ExpiresAt > SYSUTCDATETIME();
END;
GO
CREATE OR ALTER PROCEDURE ops.usp_diagnose_invoice_batches @scenario_id char(32)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (48) invoice.InvoiceId AS invoice_id, invoice.OrderId AS order_id,
        orders.CustomerId AS customer_id, invoice.BatchId AS batch_id, invoice.Attempt AS attempt,
        invoice.AmountCents AS amount_cents, invoice.Quarantined AS quarantined
    FROM lab.InvoiceRows AS invoice
    JOIN lab.InvoiceOrders AS orders ON orders.ScenarioId = invoice.ScenarioId AND orders.OrderId = invoice.OrderId
    JOIN lab.InvoiceScenarios AS scenario ON scenario.ScenarioId = invoice.ScenarioId
    WHERE invoice.ScenarioId = @scenario_id AND scenario.ExpiresAt > SYSUTCDATETIME()
    ORDER BY invoice.OrderId, invoice.InvoiceId;
END;
GO
CREATE OR ALTER PROCEDURE lab.usp_apply_invoice_operation
    @scenario_id char(32), @expected_revision int, @operation varchar(64), @duplicate_set_hash char(64) = NULL
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @operation IS NULL OR @operation NOT IN ('invoice.quarantine-duplicates.v1', 'invoice.rebuild-total.v1', 'invoice.activate-idempotent-import.v1')
        THROW 51803, 'invoice_operation_not_registered', 1;
    BEGIN TRANSACTION;
    BEGIN TRY
        IF NOT EXISTS (SELECT 1 FROM lab.InvoiceScenarios WITH (UPDLOCK, HOLDLOCK)
            WHERE ScenarioId = @scenario_id AND Revision = @expected_revision AND ExpiresAt > SYSUTCDATETIME())
            THROW 51804, 'invoice_precondition_changed', 1;
        IF @operation = 'invoice.quarantine-duplicates.v1'
        BEGIN
            DECLARE @actual_hash char(64) = LOWER(CONVERT(char(64), HASHBYTES('SHA2_256', COALESCE((
                SELECT STRING_AGG(CONVERT(varchar(max), InvoiceId), ',') WITHIN GROUP (ORDER BY InvoiceId)
                FROM lab.InvoiceDuplicateRows WHERE ScenarioId = @scenario_id AND Occurrence > 1
            ), '')), 2));
            IF @duplicate_set_hash IS NULL OR @duplicate_set_hash <> @actual_hash
                THROW 51805, 'invoice_duplicate_set_changed', 1;
            UPDATE lab.InvoiceRows SET Quarantined = 1 WHERE ScenarioId = @scenario_id AND InvoiceId IN (
                SELECT InvoiceId FROM lab.InvoiceDuplicateRows WHERE ScenarioId = @scenario_id AND Occurrence > 1);
        END
        ELSE
        BEGIN
            IF @duplicate_set_hash IS NOT NULL THROW 51806, 'unexpected_invoice_parameter', 1;
            IF EXISTS (SELECT 1 FROM lab.InvoiceDuplicateRows WHERE ScenarioId = @scenario_id AND Occurrence > 1)
                THROW 51807, 'unresolved_invoice_duplicates', 1;
            IF @operation = 'invoice.rebuild-total.v1'
                UPDATE lab.InvoiceScenarios SET ReportedCents = (
                    SELECT COALESCE(SUM(CONVERT(bigint, AmountCents)), 0) FROM lab.InvoiceRows WHERE ScenarioId = @scenario_id AND Quarantined = 0
                ) WHERE ScenarioId = @scenario_id;
            ELSE
                UPDATE lab.InvoiceScenarios SET ImportVersion = 'idempotent-v2' WHERE ScenarioId = @scenario_id;
        END;
        UPDATE lab.InvoiceScenarios SET Revision = Revision + 1 WHERE ScenarioId = @scenario_id;
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE ops.usp_verify_invoice_integrity @scenario_id char(32)
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT CAST(CASE WHEN NOT EXISTS (
        SELECT 1 FROM lab.InvoiceDuplicateRows WHERE ScenarioId = @scenario_id AND Occurrence > 1
    ) THEN 1 ELSE 0 END AS bit) AS no_duplicates,
    CAST(CASE WHEN NOT EXISTS (
        SELECT 1 FROM lab.InvoiceOrders AS orders WHERE orders.ScenarioId = @scenario_id AND (
            SELECT COUNT(*) FROM lab.InvoiceRows AS invoice WHERE invoice.ScenarioId = orders.ScenarioId
                AND invoice.OrderId = orders.OrderId AND invoice.AmountCents = orders.AmountCents AND invoice.Quarantined = 0
        ) <> 1
    ) THEN 1 ELSE 0 END AS bit) AS legitimate_invoices_preserved,
    CAST(CASE WHEN scenario.ReportedCents = (
        SELECT SUM(CONVERT(bigint, AmountCents)) FROM lab.InvoiceOrders WHERE ScenarioId = @scenario_id
    ) THEN 1 ELSE 0 END AS bit) AS total_reconciles,
    CAST(CASE WHEN scenario.ImportVersion = 'idempotent-v2' THEN 1 ELSE 0 END AS bit) AS idempotent_version_active
    FROM lab.InvoiceScenarios AS scenario WHERE ScenarioId = @scenario_id AND ExpiresAt > SYSUTCDATETIME();
END;
GO