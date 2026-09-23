SET XACT_ABORT ON;
GO

BEGIN TRANSACTION;
BEGIN TRY
    MERGE lab.HierarchyEdges AS target
    USING (VALUES
        (N'A', N'B'),
        (N'B', N'C'),
        (N'C', N'A'),
        (N'A', N'D')
    ) AS source (ParentNode, ChildNode)
    ON target.ParentNode = source.ParentNode AND target.ChildNode = source.ChildNode
    WHEN NOT MATCHED THEN INSERT (ParentNode, ChildNode) VALUES (source.ParentNode, source.ChildNode);

    MERGE lab.QueryVersions AS target
    USING (VALUES
        (N'cycle-unsafe-v1', N'unsafe', CONVERT(char(64), HASHBYTES('SHA2_256', N'ops.usp_run_controlled_unsafe_query:v1'), 2), CAST(1 AS bit)),
        (N'cycle-safe-v1', N'safe', CONVERT(char(64), HASHBYTES('SHA2_256', N'ops.usp_run_cycle_safe_query:v1'), 2), CAST(0 AS bit))
    ) AS source (QueryVersion, QueryKind, DefinitionHash, IsActive)
    ON target.QueryVersion = source.QueryVersion
    WHEN MATCHED THEN UPDATE SET QueryKind = source.QueryKind, DefinitionHash = source.DefinitionHash
    WHEN NOT MATCHED THEN INSERT (QueryVersion, QueryKind, DefinitionHash, IsActive)
        VALUES (source.QueryVersion, source.QueryKind, source.DefinitionHash, source.IsActive);
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO