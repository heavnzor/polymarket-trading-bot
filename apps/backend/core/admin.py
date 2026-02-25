from django.contrib import admin

from core.models import (
    BotCommand,
    BotSetting,
    BotStatus,
    LearningGitChange,
    LearningInsight,
    LearningJournalEntry,
    LearningProposal,
    OrderEvent,
    PerformanceSnapshot,
    Position,
    RealtimeEvent,
    Trade,
)


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = ("id", "legacy_id", "market_id", "side", "status", "size_usdc", "created_at")
    search_fields = ("market_id", "order_id", "token_id")
    list_filter = ("status", "strategy", "side", "category")


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("id", "legacy_id", "market_id", "outcome", "status", "size", "avg_price")
    search_fields = ("market_id", "token_id")
    list_filter = ("status", "strategy", "category")


@admin.register(BotCommand)
class BotCommandAdmin(admin.ModelAdmin):
    list_display = ("id", "legacy_id", "source", "command", "status", "created_at", "executed_at")
    search_fields = ("command", "requested_by")
    list_filter = ("status", "source")


@admin.register(BotSetting)
class BotSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "updated_at")
    search_fields = ("key",)


@admin.register(BotStatus)
class BotStatusAdmin(admin.ModelAdmin):
    list_display = ("key", "updated_at")
    search_fields = ("key",)


@admin.register(OrderEvent)
class OrderEventAdmin(admin.ModelAdmin):
    list_display = ("id", "legacy_id", "event_type", "status", "order_id", "created_at")
    search_fields = ("order_id", "event_type")
    list_filter = ("event_type", "status")


@admin.register(PerformanceSnapshot)
class PerformanceSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "snapshot_type", "source_created_at", "created_at")
    list_filter = ("snapshot_type",)


@admin.register(RealtimeEvent)
class RealtimeEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "emitted_at")
    list_filter = ("event_type",)


@admin.register(LearningJournalEntry)
class LearningJournalEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "legacy_id", "cycle_number", "trades_proposed", "trades_executed", "created_at")
    list_filter = ("cycle_number",)


@admin.register(LearningInsight)
class LearningInsightAdmin(admin.ModelAdmin):
    list_display = ("id", "legacy_id", "insight_type", "severity", "status", "created_at")
    list_filter = ("insight_type", "severity", "status")


@admin.register(LearningProposal)
class LearningProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "legacy_id", "proposal_type", "target", "risk_level", "status", "created_at")
    list_filter = ("proposal_type", "risk_level", "status")
    search_fields = ("target", "rationale")


@admin.register(LearningGitChange)
class LearningGitChangeAdmin(admin.ModelAdmin):
    list_display = ("id", "legacy_id", "branch_name", "commit_hash", "push_status", "created_at")
    list_filter = ("push_status", "remote_name")
    search_fields = ("branch_name", "commit_hash", "justification")
