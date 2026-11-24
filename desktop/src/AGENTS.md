# Windows desktop window, IPC, workbench panels and PDF viewer

| File | Responsibility |
|---|---|
| [app.tsx](app.tsx) | Routes the renderer between task, Voyager, knowledge and material workspaces. |
| [DeliveryPanel.tsx](DeliveryPanel.tsx) | Shows delivery attempts and guides recovery when the outcome is unknown. |
| [EditorFeedbackPreview.tsx](EditorFeedbackPreview.tsx) | Report feedback shown with its original task, version and paragraph at the owning workflow. |
| [EditorPreferences.tsx](EditorPreferences.tsx) | Writing style selection, saved preferences and feedback candidates. |
| [EditorReports.tsx](EditorReports.tsx) | Selects report sources and reads saved reports, citations and pinned snapshots. |
| [index.html](index.html) | Desktop renderer document. |
| [KeeperGroupUndoPanel.tsx](KeeperGroupUndoPanel.tsx) | Previews and confirms undo of a previously applied Keeper group. |
| [KeeperProposalPanel.tsx](KeeperProposalPanel.tsx) | Displays a proposed change, its revision and the available decisions. |
| [KnowledgeCardPanel.tsx](KnowledgeCardPanel.tsx) | Creates and edits versioned knowledge cards. |
| [KnowledgeHistoryPanel.tsx](KnowledgeHistoryPanel.tsx) | Displays knowledge history, differences and undo controls. |
| [KnowledgePanel.tsx](KnowledgePanel.tsx) | Creates and changes Frontier scope and status. |
| [KnowledgeWorkbench.tsx](KnowledgeWorkbench.tsx) | Searches and filters local knowledge and opens the selected record. |
| [main.ts](main.ts) | Owns native windows, tray behavior, IPC and shutdown. |
| [MaterialsPanel.tsx](MaterialsPanel.tsx) | Lists materials and opens their snapshots and archive actions. |
| [pdfProtocol.ts](pdfProtocol.ts) | Resolves approved PDF resources for the Electron reader. |
| [PdfReader.tsx](PdfReader.tsx) | Displays the selected PDF snapshot and evidence page. |
| [PendingMaterialActions.tsx](PendingMaterialActions.tsx) | Handles pending full text, abandonment and abstract authorization. |
| [preload.ts](preload.ts) | Exposes the approved IPC bridge to the renderer. |
| [SchedulePanel.tsx](SchedulePanel.tsx) | Edits retrieval schedules and shows their running state. |
| [style.css](style.css) | Desktop layout and control styles. |
| [SubscriptionPanel.tsx](SubscriptionPanel.tsx) | Configures subscription sources and displays incremental discovery status. |
| [ThemePanel.tsx](ThemePanel.tsx) | Edits Theme judgments, conditions and supporting or opposing evidence. |
| [VoyagerPanel.tsx](VoyagerPanel.tsx) | Starts scoped retrieval and presents candidates and coverage. |
| [VoyagerSourceSettings.tsx](VoyagerSourceSettings.tsx) | Configures retrieval sources and displays credential availability. |
| [window-caption.css](window-caption.css) | Styles the in-window title bar and native window controls. |

## Verification

Start with [main.ts](main.ts) for window creation and [app.tsx](app.tsx) for navigation to the individual panels.
