SET XACT_ABORT ON;
GO

IF SCHEMA_ID(N'control') IS NULL EXEC(N'CREATE SCHEMA control AUTHORIZATION dbo;');
IF SCHEMA_ID(N'lab') IS NULL EXEC(N'CREATE SCHEMA lab AUTHORIZATION dbo;');
IF SCHEMA_ID(N'ops') IS NULL EXEC(N'CREATE SCHEMA ops AUTHORIZATION dbo;');
GO

IF OBJECT_ID(N'control.SchemaMigrations', N'U') IS NULL
BEGIN
    CREATE TABLE control.SchemaMigrations (
        MigrationId nvarchar(128) NOT NULL CONSTRAINT PK_SchemaMigrations PRIMARY KEY,
        ContentHash char(64) NOT NULL,
        AppliedAt datetime2(0) NOT NULL CONSTRAINT DF_SchemaMigrations_AppliedAt DEFAULT SYSUTCDATETIME()
    );
END;
GO

IF OBJECT_ID(N'control.Tasks', N'U') IS NULL
BEGIN
    CREATE TABLE control.Tasks (
        TaskId nvarchar(128) NOT NULL CONSTRAINT PK_Tasks PRIMARY KEY,
        Title nvarchar(256) NOT NULL,
        State nvarchar(32) NOT NULL,
        Version int NOT NULL CONSTRAINT DF_Tasks_Version DEFAULT 1,
        CreatedAt datetime2(0) NOT NULL CONSTRAINT DF_Tasks_CreatedAt DEFAULT SYSUTCDATETIME(),
        UpdatedAt datetime2(0) NOT NULL CONSTRAINT DF_Tasks_UpdatedAt DEFAULT SYSUTCDATETIME(),
        RowVersion rowversion NOT NULL,
        CONSTRAINT CK_Tasks_State CHECK (State IN (N'open', N'contained', N'remediated', N'verified', N'completed')),
        CONSTRAINT CK_Tasks_Version CHECK (Version >= 1)
    );
END;
GO

IF OBJECT_ID(N'control.TaskEvents', N'U') IS NULL
BEGIN
    CREATE TABLE control.TaskEvents (
        EventSequence bigint IDENTITY(1, 1) NOT NULL CONSTRAINT PK_TaskEvents PRIMARY KEY,
        EventId uniqueidentifier NOT NULL CONSTRAINT DF_TaskEvents_EventId DEFAULT NEWID(),
        TaskId nvarchar(128) NOT NULL,
        EventType nvarchar(64) NOT NULL,
        ReasonCode nvarchar(128) NOT NULL,
        DataJson nvarchar(max) NOT NULL CONSTRAINT DF_TaskEvents_DataJson DEFAULT N'{}',
        CreatedAt datetime2(0) NOT NULL CONSTRAINT DF_TaskEvents_CreatedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_TaskEvents_EventId UNIQUE (EventId),
        CONSTRAINT FK_TaskEvents_Tasks FOREIGN KEY (TaskId) REFERENCES control.Tasks(TaskId),
        CONSTRAINT CK_TaskEvents_DataJson CHECK (ISJSON(DataJson) = 1)
    );
END;
GO

IF OBJECT_ID(N'control.Engagements', N'U') IS NULL
BEGIN
    CREATE TABLE control.Engagements (
        EngagementId nvarchar(128) NOT NULL CONSTRAINT PK_Engagements PRIMARY KEY,
        TaskId nvarchar(128) NOT NULL,
        WorkspaceId nvarchar(128) NOT NULL,
        SponsorSubjectHash char(64) NOT NULL,
        State nvarchar(32) NOT NULL,
        Version int NOT NULL CONSTRAINT DF_Engagements_Version DEFAULT 1,
        CreatedAt datetime2(0) NOT NULL CONSTRAINT DF_Engagements_CreatedAt DEFAULT SYSUTCDATETIME(),
        ExpiresAt datetime2(0) NOT NULL,
        RowVersion rowversion NOT NULL,
        CONSTRAINT FK_Engagements_Tasks FOREIGN KEY (TaskId) REFERENCES control.Tasks(TaskId),
        CONSTRAINT CK_Engagements_State CHECK (State IN (N'requested', N'provisioning', N'ready', N'engaged', N'revoking', N'deleted')),
        CONSTRAINT CK_Engagements_Lifetime CHECK (ExpiresAt > CreatedAt)
    );
END;
GO

IF OBJECT_ID(N'control.AgentRegistrations', N'U') IS NULL
BEGIN
    CREATE TABLE control.AgentRegistrations (
        LogicalAgentId nvarchar(128) NOT NULL CONSTRAINT PK_AgentRegistrations PRIMARY KEY,
        EngagementId nvarchar(128) NOT NULL,
        WorkspaceId nvarchar(128) NOT NULL,
        PolicyHash char(64) NOT NULL,
        State nvarchar(24) NOT NULL,
        CreatedAt datetime2(0) NOT NULL CONSTRAINT DF_AgentRegistrations_CreatedAt DEFAULT SYSUTCDATETIME(),
        ExpiresAt datetime2(0) NOT NULL,
        RowVersion rowversion NOT NULL,
        CONSTRAINT FK_AgentRegistrations_Engagements FOREIGN KEY (EngagementId) REFERENCES control.Engagements(EngagementId),
        CONSTRAINT CK_AgentRegistrations_State CHECK (State IN (N'active', N'revoked', N'expired')),
        CONSTRAINT CK_AgentRegistrations_Lifetime CHECK (ExpiresAt > CreatedAt)
    );
END;
GO

IF OBJECT_ID(N'control.ResolutionPlans', N'U') IS NULL
BEGIN
    CREATE TABLE control.ResolutionPlans (
        PlanId nvarchar(128) NOT NULL CONSTRAINT PK_ResolutionPlans PRIMARY KEY,
        TaskId nvarchar(128) NOT NULL,
        EngagementId nvarchar(128) NOT NULL,
        WorkspaceId nvarchar(128) NOT NULL,
        LogicalAgentId nvarchar(128) NOT NULL,
        PlanHash char(64) NOT NULL,
        OperationId nvarchar(128) NOT NULL,
        TargetResource nvarchar(256) NOT NULL,
        SafeQueryVersion nvarchar(64) NOT NULL,
        RollbackVersion nvarchar(64) NOT NULL,
        ParametersJson nvarchar(max) NOT NULL,
        State nvarchar(32) NOT NULL,
        Version int NOT NULL CONSTRAINT DF_ResolutionPlans_Version DEFAULT 1,
        CreatedByHash char(64) NOT NULL,
        CreatedAt datetime2(0) NOT NULL CONSTRAINT DF_ResolutionPlans_CreatedAt DEFAULT SYSUTCDATETIME(),
        RowVersion rowversion NOT NULL,
        CONSTRAINT UQ_ResolutionPlans_PlanHash UNIQUE (PlanHash),
        CONSTRAINT FK_ResolutionPlans_Tasks FOREIGN KEY (TaskId) REFERENCES control.Tasks(TaskId),
        CONSTRAINT FK_ResolutionPlans_Engagements FOREIGN KEY (EngagementId) REFERENCES control.Engagements(EngagementId),
        CONSTRAINT FK_ResolutionPlans_Agents FOREIGN KEY (LogicalAgentId) REFERENCES control.AgentRegistrations(LogicalAgentId),
        CONSTRAINT CK_ResolutionPlans_ParametersJson CHECK (ISJSON(ParametersJson) = 1),
        CONSTRAINT CK_ResolutionPlans_State CHECK (State IN (N'draft', N'awaiting_approval', N'approved', N'rejected', N'expired', N'consumed'))
    );
END;
GO

IF OBJECT_ID(N'control.Approvals', N'U') IS NULL
BEGIN
    CREATE TABLE control.Approvals (
        ApprovalId nvarchar(128) NOT NULL CONSTRAINT PK_Approvals PRIMARY KEY,
        PlanId nvarchar(128) NOT NULL,
        TaskId nvarchar(128) NOT NULL,
        EngagementId nvarchar(128) NOT NULL,
        WorkspaceId nvarchar(128) NOT NULL,
        LogicalAgentId nvarchar(128) NOT NULL,
        PlanHash char(64) NOT NULL,
        OperationId nvarchar(128) NOT NULL,
        TargetResource nvarchar(256) NOT NULL,
        SafeQueryVersion nvarchar(64) NOT NULL,
        ApprovedByHash char(64) NOT NULL,
        ApprovedAt datetime2(0) NOT NULL,
        ExpiresAt datetime2(0) NOT NULL,
        OneTimeId nvarchar(128) NOT NULL,
        ConsumedAt datetime2(0) NULL,
        State nvarchar(24) NOT NULL,
        Version int NOT NULL CONSTRAINT DF_Approvals_Version DEFAULT 1,
        ReceiptHash char(64) NOT NULL,
        RowVersion rowversion NOT NULL,
        CONSTRAINT UQ_Approvals_OneTimeId UNIQUE (OneTimeId),
        CONSTRAINT UQ_Approvals_ReceiptHash UNIQUE (ReceiptHash),
        CONSTRAINT FK_Approvals_Plans FOREIGN KEY (PlanId) REFERENCES control.ResolutionPlans(PlanId),
        CONSTRAINT FK_Approvals_Tasks FOREIGN KEY (TaskId) REFERENCES control.Tasks(TaskId),
        CONSTRAINT CK_Approvals_State CHECK (State IN (N'approved', N'expired', N'consumed')),
        CONSTRAINT CK_Approvals_Lifetime CHECK (ExpiresAt > ApprovedAt),
        CONSTRAINT CK_Approvals_Consumption CHECK ((State = N'consumed' AND ConsumedAt IS NOT NULL) OR (State <> N'consumed' AND ConsumedAt IS NULL))
    );
END;
GO

IF OBJECT_ID(N'control.Executions', N'U') IS NULL
BEGIN
    CREATE TABLE control.Executions (
        ExecutionId nvarchar(128) NOT NULL CONSTRAINT PK_Executions PRIMARY KEY,
        TaskId nvarchar(128) NOT NULL,
        PlanId nvarchar(128) NOT NULL,
        ApprovalId nvarchar(128) NOT NULL,
        PlanHash char(64) NOT NULL,
        SafeQueryVersion nvarchar(64) NOT NULL,
        WorkspaceId nvarchar(128) NOT NULL,
        TriggeredByHash char(64) NOT NULL,
        State nvarchar(24) NOT NULL,
        ResultCode nvarchar(128) NULL,
        ReceiptHash char(64) NULL,
        StartedAt datetime2(0) NOT NULL CONSTRAINT DF_Executions_StartedAt DEFAULT SYSUTCDATETIME(),
        CompletedAt datetime2(0) NULL,
        RowVersion rowversion NOT NULL,
        CONSTRAINT FK_Executions_Tasks FOREIGN KEY (TaskId) REFERENCES control.Tasks(TaskId),
        CONSTRAINT FK_Executions_Plans FOREIGN KEY (PlanId) REFERENCES control.ResolutionPlans(PlanId),
        CONSTRAINT FK_Executions_Approvals FOREIGN KEY (ApprovalId) REFERENCES control.Approvals(ApprovalId),
        CONSTRAINT CK_Executions_State CHECK (State IN (N'queued', N'running', N'succeeded', N'failed'))
    );
END;
GO

IF OBJECT_ID(N'control.Verifications', N'U') IS NULL
BEGIN
    CREATE TABLE control.Verifications (
        VerificationId nvarchar(128) NOT NULL CONSTRAINT PK_Verifications PRIMARY KEY,
        TaskId nvarchar(128) NOT NULL,
        ExecutionId nvarchar(128) NOT NULL,
        PlanHash char(64) NOT NULL,
        SafeQueryVersion nvarchar(64) NOT NULL,
        VerificationProfile nvarchar(128) NOT NULL,
        WorkspaceId nvarchar(128) NOT NULL,
        State nvarchar(24) NOT NULL,
        ChecksJson nvarchar(max) NOT NULL,
        ReceiptHash char(64) NOT NULL,
        VerifiedAt datetime2(0) NOT NULL CONSTRAINT DF_Verifications_VerifiedAt DEFAULT SYSUTCDATETIME(),
        RowVersion rowversion NOT NULL,
        CONSTRAINT FK_Verifications_Tasks FOREIGN KEY (TaskId) REFERENCES control.Tasks(TaskId),
        CONSTRAINT FK_Verifications_Executions FOREIGN KEY (ExecutionId) REFERENCES control.Executions(ExecutionId),
        CONSTRAINT CK_Verifications_State CHECK (State IN (N'pending', N'passed', N'failed')),
        CONSTRAINT CK_Verifications_ChecksJson CHECK (ISJSON(ChecksJson) = 1)
    );
END;
GO

IF OBJECT_ID(N'control.EvidenceEvents', N'U') IS NULL
BEGIN
    CREATE TABLE control.EvidenceEvents (
        SequenceNumber bigint IDENTITY(1, 1) NOT NULL CONSTRAINT PK_EvidenceEvents PRIMARY KEY,
        EventId nvarchar(128) NOT NULL,
        EventType nvarchar(64) NOT NULL,
        RequestId nvarchar(128) NOT NULL,
        TaskId nvarchar(128) NULL,
        ResourceName nvarchar(256) NOT NULL,
        Decision nvarchar(24) NOT NULL,
        ReasonCode nvarchar(128) NOT NULL,
        DetailsJson nvarchar(max) NOT NULL CONSTRAINT DF_EvidenceEvents_DetailsJson DEFAULT N'{}',
        PreviousHash char(64) NOT NULL,
        EventHash char(64) NOT NULL,
        CreatedAt datetime2(0) NOT NULL CONSTRAINT DF_EvidenceEvents_CreatedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_EvidenceEvents_EventId UNIQUE (EventId),
        CONSTRAINT UQ_EvidenceEvents_EventHash UNIQUE (EventHash),
        CONSTRAINT CK_EvidenceEvents_DetailsJson CHECK (ISJSON(DetailsJson) = 1),
        CONSTRAINT CK_EvidenceEvents_Decision CHECK (Decision IN (N'allow', N'deny', N'error'))
    );
END;
GO

IF OBJECT_ID(N'lab.HierarchyEdges', N'U') IS NULL
BEGIN
    CREATE TABLE lab.HierarchyEdges (
        ParentNode nvarchar(64) NOT NULL,
        ChildNode nvarchar(64) NOT NULL,
        CONSTRAINT PK_HierarchyEdges PRIMARY KEY (ParentNode, ChildNode),
        CONSTRAINT CK_HierarchyEdges_NoSelfEdge CHECK (ParentNode <> ChildNode)
    );
END;
GO

IF OBJECT_ID(N'lab.QueryVersions', N'U') IS NULL
BEGIN
    CREATE TABLE lab.QueryVersions (
        QueryVersion nvarchar(64) NOT NULL CONSTRAINT PK_QueryVersions PRIMARY KEY,
        QueryKind nvarchar(16) NOT NULL,
        DefinitionHash char(64) NOT NULL,
        IsActive bit NOT NULL CONSTRAINT DF_QueryVersions_IsActive DEFAULT 0,
        CreatedAt datetime2(0) NOT NULL CONSTRAINT DF_QueryVersions_CreatedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT CK_QueryVersions_Kind CHECK (QueryKind IN (N'unsafe', N'safe'))
    );
    CREATE UNIQUE INDEX UX_QueryVersions_OneActive ON lab.QueryVersions(IsActive) WHERE IsActive = 1;
END;
GO

IF OBJECT_ID(N'lab.QueryRuns', N'U') IS NULL
BEGIN
    CREATE TABLE lab.QueryRuns (
        RunId nvarchar(128) NOT NULL CONSTRAINT PK_QueryRuns PRIMARY KEY,
        QueryVersion nvarchar(64) NOT NULL,
        LeaseId nvarchar(128) NOT NULL,
        OwnerInstance nvarchar(128) NOT NULL,
        State nvarchar(32) NOT NULL,
        StartedAt datetime2(0) NOT NULL CONSTRAINT DF_QueryRuns_StartedAt DEFAULT SYSUTCDATETIME(),
        WatchdogDeadline datetime2(0) NOT NULL,
        CancelRequestedAt datetime2(0) NULL,
        CompletedAt datetime2(0) NULL,
        FailureCode nvarchar(128) NULL,
        RowVersion rowversion NOT NULL,
        CONSTRAINT UQ_QueryRuns_LeaseId UNIQUE (LeaseId),
        CONSTRAINT FK_QueryRuns_QueryVersions FOREIGN KEY (QueryVersion) REFERENCES lab.QueryVersions(QueryVersion),
        CONSTRAINT CK_QueryRuns_State CHECK (State IN (N'starting', N'running', N'cancel_requested', N'cancelled', N'failed')),
        CONSTRAINT CK_QueryRuns_Deadline CHECK (WatchdogDeadline > StartedAt)
    );
END;
GO