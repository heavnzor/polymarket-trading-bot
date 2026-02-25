from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trade",
            name="strategy",
            field=models.CharField(default="active", max_length=64),
        ),
        migrations.AlterField(
            model_name="position",
            name="strategy",
            field=models.CharField(default="active", max_length=64),
        ),
        migrations.RunSQL(
            sql=(
                "UPDATE core_trade SET strategy='active' "
                "WHERE strategy IS NULL OR strategy IN ('hold', 'scalp');"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql=(
                "UPDATE core_position SET strategy='active' "
                "WHERE strategy IS NULL OR strategy IN ('hold', 'scalp');"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.CreateModel(
            name="LearningJournalEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("cycle_number", models.IntegerField(db_index=True)),
                ("trades_proposed", models.IntegerField(default=0)),
                ("trades_executed", models.IntegerField(default=0)),
                ("trades_skipped", models.IntegerField(default=0)),
                ("skipped_markets", models.TextField(blank=True)),
                ("retrospective_json", models.TextField(blank=True)),
                ("price_snapshots", models.TextField(blank=True)),
                ("outcome_accuracy", models.FloatField(blank=True, null=True)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="LearningInsight",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("insight_type", models.CharField(db_index=True, max_length=64)),
                ("description", models.TextField()),
                ("evidence", models.TextField(blank=True)),
                ("proposed_action", models.TextField(blank=True)),
                ("severity", models.CharField(default="info", max_length=32)),
                ("status", models.CharField(db_index=True, default="active", max_length=32)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="LearningProposal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("proposal_type", models.CharField(db_index=True, max_length=64)),
                ("target", models.CharField(max_length=255)),
                ("current_value", models.TextField(blank=True)),
                ("proposed_value", models.TextField()),
                ("rationale", models.TextField()),
                ("risk_level", models.CharField(default="moderate", max_length=32)),
                ("status", models.CharField(db_index=True, default="pending", max_length=32)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="LearningGitChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("proposal_legacy_id", models.IntegerField(blank=True, db_index=True, null=True)),
                ("branch_name", models.CharField(db_index=True, max_length=255)),
                ("commit_hash", models.CharField(blank=True, max_length=255)),
                ("remote_name", models.CharField(default="origin", max_length=64)),
                ("push_status", models.CharField(db_index=True, default="pending", max_length=32)),
                ("justification", models.TextField(blank=True)),
                ("files_changed", models.JSONField(blank=True, default=list)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
