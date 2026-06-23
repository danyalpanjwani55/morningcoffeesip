# Payments setup scratch note (contains a secret — should be DROPPED on ingest)

This note deliberately carries a FAKE secret so you can watch the privacy gate
drop it. It must never become an event, never reach the model, never appear in a
wiki.

Payment processor test key (fake, for the fixture only):
api_key = sk-TESTfakekey0123456789abcdefXYZ  # pragma: allowlist secret

If the on-ramp is working, the run summary counts this file as `dropped_private`
and its content appears nowhere downstream.
