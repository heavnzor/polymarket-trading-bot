from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_managercritique"),
    ]

    operations = [
        migrations.CreateModel(
            name="MMQuote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("market_id", models.CharField(max_length=255)),
                ("token_id", models.CharField(max_length=255)),
                ("bid_order_id", models.CharField(blank=True, max_length=255, null=True)),
                ("ask_order_id", models.CharField(blank=True, max_length=255, null=True)),
                ("bid_price", models.FloatField()),
                ("ask_price", models.FloatField()),
                ("mid_price", models.FloatField(blank=True, null=True)),
                ("size", models.FloatField()),
                ("status", models.CharField(default="active", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="MMInventory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("market_id", models.CharField(max_length=255)),
                ("token_id", models.CharField(max_length=255)),
                ("net_position", models.FloatField(default=0)),
                ("avg_entry_price", models.FloatField(default=0)),
                ("unrealized_pnl", models.FloatField(default=0)),
                ("realized_pnl", models.FloatField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-updated_at"],
                "unique_together": {("market_id", "token_id")},
            },
        ),
        migrations.CreateModel(
            name="MMDailyMetric",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(unique=True)),
                ("markets_quoted", models.IntegerField(default=0)),
                ("quotes_placed", models.IntegerField(default=0)),
                ("fills_count", models.IntegerField(default=0)),
                ("round_trips", models.IntegerField(default=0)),
                ("spread_capture_rate", models.FloatField(default=0)),
                ("fill_quality_avg", models.FloatField(default=0)),
                ("adverse_selection_avg", models.FloatField(default=0)),
                ("pnl_gross", models.FloatField(default=0)),
                ("pnl_net", models.FloatField(default=0)),
                ("max_inventory", models.FloatField(default=0)),
                ("inventory_turns", models.FloatField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-date"]},
        ),
        migrations.CreateModel(
            name="CDSignal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("market_id", models.CharField(max_length=255)),
                ("token_id", models.CharField(blank=True, max_length=255, null=True)),
                ("coin", models.CharField(max_length=10)),
                ("strike", models.FloatField()),
                ("expiry_days", models.FloatField()),
                ("spot_price", models.FloatField()),
                ("vol_ewma", models.FloatField()),
                ("p_model", models.FloatField()),
                ("p_market", models.FloatField()),
                ("edge_pts", models.FloatField()),
                ("confirmation_count", models.IntegerField(default=1)),
                ("action", models.CharField(default="none", max_length=20)),
                ("size_usdc", models.FloatField(blank=True, null=True)),
                ("order_id", models.CharField(blank=True, max_length=255, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
