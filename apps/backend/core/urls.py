from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.views import (
    BotCommandViewSet,
    BotSettingViewSet,
    BotStatusViewSet,
    BridgeAuditUpsertAPIView,
    BridgeBotStatusAPIView,
    BridgeChatUpsertAPIView,
    BridgeCommandUpsertAPIView,
    BridgeCommandResultAPIView,
    BridgeLearningGitChangeUpsertAPIView,
    BridgeLearningInsightUpsertAPIView,
    BridgeLearningJournalUpsertAPIView,
    BridgeLearningProposalUpsertAPIView,
    BridgeManagerCritiqueUpsertAPIView,
    BridgeOrderEventAPIView,
    BridgePendingCommandsAPIView,
    BridgePerformanceSnapshotAPIView,
    BridgeRiskReviewsUpsertAPIView,
    BridgeSettingUpsertAPIView,
    BridgePositionUpsertAPIView,
    BridgeStrategistUpsertAPIView,
    BridgeTradeUpsertAPIView,
    CDSignalViewSet,
    ChatMessageViewSet,
    EventIngestAPIView,
    FileChangeAuditViewSet,
    HealthcheckAPIView,
    LearningGitChangeViewSet,
    LearningInsightViewSet,
    LearningJournalEntryViewSet,
    LearningProposalViewSet,
    ManagerCritiqueViewSet,
    MMDailyMetricViewSet,
    MMInventoryViewSet,
    MMQuoteViewSet,
    OrderEventViewSet,
    OverviewAPIView,
    PerformanceSnapshotViewSet,
    PositionViewSet,
    RiskOfficerReviewViewSet,
    StrategistAssessmentViewSet,
    TradeViewSet,
)

router = DefaultRouter()
router.register("trades", TradeViewSet, basename="trade")
router.register("positions", PositionViewSet, basename="position")
router.register("settings", BotSettingViewSet, basename="setting")
router.register("commands", BotCommandViewSet, basename="command")
router.register("status", BotStatusViewSet, basename="status")
router.register("performance", PerformanceSnapshotViewSet, basename="performance")
router.register("order-events", OrderEventViewSet, basename="order-event")
router.register("learning/journal", LearningJournalEntryViewSet, basename="learning-journal")
router.register("learning/insights", LearningInsightViewSet, basename="learning-insight")
router.register("learning/proposals", LearningProposalViewSet, basename="learning-proposal")
router.register("learning/critiques", ManagerCritiqueViewSet, basename="learning-critique")
router.register("learning/git-changes", LearningGitChangeViewSet, basename="learning-git-change")
router.register("risk-reviews", RiskOfficerReviewViewSet, basename="risk-reviews")
router.register("strategist", StrategistAssessmentViewSet, basename="strategist")
router.register("chat", ChatMessageViewSet, basename="chat")
router.register("audit", FileChangeAuditViewSet, basename="audit")
router.register("mm-quotes", MMQuoteViewSet, basename="mm-quotes")
router.register("mm-inventory", MMInventoryViewSet, basename="mm-inventory")
router.register("mm-metrics", MMDailyMetricViewSet, basename="mm-metrics")
router.register("cd-signals", CDSignalViewSet, basename="cd-signals")

urlpatterns = [
    path("health/", HealthcheckAPIView.as_view()),
    path("overview/", OverviewAPIView.as_view()),
    path("events/ingest/", EventIngestAPIView.as_view()),
    path("bridge/trades/upsert/", BridgeTradeUpsertAPIView.as_view()),
    path("bridge/positions/upsert/", BridgePositionUpsertAPIView.as_view()),
    path("bridge/status/upsert/", BridgeBotStatusAPIView.as_view()),
    path("bridge/settings/upsert/", BridgeSettingUpsertAPIView.as_view()),
    path("bridge/performance/upsert/", BridgePerformanceSnapshotAPIView.as_view()),
    path("bridge/order-events/upsert/", BridgeOrderEventAPIView.as_view()),
    path("bridge/learning/journal/upsert/", BridgeLearningJournalUpsertAPIView.as_view()),
    path("bridge/learning/insights/upsert/", BridgeLearningInsightUpsertAPIView.as_view()),
    path("bridge/learning/proposals/upsert/", BridgeLearningProposalUpsertAPIView.as_view()),
    path("bridge/learning/critiques/upsert/", BridgeManagerCritiqueUpsertAPIView.as_view()),
    path("bridge/learning/git-changes/upsert/", BridgeLearningGitChangeUpsertAPIView.as_view()),
    path("bridge/risk-reviews/upsert/", BridgeRiskReviewsUpsertAPIView.as_view()),
    path("bridge/strategist/upsert/", BridgeStrategistUpsertAPIView.as_view()),
    path("bridge/chat/upsert/", BridgeChatUpsertAPIView.as_view()),
    path("bridge/audit/upsert/", BridgeAuditUpsertAPIView.as_view()),
    path("bridge/commands/upsert/", BridgeCommandUpsertAPIView.as_view()),
    path("bridge/commands/pending/", BridgePendingCommandsAPIView.as_view()),
    path("bridge/commands/result/", BridgeCommandResultAPIView.as_view()),
    path("", include(router.urls)),
]
