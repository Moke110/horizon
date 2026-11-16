# Agent delivery ledger

Generation and delivery are separate. A completed turn is never authority to
send automatically. An explicit enqueue command snapshots its result into an
Agent-owned SQLite outbox. Rewriting does not enqueue or resend.

`DeliveryService` authenticates through its AgentService. Commands carry
schema_version=1, operation_id and, for existing deliveries, delivery_id and
expected_version. Reusing a command identity returns its prior result; conflicting
payloads are refused. Outbox changes, operation receipts and events commit together.

Before invoking a channel, send commits a sending state and a unique attempt key.
That reservation is the send linearization point: cancellation committed before
reservation prevents sending; a request reserved before cancellation may already
be in flight. A second cancellation check runs after acquiring the SQLite writer
lease for channel IO. The bounded channel request holds that cross-process lease,
so reconciliation/resend and cancellation writers cannot overtake an active send.
Channel implementations must enforce finite IO timeouts; they must not call
write-side Agent commands from send. Existing readers can inspect the committed
reservation. Existing receipts are retained. A known failure means the channel
proves no remote side effect; at most three attempts are allowed. Every other
exception, including lost acknowledgements, means unknown. Interrupted sending
also requires reconciliation, never automatic resend.

Channel lookup may return a verified receipt or no proof. Failed lookup remains
unknown. Unknown must be looked up before explicit duplicate-risk consent can
queue another attempt. Consent does not itself send. Channel implementations own
remote idempotency and proof rules; real Feishu lookup is validated separately.

No channel is configured by default. Tests inject a local controlled channel;
this does not authorize external recipients or sending model output to others.
