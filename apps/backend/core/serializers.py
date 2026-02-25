from rest_framework import serializers

from core.models import (
    BotCommand,
    BotSetting,
    BotStatus,
    CDSignal,
    ChatMessage,
    FileChangeAudit,
    LearningGitChange,
    LearningInsight,
    LearningJournalEntry,
    LearningProposal,
    ManagerCritique,
    MMDailyMetric,
    MMInventory,
    MMQuote,
    OrderEvent,
    PerformanceSnapshot,
    Position,
    RealtimeEvent,
    RiskOfficerReview,
    StrategistAssessment,
    Trade,
)


class TradeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Trade
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")


class PositionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Position
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")


class BotSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = BotSetting
        fields = "__all__"
        read_only_fields = ("updated_at",)


class BotCommandSerializer(serializers.ModelSerializer):
    class Meta:
        model = BotCommand
        fields = "__all__"
        read_only_fields = (
            "id",
            "legacy_id",
            "dispatched_at",
            "executed_at",
            "source_created_at",
            "created_at",
            "updated_at",
        )


class BotStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = BotStatus
        fields = "__all__"
        read_only_fields = ("updated_at",)


class OrderEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderEvent
        fields = "__all__"
        read_only_fields = ("id", "created_at")


class PerformanceSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerformanceSnapshot
        fields = "__all__"
        read_only_fields = ("id", "created_at")


class RealtimeEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RealtimeEvent
        fields = "__all__"
        read_only_fields = ("id", "emitted_at")


class LearningJournalEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningJournalEntry
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")


class LearningInsightSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningInsight
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")


class LearningProposalSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningProposal
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")


class ManagerCritiqueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagerCritique
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")


class LearningGitChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningGitChange
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")


class BridgeCommandResultSerializer(serializers.Serializer):
    command_id = serializers.IntegerField(required=False)
    legacy_id = serializers.IntegerField(required=False)
    status = serializers.ChoiceField(choices=[BotCommand.STATUS_EXECUTED, BotCommand.STATUS_FAILED])
    result = serializers.JSONField(required=False)

    def validate(self, attrs):
        if not attrs.get("command_id") and not attrs.get("legacy_id"):
            raise serializers.ValidationError("Either command_id or legacy_id is required.")
        return attrs


class EventIngestSerializer(serializers.Serializer):
    event_type = serializers.CharField(max_length=64)
    payload = serializers.JSONField(required=False)


class OverviewSerializer(serializers.Serializer):
    available_usdc = serializers.FloatField()
    onchain_balance = serializers.FloatField(allow_null=True)
    positions_count = serializers.IntegerField()
    daily_pnl = serializers.FloatField()
    daily_traded = serializers.FloatField()
    total_invested = serializers.FloatField()
    portfolio_value = serializers.FloatField()
    total_pnl = serializers.FloatField()
    roi_percent = serializers.FloatField()
    hit_rate = serializers.FloatField()
    total_trades = serializers.IntegerField()
    bot_status = serializers.CharField()
    is_paper = serializers.BooleanField()
    strategy = serializers.CharField()
    cycle_number = serializers.IntegerField()
    cycle_interval_minutes = serializers.IntegerField()


class RiskOfficerReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskOfficerReview
        fields = "__all__"


class StrategistAssessmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = StrategistAssessment
        fields = "__all__"


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = "__all__"


class FileChangeAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileChangeAudit
        fields = "__all__"


class MMQuoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MMQuote
        fields = '__all__'


class MMInventorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MMInventory
        fields = '__all__'


class MMDailyMetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = MMDailyMetric
        fields = '__all__'


class CDSignalSerializer(serializers.ModelSerializer):
    class Meta:
        model = CDSignal
        fields = '__all__'
