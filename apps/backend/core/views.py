from datetime import datetime, timezone

from django.utils import timezone as dj_timezone
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

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
    RiskOfficerReview,
    StrategistAssessment,
    Trade,
)
from core.permissions import IsBridgeClient
from core.serializers import (
    BotCommandSerializer,
    BotSettingSerializer,
    BotStatusSerializer,
    BridgeCommandResultSerializer,
    CDSignalSerializer,
    ChatMessageSerializer,
    EventIngestSerializer,
    FileChangeAuditSerializer,
    LearningGitChangeSerializer,
    LearningInsightSerializer,
    LearningJournalEntrySerializer,
    LearningProposalSerializer,
    ManagerCritiqueSerializer,
    MMDailyMetricSerializer,
    MMInventorySerializer,
    MMQuoteSerializer,
    OrderEventSerializer,
    PerformanceSnapshotSerializer,
    PositionSerializer,
    RiskOfficerReviewSerializer,
    StrategistAssessmentSerializer,
    TradeSerializer,
)
from core.services import build_overview, emit_realtime_event



def _dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class HealthcheckAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok", "service": "control-plane"})


class OverviewAPIView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request):
        return Response(build_overview())


class TradeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Trade.objects.all()
    serializer_class = TradeSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class PositionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Position.objects.all()
    serializer_class = PositionSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        position = self.get_object()
        position_id = position.legacy_id or position.id
        command = BotCommand.objects.create(
            source=BotCommand.SOURCE_DASHBOARD,
            command="close_position",
            payload={"position_id": position_id},
            status=BotCommand.STATUS_PENDING,
            requested_by=str(request.user) if request.user.is_authenticated else "dashboard",
        )
        emit_realtime_event(
            "position.close_requested",
            {
                "position_id": position.id,
                "position_legacy_id": position.legacy_id,
                "command_id": command.id,
            },
        )
        return Response(
            {
                "ok": True,
                "command_id": command.id,
                "position_id": position.id,
                "position_legacy_id": position.legacy_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class BotSettingViewSet(viewsets.ModelViewSet):
    queryset = BotSetting.objects.all()
    serializer_class = BotSettingSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    lookup_field = "key"


class BotCommandViewSet(viewsets.ModelViewSet):
    queryset = BotCommand.objects.all()
    serializer_class = BotCommandSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get("status")
        source_filter = self.request.query_params.get("source")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if source_filter:
            qs = qs.filter(source=source_filter)
        return qs

    def create(self, request, *args, **kwargs):
        payload = request.data.copy()
        payload.setdefault("source", BotCommand.SOURCE_DASHBOARD)
        payload.setdefault("status", BotCommand.STATUS_PENDING)
        payload.setdefault("payload", {})

        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        command = serializer.instance
        emit_realtime_event(
            "command.created",
            {
                "command_id": command.id,
                "command": command.command,
                "source": command.source,
            },
        )
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=["get"])
    def pending(self, request):
        limit = min(int(request.query_params.get("limit", 50)), 200)
        qs = self.get_queryset().filter(status=BotCommand.STATUS_PENDING)[:limit]
        data = self.get_serializer(qs, many=True).data
        return Response(data)


class BotStatusViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = BotStatus.objects.all()
    serializer_class = BotStatusSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class PerformanceSnapshotViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = PerformanceSnapshot.objects.all()
    serializer_class = PerformanceSnapshotSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class OrderEventViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = OrderEvent.objects.all()
    serializer_class = OrderEventSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class LearningJournalEntryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = LearningJournalEntry.objects.all()
    serializer_class = LearningJournalEntrySerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class LearningInsightViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = LearningInsight.objects.all()
    serializer_class = LearningInsightSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get("status")
        severity_filter = self.request.query_params.get("severity")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if severity_filter:
            qs = qs.filter(severity=severity_filter)
        return qs


class LearningProposalViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = LearningProposal.objects.all()
    serializer_class = LearningProposalSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get("status")
        risk_filter = self.request.query_params.get("risk_level")
        proposal_type_filter = self.request.query_params.get("proposal_type")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if risk_filter:
            qs = qs.filter(risk_level=risk_filter)
        if proposal_type_filter:
            qs = qs.filter(proposal_type=proposal_type_filter)
        return qs

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def approve(self, request, pk=None):
        proposal = self.get_object()
        command = BotCommand.objects.create(
            source=BotCommand.SOURCE_DASHBOARD,
            command="approve_proposal",
            payload={"proposal_id": proposal.legacy_id or proposal.id},
            status=BotCommand.STATUS_PENDING,
            requested_by=str(request.user),
        )
        emit_realtime_event(
            "learning.proposal.approve_requested",
            {
                "proposal_id": proposal.id,
                "proposal_legacy_id": proposal.legacy_id,
                "command_id": command.id,
            },
        )
        return Response(
            {
                "ok": True,
                "command_id": command.id,
                "proposal_id": proposal.id,
                "proposal_legacy_id": proposal.legacy_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def reject(self, request, pk=None):
        proposal = self.get_object()
        command = BotCommand.objects.create(
            source=BotCommand.SOURCE_DASHBOARD,
            command="reject_proposal",
            payload={"proposal_id": proposal.legacy_id or proposal.id},
            status=BotCommand.STATUS_PENDING,
            requested_by=str(request.user),
        )
        emit_realtime_event(
            "learning.proposal.reject_requested",
            {
                "proposal_id": proposal.id,
                "proposal_legacy_id": proposal.legacy_id,
                "command_id": command.id,
            },
        )
        return Response(
            {
                "ok": True,
                "command_id": command.id,
                "proposal_id": proposal.id,
                "proposal_legacy_id": proposal.legacy_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ManagerCritiqueViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ManagerCritique.objects.all()
    serializer_class = ManagerCritiqueSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def approve(self, request, pk=None):
        critique = self.get_object()
        command = BotCommand.objects.create(
            source=BotCommand.SOURCE_DASHBOARD,
            command="approve_critique",
            payload={"critique_id": critique.legacy_id or critique.id},
            status=BotCommand.STATUS_PENDING,
            requested_by=str(request.user),
        )
        emit_realtime_event(
            "learning.critique.approve_requested",
            {
                "critique_id": critique.id,
                "critique_legacy_id": critique.legacy_id,
                "command_id": command.id,
            },
        )
        return Response(
            {
                "ok": True,
                "command_id": command.id,
                "critique_id": critique.id,
                "critique_legacy_id": critique.legacy_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def reject(self, request, pk=None):
        critique = self.get_object()
        command = BotCommand.objects.create(
            source=BotCommand.SOURCE_DASHBOARD,
            command="reject_critique",
            payload={"critique_id": critique.legacy_id or critique.id},
            status=BotCommand.STATUS_PENDING,
            requested_by=str(request.user),
        )
        emit_realtime_event(
            "learning.critique.reject_requested",
            {
                "critique_id": critique.id,
                "critique_legacy_id": critique.legacy_id,
                "command_id": command.id,
            },
        )
        return Response(
            {
                "ok": True,
                "command_id": command.id,
                "critique_id": critique.id,
                "critique_legacy_id": critique.legacy_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class LearningGitChangeViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = LearningGitChange.objects.all()
    serializer_class = LearningGitChangeSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        push_status_filter = self.request.query_params.get("push_status")
        if push_status_filter:
            qs = qs.filter(push_status=push_status_filter)
        return qs


class BridgeTradeUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        defaults = {
            "market_id": data.get("market_id", ""),
            "market_question": data.get("market_question", ""),
            "token_id": data.get("token_id"),
            "category": data.get("category", "other"),
            "side": data.get("side", "BUY"),
            "outcome": data.get("outcome", ""),
            "size_usdc": data.get("size_usdc") or 0,
            "price": data.get("price") or 0,
            "intended_shares": data.get("intended_shares"),
            "filled_shares": data.get("filled_shares") or 0,
            "avg_fill_price": data.get("avg_fill_price"),
            "edge": data.get("edge"),
            "edge_net": data.get("edge_net"),
            "confidence": data.get("confidence"),
            "reasoning": data.get("reasoning") or "",
            "status": data.get("status", "pending"),
            "order_id": data.get("order_id"),
            "strategy": data.get("strategy", "active"),
            "executed_at": _dt(data.get("executed_at")),
            "source_created_at": _dt(data.get("created_at")),
        }

        trade, _ = Trade.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "trade.upserted",
            {
                "legacy_id": legacy_id,
                "trade_id": trade.id,
                "status": trade.status,
                "order_id": trade.order_id,
            },
            persist=False,
        )
        return Response(TradeSerializer(trade).data)


class BridgePositionUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")

        defaults = {
            "legacy_id": legacy_id,
            "market_question": data.get("market_question", ""),
            "outcome": data.get("outcome", ""),
            "size": data.get("size") or 0,
            "avg_price": data.get("avg_price") or 0,
            "current_price": data.get("current_price"),
            "pnl_unrealized": data.get("pnl_unrealized") or 0,
            "status": data.get("status", "open"),
            "category": data.get("category", "other"),
            "strategy": data.get("strategy", "active"),
            "opened_at": _dt(data.get("opened_at")),
            "closed_at": _dt(data.get("closed_at")),
            "source_updated_at": _dt(data.get("updated_at") or data.get("closed_at")),
        }

        market_id = data.get("market_id", "")
        token_id = data.get("token_id", "")
        if not market_id or not token_id:
            return Response(
                {"error": "market_id and token_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        position, _ = Position.objects.update_or_create(
            market_id=market_id,
            token_id=token_id,
            defaults=defaults,
        )

        emit_realtime_event(
            "position.upserted",
            {
                "position_id": position.id,
                "legacy_id": legacy_id,
                "market_id": market_id,
                "token_id": token_id,
                "status": position.status,
            },
            persist=False,
        )
        return Response(PositionSerializer(position).data)


class BridgeBotStatusAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        payload = request.data.get("status") if isinstance(request.data, dict) else None
        if payload is None and isinstance(request.data, dict):
            payload = request.data
        if not isinstance(payload, dict):
            return Response({"error": "status payload must be an object"}, status=status.HTTP_400_BAD_REQUEST)

        for key, value in payload.items():
            BotStatus.objects.update_or_create(key=key, defaults={"value": value})

        emit_realtime_event(
            "bot.status",
            payload,
            persist=False,
        )
        return Response({"updated": list(payload.keys())})


class BridgeSettingUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        key = data.get("key")
        if not key:
            return Response({"error": "key is required"}, status=status.HTTP_400_BAD_REQUEST)

        metadata = {
            "label_fr": data.get("label_fr"),
            "description_fr": data.get("description_fr"),
            "category": data.get("category"),
            "value_type": data.get("value_type"),
            "choices": data.get("choices"),
            "min_value": data.get("min_value"),
            "max_value": data.get("max_value"),
        }
        metadata = {k: v for k, v in metadata.items() if v is not None}

        setting, _ = BotSetting.objects.update_or_create(
            key=key,
            defaults={
                "value": str(data.get("value", "")),
                "metadata": metadata,
            },
        )
        emit_realtime_event(
            "setting.upserted",
            {
                "key": setting.key,
                "value": setting.value,
            },
            persist=False,
        )
        return Response(BotSettingSerializer(setting).data)


class BridgePerformanceSnapshotAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        snapshot = PerformanceSnapshot.objects.create(
            snapshot_type=request.data.get("snapshot_type", "stats"),
            payload=request.data.get("payload", {}),
            source_created_at=_dt(request.data.get("created_at")),
        )
        emit_realtime_event(
            "performance.snapshot",
            {
                "snapshot_id": snapshot.id,
                "snapshot_type": snapshot.snapshot_type,
            },
        )
        return Response(PerformanceSnapshotSerializer(snapshot).data, status=status.HTTP_201_CREATED)


class BridgeOrderEventAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")

        trade = None
        trade_legacy_id = data.get("trade_id")
        if trade_legacy_id is not None:
            trade = Trade.objects.filter(legacy_id=trade_legacy_id).first()

        defaults = {
            "trade": trade,
            "trade_legacy_id": trade_legacy_id,
            "order_id": data.get("order_id"),
            "event_type": data.get("event_type", "unknown"),
            "status": data.get("status", ""),
            "size_matched": data.get("size_matched"),
            "new_fill": data.get("new_fill"),
            "avg_fill_price": data.get("avg_fill_price"),
            # SQLite events may send null note; model expects empty string instead.
            "note": data.get("note") or "",
            "payload": data.get("payload_json") or data.get("payload") or {},
            "source_created_at": _dt(data.get("created_at")),
        }

        if legacy_id is not None:
            order_event, _ = OrderEvent.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        else:
            order_event = OrderEvent.objects.create(**defaults)

        emit_realtime_event(
            "order.event",
            {
                "order_event_id": order_event.id,
                "legacy_id": legacy_id,
                "event_type": order_event.event_type,
                "status": order_event.status,
                "order_id": order_event.order_id,
            },
        )
        return Response(OrderEventSerializer(order_event).data, status=status.HTTP_201_CREATED)


class BridgeLearningJournalUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        defaults = {
            "cycle_number": data.get("cycle_number") or 0,
            "trades_proposed": data.get("trades_proposed") or 0,
            "trades_executed": data.get("trades_executed") or 0,
            "trades_skipped": data.get("trades_skipped") or 0,
            "skipped_markets": data.get("skipped_markets") or "",
            "retrospective_json": data.get("retrospective_json") or "",
            "price_snapshots": data.get("price_snapshots") or "",
            "outcome_accuracy": data.get("outcome_accuracy"),
            "source_created_at": _dt(data.get("created_at")),
        }
        entry, _ = LearningJournalEntry.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "learning.journal.upserted",
            {
                "entry_id": entry.id,
                "legacy_id": entry.legacy_id,
                "cycle_number": entry.cycle_number,
            },
            persist=False,
        )
        return Response(LearningJournalEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class BridgeLearningInsightUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        defaults = {
            "insight_type": data.get("insight_type", "unknown"),
            "description": data.get("description", ""),
            "evidence": data.get("evidence") or "",
            "proposed_action": data.get("proposed_action") or "",
            "severity": data.get("severity", "info"),
            "status": data.get("status", "active"),
            "source_created_at": _dt(data.get("created_at")),
        }
        insight, _ = LearningInsight.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "learning.insight.upserted",
            {
                "insight_id": insight.id,
                "legacy_id": insight.legacy_id,
                "insight_type": insight.insight_type,
            },
            persist=False,
        )
        return Response(LearningInsightSerializer(insight).data, status=status.HTTP_201_CREATED)


class BridgeLearningProposalUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        defaults = {
            "proposal_type": data.get("proposal_type", "config"),
            "target": data.get("target", "unknown"),
            "current_value": data.get("current_value") or "",
            "proposed_value": data.get("proposed_value") or "",
            "rationale": data.get("rationale") or "",
            "risk_level": data.get("risk_level", "moderate"),
            "status": data.get("status", LearningProposal.STATUS_PENDING),
            "applied_at": _dt(data.get("applied_at")),
            "source_created_at": _dt(data.get("created_at")),
        }
        proposal, _ = LearningProposal.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "learning.proposal.upserted",
            {
                "proposal_id": proposal.id,
                "legacy_id": proposal.legacy_id,
                "status": proposal.status,
            },
            persist=False,
        )
        return Response(LearningProposalSerializer(proposal).data, status=status.HTTP_201_CREATED)


class BridgeManagerCritiqueUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        defaults = {
            "cycle_number": data.get("cycle_number") or 0,
            "critique_json": data.get("critique_json") or "",
            "summary": data.get("summary") or "",
            "trading_quality_score": data.get("trading_quality_score"),
            "risk_management_score": data.get("risk_management_score"),
            "strategy_effectiveness_score": data.get("strategy_effectiveness_score"),
            "improvement_areas": data.get("improvement_areas") or "",
            "code_changes_suggested": data.get("code_changes_suggested") or "",
            "status": data.get("status", "pending"),
            "developer_result": data.get("developer_result") or "",
            "branch_name": data.get("branch_name") or "",
            "commit_hash": data.get("commit_hash") or "",
            "deploy_status": data.get("deploy_status") or "",
            "user_feedback": data.get("user_feedback") or "",
            "source_created_at": _dt(data.get("created_at")),
            "reviewed_at": _dt(data.get("reviewed_at")),
            "deployed_at": _dt(data.get("deployed_at")),
        }
        critique, _ = ManagerCritique.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "learning.critique.upserted",
            {
                "critique_id": critique.id,
                "legacy_id": critique.legacy_id,
                "status": critique.status,
                "cycle_number": critique.cycle_number,
            },
            persist=False,
        )
        return Response(ManagerCritiqueSerializer(critique).data, status=status.HTTP_201_CREATED)


class BridgeLearningGitChangeUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        files_changed = data.get("files_changed")
        if not isinstance(files_changed, list):
            files_changed = []
        result = data.get("result")
        if not isinstance(result, dict):
            result = {}

        defaults = {
            "proposal_legacy_id": data.get("proposal_id"),
            "branch_name": data.get("branch_name", ""),
            "commit_hash": data.get("commit_hash") or "",
            "remote_name": data.get("remote_name", "origin"),
            "push_status": data.get("push_status", "pending"),
            "justification": data.get("justification") or "",
            "files_changed": files_changed,
            "result": result,
            "source_created_at": _dt(data.get("created_at")),
        }
        change, _ = LearningGitChange.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "learning.git_change.upserted",
            {
                "change_id": change.id,
                "legacy_id": change.legacy_id,
                "branch_name": change.branch_name,
                "push_status": change.push_status,
            },
            persist=False,
        )
        return Response(LearningGitChangeSerializer(change).data, status=status.HTTP_201_CREATED)


class BridgePendingCommandsAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 50)), 200)
        pending = list(
            BotCommand.objects.filter(
                status=BotCommand.STATUS_PENDING,
                dispatched_at__isnull=True,
                legacy_id__isnull=True,
            )
            .order_by("created_at")[:limit]
        )

        now = dj_timezone.now()
        for command in pending:
            command.dispatched_at = now
            command.save(update_fields=["dispatched_at", "updated_at"])

        serializer = BotCommandSerializer(pending, many=True)
        return Response(serializer.data)


class BridgeCommandUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        payload = data.get("payload")
        if isinstance(payload, str):
            try:
                import json

                payload = json.loads(payload)
            except Exception:
                payload = {"raw": payload}

        result = data.get("result")
        if isinstance(result, str):
            try:
                import json

                result = json.loads(result)
            except Exception:
                result = {"raw": result}

        defaults = {
            "source": data.get("source", BotCommand.SOURCE_DASHBOARD),
            "command": data.get("command", ""),
            "payload": payload or {},
            "status": data.get("status", BotCommand.STATUS_PENDING),
            "result": result,
            "requested_by": data.get("requested_by", ""),
            "source_created_at": _dt(data.get("created_at")),
            "executed_at": _dt(data.get("executed_at")),
        }

        command, _ = BotCommand.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "command.upserted",
            {
                "command_id": command.id,
                "legacy_id": command.legacy_id,
                "status": command.status,
                "command": command.command,
            },
            persist=False,
        )
        return Response(BotCommandSerializer(command).data)


class BridgeCommandResultAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        serializer = BridgeCommandResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        command = None
        if validated.get("command_id"):
            command = BotCommand.objects.filter(id=validated["command_id"]).first()
        elif validated.get("legacy_id"):
            command = BotCommand.objects.filter(legacy_id=validated["legacy_id"]).first()

        if command is None:
            return Response({"error": "command not found"}, status=status.HTTP_404_NOT_FOUND)

        command.status = validated["status"]
        command.result = validated.get("result")
        command.executed_at = dj_timezone.now()
        command.save(update_fields=["status", "result", "executed_at", "updated_at"])

        emit_realtime_event(
            "command.updated",
            {
                "command_id": command.id,
                "legacy_id": command.legacy_id,
                "status": command.status,
            },
        )
        return Response(BotCommandSerializer(command).data)


class EventIngestAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        serializer = EventIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = emit_realtime_event(
            serializer.validated_data["event_type"],
            serializer.validated_data.get("payload", {}),
        )
        return Response(event, status=status.HTTP_201_CREATED)


class RiskOfficerReviewViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RiskOfficerReview.objects.all()
    serializer_class = RiskOfficerReviewSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class StrategistAssessmentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = StrategistAssessment.objects.all()
    serializer_class = StrategistAssessmentSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class ChatMessageViewSet(viewsets.ModelViewSet):
    queryset = ChatMessage.objects.all()
    serializer_class = ChatMessageSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=False, methods=["post"])
    def send(self, request):
        """Send a chat message — creates a bot command for the worker."""
        message = request.data.get("message", "")
        if not message:
            return Response({"error": "message required"}, status=400)
        command = BotCommand.objects.create(
            command="chat_message",
            payload={"message": message, "source": "dashboard"},
            source=BotCommand.SOURCE_DASHBOARD,
        )
        emit_realtime_event("command.created", {"id": command.pk})
        return Response({"status": "sent", "command_id": command.pk})

    @action(detail=False, methods=["get"])
    def history(self, request):
        """Get chat history, optionally filtered by since_id."""
        since = request.query_params.get("since")
        qs = ChatMessage.objects.all().order_by("created_at")
        if since:
            qs = qs.filter(pk__gt=int(since))
        serializer = self.get_serializer(qs[:100], many=True)
        return Response(serializer.data)


class FileChangeAuditViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FileChangeAudit.objects.all()
    serializer_class = FileChangeAuditSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class BridgeRiskReviewsUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        defaults = {
            "cycle_number": data.get("cycle_number"),
            "review_json": data.get("review_json") or "",
            "portfolio_risk_summary": data.get("portfolio_risk_summary") or "",
            "trades_reviewed": data.get("trades_reviewed") or 0,
            "trades_flagged": data.get("trades_flagged") or 0,
            "trades_rejected": data.get("trades_rejected") or 0,
            "parameter_recommendations": data.get("parameter_recommendations") or [],
            "created_at": _dt(data.get("created_at")),
        }
        review, _ = RiskOfficerReview.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "risk.review.upserted",
            {
                "review_id": review.id,
                "legacy_id": review.legacy_id,
                "cycle_number": review.cycle_number,
            },
            persist=False,
        )
        return Response(RiskOfficerReviewSerializer(review).data, status=status.HTTP_201_CREATED)


class BridgeStrategistUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        defaults = {
            "assessment_json": data.get("assessment_json") or "",
            "summary": data.get("summary") or "",
            "market_regime": data.get("market_regime", "normal"),
            "regime_confidence": data.get("regime_confidence", 0.5),
            "allocation_score": data.get("allocation_score"),
            "diversification_score": data.get("diversification_score"),
            "category_allocation": data.get("category_allocation") or {},
            "recommendations": data.get("recommendations") or [],
            "strategic_insights": data.get("strategic_insights") or [],
            "created_at": _dt(data.get("created_at")),
        }
        assessment, _ = StrategistAssessment.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "strategist.assessment.upserted",
            {
                "assessment_id": assessment.id,
                "legacy_id": assessment.legacy_id,
                "market_regime": assessment.market_regime,
            },
            persist=False,
        )
        return Response(StrategistAssessmentSerializer(assessment).data, status=status.HTTP_201_CREATED)


class BridgeChatUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        defaults = {
            "source": data.get("source", "telegram"),
            "role": data.get("role", "user"),
            "agent_name": data.get("agent_name", "general"),
            "message": data.get("message") or "",
            "action_taken": data.get("action_taken"),
            "created_at": _dt(data.get("created_at")),
        }
        chat, _ = ChatMessage.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "chat.message.upserted",
            {
                "chat_id": chat.id,
                "legacy_id": chat.legacy_id,
                "role": chat.role,
                "agent_name": chat.agent_name,
            },
            persist=False,
        )
        return Response(ChatMessageSerializer(chat).data, status=status.HTTP_201_CREATED)


class BridgeAuditUpsertAPIView(APIView):
    permission_classes = [IsBridgeClient]

    def post(self, request):
        data = request.data
        legacy_id = data.get("id") or data.get("legacy_id")
        if legacy_id is None:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        defaults = {
            "file_path": data.get("file_path", ""),
            "change_type": data.get("change_type", ""),
            "tier": data.get("tier") or 0,
            "agent_name": data.get("agent_name", ""),
            "reason": data.get("reason"),
            "diff_summary": data.get("diff_summary"),
            "backup_path": data.get("backup_path"),
            "status": data.get("status", "pending"),
            "created_at": _dt(data.get("created_at")),
        }
        audit, _ = FileChangeAudit.objects.update_or_create(legacy_id=legacy_id, defaults=defaults)
        emit_realtime_event(
            "audit.upserted",
            {
                "audit_id": audit.id,
                "legacy_id": audit.legacy_id,
                "file_path": audit.file_path,
                "status": audit.status,
            },
            persist=False,
        )
        return Response(FileChangeAuditSerializer(audit).data, status=status.HTTP_201_CREATED)


class MMQuoteViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MMQuote.objects.all()
    serializer_class = MMQuoteSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs[:100]


class MMInventoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MMInventory.objects.filter(net_position__gt=0.001) | MMInventory.objects.filter(net_position__lt=-0.001)
    serializer_class = MMInventorySerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class MMDailyMetricViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MMDailyMetric.objects.all()[:30]
    serializer_class = MMDailyMetricSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class CDSignalViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CDSignal.objects.all()[:50]
    serializer_class = CDSignalSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
