from django.db import models


class Trade(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True, db_index=True)
    market_id = models.CharField(max_length=255, db_index=True)
    market_question = models.TextField(blank=True)
    token_id = models.CharField(max_length=255, null=True, blank=True)
    category = models.CharField(max_length=64, default="other")
    side = models.CharField(max_length=16)
    outcome = models.CharField(max_length=64)
    size_usdc = models.DecimalField(max_digits=18, decimal_places=6)
    price = models.DecimalField(max_digits=18, decimal_places=6)
    intended_shares = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    filled_shares = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    avg_fill_price = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    edge = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    edge_net = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    confidence = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    reasoning = models.TextField(blank=True)
    status = models.CharField(max_length=32, default="pending", db_index=True)
    order_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    strategy = models.CharField(max_length=64, default="active")
    executed_at = models.DateTimeField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Trade<{self.market_id}:{self.side}:{self.status}>"


class Position(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True, db_index=True)
    market_id = models.CharField(max_length=255, db_index=True)
    token_id = models.CharField(max_length=255)
    market_question = models.TextField(blank=True)
    outcome = models.CharField(max_length=64)
    size = models.DecimalField(max_digits=18, decimal_places=6)
    avg_price = models.DecimalField(max_digits=18, decimal_places=6)
    current_price = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    pnl_unrealized = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    status = models.CharField(max_length=32, default="open", db_index=True)
    category = models.CharField(max_length=64, default="other")
    strategy = models.CharField(max_length=64, default="active")
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["market_id", "token_id"], name="uniq_position_market_token"),
        ]

    def __str__(self) -> str:
        return f"Position<{self.market_id}:{self.outcome}:{self.status}>"


class PerformanceSnapshot(models.Model):
    snapshot_type = models.CharField(max_length=64, default="stats", db_index=True)
    payload = models.JSONField(default=dict)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BotSetting(models.Model):
    key = models.CharField(max_length=128, unique=True)
    value = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]


class BotCommand(models.Model):
    STATUS_PENDING = "pending"
    STATUS_EXECUTED = "executed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_EXECUTED, "Executed"),
        (STATUS_FAILED, "Failed"),
    ]

    SOURCE_DASHBOARD = "dashboard"
    SOURCE_TELEGRAM = "telegram"
    SOURCE_API = "api"
    SOURCE_CHOICES = [
        (SOURCE_DASHBOARD, "Dashboard"),
        (SOURCE_TELEGRAM, "Telegram"),
        (SOURCE_API, "API"),
    ]

    legacy_id = models.IntegerField(unique=True, null=True, blank=True, db_index=True)
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default=SOURCE_DASHBOARD)
    command = models.CharField(max_length=128)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    result = models.JSONField(null=True, blank=True)
    requested_by = models.CharField(max_length=128, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class BotStatus(models.Model):
    key = models.CharField(max_length=128, unique=True)
    value = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]


class OrderEvent(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True, db_index=True)
    trade = models.ForeignKey(Trade, on_delete=models.SET_NULL, null=True, blank=True, related_name="order_events")
    trade_legacy_id = models.IntegerField(null=True, blank=True, db_index=True)
    order_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    event_type = models.CharField(max_length=64)
    status = models.CharField(max_length=64, blank=True)
    size_matched = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    new_fill = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    avg_fill_price = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    note = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class RealtimeEvent(models.Model):
    event_type = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    emitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-emitted_at"]


class LearningJournalEntry(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True, db_index=True)
    cycle_number = models.IntegerField(db_index=True)
    trades_proposed = models.IntegerField(default=0)
    trades_executed = models.IntegerField(default=0)
    trades_skipped = models.IntegerField(default=0)
    skipped_markets = models.TextField(blank=True)
    retrospective_json = models.TextField(blank=True)
    price_snapshots = models.TextField(blank=True)
    outcome_accuracy = models.FloatField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class LearningInsight(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True, db_index=True)
    insight_type = models.CharField(max_length=64, db_index=True)
    description = models.TextField()
    evidence = models.TextField(blank=True)
    proposed_action = models.TextField(blank=True)
    severity = models.CharField(max_length=32, default="info")
    status = models.CharField(max_length=32, default="active", db_index=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class LearningProposal(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_APPLIED = "applied"
    STATUS_FAILED = "failed"

    legacy_id = models.IntegerField(unique=True, null=True, blank=True, db_index=True)
    proposal_type = models.CharField(max_length=64, db_index=True)
    target = models.CharField(max_length=255)
    current_value = models.TextField(blank=True)
    proposed_value = models.TextField()
    rationale = models.TextField()
    risk_level = models.CharField(max_length=32, default="moderate")
    status = models.CharField(max_length=32, default=STATUS_PENDING, db_index=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class ManagerCritique(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True, db_index=True)
    cycle_number = models.IntegerField(db_index=True)
    critique_json = models.TextField(blank=True)
    summary = models.TextField(blank=True)
    trading_quality_score = models.IntegerField(null=True, blank=True)
    risk_management_score = models.IntegerField(null=True, blank=True)
    strategy_effectiveness_score = models.IntegerField(null=True, blank=True)
    improvement_areas = models.TextField(blank=True)
    code_changes_suggested = models.TextField(blank=True)
    status = models.CharField(max_length=32, default="pending", db_index=True)
    developer_result = models.TextField(blank=True)
    branch_name = models.CharField(max_length=255, blank=True)
    commit_hash = models.CharField(max_length=255, blank=True)
    deploy_status = models.CharField(max_length=32, blank=True)
    user_feedback = models.TextField(blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    deployed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class LearningGitChange(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True, db_index=True)
    proposal_legacy_id = models.IntegerField(null=True, blank=True, db_index=True)
    branch_name = models.CharField(max_length=255, db_index=True)
    commit_hash = models.CharField(max_length=255, blank=True)
    remote_name = models.CharField(max_length=64, default="origin")
    push_status = models.CharField(max_length=32, default="pending", db_index=True)
    justification = models.TextField(blank=True)
    files_changed = models.JSONField(default=list, blank=True)
    result = models.JSONField(default=dict, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class RiskOfficerReview(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True)
    cycle_number = models.IntegerField(null=True, blank=True)
    review_json = models.TextField(default="")
    portfolio_risk_summary = models.TextField(default="")
    trades_reviewed = models.IntegerField(default=0)
    trades_flagged = models.IntegerField(default=0)
    trades_rejected = models.IntegerField(default=0)
    parameter_recommendations = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RiskReview #{self.pk} cycle={self.cycle_number}"


class StrategistAssessment(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True)
    assessment_json = models.TextField(default="")
    summary = models.TextField(default="")
    market_regime = models.CharField(max_length=20, default="normal")
    regime_confidence = models.FloatField(default=0.5)
    allocation_score = models.IntegerField(null=True, blank=True)
    diversification_score = models.IntegerField(null=True, blank=True)
    category_allocation = models.JSONField(default=dict, blank=True)
    recommendations = models.JSONField(default=list, blank=True)
    strategic_insights = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Assessment #{self.pk} regime={self.market_regime}"


class ChatMessage(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True)
    source = models.CharField(max_length=20)
    role = models.CharField(max_length=10)
    agent_name = models.CharField(max_length=50, default="general", blank=True)
    message = models.TextField()
    action_taken = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Chat #{self.pk} [{self.role}:{self.agent_name}]"


class FileChangeAudit(models.Model):
    legacy_id = models.IntegerField(unique=True, null=True, blank=True)
    file_path = models.CharField(max_length=500)
    change_type = models.CharField(max_length=20)
    tier = models.IntegerField()
    agent_name = models.CharField(max_length=50)
    reason = models.TextField(null=True, blank=True)
    diff_summary = models.TextField(null=True, blank=True)
    backup_path = models.CharField(max_length=500, null=True, blank=True)
    status = models.CharField(max_length=20, default="pending")
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Audit #{self.pk} {self.file_path} [{self.status}]"


class MMQuote(models.Model):
    market_id = models.CharField(max_length=255)
    token_id = models.CharField(max_length=255)
    bid_order_id = models.CharField(max_length=255, blank=True, null=True)
    ask_order_id = models.CharField(max_length=255, blank=True, null=True)
    bid_price = models.FloatField()
    ask_price = models.FloatField()
    mid_price = models.FloatField(null=True, blank=True)
    size = models.FloatField()
    status = models.CharField(max_length=20, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"MM Quote {self.market_id[:16]} B:{self.bid_price}/A:{self.ask_price}"


class MMInventory(models.Model):
    market_id = models.CharField(max_length=255)
    token_id = models.CharField(max_length=255)
    net_position = models.FloatField(default=0)
    avg_entry_price = models.FloatField(default=0)
    unrealized_pnl = models.FloatField(default=0)
    realized_pnl = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        unique_together = ['market_id', 'token_id']

    def __str__(self):
        return f"Inventory {self.market_id[:16]} pos:{self.net_position}"


class MMDailyMetric(models.Model):
    date = models.DateField(unique=True)
    markets_quoted = models.IntegerField(default=0)
    quotes_placed = models.IntegerField(default=0)
    fills_count = models.IntegerField(default=0)
    round_trips = models.IntegerField(default=0)
    spread_capture_rate = models.FloatField(default=0)
    fill_quality_avg = models.FloatField(default=0)
    adverse_selection_avg = models.FloatField(default=0)
    pnl_gross = models.FloatField(default=0)
    pnl_net = models.FloatField(default=0)
    max_inventory = models.FloatField(default=0)
    inventory_turns = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"MM Metrics {self.date} PnL:{self.pnl_net}"


class CDSignal(models.Model):
    market_id = models.CharField(max_length=255)
    token_id = models.CharField(max_length=255, blank=True, null=True)
    coin = models.CharField(max_length=10)
    strike = models.FloatField()
    expiry_days = models.FloatField()
    spot_price = models.FloatField()
    vol_ewma = models.FloatField()
    p_model = models.FloatField()
    p_market = models.FloatField()
    edge_pts = models.FloatField()
    confirmation_count = models.IntegerField(default=1)
    action = models.CharField(max_length=20, default='none')
    size_usdc = models.FloatField(null=True, blank=True)
    order_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"CD {self.coin} ${self.strike} edge:{self.edge_pts}pts"
