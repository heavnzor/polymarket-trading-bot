# Generated manually for initial control-plane bootstrap.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="BotCommand",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                (
                    "source",
                    models.CharField(
                        choices=[("dashboard", "Dashboard"), ("telegram", "Telegram"), ("api", "API")],
                        default="dashboard",
                        max_length=32,
                    ),
                ),
                ("command", models.CharField(max_length=128)),
                ("payload", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("executed", "Executed"), ("failed", "Failed")],
                        db_index=True,
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("result", models.JSONField(blank=True, null=True)),
                ("requested_by", models.CharField(blank=True, max_length=128)),
                ("dispatched_at", models.DateTimeField(blank=True, null=True)),
                ("executed_at", models.DateTimeField(blank=True, null=True)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BotSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=128, unique=True)),
                ("value", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["key"]},
        ),
        migrations.CreateModel(
            name="BotStatus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=128, unique=True)),
                ("value", models.JSONField(default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["key"]},
        ),
        migrations.CreateModel(
            name="PerformanceSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("snapshot_type", models.CharField(db_index=True, default="stats", max_length=64)),
                ("payload", models.JSONField(default=dict)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="RealtimeEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(db_index=True, max_length=64)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("emitted_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-emitted_at"]},
        ),
        migrations.CreateModel(
            name="Trade",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("market_id", models.CharField(db_index=True, max_length=255)),
                ("market_question", models.TextField(blank=True)),
                ("token_id", models.CharField(blank=True, max_length=255, null=True)),
                ("category", models.CharField(default="other", max_length=64)),
                ("side", models.CharField(max_length=16)),
                ("outcome", models.CharField(max_length=64)),
                ("size_usdc", models.DecimalField(decimal_places=6, max_digits=18)),
                ("price", models.DecimalField(decimal_places=6, max_digits=18)),
                ("intended_shares", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True)),
                ("filled_shares", models.DecimalField(decimal_places=6, default=0, max_digits=18)),
                ("avg_fill_price", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True)),
                ("edge", models.DecimalField(blank=True, decimal_places=6, max_digits=10, null=True)),
                ("edge_net", models.DecimalField(blank=True, decimal_places=6, max_digits=10, null=True)),
                ("confidence", models.DecimalField(blank=True, decimal_places=6, max_digits=10, null=True)),
                ("reasoning", models.TextField(blank=True)),
                ("status", models.CharField(db_index=True, default="pending", max_length=32)),
                ("order_id", models.CharField(blank=True, db_index=True, max_length=255, null=True)),
                ("strategy", models.CharField(default="hold", max_length=64)),
                ("executed_at", models.DateTimeField(blank=True, null=True)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="Position",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("market_id", models.CharField(db_index=True, max_length=255)),
                ("token_id", models.CharField(max_length=255)),
                ("market_question", models.TextField(blank=True)),
                ("outcome", models.CharField(max_length=64)),
                ("size", models.DecimalField(decimal_places=6, max_digits=18)),
                ("avg_price", models.DecimalField(decimal_places=6, max_digits=18)),
                ("current_price", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True)),
                ("pnl_unrealized", models.DecimalField(decimal_places=6, default=0, max_digits=18)),
                ("status", models.CharField(db_index=True, default="open", max_length=32)),
                ("category", models.CharField(default="other", max_length=64)),
                ("strategy", models.CharField(default="hold", max_length=64)),
                ("opened_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("source_updated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="OrderEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("trade_legacy_id", models.IntegerField(blank=True, db_index=True, null=True)),
                ("order_id", models.CharField(blank=True, db_index=True, max_length=255, null=True)),
                ("event_type", models.CharField(max_length=64)),
                ("status", models.CharField(blank=True, max_length=64)),
                ("size_matched", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True)),
                ("new_fill", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True)),
                ("avg_fill_price", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True)),
                ("note", models.TextField(blank=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "trade",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_events",
                        to="core.trade",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="position",
            constraint=models.UniqueConstraint(fields=("market_id", "token_id"), name="uniq_position_market_token"),
        ),
    ]
