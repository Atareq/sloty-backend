from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "transactions",
            "0002_transaction_is_cancelled_transaction_cancellation_reason_and_more",
        ),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'transactions_transaction'
                          AND column_name = 'is_voided'
                    ) THEN
                        UPDATE transactions_transaction
                        SET
                            is_cancelled = is_cancelled OR is_voided,
                            cancellation_reason = CASE
                                WHEN cancellation_reason = ''
                                THEN COALESCE(void_reason, '')
                                ELSE cancellation_reason
                            END,
                            cancelled_at = COALESCE(
                                cancelled_at,
                                voided_at
                            ),
                            cancelled_by_id = COALESCE(
                                cancelled_by_id,
                                voided_by_id
                            );

                        ALTER TABLE transactions_transaction
                            DROP COLUMN IF EXISTS is_voided,
                            DROP COLUMN IF EXISTS void_reason,
                            DROP COLUMN IF EXISTS voided_at,
                            DROP COLUMN IF EXISTS voided_by_id;
                    END IF;
                END
                $$;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
