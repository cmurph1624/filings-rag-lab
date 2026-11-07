-- Runs once, when the db volume is first created. POSTGRES_DB makes frl_index (the serving
-- index, restored from the Nucbox's dump); frl_app holds served_queries, feedback and
-- rate_events, the only data here that can't be rebuilt (and the only data backed up).
CREATE DATABASE frl_app;
