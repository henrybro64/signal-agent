-- Schema version 4.
-- Record which process owns a running/retrying schedule so startup reclaim
-- cannot steal a live `signal-agent schedule run` (#98).
-- signals.content_hash is rewritten in Python during init_db (legacy hashes
-- cannot be reproduced in SQL).

ALTER TABLE schedules ADD COLUMN last_owner_pid INTEGER;
ALTER TABLE schedule_runs ADD COLUMN owner_pid INTEGER;
