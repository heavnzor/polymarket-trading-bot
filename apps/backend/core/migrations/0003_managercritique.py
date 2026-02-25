from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_learning_models_active_strategy"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManagerCritique",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("cycle_number", models.IntegerField(db_index=True)),
                ("critique_json", models.TextField(blank=True)),
                ("summary", models.TextField(blank=True)),
                ("trading_quality_score", models.IntegerField(blank=True, null=True)),
                ("risk_management_score", models.IntegerField(blank=True, null=True)),
                ("strategy_effectiveness_score", models.IntegerField(blank=True, null=True)),
                ("improvement_areas", models.TextField(blank=True)),
                ("code_changes_suggested", models.TextField(blank=True)),
                ("status", models.CharField(db_index=True, default="pending", max_length=32)),
                ("developer_result", models.TextField(blank=True)),
                ("branch_name", models.CharField(blank=True, max_length=255)),
                ("commit_hash", models.CharField(blank=True, max_length=255)),
                ("deploy_status", models.CharField(blank=True, max_length=32)),
                ("user_feedback", models.TextField(blank=True)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("deployed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
